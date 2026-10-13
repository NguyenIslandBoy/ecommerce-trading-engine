# Worked example: closing January 2025 at two cursors

**The Finance inputs here are SYNTHETIC** (invented budgets and invoices; see `data/finance/README.md`).
Delivered spend comes from the ad platform extracts.

This example uses the warehouse's point-in-time cursor (`as_of_date`) to show the same month as
Finance would see it at two moments. It needs no extra files: rebuild at a cursor, query, compare.

- **2025-02-03, month-end close.** January is complete: its last day of spend landed on 1 February.
  The Agency A retainer invoice has arrived (received 3 February). Neither platform invoice has.
  Meta's is received 5 February and Google's 6 February.
- **2025-03-05, a month later.** Every January invoice has been received.

## Reproduce

```bash
cd dbt
../venv/Scripts/dbt.exe build --profiles-dir . --vars "{as_of_date: 2025-02-03}"
# query (below), then:
../venv/Scripts/dbt.exe build --profiles-dir . --vars "{as_of_date: 2025-03-05}"
# query again, then restore the default cursor:
../venv/Scripts/dbt.exe build --profiles-dir .
```

Rebuild the **whole** project at each cursor, not a `--select` subset. Staging and core models are
views, so a partial build leaves some tables at the old cursor and some views at the new one.
Existing reconciliation tests then fail on the mismatch: four did when this example was first run
that way. A full build passes at both cursors.

```sql
select r.channel, r.delivered_spend, r.media_invoiced, r.fee_invoiced,
       r.estimated_unbilled_accrual, b.recognised_media_expense,
       b.approved_budget, b.budget_variance, r.difference, r.recon_status
from main_marts.mart_marketing_invoice_recon r
join main_marts.mart_marketing_budget_vs_actual b using (month_start, channel)
where r.month_start = '2025-01-01';
```

## Results

| Channel | Figure | At 2025-02-03 (close) | At 2025-03-05 (final) | Change |
|---|---|---:|---:|---:|
| Meta | Delivered spend | £8,805.35 | £8,805.35 | — |
| | Media invoiced | £0.00 | £8,778.05 | +£8,778.05 |
| | Estimated unbilled accrual | £8,805.35 | £0.00 | −£8,805.35 |
| | Recognised media expense | £8,805.35 | £8,778.05 | **−£27.30** |
| | Budget variance (budget £9,000) | −£194.65 | −£221.95 | −£27.30 |
| | Recon status | `unbilled` | `within_tolerance` (−0.31%) | |
| Google | Delivered spend | £6,471.19 | £6,471.19 | — |
| | Media invoiced | £0.00 | £6,476.36 | +£6,476.36 |
| | Estimated unbilled accrual | £6,471.19 | £0.00 | −£6,471.19 |
| | Recognised media expense | £6,471.19 | £6,476.36 | **+£5.17** |
| | Budget variance (budget £6,500) | −£28.81 | −£23.64 | +£5.17 |
| | Recon status | `unbilled` | `within_tolerance` (+0.08%) | |
| TikTok | Delivered, accrual, recognised, variance | no data | no data | — |
| | Recon status | `no_cost_source` | `no_cost_source` | |

Agency fees (£1,700.00 Meta, £1,133.33 Google) are already in `fee_invoiced` at the close, because
that invoice arrived first. They are shown but not compared or budgeted, so they do not move any
figure above.

## What it shows

1. **At close, the estimate is the delivered spend.** With no platform invoice in hand, all of
   January's delivered media (£15,276.54) is carried as an estimated unbilled accrual. Recognised
   media expense and the budget variance are known to within the invoice adjustments on
   3 February, so a reader learns the month came in under budget without waiting for invoices.
2. **The estimate was close.** The invoices landed within 0.31% of delivered spend, inside the
   1.0% tolerance. The month-end estimate missed the final cost by £5.17 on Google and £27.30 on
   Meta.
3. **The final invoice trues the month up, in both directions.** Once a full-month platform
   invoice is received (`is_media_final`), it replaces the estimate. The accrual drops to £0.00 and
   recognised media expense becomes the invoice.
   - Google's invoice came in **above** delivered spend, so recognised expense rose £5.17.
   - Meta's came in **below** (£27.30 of credits), so recognised expense fell £27.30.
   - The size of each true-up is the recon `difference`.
   - The module has no GL, so this restates the month's figures rather than posting a journal.
4. **Missing stays missing.** TikTok's £1,500 budget never turns into a £1,500 underspend.
   With no cost source, its spend and variance stay NULL at both cursors.
