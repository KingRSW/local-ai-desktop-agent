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
        isBusy = true
        let idx = messages.count
        messages.append(ChatMessage(role: .assistant, text: "…"))
        Task {
            // 本地优先：闹钟/日历这类明确意图，无需任何模型，离线即可完成
            if let local = await handleLocal(trimmed) {
                update(idx: idx, text: local)
                isBusy = false
                return
            }
            if backend == .onDevice {
                runOnDevice(prompt: trimmed, idx: idx)
            } else if backend == .ollama {
                sendOllama(trimmed, idx: idx)
            } else {
                update(idx: idx, text: "本机不支持 Apple 端侧模型，也没连 Mac Ollama。\n去「设置」连 Mac Ollama，或用右上「+」手动添加。\n也可以直接说「明早7点叫我起床」「周五下午3点开会」离线设置。")
                isBusy = false
            }
        }
    }

    private func update(idx: Int, text: String) {
        var copy = messages; copy[idx].text = text; messages = copy
    }

    // ---- Apple 端侧 ----
    private func runOnDevice(prompt: String, idx: Int) {
        Task {
            do {
                let response = try await session!.respond(to: prompt)
                let raw = String(describing: response.content).trimmingCharacters(in: .whitespacesAndNewlines)
                let content = raw.isEmpty ? "（已处理）" : raw
                update(idx: idx, text: content)
            } catch {
                update(idx: idx, text: "出错了：\(error.localizedDescription)")
            }
            isBusy = false
        }
    }

    // ---- Mac Ollama ----
    private func sendOllama(_ prompt: String, idx: Int) {
        Task {
            let result = await callOllamaAPI(prompt: prompt)
            update(idx: idx, text: result)
            isBusy = false
        }
    }

    // MARK: - 本地离线意图解析（不依赖模型，手机端即可用）
    private func handleLocal(_ text: String) async -> String? {
        let t = text
        let isAlarm = t.contains("闹钟") || t.contains("叫我") || t.contains("提醒") || t.contains("起床")
            || t.contains("叫醒") || t.contains("闹铃") || t.contains("叫起")
        let isCalendar = t.contains("日历") || t.contains("日程") || t.contains("会议")
            || t.contains("记一下") || t.contains("写到日历") || t.contains("安排") || t.contains("约")
        guard isAlarm || isCalendar else { return nil }

        guard let when = parseTime(t) else {
            return "我识别到这是\(isAlarm ? "闹钟" : "日历")需求，但没找到时间。\n麻烦补一下时间，比如「明早7点叫我起床」或「周五下午3点开会」。"
        }
        let title = extractTitle(t) ?? (isAlarm ? "提醒" : "日程")
        if isAlarm {
            return await setAlarmLocal(date: isoStr(when), title: title)
        } else {
            let end = when.addingTimeInterval(3600)
            return await addCalendarLocal(title: title, start: isoStr(when), end: isoStr(end), notes: "")
        }
    }

    private func isoStr(_ d: Date) -> String {
        let f = ISO8601DateFormatter()
        f.timeZone = TimeZone(identifier: "Asia/Shanghai")
        return f.string(from: d)
    }

    private func extractTitle(_ t: String) -> String? {
        var s = t
        let drops = ["闹钟", "叫我", "提醒我", "提醒", "起床", "叫醒", "闹铃", "叫起", "叫起",
                     "日历", "日程", "会议", "记一下", "写到日历", "安排", "约", "开会", "聚会",
                     "明早", "明晚", "明天", "今天", "后天", "大后天",
                     "早上", "早晨", "上午", "中午", "下午", "傍晚", "晚上", "凌晨",
                     "半小时后", "之后", "帮我", "请", "我想", "要", "点", "分", "个", "半",
                     "号", "日", "月", "周", "星期", "礼拜",
                     "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "两", "几",
                     "去", "到", "和", "与", "跟", "我们", "我", "你", "他", "她", "的", "了", "在",
                     ":", "：", "。", "，", ",", ".", " ", "\t", "\n"]
        for d in drops { s = s.replacingOccurrences(of: d, with: "") }
        // 去掉剩余的数字（时间）
        s = s.replacingOccurrences(of: #"\d+"#, with: "", options: .regularExpression)
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }

    private func parseTime(_ t: String) -> Date? {
        let cal = Calendar.current
        let now = Date()
        var comps = cal.dateComponents([.year, .month, .day, .hour, .minute, .second], from: now)
        comps.second = 0

        // 相对时长
        if let m = firstInt(#"(\d+)\s*分钟"#, in: t) { return now.addingTimeInterval(Double(m) * 60) }
        if let h = firstInt(#"(\d+)\s*小时"#, in: t) { return now.addingTimeInterval(Double(h) * 3600) }
        if t.contains("半小时后") || t.contains("半小时后") { return now.addingTimeInterval(1800) }

        // 相对日
        if t.contains("大后天") { comps.day! += 3 }
        else if t.contains("后天") { comps.day! += 2 }
        else if t.contains("明天") || t.contains("明早") || t.contains("明晚") { comps.day! += 1 }
        // “今天”无需调整

        // 月日：X月X号/日
        if let md = firstTwoInts(#"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]"#, in: t) {
            comps.month = md.0; comps.day = md.1
        }
        // 周几：周X / 星期X / 礼拜X（0=周日..6=周六）
        if let w = weekday(t) {
            var target = cal.dateComponents([.year, .month, .day], from: now)
            let todayW = cal.component(.weekday, from: now)
            var diff = (w - todayW + 7) % 7
            if diff == 0 { diff = 7 }
            target.day! += diff
            comps.month = target.month; comps.day = target.day
        }
        // X点X分 / X点半
        var period = 0
        if t.contains("下午") || t.contains("傍晚") || t.contains("晚上") { period = 12 }
        guard let hour = firstInt(#"(\d{1,2})\s*点"#, in: t) else { return nil }
        var h = hour
        if h < 12 { h += period }
        if h == 12 && period == 0 && (t.contains("上午") || t.contains("早上") || t.contains("早晨") || t.contains("凌晨")) { h = 0 }
        comps.hour = h
        if t.contains("点半") { comps.minute = 30 }
        else if let min = firstInt(#"点\s*(\d{1,2})\s*分"#, in: t) { comps.minute = min }
        else { comps.minute = 0 }
        return cal.date(from: comps)
    }

    private func weekday(_ t: String) -> Int? {
        let map = ["日": 1, "一": 2, "二": 3, "三": 4, "四": 5, "五": 6, "六": 7, "天": 1]
        for (cn, v) in map {
            if t.contains("周" + cn) || t.contains("星期" + cn) || t.contains("礼拜" + cn) { return v }
        }
        return nil
    }

    private func firstInt(_ pattern: String, in s: String) -> Int? {
        guard let r = try? NSRegularExpression(pattern: pattern),
              let m = r.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)),
              let rng = Range(m.range(at: 1), in: s) else { return nil }
        return Int(s[rng])
    }

    private func firstTwoInts(_ pattern: String, in s: String) -> (Int, Int)? {
        guard let r = try? NSRegularExpression(pattern: pattern),
              let m = r.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)),
              let r1 = Range(m.range(at: 1), in: s),
              let r2 = Range(m.range(at: 2), in: s),
              let a = Int(s[r1]), let b = Int(s[r2]) else { return nil }
        return (a, b)
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
