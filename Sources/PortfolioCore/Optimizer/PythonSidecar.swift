import Foundation

/// A step emitted by the optimizer sidecar (structured progress).
public struct SidecarStep: Codable, Hashable {
    public let step: String
    public let message: String
}

public enum SidecarError: Error, CustomStringConvertible {
    case interpreterNotFound(String)
    case nonZeroExit(code: Int32, stderr: String)

    public var description: String {
        switch self {
        case .interpreterNotFound(let p): return "Python interpreter not found: \(p)"
        case .nonZeroExit(let c, let e): return "sidecar exit \(c): \(e)"
        }
    }
}

/// Runs the vendored Python scripts (numbers extraction / optimization) as a subprocess.
/// This is the single integration point reused by .numbers import (Phase 1) and the
/// optimizer (Phase 6 / capability 2).
public final class PythonSidecar {
    public let interpreterPath: String
    public let scriptsDir: URL
    public var currentDirectoryURL: URL?

    public init(interpreterPath: String, scriptsDir: URL, currentDirectoryURL: URL? = nil) {
        self.interpreterPath = interpreterPath
        self.scriptsDir = scriptsDir
        self.currentDirectoryURL = currentDirectoryURL
    }

    /// Convenience: locate a vendored script by filename.
    public func scriptURL(_ filename: String) -> URL {
        scriptsDir.appendingPathComponent(filename)
    }

    /// Run a script, returning captured stdout. Throws SidecarError on failure.
    @discardableResult
    public func run(script: String, args: [String]) throws -> String {
        let scriptPath = scriptURL(script).path
        guard FileManager.default.fileExists(atPath: interpreterPath) else {
            throw SidecarError.interpreterNotFound(interpreterPath)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: interpreterPath)
        process.arguments = [scriptPath] + args
        if let cwd = currentDirectoryURL {
            process.currentDirectoryURL = cwd
        }
        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        try process.run()
        process.waitUntilExit()

        let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
        let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
        let stdout = String(data: outData, encoding: .utf8) ?? ""
        let stderr = String(data: errData, encoding: .utf8) ?? ""

        guard process.terminationStatus == 0 else {
            throw SidecarError.nonZeroExit(code: process.terminationStatus, stderr: stderr)
        }
        return stdout
    }

    /// Convenience: decode a JSON sidecar output into a Codable type.
    public func runDecoded<T: Decodable>(script: String, args: [String], as: T.Type) throws -> T {
        let stdout = try run(script: script, args: args)
        return try JSONDecoder().decode(T.self, from: Data(stdout.utf8))
    }
}
