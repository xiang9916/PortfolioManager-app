import Foundation

/// Central schema version. Bump on every breaking change; migrations bring old DBs forward.
public enum Schema {
    public static let version: Int = 2

    /// Ordered migrations: apply each version's statements in sequence to bring an
    /// existing DB forward (capability 3: forward compatibility). Version 1 = initial schema.
    public static let migrations: [(version: Int, statements: [String])] = [
        (1, [
        // version meta (capability 3: forward compatibility)
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            ticker TEXT,
            market TEXT,
            asset_class TEXT,
            pool TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            source TEXT,
            fee_rate REAL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            quantity REAL NOT NULL,
            cost_basis REAL NOT NULL,
            value_cny REAL NOT NULL,
            as_of_date TEXT NOT NULL,
            FOREIGN KEY(asset_key) REFERENCES assets(key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_value REAL NOT NULL,
            domestic_value REAL NOT NULL,
            overseas_value REAL NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            UNIQUE(asset_key, date),
            FOREIGN KEY(asset_key) REFERENCES assets(key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS financials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            period TEXT NOT NULL,
            period_end TEXT NOT NULL,
            revenue REAL,
            net_income REAL,
            eps REAL,
            source TEXT,
            UNIQUE(asset_key, period, period_end),
            FOREIGN KEY(asset_key) REFERENCES assets(key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS optimization_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            params_hash TEXT,
            result_json TEXT,
            log_path TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS optimization_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            step TEXT NOT NULL,
            message TEXT NOT NULL,
            level TEXT NOT NULL,
            ts TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES optimization_runs(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            path TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );
        """,
        ]),
        (2, [
        // 能力2 币种化: holdings 重建为 value(标的币种) + currency 列,
        // 新增 fx_rates(币种→人民币) 表,权重统一成人民币后计算。
        // 现有 value_cny 已是人民币 → value 原样搬运,currency 默认 CNY。
        "BEGIN TRANSACTION;",
        """
        CREATE TABLE holdings_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_key TEXT NOT NULL,
            quantity REAL NOT NULL,
            cost_basis REAL NOT NULL,
            value REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            as_of_date TEXT NOT NULL,
            FOREIGN KEY(asset_key) REFERENCES assets(key)
        );
        """,
        """
        INSERT INTO holdings_v2(id, asset_key, quantity, cost_basis, value, currency, as_of_date)
            SELECT id, asset_key, quantity, cost_basis, value_cny, 'CNY', as_of_date FROM holdings;
        """,
        "DROP TABLE holdings;",
        "ALTER TABLE holdings_v2 RENAME TO holdings;",
        """
        CREATE TABLE IF NOT EXISTS fx_rates (
            currency TEXT PRIMARY KEY,
            rate_to_cny REAL NOT NULL,
            as_of_date TEXT NOT NULL,
            source TEXT
        );
        """,
        "COMMIT;",
        ]),
    ]
}
