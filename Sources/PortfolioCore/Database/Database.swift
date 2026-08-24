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

    /// Run `body` inside a transaction. On any error the transaction is rolled
    /// back before rethrowing — a failed batch must never leave the connection
    /// stuck inside an open transaction, because every later `BEGIN` on the
    /// same connection would then fail with "cannot start a transaction within
    /// a transaction" (observed after a failed backup import).
    public func inTransaction(_ body: () throws -> Void) throws {
        try exec("BEGIN TRANSACTION")
        do {
            try body()
            try exec("COMMIT")
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
    }

    /// Prepare a statement, throwing on failure. Caller is responsible for
    /// stepping + binding; the statement is auto-finalized via the returned
    /// wrapper's deinit.
    private func prepared(_ sql: String) throws -> OpaquePointer? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        return stmt
    }

    /// Execute a prepared statement, throw on failure, then reset + clear bindings
    /// so it can be reused in a batch loop.
    private func stepAndReset(_ stmt: OpaquePointer?) throws {
        if sqlite3_step(stmt) != SQLITE_DONE {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
        sqlite3_reset(stmt)
        sqlite3_clear_bindings(stmt)
    }

    /// Execute a single prepared statement (no reset needed; used for one-shot upserts).
    private func stepOnce(_ stmt: OpaquePointer?) throws {
        if sqlite3_step(stmt) != SQLITE_DONE {
            throw DatabaseError.exec(String(cString: sqlite3_errmsg(db)))
        }
    }

    public func upsertPrices(_ points: [PricePoint]) throws {
        try inTransaction {
            let stmt = try prepared("INSERT OR REPLACE INTO prices(asset_key, date, close, currency) VALUES(?,?,?,?)")
            defer { sqlite3_finalize(stmt) }
            for p in points {
                bindText(stmt, 1, p.assetKey)
                bindText(stmt, 2, p.date)
                sqlite3_bind_double(stmt, 3, p.close)
                bindText(stmt, 4, p.currency)
                try stepAndReset(stmt)
            }
        }
    }

    public func insertSnapshot(_ s: Snapshot) throws {
        let sql = "INSERT OR REPLACE INTO snapshots(date, total_value, domestic_value, overseas_value) VALUES('" +
            s.date + "', " + String(s.totalValue) + ", " + String(s.domesticValue) + ", " + String(s.overseasValue) + ")"
        try exec(sql)
    }

    private func bindText(_ stmt: OpaquePointer?, _ idx: Int32, _ s: String?) {
        if let s = s {
            s.withCString { _ = sqlite3_bind_text(stmt, idx, $0, -1, SQLITE_TRANSIENT) }
        } else {
            sqlite3_bind_null(stmt, idx)
        }
    }

    private func bindDouble(_ stmt: OpaquePointer?, _ idx: Int32, _ v: Double?) {
        if let v = v { sqlite3_bind_double(stmt, idx, v) } else { sqlite3_bind_null(stmt, idx) }
    }

    public func upsertAssets(_ assets: [Asset]) throws {
        // UPSERT (not REPLACE): re-importing an existing asset must NOT reset its
        // manual sort_order — only the insert path uses the provided value.
        let sql = """
        INSERT INTO assets(key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
            name=excluded.name, ticker=excluded.ticker, market=excluded.market,
            asset_class=excluded.asset_class, pool=excluded.pool, currency=excluded.currency,
            source=excluded.source, fee_rate=excluded.fee_rate
        """
        try inTransaction {
            let stmt = try prepared(sql)
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
                // sort_order is NOT NULL DEFAULT 0, but SQLite's column default
                // only applies when the column is OMITTED from the INSERT — an
                // explicitly bound NULL violates the constraint. Backups exported
                // before sort_order existed carry no value: bind 0 ("unsorted",
                // falls back to key order) instead of NULL.
                bindDouble(stmt, 10, a.sortOrder ?? 0)
                try stepAndReset(stmt)
            }
        }
    }

    /// Persist the manual list order for 资产透视 drag-to-reorder (module 2).
    public func updateAssetSortOrders(_ orders: [(key: String, order: Double)]) throws {
        try inTransaction {
            let stmt = try prepared("UPDATE assets SET sort_order = ? WHERE key = ?")
            defer { sqlite3_finalize(stmt) }
            for (key, order) in orders {
                sqlite3_bind_double(stmt, 1, order)
                bindText(stmt, 2, key)
                try stepAndReset(stmt)
            }
        }
    }

    public func upsertHoldings(_ holdings: [Holding]) throws {
        try inTransaction {
            let stmt = try prepared("INSERT OR REPLACE INTO holdings(asset_key, quantity, cost_basis, currency, as_of_date) VALUES(?,?,?,?,?)")
            defer { sqlite3_finalize(stmt) }
            for h in holdings {
                bindText(stmt, 1, h.assetKey)
                sqlite3_bind_double(stmt, 2, h.quantity)
                sqlite3_bind_double(stmt, 3, h.costBasis)
                bindText(stmt, 4, h.currency)
                bindText(stmt, 5, h.asOfDate)
                try stepAndReset(stmt)
            }
        }
    }

    /// Update the editable fields of a holding (资产透视 editing). 市值不在此列(派生 = 份额×最后价).
    public func updateHolding(assetKey: String, quantity: Double, costBasis: Double, currency: String) throws {
        let stmt = try prepared("UPDATE holdings SET quantity = ?, cost_basis = ?, currency = ? WHERE asset_key = ?")
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_double(stmt, 1, quantity)
        sqlite3_bind_double(stmt, 2, costBasis)
        bindText(stmt, 3, currency)
        bindText(stmt, 4, assetKey)
        try stepOnce(stmt)
    }

    // MARK: 能力2 — asset add/delete + FX rates

    /// Insert (or replace) a single asset target.
    public func insertAsset(_ a: Asset) throws {
        try upsertAssets([a])
    }

    /// Delete an asset target and its holdings / prices / financials (资产透视 删除标的).
    public func deleteAsset(key: String) throws {
        try inTransaction {
            for table in ["holdings", "prices", "assets"] {
                let col = (table == "assets") ? "key" : "asset_key"
                let sql = "DELETE FROM " + table + " WHERE " + col + " = ?"
                let stmt = try prepared(sql)
                defer { sqlite3_finalize(stmt) }
                bindText(stmt, 1, key)
                try stepOnce(stmt)
            }
        }
    }

    public func upsertFxRates(_ rates: [FxRate]) throws {
        try inTransaction {
            let stmt = try prepared("INSERT OR REPLACE INTO fx_rates(currency, rate_to_cny, as_of_date, source) VALUES(?,?,?,?)")
            defer { sqlite3_finalize(stmt) }
            for r in rates {
                bindText(stmt, 1, r.currency)
                sqlite3_bind_double(stmt, 2, r.rateToCny)
                bindText(stmt, 3, r.asOfDate)
                bindText(stmt, 4, r.source)
                try stepAndReset(stmt)
            }
        }
    }

    public func fetchFxRates() throws -> [FxRate] {
        let sql = "SELECT currency, rate_to_cny, as_of_date, source FROM fx_rates ORDER BY currency"
        let stmt = try prepared(sql)
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

    // MARK: - Quotes (latest unit price per asset, separate from price history)

    public func upsertQuotes(_ quotes: [Quote]) throws {
        try inTransaction {
            let stmt = try prepared("INSERT OR REPLACE INTO quotes(asset_key, price, date, currency, source) VALUES(?,?,?,?,?)")
            defer { sqlite3_finalize(stmt) }
            for q in quotes {
                bindText(stmt, 1, q.symbol)
                sqlite3_bind_double(stmt, 2, q.price)
                bindText(stmt, 3, q.date)
                bindText(stmt, 4, q.currency ?? "CNY")
                bindText(stmt, 5, q.source)
                try stepAndReset(stmt)
            }
        }
    }

    /// Latest quote (unit NAV / latest price) per asset key.
    public func fetchLatestQuotes() throws -> [String: Quote] {
        let sql = "SELECT asset_key, price, date, currency, source FROM quotes"
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        var out: [String: Quote] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            let key = String(cString: sqlite3_column_text(stmt, 0))
            let price = sqlite3_column_double(stmt, 1)
            let date = String(cString: sqlite3_column_text(stmt, 2))
            let cur = String(cString: sqlite3_column_text(stmt, 3))
            let src = String(cString: sqlite3_column_text(stmt, 4))
            out[key] = Quote(symbol: key, price: price, currency: cur, date: date, source: src)
        }
        return out
    }

    /// Upsert a macro rate (e.g. "cn_10y", "us_10y") into macro_rates cache.
    /// Used for dynamic RF = ov_w * us_10y + dom_w * cn_10y (能力2).
    public func upsertMacroRate(_ key: String, value: Double, asOfDate: String, source: String?) throws {
        let stmt = try prepared("INSERT OR REPLACE INTO macro_rates(key, value, as_of_date, source) VALUES(?,?,?,?)")
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, key)
        sqlite3_bind_double(stmt, 2, value)
        bindText(stmt, 3, asOfDate)
        bindText(stmt, 4, source)
        try stepOnce(stmt)
    }

    /// Fetch a macro rate by key, or nil if not stored.
    public func fetchMacroRate(_ key: String) throws -> (value: Double, asOfDate: String, source: String?)? {
        let sql = "SELECT value, as_of_date, source FROM macro_rates WHERE key = ?"
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, key)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        let value = sqlite3_column_double(stmt, 0)
        let date = String(cString: sqlite3_column_text(stmt, 1))
        let src = sqlite3_column_type(stmt, 2) == SQLITE_NULL ? nil : String(cString: sqlite3_column_text(stmt, 2))
        return (value, date, src)
    }

    public func fetchPrices(assetKey: String) throws -> [PricePoint] {
        let sql = "SELECT asset_key, date, close, currency FROM prices WHERE asset_key = ? ORDER BY date"
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, assetKey)
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
        let sql = "SELECT id, key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order FROM assets ORDER BY sort_order, key"
        let stmt = try prepared(sql)
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
                feeRate: colDouble(stmt, 9),
                sortOrder: colDouble(stmt, 10)))
        }
        return out
    }

    public func fetchHoldings(asOfDate: String? = nil) throws -> [Holding] {
        var sql = "SELECT id, asset_key, quantity, cost_basis, currency, as_of_date FROM holdings"
        if let d = asOfDate { sql += " WHERE as_of_date = '" + d + "'" }
        sql += " ORDER BY asset_key"
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        var out: [Holding] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(Holding(
                id: sqlite3_column_int64(stmt, 0),
                assetKey: String(cString: sqlite3_column_text(stmt, 1)),
                quantity: sqlite3_column_double(stmt, 2),
                costBasis: sqlite3_column_double(stmt, 3),
                currency: String(cString: sqlite3_column_text(stmt, 4)),
                asOfDate: String(cString: sqlite3_column_text(stmt, 5))))
        }
        return out
    }

    public func fetchSnapshots() throws -> [Snapshot] {
        let sql = "SELECT id, date, total_value, domestic_value, overseas_value FROM snapshots ORDER BY date"
        let stmt = try prepared(sql)
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

    public func fetchIncomeSummaries() throws -> [IncomeSummary] {
        let sql = "SELECT id, period, period_end, dividends, realized_pnl, source FROM income_periods ORDER BY period_end"
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        var out: [IncomeSummary] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let periodRaw = String(cString: sqlite3_column_text(stmt, 1))
            out.append(IncomeSummary(
                id: sqlite3_column_int64(stmt, 0),
                period: FinancialPeriod(rawValue: periodRaw) ?? .quarter,
                periodEnd: String(cString: sqlite3_column_text(stmt, 2)),
                dividends: sqlite3_column_double(stmt, 3),
                realizedPnl: sqlite3_column_double(stmt, 4),
                source: colText(stmt, 5)))
        }
        return out
    }

    public func upsertIncomeSummaries(_ items: [IncomeSummary]) throws {
        try inTransaction {
            let stmt = try prepared("INSERT OR REPLACE INTO income_periods(period, period_end, dividends, realized_pnl, source) VALUES(?,?,?,?,?)")
            defer { sqlite3_finalize(stmt) }
            for f in items {
                bindText(stmt, 1, f.period.rawValue)
                bindText(stmt, 2, f.periodEnd)
                sqlite3_bind_double(stmt, 3, f.dividends)
                sqlite3_bind_double(stmt, 4, f.realizedPnl)
                bindText(stmt, 5, f.source)
                try stepAndReset(stmt)
            }
        }
    }

    /// Delete one income summary record by id (财务分析 editing).
    public func deleteIncomeSummary(id: Int64) throws {
        try exec("DELETE FROM income_periods WHERE id = " + String(id))
    }

    @discardableResult
    public func insertRun(_ run: OptimizationRun) throws -> Int64 {
        let stmt = try prepared("INSERT INTO optimization_runs(started_at, finished_at, status, params_hash, result_json, log_path) VALUES(?,?,?,?,?,?)")
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, run.startedAt)
        bindText(stmt, 2, run.finishedAt)
        bindText(stmt, 3, run.status)
        bindText(stmt, 4, run.paramsHash)
        bindText(stmt, 5, run.resultJSON)
        bindText(stmt, 6, run.logPath)
        try stepOnce(stmt)
        return sqlite3_last_insert_rowid(db)
    }

    public func updateRun(id: Int64, finishedAt: String?, status: String, resultJSON: String?) throws {
        let stmt = try prepared("UPDATE optimization_runs SET finished_at = ?, status = ?, result_json = ? WHERE id = ?")
        defer { sqlite3_finalize(stmt) }
        bindText(stmt, 1, finishedAt)
        bindText(stmt, 2, status)
        bindText(stmt, 3, resultJSON)
        sqlite3_bind_int64(stmt, 4, id)
        try stepOnce(stmt)
    }

    public func insertLog(runID: Int64, seq: Int, step: String, message: String, level: String, ts: String) throws {
        let stmt = try prepared("INSERT INTO optimization_logs(run_id, seq, step, message, level, ts) VALUES(?,?,?,?,?,?)")
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_int64(stmt, 1, runID)
        sqlite3_bind_int(stmt, 2, Int32(seq))
        bindText(stmt, 3, step)
        bindText(stmt, 4, message)
        bindText(stmt, 5, level)
        bindText(stmt, 6, ts)
        try stepOnce(stmt)
    }

    public func fetchRuns(limit: Int = 20) throws -> [OptimizationRun] {
        let sql = "SELECT id, started_at, finished_at, status, params_hash, result_json, log_path FROM optimization_runs ORDER BY id DESC LIMIT " + String(limit)
        let stmt = try prepared(sql)
        defer { sqlite3_finalize(stmt) }
        var out: [OptimizationRun] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let r = OptimizationRun(
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
        let stmt = try prepared(sql)
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
