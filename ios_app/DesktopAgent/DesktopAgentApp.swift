import SwiftUI
import FoundationModels
import UserNotifications
import EventKit

@main
struct iPhoneClawApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}

// MARK: - 连接设置（Mac Ollama）
final class ClawSettings: ObservableObject {
    static let shared = ClawSettings()
    private let d = UserDefaults.standard
    @Published var baseURL: String {
        didSet { d.set(baseURL, forKey: "ollamaBaseURL") }
    }
    @Published var modelName: String {
        didSet { d.set(modelName, forKey: "ollamaModel") }
    }
    @Published var useOllama: Bool {
        didSet { d.set(useOllama, forKey: "useOllama") }
    }
    init() {
        self.baseURL = d.string(forKey: "ollamaBaseURL") ?? "http://192.168.101.161:11434"
        self.modelName = d.string(forKey: "ollamaModel") ?? "qwen3.5:9b"
        self.useOllama = d.object(forKey: "useOllama") as? Bool ?? true
    }
}

// MARK: - 对话消息
struct ChatMessage: Identifiable {
    let id = UUID()
    let role: Role
    var text: String
    enum Role { case user, assistant }
}

// MARK: - 助手（双后端：Apple 端侧 / Mac Ollama）
@MainActor
final class Assistant: ObservableObject {
    static let shared = Assistant()
    @Published var messages: [ChatMessage] = []
    @Published var isBusy = false
    @Published var statusText = ""
    @Published var backend: Backend = .none
    enum Backend: String { case onDevice = "Apple 端侧", ollama = "Mac Ollama", none = "未连接" }

    private var session: LanguageModelSession?
    private let instructions = """
    你是 iPhoneClaw，一个运行在 iPhone 上的 AI 助手。
    你可以帮用户设置闹钟（调用 set_alarm 工具）和创建日历事件（调用 create_calendar_event 工具）。
    当用户表达时间相关意图时，请尽量从对话中推断出准确时间，以 ISO8601 格式（含时区 +08:00）传给工具。
    用简洁友好的中文回答，并确认已完成的操作。
    """

    init() { refresh() }

    func refresh() {
        let model = SystemLanguageModel.default
        if case .available = model.availability {
            backend = .onDevice
            if session == nil {
                session = LanguageModelSession(tools: [FoundationAlarmTool(), FoundationCalendarTool()],
                                               instructions: instructions)
            }
            statusText = "Apple 端侧模型已就绪"
        } else if ClawSettings.shared.useOllama {
            backend = .ollama
            statusText = "已连 Mac Ollama：\(ClawSettings.shared.modelName)"
        } else {
            backend = .none
            statusText = "端侧模型不可用：去「设置」连 Mac Ollama，或用右上「+」手动添加"
        }
    }

