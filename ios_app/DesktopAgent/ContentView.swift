import SwiftUI
import UserNotifications
import EventKit

struct ContentView: View {
    @StateObject private var assistant = Assistant.shared
    @State private var input = ""
    @State private var showManual = false
    @State private var showModels = false
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if assistant.backend != .onDevice {
                    HStack(spacing: 8) {
                        Image(systemName: assistant.backend == .ollama ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .foregroundStyle(assistant.backend == .ollama ? .green : .orange)
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
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showModels = true } label: { Image(systemName: "cpu") }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showManual = true } label: { Image(systemName: "plus.circle") }
                }
            }
            .sheet(isPresented: $showManual) { ManualAddView() }
            .sheet(isPresented: $showModels) { ModelsView() }
            .sheet(isPresented: $showSettings) { SettingsView() }
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
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
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

// MARK: - 设置（Mac Ollama）
struct SettingsView: View {
    @Environment(\.dismiss) var dismiss
    @ObservedObject private var settings = ClawSettings.shared
    @State private var testResult = ""
    @State private var testing = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac Ollama（局域网 / 云端 AI）") {
                    Toggle("使用 Mac Ollama", isOn: $settings.useOllama)
                    TextField("服务器地址", text: $settings.baseURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("模型名", text: $settings.modelName)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button { Task { await test() } } label: {
                        if testing { ProgressView() } else { Text("测试连接") }
                    }
                    if !testResult.isEmpty {
                        Text(testResult).font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("说明") {
                    Text("曼德尔单元不支持 Apple 端侧模型。开启后用 Mac 上已运行的 Ollama，即可用 AI 设闹钟/日历。模型跑在 Mac，手机不下载权重，任何 iPhone 都能用。").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { Assistant.shared.refresh(); dismiss() }
                }
            }
        }
    }

    func test() async {
        testing = true; defer { testing = false }
        let base = settings.baseURL.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: ["/"])
        guard let url = URL(string: base + "/api/tags") else { testResult = "地址无效"; return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let models = json["models"] as? [[String: Any]] {
                let names = models.compactMap { $0["name"] as? String }
                testResult = "✅ 已连，可用模型：\(names.joined(separator: ", "))"
            } else { testResult = "⚠️ 有响应但格式异常" }
        } catch { testResult = "❌ 连接失败：\(error.localizedDescription)" }
    }
}

// MARK: - 模型下载管理（国内镜像 + 进度 + 错误提示）
@MainActor
final class ModelStore: ObservableObject {
    @Published var downloading = Set<String>()
    @Published var done = Set<String>()
    @Published var progress: [String: Double] = [:]
    @Published var errors: [String: String] = [:]
    private var sessions: [String: URLSession] = [:]
    private var delegates: [String: DLDelegate] = [:]

    func download(_ m: LocalModel) {
        guard let url = URL(string: m.url), !downloading.contains(m.id) else { return }
        downloading.insert(m.id); errors[m.id] = nil; progress[m.id] = 0
        let finish: (URL?, Error?) -> Void = { [weak self] tmp, err in
            guard let self else { return }
            Task { @MainActor in
                self.downloading.remove(m.id)
                self.sessions[m.id]?.finishTasksAndInvalidate()
                self.sessions[m.id] = nil; self.delegates[m.id] = nil
                if let err { self.errors[m.id] = "下载失败：\(err.localizedDescription)"; return }
                guard let tmp else { self.errors[m.id] = "未获取到文件"; return }
                let dest = LocalModel.dir.appendingPathComponent(m.file)
                do {
                    try FileManager.default.createDirectory(at: LocalModel.dir, withIntermediateDirectories: true, attributes: nil)
                    try? FileManager.default.removeItem(at: dest)
                    try FileManager.default.moveItem(at: tmp, to: dest)
                    let sz = (try? FileManager.default.attributesOfItem(atPath: dest.path)[.size] as? Int) ?? 0
                    if sz < 1024 * 1024 {
                        try? FileManager.default.removeItem(at: dest)
                        self.errors[m.id] = "文件过小（\(sz)B），可能下到了错误页。请检查网络后重试。"
                    } else {
                        self.done.insert(m.id); self.progress[m.id] = 1
                    }
                } catch {
                    self.errors[m.id] = error.localizedDescription
                }
            }
        }
        let del = DLDelegate(onProgress: { [weak self] got, total in
            Task { @MainActor in
                if total > 0 { self?.progress[m.id] = Double(got) / Double(total) }
            }
        }, onFinish: finish)
        delegates[m.id] = del
        let session = URLSession(configuration: .default, delegate: del, delegateQueue: .main)
        sessions[m.id] = session
        session.downloadTask(with: url).resume()
    }
}

