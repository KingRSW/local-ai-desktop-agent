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

// MARK: - 对话消息
struct ChatMessage: Identifiable {
    let id = UUID()
    let role: Role
    var text: String
    enum Role { case user, assistant }
}

// MARK: - 助手（端侧模型 + 工具）
@MainActor
final class Assistant: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var isBusy = false
    @Published var statusText = ""
    @Published var modelAvailable = false
    private var session: LanguageModelSession?

    init() {
        let model = SystemLanguageModel.default
        switch model.availability {
        case .available:
            modelAvailable = true
            session = LanguageModelSession(tools: [AlarmTool(), CalendarTool()], instructions: """
            你是 iPhoneClaw，一个完全运行在 iPhone 本地的 AI 助手。
            你可以帮用户设置闹钟（调用 set_alarm 工具）和创建日历事件（调用 create_calendar_event 工具）。
            当用户表达时间相关意图时，请尽量从对话中推断出准确时间，以 ISO8601 格式（含时区 +08:00）传给工具。
            用简洁友好的中文回答，并确认已完成的操作。
            """)
            statusText = "Apple 端侧模型已就绪"
        case .unavailable(let reason):
            modelAvailable = false
            statusText = "端侧模型不可用：\(String(describing: reason))"
        }
    }

    func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        messages.append(ChatMessage(role: .user, text: trimmed))
        guard modelAvailable, let session else {
            messages.append(ChatMessage(role: .assistant,
                text: "本机暂不支持端侧模型（需 iOS 26 + 支持 Apple Intelligence 的设备）。\n可用右上角「+」手动添加闹钟/日历。"))
            return
        }
        isBusy = true
        let idx = messages.count
        messages.append(ChatMessage(role: .assistant, text: "…"))
        Task {
            do {
                let response = try await session.respond(to: trimmed)
                let raw = String(describing: response.content)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                let content = raw.isEmpty ? "（已处理）" : raw
                var copy = messages
                copy[idx].text = content
                messages = copy
            } catch {
                var copy = messages
                copy[idx].text = "出错了：\(error.localizedDescription)"
                messages = copy
            }
            isBusy = false
        }
    }
}

// MARK: - 工具：设闹钟（本地通知）
struct AlarmTool: Tool {
    var name = "set_alarm"
    var description = "在指定时间设置闹钟/提醒，到时手机响铃并弹通知。用户说“叫我”“闹钟”“提醒我”“几点叫我起床”时使用。"

    @Generable
    struct Arguments: Sendable {
        @Guide(description: "闹钟触发时间，ISO8601 含时区，例如 2026-09-14T07:00:00+08:00")
        var date: String
        @Guide(description: "闹钟标题/内容，简短中文，例如 “起床” 或 “吃药”")
        var title: String
    }

    func call(arguments: Arguments) async throws -> String {
        let iso = ISO8601DateFormatter()
        guard let when = iso.date(from: arguments.date) else {
            return "无法解析时间：\(arguments.date)。请给 ISO8601 格式，如 2026-09-14T07:00:00+08:00"
        }
        let center = UNUserNotificationCenter.current()
        _ = try await center.requestAuthorization(options: [.alert, .sound, .badge])
        let content = UNMutableNotificationContent()
        content.title = "⏰ iPhoneClaw 闹钟"
        content.body = arguments.title
        content.sound = .default
        let comps = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: when)
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: trigger)
        do {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                center.add(request) { error in
                    if let error { cont.resume(throwing: error) } else { cont.resume() }
                }
            }
            let fmt = DateFormatter()
            fmt.dateStyle = .medium; fmt.timeStyle = .short; fmt.locale = Locale(identifier: "zh_CN")
            return "✅ 已设置闹钟「\(arguments.title)」，时间 \(fmt.string(from: when))"
        } catch {
            return "❌ 闹钟设置失败：\(error.localizedDescription)"
        }
    }
}

// MARK: - 工具：建日历事件（EventKit）
struct CalendarTool: Tool {
    var name = "create_calendar_event"
    var description = "在系统日历新建日程/事件。用户说“加日历”“日程”“会议”“记一下”“写到日历”时使用。"

    @Generable
    struct Arguments: Sendable {
        @Guide(description: "事件标题")
        var title: String
        @Guide(description: "开始时间 ISO8601 含时区，例如 2026-09-14T15:00:00+08:00")
        var start: String
        @Guide(description: "结束时间 ISO8601 含时区，例如 2026-09-14T16:00:00+08:00")
        var end: String
        @Guide(description: "备注/地点，没有就留空字符串")
        var notes: String
    }

    func call(arguments: Arguments) async throws -> String {
        let iso = ISO8601DateFormatter()
        guard let s = iso.date(from: arguments.start), let e = iso.date(from: arguments.end) else {
            return "无法解析时间，请给 ISO8601 格式含时区"
        }
        let store = EKEventStore()
        do {
            let ok = try await store.requestFullAccessToEvents()
            if !ok { return "❌ 日历访问被拒绝" }
        } catch {
            return "❌ 日历授权失败：\(error.localizedDescription)"
        }
        let event = EKEvent(eventStore: store)
        event.title = arguments.title
        event.startDate = s
        event.endDate = e
        event.notes = arguments.notes.isEmpty ? nil : arguments.notes
        event.calendar = store.defaultCalendarForNewEvents
        do {
            try store.save(event, span: .thisEvent)
            let fmt = DateFormatter()
            fmt.dateStyle = .medium; fmt.timeStyle = .short; fmt.locale = Locale(identifier: "zh_CN")
            return "✅ 已添加到日历「\(arguments.title)」：\(fmt.string(from: s)) ~ \(fmt.string(from: e))"
        } catch {
            return "❌ 保存失败：\(error.localizedDescription)"
        }
    }
}
