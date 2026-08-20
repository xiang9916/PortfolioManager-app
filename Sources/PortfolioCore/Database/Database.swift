import Foundation
import SQLite3

// SQLITE_TRANSIENT is a C macro, not exposed to Swift; define it here.
private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

public enum DatabaseError: Error, CustomStringConvertible {
    case open(String)
    case exec(String)

    public var description: String {
        switch self {
        case .open(let m): return "open failed: " + m
        case .exec(let m): return "sql error: " + m
        }
    }
}

/// Thin SQLite3 wrapper (system libsqlite3, no external dependency).
public final class Database {
    private var db: OpaquePointer?
    public let path: String

    public init(path: String) throws {
        self.path = path
        if sqlite3_open(path, &db) != SQLITE_OK {
            let msg = db.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
            throw DatabaseError.open(msg)
        }
        try migrate()
    }

    deinit { sqlite3_close(db) }

    public func migrate() throws {
        for ddl in Schema.ddl { try exec(ddl) }
        try exec("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', '" + String(Schema.version) + "')")
    }

    public func exec(_ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK {
            let msg = err.map { String(cString: $0) } ?? "unknown"
            sqlite3_free(err)
            throw DatabaseError.exec(msg)
        }
    }

    public func upsertPrices(_ points: [PricePoint]) throws {
        try exec("BEGIN TRANSACTION")
        let sql = "INSERT OR REPLACE INTO prices(asset_key, date, close, currency) VALUES(?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for p in points {
            p.assetKey.withCString { key in
                p.date.withCString { date in
                    p.currency.withCString { cur in
                        sqlite3_bind_text(stmt, 1, key, -1, SQLITE_TRANSIENT)
                        sqlite3_bind_text(stmt, 2, date, -1, SQLITE_TRANSIENT)
                        sqlite3_bind_double(stmt, 3, p.close)
                        sqlite3_bind_text(stmt, 4, cur, -1, SQLITE_TRANSIENT)
                    }
                }
            }
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
    }

    public func insertSnapshot(_ s: Snapshot) throws {
        let sql = "INSERT OR REPLACE INTO snapshots(date, total_value, domestic_value, overseas_value) VALUES('" +
            s.date + "', " + String(s.totalValue) + ", " + String(s.domesticValue) + ", " + String(s.overseasValue) + ")"
        try exec(sql)
    }

    public func fetchPrices(assetKey: String) throws -> [PricePoint] {
        let sql = "SELECT asset_key, date, close, currency FROM prices WHERE asset_key = ? ORDER BY date"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_text(stmt, 1, assetKey, -1, nil)
        var out: [PricePoint] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let key = String(cString: sqlite3_column_text(stmt, 0))
            let date = String(cString: sqlite3_column_text(stmt, 1))
            let close = sqlite3_column_double(stmt, 2)
            let cur = String(cString: sqlite3_column_text(stmt, 3))
            out.append(PricePoint(assetKey: key, date: date, close: close, currency: cur))
        }
        return out
    }

    public func count(table: String) -> Int {
        let sql = "SELECT COUNT(*) FROM " + table
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        defer { sqlite3_finalize(stmt) }
        if sqlite3_step(stmt) == SQLITE_ROW { return Int(sqlite3_column_int64(stmt, 0)) }
        return 0
    }
}