final class DLDelegate: NSObject, URLSessionDownloadDelegate {
    let onProgress: (Int64, Int64) -> Void
    let onFinish: (URL?, Error?) -> Void
    init(onProgress: @escaping (Int64, Int64) -> Void, onFinish: @escaping (URL?, Error?) -> Void) {
        self.onProgress = onProgress; self.onFinish = onFinish
    }
    func urlSession(_ s: URLSession, downloadTask t: URLSessionDownloadTask,
                    didWriteData b: Int64, totalBytesWritten w: Int64, totalBytesExpectedToWrite e: Int64) {
        onProgress(w, e)
    }
    func urlSession(_ s: URLSession, downloadTask t: URLSessionDownloadTask, didFinishDownloadingTo loc: URL) {
        onFinish(loc, nil)
    }
    func urlSession(_ s: URLSession, task: URLSessionTask, didCompleteWithError err: Error?) {
        if let err { onFinish(nil, err) }
    }
}

// MARK: - 模型页（Ollama + 可下载 GGUF）
struct ModelsView: View {
    @Environment(\.dismiss) var dismiss
    @StateObject private var store = ModelStore()

    var body: some View {
        NavigationStack {
            List {
                Section("当前 AI 引擎") {
                    LabeledContent("后端", value: Assistant.shared.backend.rawValue)
                    if Assistant.shared.backend == .ollama {
                        LabeledContent("模型", value: ClawSettings.shared.modelName)
                    }
                }
                Section("Mac Ollama（局域网 / 云端）") {
                    Text("在「设置」里填 Mac 地址即可。模型跑在 Mac，手机不下载权重，任何 iPhone 都能用。").font(.footnote).foregroundStyle(.secondary)
                }
                Section("可下载模型（本地离线 · 推理引擎接入中）") {
                    Text("下载源：hf-mirror.com（国内可直连）。模型仅下载到本机，离线推理引擎后续接入后可用。注：Gemma 等在 HuggingFace 属需登录的门控模型，无法在 App 内匿名下载，故未列入。").font(.footnote).foregroundStyle(.secondary)
                    ForEach(LocalModel.catalog) { m in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(m.name).font(.headline)
                                    Text(m.desc).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if store.done.contains(m.id) {
                                    Label("已下载", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                                } else if store.downloading.contains(m.id) {
                                    Button { } label: { ProgressView(value: store.progress[m.id] ?? 0) }
                                        .frame(width: 70)
                                } else {
                                    Button("下载") { store.download(m) }.buttonStyle(.bordered)
                                }
                            }
                            if let err = store.errors[m.id], !store.done.contains(m.id) {
                                Text(err).font(.caption2).foregroundStyle(.red)
                            }
                            if store.downloading.contains(m.id), let p = store.progress[m.id], p > 0 {
                                ProgressView(value: p)
                                Text("\(Int(p * 100))%").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 2)
                    }
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

struct LocalModel: Identifiable {
    let id: String; let name: String; let desc: String; let url: String; let file: String
    static let dir: URL = {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("models")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()
    // 走 hf-mirror.com 国内镜像（huggingface.co 在国内常被墙，直连会超时）
    static let mirror = "https://hf-mirror.com"
    static let catalog = [
        LocalModel(id: "qwen2.5-0.5b", name: "Qwen2.5-0.5B-Instruct (GGUF Q4)", desc: "约0.5GB，最轻量、最快（引擎接入中）",
                   url: "\(mirror)/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
                   file: "qwen2.5-0.5b-q4.gguf"),
        LocalModel(id: "qwen2.5-1.5b", name: "Qwen2.5-1.5B-Instruct (GGUF Q4)", desc: "约1.1GB，轻快（引擎接入中）",
                   url: "\(mirror)/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
                   file: "qwen2.5-1.5b-q4.gguf"),
        LocalModel(id: "qwen2.5-3b", name: "Qwen2.5-3B-Instruct (GGUF Q4)", desc: "约2.0GB，效果更稳（引擎接入中）",
                   url: "\(mirror)/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
                   file: "qwen2.5-3b-q4.gguf"),
        LocalModel(id: "llama3.2-3b", name: "Llama-3.2-3B-Instruct (GGUF Q4)", desc: "约2.0GB，Meta 系（引擎接入中）",
                   url: "\(mirror)/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
                   file: "llama-3.2-3b-q4.gguf")
    ]
}
