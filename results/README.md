# Verified MRP example runs

These three small runs verify end-to-end execution on the downloaded data. Each selects the first 50 part IDs, starts from zero stock, uses lot-for-lot integer orders and includes startup effects. They are not representative samples or evidence for/against DDMRP.

| Run directory | Scenario | Evaluation periods | Immediate unit fill rate | Stockout item-periods | Ending backlog |
|---|---|---:|---:|---:|---:|
| `mrp_oil041_verified` | Synthetic pooled OIL-041, deterministic lead times | 275 days | 58.57% | 169 | 1 |
| `mrp_offshore_verified` | Synthetic offshore subset, lead-time CV 0.5, seed 42 | 275 days | 50.66% | 30 | 1 |
| `mrp_spdf_verified` | Original oil data, explicitly clipped negative net consumption, deterministic lead times | 21 months | 10.92% | 46 | 95 |

The scenarios differ in demand, safety-stock assumptions, units and geography, so these figures must not be read as a controlled comparison. SPDF's zero safety stock and sparse selected items yield weak cold-start performance; parameters have not been tuned on the evaluation data. OIL-041 supply uncertainty is assumed lognormal, not fitted to its supplier samples.

Reproduce into **new** output directories:

```powershell
python -m src.mrp --output results/reproduction_oil041
python -m src.mrp --offshore-only --lead-time-cv 0.5 --seed 42 --output results/reproduction_offshore
python -m src.mrp --source spdf-oil --returns clip --output results/reproduction_spdf
```

Each run includes source/implementation hashes, canonical data, initial MRP plan, rolling decisions, orders, inventory ledger and metrics. See [model documentation](../docs/mrp_baseline.md) for assumptions and KPI definitions. Raw files were not modified. Earlier development runs were discarded; only final verified runs are retained here.
