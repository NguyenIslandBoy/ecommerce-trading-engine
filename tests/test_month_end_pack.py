"""The month-end pack keeps its logic in formulas, not pasted results.

Builds the pack from the exported marts into a temp directory and checks
the cell CONTENTS openpyxl wrote: the calculation and check cells must be
formulas, and Inputs must hold plain values. Whether the formulas evaluate
to the right numbers is checked by recalculating in LibreOffice or Excel
(see the Checks sheet); CI has neither, so that step is manual.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from openpyxl import load_workbook

from scripts.build_month_end_pack import MARTS, build

pytestmark = pytest.mark.integration

needs_marts = pytest.mark.skipif(
    not (MARTS / "mart_marketing_budget_vs_actual.parquet").exists(),
    reason="run dbt build + scripts/export_marts.py first",
)


@pytest.fixture(scope="module")
def workbook(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack") / "pack.xlsx"
    build(dt.date(2025, 3, 1), out, tolerance=1.0)
    return load_workbook(out)


@needs_marts
def test_the_pack_has_the_five_sheets(workbook):
    assert workbook.sheetnames == ["Read me", "Inputs", "Month_End", "Checks", "Commentary"]


@needs_marts
def test_month_end_figures_are_formulas(workbook):
    sheet = workbook["Month_End"]
    first = 8
    channels = [sheet.cell(r, 1).value for r in range(first, sheet.max_row + 1)
                if str(sheet.cell(r, 1).value or "").startswith("=Inputs!A")]
    assert len(channels) >= 3
    for row in range(first, first + len(channels)):
        for col in "BCDEFGHIJKLMN":
            value = sheet[f"{col}{row}"].value
            assert isinstance(value, str) and value.startswith("="), f"Month_End!{col}{row} is not a formula"
    # Accrual and status use the stated rule and the tolerance cell.
    assert "MAX(C8-D8,0)" in sheet["F8"].value
    assert "$B$3" in sheet["N8"].value
    assert sheet["B3"].value == 1, "tolerance input cell holds a number, not a formula"


@needs_marts
def test_checks_and_commentary_are_formulas(workbook):
    checks = workbook["Checks"]
    results = [checks.cell(r, 2).value for r in range(2, checks.max_row + 1) if checks.cell(r, 2).value]
    assert len(results) >= 20
    assert all(str(v).startswith("=") for v in results)
    sentences = [c.value for c in workbook["Commentary"]["A"][2:7]]
    assert all(str(v).startswith("=") for v in sentences)


@needs_marts
def test_inputs_are_values_and_missing_stays_blank(workbook):
    sheet = workbook["Inputs"]
    for row in sheet.iter_rows(min_row=2, max_col=13):
        for cell in row:
            assert not (isinstance(cell.value, str) and cell.value.startswith("="))
    tiktok = next(r for r in range(2, sheet.max_row + 1) if sheet.cell(r, 1).value == "tiktok")
    assert sheet.cell(tiktok, 4).value is None, "TikTok delivered spend must be blank, not 0"
    assert sheet.cell(tiktok, 10).value is None, "TikTok budget variance must be blank, not 0"


@needs_marts
def test_every_sheet_says_the_inputs_are_synthetic(workbook):
    for name in ("Read me", "Inputs", "Month_End", "Commentary"):
        text = " ".join(str(c.value) for row in workbook[name].iter_rows() for c in row if c.value)
        assert "SYNTHETIC" in text, name
