import Foundation
import SQLite3

/// 能力3 data safety: daily snapshot backup, rollback, and JSON export/import.
/// Backups are timestamped copies of the SQLite file + a JSON export of the
/// small tables (assets/holdings/snapshots) for portability & inspection.
public final class BackupManager {
    public let backupDir: URL
    private let db: Database

    public init(db: Database, backupDir: URL) {
        self.db = db
        self.backupDir = backupDir
    }

    public func createBackup(reason: String = "manual") throws -> URL {
        try FileManager.default.createDirectory(at: backupDir, withIntermediateDirectories: true)
        let ts = Self.timestamp()
        let dest = backupDir.appendingPathComponent("portfolio-" + ts + ".db")
        // Checkpoint so the main file is consistent, then copy.
        try db.exec("PRAGMA wal_checkpoint(TRUNCATE)")
        try FileManager.default.copyItem(at: URL(fileURLWithPath: db.path), to: dest)
        try db.exec("INSERT INTO backups(created_at, path, schema_version) VALUES('" + ts + "', '" + dest.path + "', " + String(Schema.version) + ")")
        return dest
    }

    public func listBackups() -> [URL] {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: backupDir, includingPropertiesForKeys: nil) else { return [] }
        return items.filter { $0.pathExtension == "db" }.sorted { $0.lastPathComponent > $1.lastPathComponent }
    }

    public func restore(from backup: URL) throws {
        // Close any WAL state, then replace the live DB with the backup file.
        try db.exec("PRAGMA wal_checkpoint(TRUNCATE)")
        try FileManager.default.removeItem(at: URL(fileURLWithPath: db.path))
        try FileManager.default.copyItem(at: backup, to: URL(fileURLWithPath: db.path))
    }

    /// Export small tables to JSON for portability / audit.
    public func exportJSON(to url: URL) throws {
        var out: [String: Any] = [:]
        out["schema_version"] = Schema.version
        out["assets"] = try queryRows("SELECT key, name, ticker, asset_class, pool, currency FROM assets ORDER BY key")
        out["holdings"] = try queryRows("SELECT asset_key, quantity, cost_basis, value_cny, as_of_date FROM holdings ORDER BY asset_key")
        out["snapshots"] = try queryRows("SELECT date, total_value, domestic_value, overseas_value FROM snapshots ORDER BY date")
        let data = try JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }

    private func queryRows(_ sql: String) throws -> [[String: Any]] {
        var rows: [[String: Any]] = []
        var conn: OpaquePointer?
        guard sqlite3_open(db.path, &conn) == SQLITE_OK else { return [] }
        defer { sqlite3_close(conn) }
        var s: OpaquePointer?
        guard sqlite3_prepare_v2(conn, sql, -1, &s, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(s) }
        while sqlite3_step(s) == SQLITE_ROW {
            var row: [String: Any] = [:]
            for i in 0..<sqlite3_column_count(s) {
                let name = String(cString: sqlite3_column_name(s, i))
                let type = sqlite3_column_type(s, i)
                switch type {
                case SQLITE_INTEGER: row[name] = sqlite3_column_int64(s, i)
                case SQLITE_FLOAT: row[name] = sqlite3_column_double(s, i)
                case SQLITE_TEXT: row[name] = String(cString: sqlite3_column_text(s, i))
                default: row[name] = NSNull()
                }
            }
            rows.append(row)
        }
        return rows
    }

    private static func timestamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        f.timeZone = TimeZone.current
        return f.string(from: Date())
    }
}
