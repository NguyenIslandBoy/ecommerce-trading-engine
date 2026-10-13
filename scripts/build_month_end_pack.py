"""
Build the formula-driven Excel month-end marketing pack for one month.

Reads the exported marts (data/marts/*.parquet, written by export_marts.py),
pastes that month's rows as VALUES on the Inputs sheet -- the only pasted
numbers in the workbook -- and builds everything else as formulas:

    Read me     synthetic statement, definitions, sign convention, stamps
    Inputs      mart rows for the month, as values
    Month_End   accrual, recognised expense, variance, recon difference and
                status, recomputed from Inputs with formulas and driven by a
                tolerance cell
    Checks      TRUE/FALSE ties: workbook totals against the pasted mart
                totals, and every formula figure against the mart's own
    Commentary  plain-English sentences assembled by formulas from Month_End

Formulas use only functions that behave the same in Excel, LibreOffice and
Google Sheets (no LET, LAMBDA, XLOOKUP or spilling arrays). Blank means "no
data", never zero: every formula carries a blank through instead of treating
it as 0.

The Finance inputs behind these marts are SYNTHETIC; the workbook says so on
every sheet.

Run from the repo root after dbt build + export_marts.py:
    venv/Scripts/python.exe scripts/build_month_end_pack.py --month 2025-06
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REPO_ROOT = Path(__file__).resolve().parent.parent
MARTS = REPO_ROOT / "data" / "marts"
CHANNEL_ORDER = ["meta", "google", "tiktok", "unallocated"]
DEFAULT_TOLERANCE_PCT = 1.0  # mirrors dbt var recon_tolerance_pct

SYNTHETIC_NOTE = (
    "SYNTHETIC Finance inputs: budgets and invoices are invented for demonstration. "
    "Delivered spend comes from the ad platform extracts."
)

FONT = "Arial"
BLUE = Font(name=FONT, color="0000FF")          # pasted inputs
GREEN = Font(name=FONT, color="008000")         # links to another sheet
BLACK = Font(name=FONT, color="000000")         # formulas
BOLD = Font(name=FONT, bold=True)
TITLE = Font(name=FONT, bold=True, size=14)
NOTE = Font(name=FONT, italic=True, color="7F7F7F")
WARN = Font(name=FONT, bold=True, color="C00000")
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
INPUT_FILL = PatternFill("solid", fgColor="FFFF00")
THIN_TOP = Border(top=Side(style="thin"))

GBP = '£#,##0.00;(£#,##0.00);"-"'
PCT = '0.0%;(0.0%);"-"'

# Inputs sheet columns: (header, source column)
INPUT_COLUMNS = [
    ("channel", "channel"),
    ("approved_budget", "approved_budget"),
    ("budget_version_used", "budget_version_used"),
    ("delivered_spend", "delivered_spend"),
    ("media_invoiced", "media_invoiced"),
    ("fee_invoiced", "fee_invoiced"),
    ("estimated_unbilled_accrual", "estimated_unbilled_accrual"),
    ("recognised_media_expense", "recognised_media_expense"),
    ("recognised_expense", "recognised_expense"),
    ("budget_variance", "budget_variance"),
    ("recon_difference", "difference"),
    ("recon_status", "recon_status"),
    ("data_status", "data_status"),
]


def load_month(month: dt.date) -> tuple[pd.DataFrame, dt.date, dt.date | None]:
    budget = pd.read_parquet(MARTS / "mart_marketing_budget_vs_actual.parquet")
    recon = pd.read_parquet(MARTS / "mart_marketing_invoice_recon.parquet")
    pacing = pd.read_parquet(MARTS / "mart_marketing_pacing_daily.parquet")

    budget = budget[pd.to_datetime(budget["month_start"]).dt.date == month]
    recon = recon[pd.to_datetime(recon["month_start"]).dt.date == month]
    if budget.empty:
        raise SystemExit(f"No mart rows for {month:%Y-%m}. Is the month inside the build's date spine?")

    rows = budget.merge(
        recon[["channel", "difference", "difference_pct", "recon_status"]], on="channel", how="left"
    )
    rows["order"] = rows["channel"].map(CHANNEL_ORDER.index)
    rows = rows.sort_values("order").reset_index(drop=True)

    as_of = pd.to_datetime(budget["as_of_date"].iloc[0]).date()
    delivered = pacing[pacing["spend_status"] == "delivered"]
    data_through = pd.to_datetime(delivered["date_day"]).max().date() if not delivered.empty else None
    return rows, as_of, data_through


def cell_value(value):
    """Mart value -> Excel value. NULL stays blank; it is never written as 0."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "is_finite") or isinstance(value, (int, float)):  # Decimal or number
        return round(float(value), 2)
    return str(value)


