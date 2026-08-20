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
        // Ensure schema_meta exists first so we can read the persisted version.
        try exec("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        let current = currentSchemaVersion()
        for (v, statements) in Schema.migrations where v > current {
            for sql in statements { try exec(sql) }
            try exec("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', '" + String(v) + "')")
        }
    }

    /// Read the persisted schema version (0 for a brand-new / empty DB).
    public func currentSchemaVersion() -> Int {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT value FROM schema_meta WHERE key = 'version'", -1, &stmt, nil) == SQLITE_OK else { return 0 }
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_step(stmt) == SQLITE_ROW, sqlite3_column_type(stmt, 0) == SQLITE_TEXT else { return 0 }
        let s = String(cString: sqlite3_column_text(stmt, 0))
        return Int(s) ?? 0
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
        let sql = "INSERT OR REPLACE INTO holdings(asset_key, quantity, cost_basis, value, currency, as_of_date) VALUES(?,?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for h in holdings {
            bindText(stmt, 1, h.assetKey)
            sqlite3_bind_double(stmt, 2, h.quantity)
            sqlite3_bind_double(stmt, 3, h.costBasis)
            sqlite3_bind_double(stmt, 4, h.value)
            bindText(stmt, 5, h.currency)
            bindText(stmt, 6, h.asOfDate)
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
    }

    /// Update the editable fields of a holding (资产透视 editing).
    public func updateHolding(assetKey: String, quantity: Double, costBasis: Double, value: Double, currency: String) throws {
        let sql = "UPDATE holdings SET quantity = ?, cost_basis = ?, value = ?, currency = ? WHERE asset_key = ?"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_double(stmt, 1, quantity)
        sqlite3_bind_double(stmt, 2, costBasis)
        sqlite3_bind_double(stmt, 3, value)
        currency.withCString { sqlite3_bind_text(stmt, 4, $0, -1, SQLITE_TRANSIENT) }
        assetKey.withCString { sqlite3_bind_text(stmt, 5, $0, -1, SQLITE_TRANSIENT) }
        if sqlite3_step(stmt) != SQLITE_DONE {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
    }

    // MARK: 能力2 — asset add/delete + FX rates

    /// Insert (or replace) a single asset target.
    public func insertAsset(_ a: Asset) throws {
        try upsertAssets([a])
    }

    /// Delete an asset target and its holdings / prices / financials (资产透视 删除标的).
    public func deleteAsset(key: String) throws {
        try exec("BEGIN TRANSACTION")
        for table in ["holdings", "prices", "financials", "assets"] {
            let col = (table == "assets") ? "key" : "asset_key"
            let sql = "DELETE FROM " + table + " WHERE " + col + " = ?"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            defer { sqlite3_finalize(stmt) }
            key.withCString { sqlite3_bind_text(stmt, 1, $0, -1, SQLITE_TRANSIENT) }
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
        }
        try exec("COMMIT")
    }

    public func upsertFxRates(_ rates: [FxRate]) throws {
        try exec("BEGIN TRANSACTION")
        let sql = "INSERT OR REPLACE INTO fx_rates(currency, rate_to_cny, as_of_date, source) VALUES(?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for r in rates {
            bindText(stmt, 1, r.currency)
            sqlite3_bind_double(stmt, 2, r.rateToCny)
            bindText(stmt, 3, r.asOfDate)
            bindText(stmt, 4, r.source)
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
    }

    public func fetchFxRates() throws -> [FxRate] {
        let sql = "SELECT currency, rate_to_cny, as_of_date, source FROM fx_rates ORDER BY currency"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [FxRate] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(FxRate(
                currency: String(cString: sqlite3_column_text(stmt, 0)),
                rateToCny: sqlite3_column_double(stmt, 1),
                asOfDate: String(cString: sqlite3_column_text(stmt, 2)),
                source: sqlite3_column_type(stmt, 3) != SQLITE_NULL ? String(cString: sqlite3_column_text(stmt, 3)) : nil))
        }
        return out
    }

    public func fetchPrices(assetKey: String) throws -> [PricePoint] {
        let sql = "SELECT asset_key, date, close, currency FROM prices WHERE asset_key = ? ORDER BY date"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        assetKey.withCString { sqlite3_bind_text(stmt, 1, $0, -1, SQLITE_TRANSIENT) }
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

    // MARK: - Queries (Phase 3-6 UI + financials + optimizer runs)

    private func colText(_ stmt: OpaquePointer?, _ idx: Int32) -> String? {
        guard sqlite3_column_type(stmt, idx) != SQLITE_NULL else { return nil }
        return String(cString: sqlite3_column_text(stmt, idx))
    }

    private func colDouble(_ stmt: OpaquePointer?, _ idx: Int32) -> Double? {
        guard sqlite3_column_type(stmt, idx) != SQLITE_NULL else { return nil }
        return sqlite3_column_double(stmt, idx)
    }

    public func fetchAssets() throws -> [Asset] {
        let sql = "SELECT id, key, name, ticker, market, asset_class, pool, currency, source, fee_rate FROM assets ORDER BY key"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [Asset] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let poolRaw = String(cString: sqlite3_column_text(stmt, 6))
            out.append(Asset(
                id: sqlite3_column_int64(stmt, 0),
                key: String(cString: sqlite3_column_text(stmt, 1)),
                name: String(cString: sqlite3_column_text(stmt, 2)),
                ticker: colText(stmt, 3),
                market: colText(stmt, 4),
                assetClass: colText(stmt, 5),
                pool: Pool(rawValue: poolRaw) ?? .overseas,
                currency: String(cString: sqlite3_column_text(stmt, 7)),
                source: colText(stmt, 8),
                feeRate: colDouble(stmt, 9)))
        }
        return out
    }

    public func fetchHoldings(asOfDate: String? = nil) throws -> [Holding] {
        var sql = "SELECT id, asset_key, quantity, cost_basis, value, currency, as_of_date FROM holdings"
        if let d = asOfDate { sql += " WHERE as_of_date = '" + d + "'" }
        sql += " ORDER BY asset_key"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [Holding] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(Holding(
                id: sqlite3_column_int64(stmt, 0),
                assetKey: String(cString: sqlite3_column_text(stmt, 1)),
                quantity: sqlite3_column_double(stmt, 2),
                costBasis: sqlite3_column_double(stmt, 3),
                value: sqlite3_column_double(stmt, 4),
                currency: String(cString: sqlite3_column_text(stmt, 5)),
                asOfDate: String(cString: sqlite3_column_text(stmt, 6))))
        }
        return out
    }

    public func fetchSnapshots() throws -> [Snapshot] {
        let sql = "SELECT id, date, total_value, domestic_value, overseas_value FROM snapshots ORDER BY date"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [Snapshot] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(Snapshot(
                id: sqlite3_column_int64(stmt, 0),
                date: String(cString: sqlite3_column_text(stmt, 1)),
                totalValue: sqlite3_column_double(stmt, 2),
                domesticValue: sqlite3_column_double(stmt, 3),
                overseasValue: sqlite3_column_double(stmt, 4)))
        }
        return out
    }

    public func fetchFinancials(assetKey: String? = nil) throws -> [Financial] {
        var sql = "SELECT id, asset_key, period, period_end, revenue, net_income, eps, source FROM financials"
        if let k = assetKey { sql += " WHERE asset_key = '" + k + "'" }
        sql += " ORDER BY period_end"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [Financial] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let periodRaw = String(cString: sqlite3_column_text(stmt, 2))
            out.append(Financial(
                id: sqlite3_column_int64(stmt, 0),
                assetKey: String(cString: sqlite3_column_text(stmt, 1)),
                period: FinancialPeriod(rawValue: periodRaw) ?? .quarter,
                periodEnd: String(cString: sqlite3_column_text(stmt, 3)),
                revenue: colDouble(stmt, 4),
                netIncome: colDouble(stmt, 5),
                eps: colDouble(stmt, 6),
                source: colText(stmt, 7)))
        }
        return out
    }

    public func upsertFinancials(_ items: [Financial]) throws {
        try exec("BEGIN TRANSACTION")
        let sql = "INSERT OR REPLACE INTO financials(asset_key, period, period_end, revenue, net_income, eps, source) VALUES(?,?,?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        for f in items {
            bindText(stmt, 1, f.assetKey)
            bindText(stmt, 2, f.period.rawValue)
            bindText(stmt, 3, f.periodEnd)
            bindDouble(stmt, 4, f.revenue)
            bindDouble(stmt, 5, f.netIncome)
            bindDouble(stmt, 6, f.eps)
            bindText(stmt, 7, f.source)
            if sqlite3_step(stmt) != SQLITE_DONE {
                throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
            }
            sqlite3_reset(stmt)
            sqlite3_clear_bindings(stmt)
        }
        try exec("COMMIT")
    }

    /// Delete one financial statement record by id (财务报表 editing).
    public func deleteFinancial(id: Int64) throws {
        try exec("DELETE FROM financials WHERE id = " + String(id))
    }

    @discardableResult
    public func insertRun(_ run: OptimizationRun) throws -> Int64 {
        let sql = "INSERT INTO optimization_runs(started_at, finished_at, status, params_hash, result_json, log_path) VALUES(?,?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, run.startedAt)
        bindText(stmt, 2, run.finishedAt)
        bindText(stmt, 3, run.status)
        bindText(stmt, 4, run.paramsHash)
        bindText(stmt, 5, run.resultJSON)
        bindText(stmt, 6, run.logPath)
        guard sqlite3_step(stmt) == SQLITE_DONE else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        return sqlite3_last_insert_rowid(db)
    }

    public func updateRun(id: Int64, finishedAt: String?, status: String, resultJSON: String?) throws {
        let sql = "UPDATE optimization_runs SET finished_at = ?, status = ?, result_json = ? WHERE id = ?"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, finishedAt)
        bindText(stmt, 2, status)
        bindText(stmt, 3, resultJSON)
        sqlite3_bind_int64(stmt, 4, id)
        guard sqlite3_step(stmt) == SQLITE_DONE else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
    }

    public func insertLog(runID: Int64, seq: Int, step: String, message: String, level: String, ts: String) throws {
        let sql = "INSERT INTO optimization_logs(run_id, seq, step, message, level, ts) VALUES(?,?,?,?,?,?)"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_int64(stmt, 1, runID)
        sqlite3_bind_int(stmt, 2, Int32(seq))
        bindText(stmt, 3, step)
        bindText(stmt, 4, message)
        bindText(stmt, 5, level)
        bindText(stmt, 6, ts)
        guard sqlite3_step(stmt) == SQLITE_DONE else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
    }

    public func fetchRuns(limit: Int = 20) throws -> [OptimizationRun] {
        let sql = "SELECT id, started_at, finished_at, status, params_hash, result_json, log_path FROM optimization_runs ORDER BY id DESC LIMIT " + String(limit)
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        var out: [OptimizationRun] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            var r = OptimizationRun(
                id: sqlite3_column_int64(stmt, 0),
                startedAt: String(cString: sqlite3_column_text(stmt, 1)),
                finishedAt: colText(stmt, 2),
                status: String(cString: sqlite3_column_text(stmt, 3)),
                paramsHash: colText(stmt, 4),
                resultJSON: colText(stmt, 5),
                logPath: colText(stmt, 6))
            out.append(r)
        }
        return out
    }

    public func fetchLogs(runID: Int64) throws -> [OptimizationLogEntry] {
        let sql = "SELECT id, run_id, seq, step, message, level, ts FROM optimization_logs WHERE run_id = ? ORDER BY seq"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_int64(stmt, 1, runID)
        var out: [OptimizationLogEntry] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(OptimizationLogEntry(
                id: sqlite3_column_int64(stmt, 0),
                runID: sqlite3_column_int64(stmt, 1),
                seq: Int(sqlite3_column_int(stmt, 2)),
                step: String(cString: sqlite3_column_text(stmt, 3)),
                message: String(cString: sqlite3_column_text(stmt, 4)),
                level: String(cString: sqlite3_column_text(stmt, 5)),
                ts: String(cString: sqlite3_column_text(stmt, 6))))
        }
        return out
    }
}
