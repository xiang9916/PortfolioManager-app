#!/usr/bin/env python3
"""Extract pool ratios and full holdings from an external workbook.

Reads an Apple Numbers document through the `numbers_parser` library (the file
format is the only thing that library is needed for), and writes a neutral JSON
the app/optimizer consume. Adds a "holdings" field (all tickers with currency +
value) on top of the original pool / US-equity output.
Backward compatible: us_equity + pool fields are unchanged.
"""

import argparse
import json
import sys


def cell_str(value):
    if value is None:
        return ""
    return str(value).strip()


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def find_explicit_pool_table(doc):
    """Return (domestic, overseas) if a table contains an explicit pool row."""
    for sheet in doc.sheets:
        for table in sheet.tables:
            header = None
            for r in range(min(table.num_rows, 8)):
                row = [cell_str(table.cell(r, c).value) for c in range(table.num_cols)]
                joined = "".join(row)
                if "境内" in joined and "境外" in joined:
                    header = r
                    break
            if header is None:
                continue
            for r in range(header + 1, table.num_rows):
                values = []
                for c in range(table.num_cols):
                    v = table.cell(r, c).value
                    if is_number(v):
                        values.append((c, float(v)))
                if len(values) >= 2:
                    return values[0][1], values[1][1]
    return None


def classify_by_currency(doc):
    """Return (domestic, overseas, us_holdings, all_holdings)."""
    domestic = 0.0
    overseas = 0.0
    us_holdings = []
    all_holdings = []
    for sheet in doc.sheets:
        for table in sheet.tables:
            if table.num_rows < 2 or table.num_cols < 11:
                continue
            header = [cell_str(table.cell(0, c).value) for c in range(min(table.num_cols, 11))]
            if "股票代码" not in header or "货币" not in header:
                continue
            is_us = "美国权益" in table.name or "美股" in table.name or "缓冲" in table.name
            for r in range(1, table.num_rows):
                ticker = cell_str(table.cell(r, 0).value)
                name = cell_str(table.cell(r, 1).value)
                currency = cell_str(table.cell(r, 2).value)
                if not ticker or not currency:
                    continue
                cny_value = table.cell(r, 10).value
                if not is_number(cny_value) or float(cny_value) <= 0:
                    continue
                cny_value = float(cny_value)
                if currency == "CNY":
                    domestic += cny_value
                else:
                    overseas += cny_value
                if is_us and ticker:
                    us_holdings.append({
                        "ticker": ticker,
                        "name": name,
                        "value_cny": cny_value,
                    })
                all_holdings.append({
                    "ticker": ticker,
                    "name": name,
                    "currency": currency,
                    "value_cny": cny_value,
                    "table": table.name,
                })
    return domestic, overseas, us_holdings, all_holdings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", help="外部表格文件路径（Apple Numbers 文档）")
    parser.add_argument("--override-pool",
                        help='JSON like {"domestic": 123456, "overseas": 234567}（示例数字为虚构）')
    parser.add_argument("--json", dest="out_path", help="Write JSON to file instead of stdout")
    args = parser.parse_args()

    try:
        from numbers_parser import Document
    except ImportError:
        print("numbers_parser is required. Install it or set PYTHONPATH.", file=sys.stderr)
        sys.exit(2)

    doc = Document(args.workbook)
    warnings = []
    domestic = overseas = 0.0

    # Classify once: currency-based pools + US internals + full holdings.
    cur_domestic, cur_overseas, us_holdings, all_holdings = classify_by_currency(doc)

    explicit = find_explicit_pool_table(doc)
    if explicit is not None:
        domestic, overseas = explicit
        pool_mode = "explicit"
    else:
        domestic, overseas = cur_domestic, cur_overseas
        pool_mode = "currency"

    if args.override_pool:
        override = json.loads(args.override_pool)
        domestic = float(override.get("domestic", domestic))
        overseas = float(override.get("overseas", overseas))
        pool_mode = "override"
        warnings.append("已使用 --override-pool 覆盖表格里提取到的境内/境外金额。")

    total = domestic + overseas
    if total <= 0:
        print("No pool values found.", file=sys.stderr)
        sys.exit(1)

    us_total = sum(h["value_cny"] for h in us_holdings)
    for h in us_holdings:
        h["weight"] = h["value_cny"] / us_total if us_total else 0.0

    result = {
        "source_file": args.workbook,
        "pool_mode": pool_mode,
        "domestic_value": domestic,
        "overseas_value": overseas,
        "total_value": total,
        "domestic_weight": domestic / total,
        "overseas_weight": overseas / total,
        "us_equity": {
            "total_value": us_total,
            "holdings": us_holdings,
        },
        "holdings": all_holdings,
        "warnings": warnings,
    }

    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