def build(month: dt.date, out: Path, tolerance: float) -> Path:
    rows, as_of, data_through = load_month(month)
    n = len(rows)
    month_label = f"{month:%Y-%m}"

    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"
    inputs = wb.create_sheet("Inputs")
    me = wb.create_sheet("Month_End")
    checks = wb.create_sheet("Checks")
    commentary = wb.create_sheet("Commentary")

    # ---- Inputs: the only pasted numbers ----------------------------------
    for col, (header, _) in enumerate(INPUT_COLUMNS, start=1):
        c = inputs.cell(row=1, column=col, value=header)
        c.font, c.fill = BOLD, HEADER_FILL
    for i, record in rows.iterrows():
        for col, (_, source) in enumerate(INPUT_COLUMNS, start=1):
            c = inputs.cell(row=i + 2, column=col, value=cell_value(record[source]))
            c.font = BLUE
            if col in (2, 4, 5, 6, 7, 8, 9, 10, 11):
                c.number_format = GBP
    note_row = n + 3
    inputs.cell(row=note_row, column=1,
                value=f"Pasted as values from the dbt marts for {month_label} (as of {as_of}). "
                      "Blank = no data (NULL in the mart), never zero.").font = NOTE
    inputs.cell(row=note_row + 1, column=1, value=SYNTHETIC_NOTE).font = WARN

    # ---- Month_End: the logic, as formulas ---------------------------------
    me["A1"] = f"Month-end marketing pack: {month_label}"
    me["A1"].font = TITLE
    me["A2"] = SYNTHETIC_NOTE
    me["A2"].font = WARN
    me["A3"], me["B3"] = "Recon tolerance (%)", tolerance
    me["A3"].font = BOLD
    me["B3"].font, me["B3"].fill = BLUE, INPUT_FILL
    me["C3"] = "Input. Matches the dbt var recon_tolerance_pct. Change it to re-run the recon."
    me["C3"].font = NOTE
    me["A4"], me["B4"] = "Month", "='Read me'!B4"
    me["A5"], me["B5"] = "As of (cursor)", "='Read me'!B5"
    for ref in ("A4", "A5"):
        me[ref].font = BOLD
    for ref in ("B4", "B5"):
        me[ref].font = GREEN

    headers = [
        "Channel", "Approved media budget", "Delivered spend", "Media invoiced", "Fee invoiced",
        "Estimated unbilled accrual", "Recognised media expense", "Recognised expense (incl. fees)",
        "Budget variance (+ = overspend)", "Budget variance %", "Remaining budget",
        "Recon difference (invoiced - delivered)", "Recon difference %", "Recon status",
        "Data status", "|Variance| (helper)", "Budget with known spend (helper)",
    ]
    header_row = 7
    for col, text in enumerate(headers, start=1):
        c = me.cell(row=header_row, column=col, value=text)
        c.font, c.fill = BOLD, HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")

    first = header_row + 1
    last = first + n - 1
    for k in range(n):
        r = first + k
        i = k + 2  # Inputs row
        f = {
            "A": f"=Inputs!A{i}",
            "B": f'=IF(Inputs!B{i}="","",Inputs!B{i})',
            "C": f'=IF(Inputs!D{i}="","",Inputs!D{i})',
            "D": f"=SUMIFS(Inputs!$E$2:$E${n + 1},Inputs!$A$2:$A${n + 1},$A{r})",
            "E": f"=SUMIFS(Inputs!$F$2:$F${n + 1},Inputs!$A$2:$A${n + 1},$A{r})",
            "F": f'=IF(C{r}="","",ROUND(MAX(C{r}-D{r},0),2))',
            "G": f'=IF(C{r}="",IF(D{r}>0,D{r},""),ROUND(MAX(C{r},D{r}),2))',
            "H": f'=IF(C{r}="",IF(D{r}+E{r}>0,ROUND(D{r}+E{r},2),""),ROUND(G{r}+E{r},2))',
            "I": f'=IF(OR(B{r}="",G{r}=""),"",ROUND(G{r}-B{r},2))',
            "J": f'=IF(I{r}="","",IF(B{r}=0,"",I{r}/B{r}))',
            "K": f'=IF(I{r}="","",ROUND(B{r}-G{r},2))',
            "L": f'=IF(OR(C{r}="",D{r}=0),"",ROUND(D{r}-C{r},2))',
            "M": f'=IF(L{r}="","",IF(C{r}=0,"",L{r}/C{r}))',
            "N": (f'=IF($A{r}="unallocated","unallocated_cost",IF(C{r}="","no_cost_source",'
                  f'IF(D{r}=0,"unbilled",IF(ABS(D{r}-C{r})<=ABS(C{r})*$B$3/100,'
                  f'"within_tolerance","exception"))))'),
            "O": f"=Inputs!M{i}",
            "P": f'=IF(I{r}="","",ABS(I{r}))',
            "Q": f'=IF(I{r}="","",B{r})',
        }
        for letter, formula in f.items():
            c = me[f"{letter}{r}"]
            c.value = formula
            c.font = GREEN if letter in ("A", "B", "C", "D", "E", "O") else BLACK
            if letter in "BCDEFGHIKLQP":
                c.number_format = GBP
            if letter in "JM":
                c.number_format = PCT

    total = last + 1
    me[f"A{total}"] = "Total"
    me[f"A{total}"].font = BOLD
    for letter in "BCDEFGHIKLQ":
        c = me[f"{letter}{total}"]
        c.value = f"=SUM({letter}{first}:{letter}{last})"
        c.font, c.number_format, c.border = BOLD, GBP, THIN_TOP
    # Ratio of sums, over the channels where the variance is known.
    me[f"J{total}"] = f'=IF(Q{total}=0,"",I{total}/Q{total})'
    me[f"J{total}"].font, me[f"J{total}"].number_format = BOLD, PCT
    me[f"A{total + 2}"] = (
        "Blank = no data, never zero. TikTok has no cost source, so its spend and variance are blank. "
        "Total variance % = total variance / budget of channels with known spend (a ratio of sums)."
    )
    me[f"A{total + 2}"].font = NOTE

    # ---- Checks ------------------------------------------------------------
    checks["A1"], checks["B1"] = "Check", "Result"
    for ref in ("A1", "B1"):
        checks[ref].font, checks[ref].fill = BOLD, HEADER_FILL
    check_rows: list[tuple[str, str]] = []
    for letter, col, label in (("B", "B", "Approved budget"), ("C", "D", "Delivered spend"),
                               ("D", "E", "Media invoiced"), ("E", "F", "Fee invoiced")):
        check_rows.append((
            f"{label}: Month_End total equals the pasted mart total",
            f"=ROUND(Month_End!{letter}{total}-SUM(Inputs!{col}2:{col}{n + 1}),2)=0",
        ))

    def tie(me_col: str, in_col: str, r: int, i: int) -> str:
        a, b = f"Month_End!{me_col}{r}", f"Inputs!{in_col}{i}"
        return f'=IF(AND({a}="",{b}=""),TRUE,IF(OR({a}="",{b}=""),FALSE,ROUND({a}-{b},2)=0))'

    for k in range(n):
        r, i = first + k, k + 2
        channel = rows.loc[k, "channel"]
        check_rows += [
            (f"{channel}: formula accrual equals the mart accrual", tie("F", "G", r, i)),
            (f"{channel}: formula recognised media expense equals the mart", tie("G", "H", r, i)),
            (f"{channel}: formula recognised expense equals the mart", tie("H", "I", r, i)),
            (f"{channel}: formula budget variance equals the mart", tie("I", "J", r, i)),
            (f"{channel}: formula recon difference equals the mart", tie("L", "K", r, i)),
            (f"{channel}: formula recon status equals the mart", f"=Month_End!N{r}=Inputs!L{i}"),
        ]
    for k, (label, formula) in enumerate(check_rows, start=2):
        checks.cell(row=k, column=1, value=label).font = BLACK
        checks.cell(row=k, column=2, value=formula).font = BLACK
    last_check = len(check_rows) + 1
    checks.cell(row=last_check + 2, column=1, value="ALL CHECKS PASS").font = BOLD
    c = checks.cell(row=last_check + 2, column=2, value=f"=AND(B2:B{last_check})")
    c.font = BOLD
    checks.cell(row=last_check + 3, column=1,
                value="Recalculates on open. The recon status checks assume the tolerance is "
                      "left at the dbt default.").font = NOTE

    # ---- Commentary: sentences built by formulas ---------------------------
    rng = lambda col: f"Month_End!${col}${first}:${col}${last}"  # noqa: E731
    pick = lambda col, match_col, key: (  # noqa: E731
        f"INDEX({rng(col)},MATCH({key},{rng(match_col)},0))")
    largest = f"MAX({rng('P')})"
    # Channel name for prose: "meta" -> "Meta", "tiktok" -> "TikTok".
    name = lambda expr: f'SUBSTITUTE(PROPER({expr}),"Tiktok","TikTok")'  # noqa: E731
    exc = '"exception"'
    sentences = [
        '="These figures use SYNTHETIC Finance inputs (invented budgets and invoices) '
        'and are for demonstration only."',
        (f'=IF(COUNT({rng("P")})=0,"No channel has both a media budget and known spend this month.",'
         f'"The largest budget variance in "&Month_End!$B$4&" is "&{name(pick("A", "P", largest))}'
         f'&" at £"&TEXT(ABS({pick("I", "P", largest)}),"#,##0")'
         f'&" ("&TEXT(ABS({pick("J", "P", largest)}),"0.0%")&"), "'
         f'&IF({pick("I", "P", largest)}>0,"an overspend","an underspend")&" against its media budget.")'),
        (f'=IF(COUNTIF({rng("N")},"no_cost_source")=0,"",'
         f'{name(pick("A", "N", chr(34) + "no_cost_source" + chr(34)))}&" has an approved budget of £"'
         f'&TEXT({pick("B", "N", chr(34) + "no_cost_source" + chr(34))},"#,##0")'
         f'&" but no cost source, so its spend and variance are unknown (left blank), not zero.")'),
        (f'=IF(COUNTIF({rng("N")},{exc})>0,"Main risk: the "&{name(pick("A", "N", exc))}'
         f'&" invoice differs from delivered spend by "&TEXT(ABS({pick("M", "N", exc)}),"0.0%")'
         f'&", above the "&TEXT(Month_End!$B$3,"0.0")&"% tolerance."'
         f'&IF({pick("O", "N", exc)}="source_gap"," The spend extract is missing days for this month, '
         f'which may explain the gap.",""),'
         f'IF(COUNTIF({rng("N")},"unbilled")>0,"Main risk: £"&TEXT(SUM({rng("F")}),"#,##0")'
         f'&" of delivered media is not yet invoiced and is carried as an estimated accrual until the '
         f'invoices arrive.","Main risk: none flagged. Every platform invoice is within the "'
         f'&TEXT(Month_End!$B$3,"0.0")&"% tolerance."))'),
        (f'=IF(COUNTIF({rng("N")},{exc})>0,IF({pick("O", "N", exc)}="source_gap",'
         f'"Recommended action: obtain the missing days of spend from the platform and re-run the '
         f'reconciliation before querying the invoice.","Recommended action: query the "'
         f'&{name(pick("A", "N", exc))}&" invoice with the platform before approving it for payment."),'
         f'IF(COUNTIF({rng("N")},"unbilled")>0,"Recommended action: post the £"&TEXT(SUM({rng("F")}),"#,##0")'
         f'&" accrual and replace it with the actual invoices when they arrive early next month.",'
         f'"Recommended action: none beyond normal review."))'
         f'&IF(COUNTIF({rng("N")},"unallocated_cost")>0," Also allocate the £"'
         f'&TEXT(SUMIFS({rng("H")},{rng("N")},"unallocated_cost"),"#,##0")'
         f'&" of invoiced cost that has no channel.","")'),
    ]
    commentary["A1"] = f"Commentary: {month_label}"
    commentary["A1"].font = TITLE
    for k, formula in enumerate(sentences, start=3):
        c = commentary.cell(row=k, column=1, value=formula)
        c.font = BLACK
        c.alignment = Alignment(wrap_text=True, vertical="top")
    commentary.cell(row=len(sentences) + 4, column=1,
                    value="Each sentence is a formula over Month_End, so it updates if Inputs or the "
                          "tolerance change.").font = NOTE
    commentary.column_dimensions["A"].width = 120

    # ---- Read me -------------------------------------------------------------
    readme_rows = [
        ("Month-end marketing pack", None),
        (SYNTHETIC_NOTE, None),
        (None, None),
        ("Month", month_label),
        ("As of (warehouse cursor)", as_of.isoformat()),
        ("Data through (last day of delivered spend)", data_through.isoformat() if data_through else "none"),
        ("Built by", "scripts/build_month_end_pack.py from data/marts/*.parquet"),
        (None, None),
        ("Sheets", None),
        ("Inputs", "Mart rows for the month, pasted as values. The only pasted numbers."),
        ("Month_End", "All logic as formulas over Inputs, driven by the tolerance cell (B3)."),
        ("Checks", "TRUE/FALSE ties of the workbook against the marts. All must be TRUE."),
        ("Commentary", "Plain-English summary, assembled by formulas."),
        (None, None),
        ("Definitions", None),
        ("Delivered spend", "Ad platform spend for the month, days visible at the cursor. Blank where the "
                            "channel has no cost source (TikTok)."),
        ("Media invoiced", "Platform media invoice lines for the month, received by the cursor."),
        ("Fee invoiced", "Agency fee lines allocated to the channel (60/40 Meta/Google retainer). "
                         "Not budgeted and not compared with delivered spend."),
        ("Estimated unbilled accrual", "MAX(delivered - media invoiced, 0): delivered media not yet invoiced."),
        ("Recognised media expense", "MAX(delivered, media invoiced). Blank delivered: media invoiced if any."),
        ("Recognised expense (incl. fees)", "Recognised media expense + fee invoiced."),
        ("Approved media budget", "Latest budget version visible at the cursor. Budgets cover media only."),
        ("Budget variance", "Recognised media expense - approved media budget. POSITIVE = OVERSPEND."),
        ("Remaining budget", "Approved media budget - recognised media expense."),
        ("Recon difference", "Media invoiced - delivered spend (positive: billed more than delivered)."),
        ("Recon status", "unallocated_cost, no_cost_source, unbilled (no media invoice yet), "
                         "within_tolerance (|difference| <= tolerance), exception."),
        ("Data status", "complete, partial_month, source_gap (days missing from the extract), "
                        "no_cost_source, unallocated."),
        (None, None),
        ("Limitations", "Synthetic Finance inputs. Channel grain only. No GL, journal entries or true-ups. "
                        "Fees are recognised on receipt, never accrued."),
    ]
    for k, (label, value) in enumerate(readme_rows, start=1):
        if label is not None:
            readme.cell(row=k, column=1, value=label).font = BOLD if value is not None or k == 9 or k == 15 else BLACK
        if value is not None:
            readme.cell(row=k, column=2, value=value).font = BLACK
    readme["A1"].font = TITLE
    readme["A2"].font = WARN
    readme.column_dimensions["A"].width = 42
    readme.column_dimensions["B"].width = 110

    # ---- Layout --------------------------------------------------------------
    for ws in (inputs, checks):
        for col in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(col)].width = 26
    checks.column_dimensions["A"].width = 64
    me.column_dimensions["A"].width = 22
    for col in range(2, len(headers) + 1):
        me.column_dimensions[get_column_letter(col)].width = 17
    me.row_dimensions[header_row].height = 45
    me.freeze_panes = me[f"B{first}"]
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.font is None or c.font.name != FONT:
                    c.font = Font(name=FONT, bold=c.font.bold, italic=c.font.italic,
                                  color=c.font.color, size=c.font.size)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--month", required=True, help="Month to report, YYYY-MM")
    parser.add_argument("--out", type=Path, help="Output .xlsx path "
                        "(default reports/month_end/month_end_pack_<month>.xlsx)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PCT,
                        help="Recon tolerance in percent (default 1.0, matching dbt)")
    args = parser.parse_args()

    month = dt.datetime.strptime(args.month, "%Y-%m").date()
    out = args.out or REPO_ROOT / "reports" / "month_end" / f"month_end_pack_{args.month}.xlsx"
    path = build(month, out, args.tolerance)
    print(f"Wrote {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")


if __name__ == "__main__":
    main()
