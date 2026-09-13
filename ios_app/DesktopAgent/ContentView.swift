import SwiftUI

struct ContentView: View {
    @AppStorage("da_host") var host: String = ""
    @AppStorage("da_port") var port: String = "8742"
    @AppStorage("da_token") var token: String = ""
    @AppStorage("da_safe") var safeMode: Bool = false

    @State private var cmd: String = ""
    @State private var running = false
    @State private var logs: [LogLine] = []
    @State private var shot: UIImage? = nil
    @State private var showingSettings = false
    @State private var baseURL: String = ""
    @State private var timer: Timer? = nil

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                screenView
                Divider().opacity(0.2)
                controlPanel
                Divider().opacity(0.2)
                logView
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("桌面员工")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showingSettings = true } label: {
                        Image(systemName: "gearshape.fill")
                    }
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView(host: $host, port: $port, token: $token) }
            .onAppear { updateBaseURL(); startPolling() }
            .onDisappear { timer?.invalidate() }
        }
    }

    private var screenView: some View {
        Group {
            if let img = shot {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .background(Color.black)
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "display")
                        .font(.system(size: 40))
                        .foregroundStyle(.secondary)
                    Text("Mac 实时画面")
                        .foregroundStyle(.secondary)
                    Text("发指令或点刷新即可看到屏幕")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, minHeight: 220)
            }
        }
    }

    private var controlPanel: some View {
        VStack(spacing: 10) {
            HStack(spacing: 8) {
                TextField("指令…", text: $cmd, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...3)
                Button(action: send) {
                    Text(running ? "执行中" : "发送")
                        .fontWeight(.semibold)
                        .frame(minWidth: 60)
                }
                .buttonStyle(.borderedProminent)
                .disabled(running || cmd.isEmpty)
            }
            HStack {
                Toggle("安全模式", isOn: $safeMode)
                Spacer()
                Button("停止") { stop() }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(!running)
                Button("刷新画面") { snap() }
                    .buttonStyle(.bordered)
                    .disabled(running)
            }
            .font(.subheadline)
        }
        .padding()
    }

    private var logView: some View {
        List {
            Section("日志") {
                ForEach(logs.reversed()) { line in
                    HStack(alignment: .top, spacing: 6) {
                        Text(line.time)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .frame(width: 46, alignment: .leading)
                        Text(line.text)
                            .font(.caption)
                            .foregroundStyle(line.isError ? Color.red : Color.primary)
                            .textSelection(.enabled)
                    }
                }
            }
        }
        .listStyle(.plain)
    }

    private func updateBaseURL() {
        let h = host.isEmpty ? "127.0.0.1" : host
        baseURL = "http://\(h):\(port)"
    }

    private func startPolling() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { _ in
            fetchStatus()
        }
        fetchStatus()
    }

    private func send() {
        updateBaseURL()
        guard let url = URL(string: "\(baseURL)/api/run") else { return }
        let body: [String: Any] = ["cmd": cmd, "no_exec": safeMode, "token": token]
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        running = true
        URLSession.shared.dataTask(with: req) { _, _, _ in
            DispatchQueue.main.async { fetchStatus() }
        }.resume()
        cmd = ""
    }

    private func fetchStatus() {
        guard let url = URL(string: "\(baseURL)/api/status") else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return }
            DispatchQueue.main.async {
                running = (json["running"] as? Bool) ?? false
                if let arr = json["log"] as? [[String: Any]] {
                    logs = arr.map { LogLine(time: $0["t"] as? String ?? "", text: $0["msg"] as? String ?? "") }
                }
                fetchShot()
            }
        }.resume()
    }

    private func fetchShot() {
        guard let url = URL(string: "\(baseURL)/api/shot?t=\(Date().timeIntervalSince1970)") else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data, let img = UIImage(data: data) else { return }
            DispatchQueue.main.async { shot = img }
        }.resume()
    }

    private func snap() {
        guard let url = URL(string: "\(baseURL)/api/snap") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["token": token])
        URLSession.shared.dataTask(with: req) { _, _, _ in
            DispatchQueue.main.async { fetchShot() }
        }.resume()
    }

    private func stop() {
        updateBaseURL()
        guard let url = URL(string: "\(baseURL)/api/stop") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["token": token])
        URLSession.shared.dataTask(with: req) { _, _, _ in
            DispatchQueue.main.async { fetchStatus() }
        }.resume()
    }
}

struct LogLine: Identifiable {
    let id = UUID()
    let time: String
    let text: String
    var isError: Bool { text.hasPrefix("⛔") || text.hasPrefix("🔍 OCR 没找到") }
}

struct SettingsView: View {
    @Binding var host: String
    @Binding var port: String
    @Binding var token: String
    @Environment(\.dismiss) var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac 地址") {
                    TextField("IP 地址（如 192.168.1.5）", text: $host)
                        .keyboardType(.numbersAndPunctuation)
                    TextField("端口", text: $port)
                        .keyboardType(.numberPad)
                }
                Section("连接令牌") {
                    TextField("token（Mac 端启动时显示）", text: $token)
                }
                Section {
                    Text("手机和 Mac 必须连同一个 WiFi。Mac 端启动后会打印 IP 和 token。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("连接设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
        }
    }
}
