import AppKit
import ImageIO

// Quick Look pads rectangular SVG thumbnails to a square. Keep the SVG's top-left canvas.
let source = URL(fileURLWithPath: CommandLine.arguments[1])
let destination = URL(fileURLWithPath: CommandLine.arguments[2])
guard let input = CGImageSourceCreateWithURL(source as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(input, 0, nil),
      let cropped = image.cropping(to: CGRect(x: 0, y: 0, width: 540, height: 380)),
      let output = CGImageDestinationCreateWithURL(destination as CFURL, "public.png" as CFString, 1, nil)
else { fatalError("Cannot render DMG background") }
CGImageDestinationAddImage(output, cropped, nil)
if !CGImageDestinationFinalize(output) { fatalError("Cannot save DMG background") }
