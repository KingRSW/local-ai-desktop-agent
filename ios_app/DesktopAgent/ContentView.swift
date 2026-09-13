import SwiftUI
import UserNotifications
import EventKit

struct ContentView: View {
    @StateObject private var assistant = Assistant()
    @State private var input = ""
    @State private var showManual = false
    @State private var showModels = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !assistant.modelAvailable {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(assistant.statusText)
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                    }
                    .padding(.horizontal).padding(.vertical, 6)
                    .background(.ultraThinMaterial)
                }

                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(assistant.messages) { m in
                                MessageBubble(message: m).id(m.id)
                            }
                        }
                        .padding(.horizontal).padding(.top, 8)
                    }
                    .onChange(of: assistant.messages.count) { _, _ in
                        if let last = assistant.messages.last {
                            withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }

                inputBar
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("iPhoneClaw")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showManual = true } label: { Image(systemName: "plus.circle") }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showModels = true } label: { Image(systemName: "cpu") }
                }
            }
            .sheet(isPresented: $showManual) { ManualAddView() }
            .sheet(isPresented: $showModels) { ModelsView() }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("说点什么，例如：明早7点叫我起床", text: $input, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(10)
                .lineLimit(1...4)
            Button {
                let t = input; input = ""
                Task { await assistant.send(t) }
            } label: {
                Image(systemName: "arrow.up.circle.fill").font(.title)
            }
            .disabled(input.isEmpty || assistant.isBusy)
        }
        .padding(8)
        .glassEffect(.regular, in: Capsule())
        .padding(.horizontal, 10)
        .padding(.bottom, 6)
    }
}

struct MessageBubble: View {
    let message: ChatMessage
    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 44) }
            Text(message.text.isEmpty ? "…" : message.text)
                .padding(12)
                .background(
                    message.role == .user
                        ? Color.accentColor
                        : Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 16)
                )
                .foregroundStyle(message.role == .user ? Color.white : Color.primary)
                .textSelection(.enabled)
            if message.role == .assistant { Spacer(minLength: 44) }
        }
    }
}

// MARK: - 手动添加（模型不可用时的兜底）
struct ManualAddView: View {
    @Environment(\.dismiss) var dismiss
    @State private var mode: AddMode = .alarm
    @State private var date = Date().addingTimeInterval(3600)
    @State private var title = ""
    @State private var note = ""
    @State private var result = ""

    enum AddMode: String, CaseIterable, Identifiable {
        case alarm = "闹钟"; case event = "日历"
        var id: String { rawValue }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("类型", selection: $mode) {
                        ForEach(AddMode.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
                DatePicker("时间", selection: $date, displayedComponents: [.date, .hourAndMinute])
                TextField("标题", text: $title)
                if mode == .event { TextField("备注 / 地点", text: $note) }
                if !result.isEmpty {
                    Section { Text(result).font(.footnote).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("手动添加")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
                ToolbarItem(placement: .bottomBar) {
                    Button(mode == .alarm ? "设为闹钟" : "加到日历") {
                        if mode == .alarm { setAlarm() } else { addEvent() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(title.isEmpty)
                }
            }
        }
    }

    func setAlarm() {
        let center = UNUserNotificationCenter.current()
        Task {
            _ = try await center.requestAuthorization(options: [.alert, .sound, .badge])
            let content = UNMutableNotificationContent()
            content.title = "⏰ iPhoneClaw 闹钟"
            content.body = title
            content.sound = .default
            let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
            let req = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: trigger)
            do {
                try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
                    center.add(req) { e in if let e { c.resume(throwing: e) } else { c.resume() } }
                }
                let fmt = DateFormatter(); fmt.dateStyle = .medium; fmt.timeStyle = .short; fmt.locale = Locale(identifier: "zh_CN")
                result = "✅ 已设置闹钟：\(fmt.string(from: date))"
            } catch { result = "❌ \(error.localizedDescription)" }
        }
    }

    func addEvent() {
        let store = EKEventStore()
        Task {
            do {
                let ok = try await store.requestFullAccessToEvents()
                guard ok else { result = "❌ 日历访问被拒绝"; return }
                let ev = EKEvent(eventStore: store)
                ev.title = title
                ev.startDate = date
                ev.endDate = date.addingTimeInterval(3600)
                ev.notes = note.isEmpty ? nil : note
                ev.calendar = store.defaultCalendarForNewEvents
                try store.save(ev, span: .thisEvent)
                result = "✅ 已添加到日历"
            } catch { result = "❌ \(error.localizedDescription)" }
        }
    }
}

// MARK: - 模型页
struct ModelsView: View {
    @Environment(\.dismiss) var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("当前可用") {
                    HStack(spacing: 12) {
                        Image(systemName: "checkmark.seal.fill").foregroundStyle(.green).font(.title2)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Apple 端侧模型").font(.headline)
                            Text("Foundation Models · 完全离线 · 隐私").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Section("即将推出（可下载）") {
                    LabeledContent("Qwen 小模型", value: "敬请期待")
                    LabeledContent("下载模型管理", value: "规划中")
                }
                Section {
                    Text("iPhoneClaw 默认用 iPhone 自带 AI 芯片在本地推理，不上传任何数据。后续版本会加入可下载的额外模型。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("模型")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
        }
    }
}
