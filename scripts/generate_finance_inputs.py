"""
Generate the SYNTHETIC Finance inputs for the marketing reporting module.

Every number this script writes is invented. The ad platform extracts in
data/*.csv are the only real-shaped inputs; this script reads them so that
budgets and invoices are plausible against delivered spend, then writes three
CSVs to data/finance/:

    marketing_budget.csv          approved and revised monthly budgets
    vendor_invoice_lines.csv      invoice lines with service periods
    invoice_line_allocations.csv  allocation of invoice lines to channels

Deterministic: a fixed seed, and all money handled as integer pence, so two runs
produce byte-identical files. Vendors are generic placeholders.

Run from the repo root:
    venv/Scripts/python.exe scripts/generate_finance_inputs.py
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import duckdb

SEED = 20240701
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUT_DIR = DATA_DIR / "finance"
CURRENCY = "GBP"

# Platform invoices normally differ from delivered spend by small credits and
# FX rounding. Kept well inside the default 1.0% recon tolerance.
INVOICE_NOISE_BPS = 35  # +/- 0.35%

AGENCY_RETAINER_PENCE = 283_333  # GBP 2,833.33 a month
AGENCY_SPLIT = (("meta", Decimal("0.6000")), ("google", Decimal("0.4000")))
TIKTOK_BUDGET_PENCE = 150_000  # GBP 1,500 a month; TikTok has no cost file

# Scenario anchors. Chosen from the data, not at random, so each scenario lands
# where the underlying spend makes it meaningful.
OVERSPEND = ("meta", dt.date(2025, 5, 1))  # Meta CPC/CPM climb through Q2 2025
REVISED = ("google", dt.date(2024, 11, 1), dt.date(2024, 11, 8), Decimal("1.15"))
FRONT_LOADED = {("meta", dt.date(2025, 1, 1))}
META_GAP_MONTH = dt.date(2025, 3, 1)  # meta_ads_daily has no rows for 03-15/16
META_GAP_DAYS = (dt.date(2025, 3, 15), dt.date(2025, 3, 16))
UNALLOCATED_MONTH = dt.date(2025, 2, 1)
UNALLOCATED_PENCE = 125_000  # GBP 1,250.00 one-off creative production


def money(pence: int) -> str:
    return f"{Decimal(pence) / 100:.2f}"


def to_pence(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def month_end(month: dt.date) -> dt.date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def next_month(month: dt.date) -> dt.date:
    return (month_end(month) + dt.timedelta(days=1)).replace(day=1)


def daily_spend() -> dict[tuple[str, dt.date], int]:
    """Daily spend in pence per platform, cast exactly as stg_ads_daily does."""
    con = duckdb.connect()
    rows = con.execute(
        f"""
        select 'meta' as platform, cast(date as date) as d,
               sum(cast(spend as decimal(12,2))) as spend
        from read_csv_auto('{(DATA_DIR / "meta_ads_daily.csv").as_posix()}')
        group by all
        union all
        select 'google', cast(date as date),
               sum(cast(cost_micros / 1000000.0 as decimal(12,2)))
        from read_csv_auto('{(DATA_DIR / "google_ads_daily.csv").as_posix()}')
        group by all
        """
    ).fetchall()
    return {(p, d): to_pence(Decimal(str(s))) for p, d, s in rows}


def monthly(daily: dict[tuple[str, dt.date], int]) -> dict[tuple[str, dt.date], int]:
    out: dict[tuple[str, dt.date], int] = {}
    for (platform, day), pence in daily.items():
        key = (platform, day.replace(day=1))
        out[key] = out.get(key, 0) + pence
    return out


def mid_month_forecast(daily: dict, platform: str, month: dt.date) -> int:
    """Run-rate forecast as the pacing mart will compute it on the 15th.

    Spend lands one day late, so on the 15th days 1-14 are visible. Forecast =
    delivered to date + mean of the last 7 visible days x remaining days.
    """
    visible = [daily.get((platform, month.replace(day=d)), 0) for d in range(1, 15)]
    run_rate = Decimal(sum(visible[-7:])) / 7
    remaining = month_end(month).day - 14
    return sum(visible) + int(run_rate * remaining)


def round_down(pence: int, step_pence: int) -> int:
    return pence // step_pence * step_pence


def build(rng: random.Random):
    daily = daily_spend()
    delivered = monthly(daily)
    months = sorted({m for _, m in delivered})

    budgets, lines, allocations = [], [], []
    notes: dict[str, str] = {}

    # ---- Budgets -----------------------------------------------------------
    for month in months:
        approved_on = (month - dt.timedelta(days=1)).replace(day=20)
        for channel in ("meta", "google", "tiktok"):
            if channel == "tiktok":
                amount = TIKTOK_BUDGET_PENCE
            elif (channel, month) == OVERSPEND:
                forecast = mid_month_forecast(daily, channel, month)
                amount = round_down(min(forecast, delivered[(channel, month)]) * 95 // 100, 25_000)
            else:
                factor = Decimal(rng.randint(9600, 11000)) / 10000
                amount = round_down(int(delivered[(channel, month)] * factor), 25_000)
            profile = "front_loaded" if (channel, month) in FRONT_LOADED else "linear"
            budgets.append({
                "budget_id": f"BUD-{month:%Y-%m}-{channel}-original",
                "budget_month": month.isoformat(),
                "channel": channel,
                "budget_version": "original",
                "approved_amount": money(amount),
                "currency": CURRENCY,
                "pacing_profile": profile,
                "approved_on": approved_on.isoformat(),
                "is_synthetic": "true",
            })
            if (channel, month) == REVISED[:2]:
                revised = round_down(int(amount * REVISED[3]), 25_000)
                budgets.append({
                    **budgets[-1],
                    "budget_id": f"BUD-{month:%Y-%m}-{channel}-revised",
                    "budget_version": "revised",
                    "approved_amount": money(revised),
                    "approved_on": REVISED[2].isoformat(),
                })

    # ---- Invoices ----------------------------------------------------------
    line_no = 0

    def add_line(invoice_id, vendor, billed, month, invoice_date, received, desc, pence, alloc):
        nonlocal line_no
        line_no += 1
        line_id = f"IL-{line_no:04d}"
        lines.append({
            "invoice_line_id": line_id,
            "invoice_id": invoice_id,
            "vendor": vendor,
            "billed_channel": billed,
            "service_period_start": month.isoformat(),
            "service_period_end": month_end(month).isoformat(),
            "invoice_date": invoice_date.isoformat(),
            "received_date": received.isoformat(),
            "line_description": desc,
            "amount": money(pence),
            "currency": CURRENCY,
            "is_synthetic": "true",
        })
        for channel, pct in alloc:
            allocations.append({
                "invoice_line_id": line_id,
                "channel": channel,
                "allocation_pct": f"{pct:.4f}",
                "is_synthetic": "true",
            })
        return line_id

    for month in months:
        following = next_month(month)

        # Platforms bill in arrears: issued early in M+1, so at month-end close
        # the month is delivered but not yet invoiced.
        for platform, vendor, issue_day in (("meta", "Platform Meta", 2), ("google", "Platform Google", 3)):
            invoice_date = following.replace(day=issue_day)
            received = invoice_date + dt.timedelta(days=rng.randint(2, 6))
            base = delivered[(platform, month)]
            noise_bps = rng.randint(-INVOICE_NOISE_BPS, INVOICE_NOISE_BPS)
            pence = base + base * noise_bps // 10_000
            desc = "Media spend"
            if platform == "meta" and month == META_GAP_MONTH:
                # The platform billed for the days whose extract never arrived.
                # Estimated as the mean of the nearest days either side.
                neighbours = (daily[("meta", dt.date(2025, 3, 14))] + daily[("meta", dt.date(2025, 3, 17))]) // 2
                pence = base + neighbours * len(META_GAP_DAYS)
                desc = "Media spend (includes 2025-03-15/16, absent from the extract)"
            line_id = add_line(
                f"INV-{platform.upper()}-{month:%Y%m}", vendor, platform, month,
                invoice_date, received, desc, pence, [(platform, Decimal("1.0000"))],
            )
            if (platform, month) == ("meta", dt.date(2025, 1, 1)):
                notes["arrears"] = (
                    f"meta 2025-01: delivered in January, {line_id} invoiced {invoice_date}, "
                    f"received {received}"
                )
            if platform == "meta" and month == META_GAP_MONTH:
                notes["tolerance"] = (
                    f"meta 2025-03: {line_id} bills GBP {money(pence)} against GBP {money(base)} "
                    f"delivered in the extract ({(pence - base) / base:+.1%})"
                )

        # Agency retainer, invoiced on the 1st of M+1, split 60/40 meta/google.
        invoice_date = following
        received = invoice_date + dt.timedelta(days=rng.randint(1, 4))
        invoice_id = f"INV-AGENCYA-{month:%Y%m}"
        retainer_id = add_line(
            invoice_id, "Agency A", "agency", month, invoice_date, received,
            "Monthly management retainer", AGENCY_RETAINER_PENCE, AGENCY_SPLIT,
        )
        if month == dt.date(2024, 7, 1):
            notes["split"] = f"agency 2024-07: {retainer_id} GBP {money(AGENCY_RETAINER_PENCE)} split 60/40 meta/google"
        if month == UNALLOCATED_MONTH:
            one_off = add_line(
                invoice_id, "Agency A", "agency", month, invoice_date, received,
                "Creative production (one-off)", UNALLOCATED_PENCE, [],
            )
            notes["unallocated"] = f"agency 2025-02: {one_off} GBP {money(UNALLOCATED_PENCE)} has no allocation rows"

    channel, month = OVERSPEND
    budget = next(b for b in budgets if b["channel"] == channel and b["budget_month"] == month.isoformat())
    forecast = mid_month_forecast(daily, channel, month)
    budget_pence = to_pence(Decimal(budget["approved_amount"]))
    assert forecast > budget_pence and delivered[OVERSPEND] > budget_pence, "overspend scenario did not land"
    notes["overspend"] = (
        f"meta 2025-05: budget GBP {budget['approved_amount']}, forecast on 2025-05-15 GBP {money(forecast)}, "
        f"final delivered GBP {money(delivered[OVERSPEND])}"
    )
    notes["no_cost"] = f"tiktok: budget GBP {money(TIKTOK_BUDGET_PENCE)} every month, no cost file, no invoices"
    notes["revised"] = (
        f"google 2024-11: original approved 2024-10-20, revised x{REVISED[3]} approved {REVISED[2]}"
    )
    return budgets, lines, allocations, notes


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    budgets, lines, allocations, notes = build(random.Random(SEED))
    write(OUT_DIR / "marketing_budget.csv", budgets)
    write(OUT_DIR / "vendor_invoice_lines.csv", lines)
    write(OUT_DIR / "invoice_line_allocations.csv", allocations)

    print("SYNTHETIC Finance inputs written to data/finance/ (seed %d)" % SEED)
    print(f"  marketing_budget.csv          {len(budgets):>4} rows")
    print(f"  vendor_invoice_lines.csv      {len(lines):>4} rows")
    print(f"  invoice_line_allocations.csv  {len(allocations):>4} rows")
    print()
    print("Scenarios (query from the repo root with duckdb):")
    f = "data/finance"
    print(f"  1 forecast overspend   {notes['overspend']}")
    print(f"      select * from '{f}/marketing_budget.csv' where channel='meta' and budget_month='2025-05-01'")
    print(f"  2 invoiced in arrears  {notes['arrears']}")
    print(f"      select * from '{f}/vendor_invoice_lines.csv' where invoice_id='INV-META-202501'")
    print(f"  3 agency split 60/40   {notes['split']}")
    print(f"      select * from '{f}/invoice_line_allocations.csv' where invoice_line_id in "
          f"(select invoice_line_id from '{f}/vendor_invoice_lines.csv' where billed_channel='agency' "
          f"and service_period_start='2024-07-01')")
    print(f"  4 missing cost source  {notes['no_cost']}")
    print(f"      select * from '{f}/marketing_budget.csv' where channel='tiktok'")
    print(f"  5 unallocated line     {notes['unallocated']}")
    print(f"      select l.* from '{f}/vendor_invoice_lines.csv' l anti join '{f}/invoice_line_allocations.csv' a "
          f"using (invoice_line_id)")
    print(f"  + recon exception      {notes['tolerance']}")
    print(f"      select * from '{f}/vendor_invoice_lines.csv' where invoice_id='INV-META-202503'")
    print(f"  + revised budget       {notes['revised']}")
    print(f"      select * from '{f}/marketing_budget.csv' where budget_version='revised'")


if __name__ == "__main__":
    main()
