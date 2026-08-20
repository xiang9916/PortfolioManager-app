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

    private func bindText(_ stmt: OpaquePointer?, _ idx: Int32, _ s: String?) {
        if let s = s {
            s.withCString { sqlite3_bind_text(stmt, idx, $0, -1, SQLITE_TRANSIENT) }
        } else {
            sqlite3_bind_null(stmt, idx)
        }
    }

    private func bindDouble(_ stmt: OpaquePointer?, _ idx: Int32, _ v: Double?) {
        if let v = v { sqlite3_bind_double(stmt, idx, v) } else { sqlite3_bind_null(stmt, idx) }
    }

    public func upsertAssets(_ assets: [Asset]) throws {
        try exec("BEGIN TRANSACTION")
        let sql = "INSERT OR REPLACE INTO assets(key, name, ticker, market, asset_class, pool, currency, source, fee_rate) VALUES(?,?,?,?,?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for a in assets {
            bindText(stmt, 1, a.key)
            bindText(stmt, 2, a.name)
            bindText(stmt, 3, a.ticker)
            bindText(stmt, 4, a.market)
            bindText(stmt, 5, a.assetClass)
            bindText(stmt, 6, a.pool.rawValue)
            bindText(stmt, 7, a.currency)
            bindText(stmt, 8, a.source)
            bindDouble(stmt, 9, a.feeRate)
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
    }

    public func upsertHoldings(_ holdings: [Holding]) throws {
        try exec("BEGIN TRANSACTION")
        let sql = "INSERT OR REPLACE INTO holdings(asset_key, quantity, cost_basis, value_cny, as_of_date) VALUES(?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for h in holdings {
            bindText(stmt, 1, h.assetKey)
            sqlite3_bind_double(stmt, 2, h.quantity)
            sqlite3_bind_double(stmt, 3, h.costBasis)
            sqlite3_bind_double(stmt, 4, h.valueCny)
            bindText(stmt, 5, h.asOfDate)
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
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