    func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBusy else { return }
        messages.append(ChatMessage(role: .user, text: trimmed))
        if backend == .onDevice {
            sendOnDevice(trimmed)
        } else if backend == .ollama {
            sendOllama(trimmed)
        } else {
            messages.append(ChatMessage(role: .assistant,
                text: "本机不支持 Apple 端侧模型，也没连 Mac Ollama。\n去「设置」连 Mac Ollama，或用右上「+」手动添加闹钟/日历。"))
        }
    }

    // ---- Apple 端侧 ----
    private func sendOnDevice(_ prompt: String) {
        isBusy = true
        let idx = messages.count
        messages.append(ChatMessage(role: .assistant, text: "…"))
        Task {
            do {
                let response = try await session!.respond(to: prompt)
                let raw = String(describing: response.content).trimmingCharacters(in: .whitespacesAndNewlines)
                let content = raw.isEmpty ? "（已处理）" : raw
                var copy = messages; copy[idx].text = content; messages = copy
            } catch {
                var copy = messages; copy[idx].text = "出错了：\(error.localizedDescription)"; messages = copy
            }
            isBusy = false
        }
    }

    // ---- Mac Ollama ----
    private func sendOllama(_ prompt: String) {
        isBusy = true
        let idx = messages.count
        messages.append(ChatMessage(role: .assistant, text: "…"))
        Task {
            let result = await callOllamaAPI(prompt: prompt)
            var copy = messages; copy[idx].text = result; messages = copy
            isBusy = false
        }
    }

    private func callOllamaAPI(prompt: String) async -> String {
        let s = ClawSettings.shared
        var base = s.baseURL.trimmingCharacters(in: .whitespaces)
        if base.hasSuffix("/") { base.removeLast() }
        guard let url = URL(string: base + "/api/chat") else { return "❌ 服务器地址无效" }
        let payload: [String: Any] = [
            "model": s.modelName,
            "messages": [["role": "system", "content": instructions],
                         ["role": "user", "content": prompt]],
            "tools": [alarmSchema, calendarSchema],
            "stream": false
        ]
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 120
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let message = json["message"] as? [String: Any] else {
                return "⚠️ Ollama 返回无法解析（可能模型名不对或正在下载）"
            }
            if let tcs = message["tool_calls"] as? [[String: Any]], !tcs.isEmpty {
                var lines: [String] = []
                for tc in tcs {
                    if let fn = tc["function"] as? [String: Any],
                       let name = fn["name"] as? String,
                       let args = fn["arguments"] as? [String: Any] {
                        lines.append(await executeTool(name: name, args: args))
                    }
                }
                return lines.joined(separator: "\n")
            } else if let content = message["content"] as? String {
                return content.isEmpty ? "（已处理）" : content
            }
            return "（已处理）"
        } catch {
            return "❌ 连接 Ollama 失败：\(error.localizedDescription)\n检查 Mac Ollama 是否运行、是否监听局域网、地址是否正确"
        }
    }

    private func executeTool(name: String, args: [String: Any]) async -> String {
        switch name {
        case "set_alarm":
            return await setAlarmLocal(date: args["date"] as? String ?? "", title: args["title"] as? String ?? "")
        case "create_calendar_event":
            return await addCalendarLocal(title: args["title"] as? String ?? "",
                                          start: args["start"] as? String ?? "",
                                          end: args["end"] as? String ?? "",
                                          notes: args["notes"] as? String ?? "")
        default:
            return "未知工具：\(name)"
        }
    }

    // MARK: 本地执行（闹钟/日历）
    func setAlarmLocal(date: String, title: String) async -> String {
        let iso = ISO8601DateFormatter()
        guard let when = iso.date(from: date) else {
            return "无法解析时间：\(date)。请给 ISO8601 含时区，如 2026-09-14T07:00:00+08:00"
        }
        let center = UNUserNotificationCenter.current()
        _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
        let content = UNMutableNotificationContent()
        content.title = "⏰ iPhoneClaw 闹钟"
        content.body = title
        content.sound = .default
        let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: when)
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: trigger)
        do {
            try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
                center.add(request) { e in if let e { c.resume(throwing: e) } else { c.resume() } }
            }
            let fmt = DateFormatter(); fmt.dateStyle = .medium; fmt.timeStyle = .short; fmt.locale = Locale(identifier: "zh_CN")
            return "✅ 已设置闹钟「\(title)」，时间 \(fmt.string(from: when))"
        } catch {
            return "❌ 闹钟设置失败：\(error.localizedDescription)"
        }
    }

    func addCalendarLocal(title: String, start: String, end: String, notes: String) async -> String {
        let iso = ISO8601DateFormatter()
        guard let s = iso.date(from: start), let e = iso.date(from: end) else {
            return "无法解析时间，请给 ISO8601 含时区"
        }
        let store = EKEventStore()
        do {
            let ok = try await store.requestFullAccessToEvents()
            guard ok else { return "❌ 日历访问被拒绝" }
        } catch {
            return "❌ 日历授权失败：\(error.localizedDescription)"
        }
        let ev = EKEvent(eventStore: store)
        ev.title = title; ev.startDate = s; ev.endDate = e
        ev.notes = notes.isEmpty ? nil : notes
        ev.calendar = store.defaultCalendarForNewEvents
        do {
            try store.save(ev, span: .thisEvent)
            let fmt = DateFormatter(); fmt.dateStyle = .medium; fmt.timeStyle = .short; fmt.locale = Locale(identifier: "zh_CN")
            return "✅ 已添加到日历「\(title)」：\(fmt.string(from: s)) ~ \(fmt.string(from: e))"
        } catch {
            return "❌ 保存失败：\(error.localizedDescription)"
        }
    }

    // MARK: Ollama 工具 schema
    private var alarmSchema: [String: Any] {
        ["type": "function", "function": [
            "name": "set_alarm",
            "description": "在指定时间设置闹钟/提醒，到时手机响铃并弹通知。用户说“叫我”“闹钟”“提醒我”“几点叫我起床”时使用。",
            "parameters": ["type": "object", "properties": [
                "date": ["type": "string", "description": "闹钟触发时间，ISO8601 含时区 +08:00，例如 2026-09-14T07:00:00+08:00"],
                "title": ["type": "string", "description": "闹钟标题/内容，简短中文"]
            ], "required": ["date", "title"]]
        ]]
    }
    private var calendarSchema: [String: Any] {
        ["type": "function", "function": [
            "name": "create_calendar_event",
            "description": "在系统日历新建日程/事件。用户说“加日历”“日程”“会议”“记一下”“写到日历”时使用。",
            "parameters": ["type": "object", "properties": [
                "title": ["type": "string", "description": "事件标题"],
                "start": ["type": "string", "description": "开始时间 ISO8601 含时区"],
                "end": ["type": "string", "description": "结束时间 ISO8601 含时区"],
                "notes": ["type": "string", "description": "备注/地点，没有就留空字符串"]
            ], "required": ["title", "start", "end", "notes"]]
        ]]
    }
}

// MARK: - Apple 端侧模型工具（包装本地执行）
struct FoundationAlarmTool: Tool {
    var name = "set_alarm"
    var description = "在指定时间设置闹钟/提醒，到时手机响铃并弹通知。用户说“叫我”“闹钟”“提醒我”“几点叫我起床”时使用。"
    @Generable struct Arguments: Sendable {
        @Guide(description: "闹钟触发时间，ISO8601 含时区，例如 2026-09-14T07:00:00+08:00") var date: String
        @Guide(description: "闹钟标题/内容，简短中文，例如 “起床” 或 “吃药”") var title: String
    }
    func call(arguments: Arguments) async throws -> String {
        await Assistant.shared.setAlarmLocal(date: arguments.date, title: arguments.title)
    }
}
struct FoundationCalendarTool: Tool {
    var name = "create_calendar_event"
    var description = "在系统日历新建日程/事件。用户说“加日历”“日程”“会议”“记一下”“写到日历”时使用。"
    @Generable struct Arguments: Sendable {
        @Guide(description: "事件标题") var title: String
        @Guide(description: "开始时间 ISO8601 含时区，例如 2026-09-14T15:00:00+08:00") var start: String
        @Guide(description: "结束时间 ISO8601 含时区，例如 2026-09-14T16:00:00+08:00") var end: String
        @Guide(description: "备注/地点，没有就留空字符串") var notes: String
    }
    func call(arguments: Arguments) async throws -> String {
        await Assistant.shared.addCalendarLocal(title: arguments.title, start: arguments.start, end: arguments.end, notes: arguments.notes)
    }
}
