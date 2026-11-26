# MRP baseline review and validation

The deterministic OIL-041 and SPDF runs reproduce the archived orders, inventory trajectories and all previously reported portfolio KPIs. The audit now shows the executable first release and the conditions under which those service levels were obtained. The following runs use format version 2 and the local inputs identified in the [source guide](../SOURCES.md).

## Results on the supplied data

Every row uses 50 items. Daily runs score 1 April–31 December 2021 (275 days). The monthly run scores December 1999–August 2001 (21 months). Values in the daily rows are USD. The monthly row uses unspecified source currency.

| Scenario | Active SKUs | Requested units | Immediate unit fill | Mean item fill | Average on-hand value | Ending backlog / pipeline |
|---|---:|---:|---:|---:|---:|---:|
| OIL-041, first IDs, cold start | 49 | 1,668 | 58.57% | 77.00% | 337,787.84 | 1 / 105 |
| Same IDs and dates, 30 days of replenishment before scoring | 49 | 1,668 | 66.31% | 88.02% | 380,444.86 | 1 / 105 |
| Same IDs and dates, undated snapshot as opening stock | 49 | 1,668 | 66.43% | 91.49% | 456,738.89 | 1 / 101 |
| OIL-041, uniform master-ID sample, selection seed 42, cold start | 50 | 1,549 | 82.05% | 86.03% | 326,002.33 | 10 / 140 |
| Offshore facilities, first IDs, nominal leads | 26 | 152 | 30.26% | 27.05% | 54,617.18 | 1 / 21 |
| Same offshore cohort, lead-time CV 0.5, supply seed 42 | 26 | 152 | 53.29% | 29.98% | 52,961.69 | 1 / 19 |
| SPDF oil, first IDs, explicit clipping, cold start | 33 | 476 | 10.92% | 14.97% | 22,024.58 | 95 / 113 |

The warmup run starts reviewing after 60 training days and carries 117 units on hand, 118 backordered and 558 on order into April. All policies can be given the same forecasting history at the scoring boundary and the same opportunity to establish stock during warmup. The snapshot scenario instead starts with 619 available units and an empty pipeline. Its extra inventory value matters when interpreting the similar fill rates.

Changing only the item sample moved pooled unit fill from 58.57% to 82.05%. Changing only the supply scenario in the offshore cohort changed the delivery dates encountered by 65 orders on a sparse demand path. These examples demonstrate sensitivity to cohort and scenario choice. The monthly denominator, price units and observation process differ from those of the daily runs.

## Findings and behaviour changes

| Finding | Change and consequence |
|---|---|
| The archived unconstrained initial plan gave different first releases from the recorded decision for all 50 daily items. | Export the feasible first-review plan. For PART-000000001 the exported period-zero release changes from 0 to 5, the quantity actually ordered. The standalone planner retains textbook past-due offsetting. |
| Every review built and serialized a complete future plan, then the engine independently recomputed the MRP forecast. | Compute only the release at the nominal receipt fence during simulation. Record the forecast returned by the policy. Retain the full plan for inspection. The obsolete rolling past-due quantity was always zero under the fence and is omitted from version 2. |
| Applying package rounding after fixed-lot rounding could violate the fixed lot. | Use the smallest common feasible increment and respect MOQ. This changes cases with interacting constraints. Default integer lot-for-lot cases reproduce the archived deterministic trajectories. |
| Ceiling every stochastic delay raised the mean as well as changing variability. | The new positive-integer scenario preserves the nominal mean in expectation. CV targets the continuous delay before stochastic rounding. A one-period nominal lead stays fixed. The archived offshore CV 0.5 result was 50.66% fill. Version 2 gives 53.29% with the same seed under a different supply law. |
| Cold starts, first-ID selection and per-unit weighting could dominate a comparison. | Make warmup and seeded sampling explicit options. Report the scored dates, opening/terminal states, active-SKU count, per-SKU measures and equal-item fill alongside pooled service and value. The historical cold start and cohort remain the defaults. |
| Aggregation hid negative source cells, stock coverage and explanatory item attributes. | Check master and transaction keys, references, quantities and month axes. Retain signed monthly net cells, blank/clip treatments, criticality information, original lead days and snapshot coverage. OIL-041 floors warehouse availability where reservations exceed stock and flags those rows. |

