# Month-end marketing packs — SYNTHETIC Finance inputs

**The budgets and invoices behind these workbooks are invented** (see `data/finance/README.md`).
Delivered spend comes from the ad platform extracts. Vendors are placeholders.

| File | Month | What it shows |
|---|---|---|
| `month_end_pack_2025-06.xlsx` | 2025-06 | The real close at the default cursor (2025-07-01). June spend is delivered but not yet invoiced, so £20,489 is carried as an estimated accrual, and both platforms are `unbilled`. |
| `month_end_pack_2025-03.xlsx` | 2025-03 | A recon exception. The Meta invoice is 6.9% above delivered spend, above the 1.0% tolerance. The Meta extract is missing 2025-03-15/16 (`source_gap`), which explains the gap. Both platform invoices are final, so the true-up has replaced the estimate (accrual 0). |

Each workbook has five sheets:
- `Read me`: definitions, the variance sign convention (positive = overspend) and the as-of stamp.
- `Inputs`: the mart rows, pasted as values. These are the only pasted numbers.
- `Month_End`: every figure is a formula, driven by the tolerance cell `B3`.
- `Checks`: TRUE/FALSE ties against the marts.
- `Commentary`: sentences built by formulas.

The functions used behave the same in Excel, LibreOffice and Google Sheets.

Regenerate after `dbt build` and `scripts/export_marts.py`:

```bash
venv/Scripts/python.exe scripts/build_month_end_pack.py --month 2025-06
```

The committed copies were recalculated in LibreOffice (99 formulas, 0 errors, all 23 checks TRUE),
so their cached values show in previewers. Excel recalculates on open.
