import AppKit
import Foundation
import Vision

struct OCRBox: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct OCRPage: Codable {
    let page: Int
    let observations: [OCRBox]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.isEmpty {
    fail("usage: vision_ocr.swift PAGE.png [PAGE.png ...]")
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]

for (index, path) in arguments.enumerated() {
    guard let image = NSImage(contentsOfFile: path) else {
        fail("cannot open image: \(path)")
    }
    var proposed = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &proposed, context: nil, hints: nil) else {
        fail("cannot create CGImage: \(path)")
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    do {
        try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    } catch {
        fail("Vision OCR failed for \(path): \(error)")
    }

    let observations = (request.results ?? []).compactMap { observation -> OCRBox? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        return OCRBox(
            text: candidate.string,
            confidence: candidate.confidence,
            bbox: [rect.minX, 1.0 - rect.maxY, rect.maxX, 1.0 - rect.minY]
        )
    }.sorted {
        if abs($0.bbox[1] - $1.bbox[1]) > 0.005 { return $0.bbox[1] < $1.bbox[1] }
        return $0.bbox[0] < $1.bbox[0]
    }

    let page = OCRPage(page: index + 1, observations: observations)
    guard let payload = try? encoder.encode(page) else { fail("cannot encode OCR output") }
    FileHandle.standardOutput.write(payload)
    FileHandle.standardOutput.write(Data("\n".utf8))
}
