// macOS 系统级 OCR（Vision 框架）——把截图里的文字识别出来，中文精度高、离线免费。
// 编译：swiftc -O ocr.swift -o ocr
// 运行：./ocr <图片路径>
// 输出：每行 "文本<TAB>x<TAB>y<TAB>w<TAB>h"（归一化坐标 0~1，左下角为原点）
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: ocr <image>\n".data(using: .utf8)!)
    exit(2)
}
guard let img = NSImage(contentsOfFile: args[1]),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image: \(args[1])\n".data(using: .utf8)!)
    exit(3)
}

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
} catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(4)
}

if let results = req.results {
    for obs in results {
        guard let top = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        print("\(top.string)\t\(b.origin.x)\t\(b.origin.y)\t\(b.size.width)\t\(b.size.height)")
    }
}
