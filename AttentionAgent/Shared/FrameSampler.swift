import CoreVideo
import CoreImage

struct LumaFrame {
    static let width = 36
    static let height = 64
    var px: [UInt8]
}

enum FrameSampler {
    private static let ciContext = CIContext()

    /// Reduces a screen frame to a 36x64 grayscale grid for cheap comparison.
    static func luma(from pb: CVPixelBuffer) -> LumaFrame? {
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }

        let w = LumaFrame.width, h = LumaFrame.height
        var out = [UInt8](repeating: 0, count: w * h)

        if CVPixelBufferIsPlanar(pb) {
            guard let base = CVPixelBufferGetBaseAddressOfPlane(pb, 0) else { return nil }
            let stride = CVPixelBufferGetBytesPerRowOfPlane(pb, 0)
            let pw = CVPixelBufferGetWidthOfPlane(pb, 0)
            let ph = CVPixelBufferGetHeightOfPlane(pb, 0)
            let p = base.assumingMemoryBound(to: UInt8.self)
            for y in 0..<h {
                for x in 0..<w {
                    var sum = 0
                    for sy in 0..<3 {
                        let py = min(ph - 1, (y * ph + (sy * ph) / 3) / h)
                        for sx in 0..<3 {
                            let pxl = min(pw - 1, (x * pw + (sx * pw) / 3) / w)
                            sum += Int(p[py * stride + pxl])
                        }
                    }
                    out[y * w + x] = UInt8(sum / 9)
                }
            }
            return LumaFrame(px: out)
        }

        guard CVPixelBufferGetPixelFormatType(pb) == kCVPixelFormatType_32BGRA,
              let base = CVPixelBufferGetBaseAddress(pb) else { return nil }
        let stride = CVPixelBufferGetBytesPerRow(pb)
        let pw = CVPixelBufferGetWidth(pb)
        let ph = CVPixelBufferGetHeight(pb)
        let p = base.assumingMemoryBound(to: UInt8.self)
        for y in 0..<h {
            for x in 0..<w {
                var sum = 0
                for sy in 0..<3 {
                    let py = min(ph - 1, (y * ph + (sy * ph) / 3) / h)
                    for sx in 0..<3 {
                        let pxl = min(pw - 1, (x * pw + (sx * pw) / 3) / w)
                        let i = py * stride + pxl * 4
                        sum += (29 * Int(p[i]) + 150 * Int(p[i + 1]) + 77 * Int(p[i + 2])) >> 8
                    }
                }
                out[y * w + x] = UInt8(sum / 9)
            }
        }
        return LumaFrame(px: out)
    }

    /// Downscaled copy for OCR. Keeps memory low inside the extension.
    static func cgImage(from pb: CVPixelBuffer, maxWidth: CGFloat = 540) -> CGImage? {
        let ci = CIImage(cvPixelBuffer: pb)
        let scale = min(1, maxWidth / ci.extent.width)
        let scaled = ci.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        return ciContext.createCGImage(scaled, from: scaled.extent)
    }
}
