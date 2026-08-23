"""
Export dbt marts and key dimension tables to Parquet for Power BI.

Power BI reads Parquet, not the DuckDB file directly: there is no
first-class Power BI connector for DuckDB, and ODBC against a local
.duckdb file is fragile and blocks Import-mode refresh (the file must be
closed and locked, which conflicts with dbt re-running against it). The
.duckdb file is also gitignored, so a reviewer never has it -- Parquet is
the portable, engine-agnostic handoff format a reviewer or report author
can actually open. It also doubles as the handoff artefact for the later
detection and simulation layers.

Run after `dbt build`:
    venv/Scripts/python.exe scripts/export_marts.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "dbt" / "trading_engine.duckdb"
OUT_DIR = REPO_ROOT / "data" / "marts"

# (schema, table_name) -- marts first, then the dimension tables the
# report needs for slicers and relationships.
EXPORTS = [
    ("main_marts", "mart_daily_trading"),
    ("main_marts", "mart_product_daily"),
    ("main_marts", "mart_cohort_retention"),
    ("main_marts", "mart_ltv"),
    ("main_marts", "mart_email_flow_weekly"),
    ("main_marts", "mart_data_quality"),
    ("main_core", "dim_date"),
    ("main_core", "dim_product"),
    ("main_core", "dim_campaign"),
    # Campaign-grain ad fact. Without it dim_campaign has nothing to join to,
    # and the CPC decomposition could only be shown at channel level.
    ("main_core", "fct_ad_spend_daily"),
]


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # core-layer models (dim_date, dim_product, dim_campaign) are dbt
    # VIEWS, not tables -- querying them re-executes the chain down to
    # the source CSVs, which are read in place via a path relative to
    # the dbt project directory (see dbt_project.yml's `data_dir` var).
    # Run from there so those relative reads resolve regardless of the
    # caller's own working directory.
    os.chdir(REPO_ROOT / "dbt")

    con = duckdb.connect(str(DB_PATH), read_only=True)

    results = []
    for schema, table in EXPORTS:
        out_path = OUT_DIR / f"{table}.parquet"
        # ORDER BY ALL, not a bare COPY. DuckDB scans in parallel and emits rows
        # in whatever order threads finish, so two identical builds produced
        # byte-different Parquet -- fct_ad_spend_daily's 4,003 rows came out
        # shuffled every time. The DATA was identical, but a file that changes
        # on every build cannot be diffed, cached or checksummed, and would hide
        # a real change among the noise. Every table here is grain-unique, so
        # ordering by all columns is total and deterministic.
        con.execute(
            f"COPY (SELECT * FROM {schema}.{table} ORDER BY ALL) "
            f"TO '{out_path.as_posix()}' (FORMAT PARQUET)"
        )
        row_count = con.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
        results.append((table, row_count, out_path.stat().st_size))

    con.close()

    name_width = max(len(name) for name, _, _ in results)
    print(f"{'name'.ljust(name_width)} | {'rows':>8} | file size")
    print("-" * (name_width + 24))
    for name, rows, size in results:
        print(f"{name.ljust(name_width)} | {rows:>8} | {human_size(size)}")

    empty = [name for name, rows, _ in results if rows == 0]
    if empty:
        print(f"\nERROR: zero rows exported for: {', '.join(empty)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