Fractional quantities exposed two additional boundary errors in hand-checkable examples. One hundred scheduled receipts of 0.1 can cover ten units exactly while repeated binary addition leaves an apparent shortfall. The full planner now compensates cumulative gross demand and receipts and counts planned lots as integers. Fulfilment also treats rounding noise at the available-stock boundary as full service. Tests guard against an extra lot and a false stockout.

The model still uses a trailing mean. It is causal and hand-checkable, and this review found no evidence for fitting a different forecast to the final holdout. The notebook displays signed and absolute one-step forecast errors for a training-selected item. Future forecasting and buffer optimization need earlier validation origins and a separate final evaluation.

## Validation record

- The original 18 tests passed before refactoring. All 29 updated tests passed. They cover the worked plan, interacting lot sizes, 300 comparisons of the direct release against full feasible plans, hidden demand, initialization, constraint enforcement, mean-preserving supply, KPI denominators, preprocessing and CLI exports. Run it with `python -m unittest discover -s tests -v`.
- All seven CLI runs reconciled orders, receipts, immediate fulfilment, backlog priority, on-hand stock, pipeline and state continuity. The largest absolute balance residual was **0 units** across **85,050 executed SKU-periods** including warmup. Independently reading the CSVs reproduced portfolio fill, demand-period service, valuation from source unit costs, and the scored backlog and pipeline balances. Every SKU's initial release matched its first decision.
- All four OIL-041 input hashes and both SPDF workbook hashes matched the original run manifests. Full-frame preprocessing found 72 of 1,500 OIL-041 parts with no snapshot rows and 14 over-reserved warehouse rows. In the 45 retained SPDF months across 14,523 materials, 3,672 negative cells represented 42,351 units of negative net withdrawals. The explicit clip scenario recorded 328,278 whole-year blank cells as zeros. The 50-item monthly run clipped 8 cells totalling 13 units and converted 1,196 documented blanks.
- The notebook ran from a fresh kernel, including three stochastic seeds, and passed its assertions. Execution used Python 3.14.4, xlrd 2.0.2, pandas 3.1.1, matplotlib 3.11.2 and ipykernel 7.4.0.
- On this machine the median of five simulator-only runs on the first five OIL-041 items fell from 0.392 s at revision `dfaac66` to 0.011 s. Both used default settings on preloaded inputs. This timing excludes data loading, CSV export and verification. The simulator still retains the ledger and decisions in memory.

## Reproduction

From the repository root, choose new output directories. The review's local full ledgers are under `results/local/mrp_review_2026-11-26/`. They can be regenerated with the following commands.

```powershell
python -m src.mrp --output results/local/replay/oil041
python -m src.mrp --train-periods 60 --warmup-periods 30 --output results/local/replay/warm
python -m src.mrp --source-stock --output results/local/replay/snapshot
python -m src.mrp --selection-seed 42 --output results/local/replay/sample
python -m src.mrp --offshore-only --output results/local/replay/offshore_fixed
python -m src.mrp --offshore-only --lead-time-cv 0.5 --seed 42 --output results/local/replay/offshore
python -m src.mrp --source spdf-oil --returns clip --output results/local/replay/spdf
```

The tracked directories `mrp_oil041_verified`, `mrp_offshore_verified`, `mrp_spdf_verified` and `my_mrp_run` are historical exports. The last has the same portfolio scores as the historical daily example. Use their original manifests when citing them. The [model specification](../docs/mrp_baseline.md) defines the current run format, remaining data and network limitations, and the protocol for a future paired MRP/DDMRP comparison.
