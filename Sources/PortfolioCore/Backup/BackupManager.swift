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
        // Full asset row (incl. market/source/fee_rate/sort_order) so an
        // export → import roundtrip loses nothing; older backups that lack the
        // extra fields still import (missing keys decode as nil → defaults).
        out["assets"] = try queryRows("SELECT key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order FROM assets ORDER BY key")
        out["holdings"] = try queryRows("SELECT asset_key, quantity, cost_basis, currency, as_of_date FROM holdings ORDER BY asset_key")
        out["snapshots"] = try queryRows("SELECT date, total_value, domestic_value, overseas_value FROM snapshots ORDER BY date")
        out["income_periods"] = try queryRows("SELECT id, period, period_end, dividends, realized_pnl, source FROM income_periods ORDER BY period_end")
        out["fx_rates"] = try queryRows("SELECT currency, rate_to_cny, as_of_date, source FROM fx_rates ORDER BY currency")
        out["quotes"] = try queryRows("SELECT asset_key, price, date, currency, source FROM quotes ORDER BY asset_key")
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

    /// Import JSON previously written by `exportJSON`. Upserts assets / holdings / snapshots.
    public func importJSON(from url: URL) throws {
        let data = try Data(contentsOf: url)
        guard let obj = (try JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            throw DatabaseError.exec("invalid JSON root (expected object)")
        }

        if let rows = obj["assets"] as? [[String: Any]] {
            var assets: [Asset] = []
            for r in rows {
                guard let key = asString(r["key"]), let name = asString(r["name"]) else { continue }
                let pool = Pool(rawValue: asString(r["pool"]) ?? "overseas") ?? .overseas
                assets.append(Asset(key: key, name: name,
                                    ticker: asString(r["ticker"]),
                                    market: asString(r["market"]),
                                    assetClass: asString(r["asset_class"]),
                                    pool: pool,
                                    currency: asString(r["currency"]) ?? "CNY",
                                    source: asString(r["source"]),
                                    feeRate: asDouble(r["fee_rate"]),
                                    sortOrder: asDouble(r["sort_order"])))
            }
            if !assets.isEmpty { try db.upsertAssets(assets) }
        }

        if let rows = obj["holdings"] as? [[String: Any]] {
            var holdings: [Holding] = []
            for r in rows {
                guard let key = asString(r["asset_key"]) else { continue }
                holdings.append(Holding(assetKey: key,
                                        quantity: asDouble(r["quantity"]) ?? 0,
                                        costBasis: asDouble(r["cost_basis"]) ?? 0,
                                        currency: asString(r["currency"]) ?? "CNY",
                                        asOfDate: asString(r["as_of_date"]) ?? ""))
            }
            if !holdings.isEmpty { try db.upsertHoldings(holdings) }
        }

        if let rows = obj["snapshots"] as? [[String: Any]] {
            for r in rows {
                guard let date = asString(r["date"]) else { continue }
                try db.insertSnapshot(Snapshot(date: date,
                                               totalValue: asDouble(r["total_value"]) ?? 0,
                                               domesticValue: asDouble(r["domestic_value"]) ?? 0,
                                               overseasValue: asDouble(r["overseas_value"]) ?? 0))
            }
        }

        if let rows = obj["income_periods"] as? [[String: Any]] {
            var summaries: [IncomeSummary] = []
            for r in rows {
                guard let periodRaw = asString(r["period"]),
                      let period = FinancialPeriod(rawValue: periodRaw) else { continue }
                summaries.append(IncomeSummary(period: period,
                                               periodEnd: asString(r["period_end"]) ?? "",
                                               dividends: asDouble(r["dividends"]) ?? 0,
                                               realizedPnl: asDouble(r["realized_pnl"]) ?? 0,
                                               source: asString(r["source"])))
            }
            if !summaries.isEmpty { try db.upsertIncomeSummaries(summaries) }
        }

        if let rows = obj["fx_rates"] as? [[String: Any]] {
            var rates: [FxRate] = []
            for r in rows {
                guard let ccy = asString(r["currency"]) else { continue }
                rates.append(FxRate(currency: ccy,
                                    rateToCny: asDouble(r["rate_to_cny"]) ?? 0,
                                    asOfDate: asString(r["as_of_date"]) ?? "",
                                    source: asString(r["source"])))
            }
            if !rates.isEmpty { try db.upsertFxRates(rates) }
        }

        if let rows = obj["quotes"] as? [[String: Any]] {
            var quotes: [Quote] = []
            for r in rows {
                guard let key = asString(r["asset_key"]) else { continue }
                quotes.append(Quote(symbol: key,
                                     price: asDouble(r["price"]) ?? 0,
                                     currency: asString(r["currency"]),
                                     date: asString(r["date"]) ?? "",
                                     source: asString(r["source"]) ?? ""))
            }
            if !quotes.isEmpty { try db.upsertQuotes(quotes) }
        }
    }

    /// Export holdings (joined with asset metadata) to a CSV for spreadsheet interop.
    public func exportCSV(to url: URL) throws {
        let rows = try queryRows("""
            SELECT h.asset_key, a.name, a.asset_class, a.pool, h.quantity, h.cost_basis, h.currency, h.as_of_date
            FROM holdings h LEFT JOIN assets a ON a.key = h.asset_key
            ORDER BY h.asset_key
            """)
        var lines: [String] = ["asset_key,name,asset_class,pool,quantity,cost_basis,currency,as_of_date"]
        for r in rows {
            let cols = ["asset_key", "name", "asset_class", "pool", "quantity",
                        "cost_basis", "currency", "as_of_date"]
            lines.append(cols.map { csvField(r[$0]) }.joined(separator: ","))
        }
        try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }

    /// Create a backup only if the newest one is older than `interval` seconds.
    /// Returns the new backup URL, or nil when a fresh backup already exists.
    public func ensureDailyBackup(interval: TimeInterval = 86_400) throws -> URL? {
        if let latest = listBackups().first,
           let attrs = try? FileManager.default.attributesOfItem(atPath: latest.path),
           let created = attrs[.creationDate] as? Date,
           Date().timeIntervalSince(created) < interval {
            return nil
        }
        return try createBackup(reason: "auto-daily")
    }

    private func asDouble(_ v: Any?) -> Double? {
        guard let v = v else { return nil }
        if let d = v as? Double { return d }
        if let n = v as? NSNumber { return n.doubleValue }
        return nil
    }

    private func asString(_ v: Any?) -> String? {
        guard let v = v, !(v is NSNull) else { return nil }
        return v as? String
    }

    private func csvField(_ v: Any?) -> String {
        guard let v = v, !(v is NSNull) else { return "" }
        var s = String(describing: v)
        if s.contains(",") || s.contains("\"") || s.contains("\n") {
            s = "\"" + s.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return s
    }

    private static let timestampFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        f.timeZone = TimeZone.current
        return f
    }()

    private static func timestamp() -> String {
        timestampFormatter.string(from: Date())
    }
}
