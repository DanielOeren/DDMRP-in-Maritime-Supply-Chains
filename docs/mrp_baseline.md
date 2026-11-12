# MRP baseline

This is a runnable independent-demand, single-echelon MRO baseline, not BOM-explosion manufacturing MRP. No supplied dataset establishes a complete BOM. A future BOM adapter can produce gross requirements for the same planner. The baseline is forecast-driven time-phased netting, not an optimal inventory policy or evidence that DDMRP wins.

## Run from the repository root

Python 3.10+; OIL-041 and the simulator use only the standard library. The optional original Excel adapter uses `xlrd`:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.mrp --output results/mrp_oil041
python -m src.mrp --offshore-only --lead-time-cv 0.5 --seed 42 --output results/mrp_offshore_uncertain
python -m src.mrp --source spdf-oil --returns clip --output results/mrp_spdf
```

Output directories must not already exist. Default: first 50 parts sorted by ID (not selected by future activity); use `--max-items 1500` for all OIL-041 parts. Initial inventory is zero unless `--source-stock` explicitly enables the undated source snapshot as a scenario assumption. Cold-start losses remain in reported KPIs. Compare both initialization settings and longer warmup experiments before thesis conclusions.

OIL-041 uses 365 daily periods in 2021, first 90 for training, and a 60-day trailing mean. SPDF uses contiguous monthly data December 1997–August 2001, first 24 months for training, and a 12-month trailing mean. Override with `--train-periods`, `--forecast-window`; these are periods in the selected dataset, not always days. `--planning-horizon` defaults to 60 and is extended to at least lead time + 1 for each item. The initial deterministic plan may extend beyond the observed demand horizon; it contains forecasts, never unseen actual requirements.

## Planning and execution

For each period, net requirement is `max(0, gross + safety_stock - prior_projected_available - scheduled_receipts)`. Planned receipts cover this net amount subject to lot sizing. Projected stock then equals previous projected stock + scheduled receipts + planned receipts − gross. Opening backlog is subtracted from initial availability. Planned release date is receipt period minus nominal lead time. Negative release dates are explicitly flagged as past due, not silently dropped.

Lot-for-lot is the default, rounded up to integer units. Optional `--minimum-order`, `--order-multiple`, `--fixed-order` apply to all selected SKUs; the canonical Item schema permits per-SKU settings. Fixed lots round the requirement up to a multiple of the fixed quantity, then packaging multiples are applied. When fixed lots and packaging multiples differ, packaging rounding takes precedence. No order is generated merely to satisfy an MOQ when net requirement is zero. `--safety-stock` overrides the adapter's buffer in units.

Simulation ordering is explicit:

1. Receive orders that actually arrive; fulfil older backlog first.
2. Forecast with past demand only, replan using on-hand stock, backlog and outstanding nominal receipts.
3. Apply a feasible receipt fence: no new receipt is possible before nominal lead time. Carry intervening shortages forward, net against all committed arrivals, and release the requirement at the first feasible receipt date. This avoids repeatedly ordering shortages already covered by outstanding supply. There is no instantaneous or emergency receipt.
4. Observe current demand; fulfil immediately available units and backorder the remainder. Append demand to history for next period's forecast.

Future planned releases are proposals and are replanned, not committed orders. Released orders remain in the pipeline. Simulation records actual arrival times, but policies see only nominal promises; overdue outstanding orders are assumed to arrive next period until actually received. This explicit promise-date assumption can be replaced for later experiments. No receipts are silently brought forward to the experiment's end.

`--lead-time-cv 0` uses deterministic nominal lead times. Positive CV applies a mean-one lognormal multiplier, rounds upward, and enforces at least one period. Rounding changes the realized mean/CV. Randomness is keyed by seed, SKU and release period, so policies ordering the same item on the same date receive the same supply shock. No future shock is exposed to the policy. This is a configurable assumed uncertainty model, not a fitted supplier distribution. Repeat multiple seeds/CVs and report uncertainty intervals for robustness; one run does not estimate robustness.

## Data preparation and limitations

- Raw data is never changed. Canonical items/demand and a source SHA-256 manifest are saved beside each run. Train/evaluation splits and all effective arguments are recorded.
- OIL-041 consumption is summed across selected facilities into one virtual pooled stockpoint per part. Offshore filtering uses facility flags. Missing daily events become zero; consumption is assumed to proxy demand. This is not a warehouse network model. Do not treat warehouse issues as additional demand. Source safety stocks are summed across matching inventory rows; absent pairs contribute zero. The undated snapshot safety stock is an explicit synthetic scenario parameter, not a historically validated buffer. Snapshot stock (if requested) subtracts reservations. Prices are USD. No generated stockout/reliability/recommendation labels drive decisions.
- OIL-041 nominal lead times come from the part master, rounded upward to days. Its supplier samples could support future conditional distribution fitting, but lack order/date linkage; no such fitting is claimed here. Its emergency-event chronology is inconsistent, so source emergency results are not replayed.
- SPDF reads the two original oil halves, not the altered OIL workbook. It discards the prefix through unreliable November 1997 to preserve a contiguous calendar. Whole-year blanks are converted to zero only according to source documentation; partial-year blanks cause an error. Negative net consumption is rejected by default. Explicit `--returns clip` creates a nonnegative proxy and records the number clipped; it cannot recover gross demand or separately model returns. Nominal days are divided by 30.4375 then rounded upward to monthly buckets, a coarse approximation. Original `Min` is a reorder point and is not misused as safety stock: default safety stock is zero. Source currency is unknown; zero prices are retained and counted. Item quantities may have differing source units.
- Dataset selection is a deterministic small demonstration, not a representative statistical sample. The source snapshots, demand censoring, aggregation, fractional mean forecast, cold start and terminal backlog affect results. These assumptions must be varied/justified in the thesis.

## Outputs and KPI definitions

| File | Meaning |
|---|---|
| `items.csv`, `demand.csv` | Canonical parameters and chronological demand with train/evaluation split. |
| `initial_plan.csv` | Complete initial time-phased gross/net requirements, scheduled/planned receipts, projected availability, release offsets and past-due exceptions; local period 0 is first evaluation period. |
| `decisions.csv` | Rolling forecast, order release quantity and past-due planned quantity per item/period. |
| `orders.csv` | Committed releases, nominal and actual due periods, quantities, realized lead times, horizon-censoring flag. Period indices refer to full canonical calendar. |
| `inventory.csv` | Receipts, stock, demand, immediate fulfilment, backlog service, backlog, releases, pipeline and on-hand value each evaluation period. |
| `metrics.json` | Demand-weighted immediate unit fill rate; fraction of positive-demand item-periods fully served; stockout/backlog item-periods; ending backlog/pipeline; average aggregate end-period stock/value; order counts. Zero-demand service metrics are null. |
| `run.json` | Source hashes, transformations, units, configuration, effective CLI arguments and modelling assumptions. |

The demand-period service rate is **not cycle service level**. Stockout item-periods are not distinct stockout episodes. Backorders fulfilled later do not improve immediate fill rate. Inventory value excludes pipeline and backorders; average value sums across SKUs then averages across evaluation periods. No mixed-currency aggregation across datasets is performed. Emergency orders are zero by design because no expedite policy is enabled; this is not a prediction of emergency usage. Costs are not total operating costs: no unsupported holding/freight/downtime rates are invented.

## Extension contract

`src/data/schema.py`: validated `Item` and `Dataset`. `src/mrp/planning.py`: pure netting/offsetting/lot sizing plus `MRPPolicy`. `src/simulation/engine.py`: inventory execution and keyed lead-time scenarios. `src/evaluation/metrics.py`: common metrics. The CLI orchestrates adapters and serialization.

A DDMRP policy implements `decide(item, history, on_hand, backlog, scheduled, horizon, window) -> (quantity, audit_rows)`. Inputs contain only information available at the release decision. Audit rows may be empty; any past-due rows use `planned_receipts` and `past_due_release`. The simulator accepts nonnegative finite order quantities, not future inventory outcomes. Use identical canonical inputs, initialization, calendar, supply shocks and KPI definitions for both policies. Add explicit policy information/state and an expedite interface if the later DDMRP specification needs them rather than bypassing the engine.

Tests cover hand-calculated netting/offsetting, backlog and buffer treatment, MOQ/multiples/fixed lots, invalid inputs, no magical receipts, conservation of stock/backlog/pipeline, temporal leakage, seeded uncertainty, zero-demand metrics, daily offshore preprocessing and CLI/source preservation.
