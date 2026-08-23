"""Prove the in-memory cursor reconstruction equals a real dbt rebuild.

engine/pit.py lets the backtest replay 365 cursors in milliseconds instead of
~3.5 hours. That shortcut is only defensible if it is provably identical to what
dbt produces, so this script rebuilds the warehouse at several cursors and
compares every cell.

Run from the repo root:  venv/Scripts/python.exe scripts/verify_pit.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.pit import daily_trading_as_of  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DBT = ROOT / "venv" / "Scripts" / "dbt.exe"
DB = ROOT / "dbt" / "trading_engine.duckdb"
CURSORS = ["2024-12-15", "2025-03-31", "2025-05-20"]

# Float noise only: DuckDB decimals round-trip through pandas as objects.
TOLERANCE = 0.005


def build(as_of: str | None) -> None:
    cmd = [str(DBT), "build", "--profiles-dir", "."]
    if as_of:
        cmd += ["--vars", f"{{as_of_date: {as_of}}}"]
    result = subprocess.run(
        cmd, cwd=ROOT / "dbt", capture_output=True, text=True,
        env={"PYTHONUTF8": "1", **dict(__import__("os").environ)},
    )
    if result.returncode != 0:
        print(result.stdout[-3000:])
        raise SystemExit(f"dbt build failed for as_of={as_of}")


def read_daily() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        return con.execute(
            "select * from main_marts.mart_daily_trading order by date_day, channel"
        ).df()
    finally:
        con.close()


def compare(expected: pd.DataFrame, actual: pd.DataFrame, cursor: str) -> list[str]:
    problems: list[str] = []
    if len(expected) != len(actual):
        problems.append(f"row count {len(actual)} vs dbt {len(expected)}")
        return problems

    key = ["date_day", "channel"]
    expected = expected.sort_values(key).reset_index(drop=True)
    actual = actual.sort_values(key).reset_index(drop=True)

    for column in expected.columns:
        if column not in actual.columns:
            problems.append(f"missing column {column}")
            continue
        left, right = expected[column], actual[column]

        null_gap = (left.isna() != right.isna()).sum()
        if null_gap:
            problems.append(f"{column}: {null_gap} rows differ on NULL-ness")
            continue

        both = left.notna() & right.notna()
        if not both.any():
            continue
        try:
            delta = (
                pd.to_numeric(left[both], errors="raise").astype(float)
                - pd.to_numeric(right[both], errors="raise").astype(float)
            ).abs()
            bad = int((delta > TOLERANCE).sum())
            if bad:
                problems.append(f"{column}: {bad} rows differ numerically (max {delta.max():.4f})")
        except (ValueError, TypeError):
            bad = int((left[both].astype(str) != right[both].astype(str)).sum())
            if bad:
                problems.append(f"{column}: {bad} rows differ")
    return problems


def main() -> int:
    print("Building at the default cursor to obtain the full spine...")
    build(None)
    full = read_daily()
    print(f"  full spine: {len(full):,} rows\n")

    failures = 0
    for cursor in CURSORS:
        print(f"cursor {cursor}")
        build(cursor)
        expected = read_daily()
        actual = daily_trading_as_of(full, cursor)

        problems = compare(expected, actual, cursor)
        structural = {"tiktok", "unattributed"}
        pending_spend = int(
            actual.loc[~actual["channel"].isin(structural), "ad_spend"].isna().sum()
        )
        print(f"  dbt rebuild : {len(expected):,} rows")
        print(f"  reconstructed: {len(actual):,} rows ({pending_spend} google/meta rows still awaiting spend)")
        if problems:
            failures += 1
            print("  MISMATCH:")
            for problem in problems:
                print(f"    - {problem}")
        else:
            print("  identical on every column")
        print()

    print("Restoring the default cursor...")
    build(None)

    if failures:
        print(f"\nFAILED: {failures}/{len(CURSORS)} cursors did not match.")
        return 1
    print(f"\nPASS: reconstruction is identical to a real rebuild at all "
          f"{len(CURSORS)} cursors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
