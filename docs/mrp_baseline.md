# Forecast-driven MRP baseline

The model plans purchased spares with independent demand. Each SKU has one pooled stocking point, one nominal replenishment lead time and a review every period. The available sources lack the bill of materials, dated opening inventory and supplier order-to-receipt history required to reconstruct an industrial MRP installation.

## Inputs and assumptions

| Input | Source information | Simulation treatment |
|---|---|---|
| OIL-041 demand | Synthetic part, facility, quantity and work-order timestamp in 2021 | Sum consumption by part/day across included facilities. A day without an event contributes zero. Offshore mode filters on the facility master. All eligible master parts stay in the selection frame. |
| OIL-041 stock and cost | Undated synthetic warehouse snapshot, reservations, safety stock, USD unit cost | Default opening stock is zero. `--source-stock` sums available stock after flooring stock less reservations at zero **at each warehouse**. The buffer sums recorded safety stock. Missing rows contribute zero and their coverage is reported. |
| SPDF consumption | Signed monthly net withdrawals, Jan 1997–Aug 2001, reverse chronological order | Use the contiguous 45 months starting Dec 1997. The source flags Nov 1997 as unreliable. Omitting Jan–Oct sacrifices ten valid months and avoids treating Oct and Dec as adjacent. A whole documented blank year maps to zero. A partial-year blank raises an error. |
| SPDF returns | Negative values and zeros can reflect returns | Reject negatives by default. Explicit `--returns clip` sets negatives to zero in the demand proxy and retains the signed cell, date, treatment and clipped quantity. Gross requests and return dates cannot be reconstructed from a net monthly total. |
| SPDF stock and cost | Undated stock, Min, Max, unit price and equipment-installation counts | Optional snapshot stock. Min is a reorder point. The baseline buffer is zero. Preserve Min, Max, raw lead days, H/M/L and total installations for analysis. Keep blank criticality associations and zero prices visible. Currency is unspecified. |
| Nominal lead time | One part-master value in calendar days | Round upward to whole days for OIL-041 or divide by 30.4375 then round upward for SPDF. Both use a one-period minimum. The raw value remains available, including zero days. |
| Orders and demand | Observed demand is proxied by consumption | One regular release per part per review, unlimited supply capacity, full backordering and backlog-first fulfilment. No estimated expedite, freight, holding or downtime cost enters the simulation. |

Selection uses IDs from the master, independent of dates and activity. The first 50 sorted IDs reproduce the historical demonstration. `--selection-seed N` samples up to `--max-items` uniformly without replacement from the sorted frame. The supply seed is separate. Full frames contain 1,500 OIL-041 and 14,523 SPDF parts. Preserve inactive items when estimating portfolio inventory and state the number of items with scored demand when reporting service.

## Timing and planning equations

At review origin $t$, receive actual arrivals and apply available stock to older backlog. The policy sees remaining on-hand $I$, backlog $B$, and outstanding nominal receipts $S_j$ at offsets $j$ from this review. An overdue outstanding commitment is provisionally assigned offset one. Early arrivals are immediately visible. Future actual arrival dates and current-period demand are hidden. Release today's order, observe demand, serve the available quantity immediately and carry the rest into backlog.

The MRP forecast $f_t$ is the arithmetic mean of the last $W$ observed consumption buckets strictly before $t$, including zeros. Use all available history when there are fewer than $W$ observations. Forecasts stay constant over a plan. This estimates a level for intermittent series. It has no seasonal, occurrence/size, maintenance-schedule or failure model.

For gross forecast $G_j$, safety stock $SS$, nominal lead $L$ and initial projected balance $A_{-1}=I-B$, the planner uses

$$
N_j=\max(0,G_j+SS-A_{j-1}-S_j),\qquad
P_j=\begin{cases}0 & j<L\\ \operatorname{lot}(N_j)& j\ge L,\end{cases}
\qquad A_j=A_{j-1}+S_j+P_j-G_j.
$$

A planned receipt $P_j$ requires a release at $j-L$. Before the feasible receipt date, negative projected availability carries the expected shortage forward. Deficits from successive rows must never be added together. The first possible new receipt determines today's release. With a level forecast the same calculation reduces to

$$
Q_t=\operatorname{lot}\!\left(\max\!\left[0,(L+1)f_t+SS+B-I-\sum_{j=0}^{L} S_j\right]\right).
$$

The receipt-day demand bucket is included because orders arrive before demand. Later proposals can change at every review. The rolling simulator computes this one quantity. The detailed planner produces the complete schedule at the first review and for hand checks. `plan(..., earliest_receipt=0)` also supports textbook backward offsetting and marks past-due proposals. The exported first plan applies the executable fence $L$.

Lot sizing returns zero for a zero requirement. A positive order covers both the net requirement and MOQ, rounded upward to the smallest increment divisible by both active lot sizes. With fixed lots of 20 and packs of 6, 60 is the first feasible quantity. The default increment is one. Decimal lot sizes are supported. Numerical comparisons at arithmetic boundaries allow floating-point roundoff.

## Calendar, initialization and supply scenarios

Training establishes history. Simulation then starts with the chosen stock, zero open orders and zero backlog. Optional `--warmup-periods` executes reviews after training. All warmup stock, orders and backlog carry into evaluation. The ledger retains these rows and labels the split. Scores use evaluation demand and inventory, and count orders by **release date** within evaluation. Receipts can therefore include orders released during warmup.

| Default | OIL-041 | SPDF oil |
|---|---:|---:|
| Period | day | month |
| Training periods | 90 | 24 |
| Forecast window | 60 | 12 |
| Warmup periods | 0 | 0 |
| Scored periods | 275 | 21 |

The requested planning horizon defaults to 60 and extends to at least $L+1$ for each item. Forecasts and committed orders may extend beyond the observed calendar. The model carries outstanding terminal orders and backlog at face quantity. A finite horizon therefore needs terminal-state reporting alongside service.

Supply is deterministic by default. A positive `--lead-time-cv c` specifies a sensitivity scenario. For $L>1$, draw a mean-one lognormal multiplier $Z$ with $\mathrm{CV}(Z)=cL/(L-1)$ and set $X=1+(L-1)Z$. Then $E[X]=L$ and $\mathrm{SD}(X)=cL$. Round $X$ up with probability equal to its fractional part and down otherwise. Conditional expectation is $X$, so integer lead times preserve the nominal mean. Rounding adds variance. When $L=1$, the only positive integer distribution with that mean is a constant one. The realized distribution among released orders depends on the SKU mix and release dates.

The seed, SKU and release period key an independent supply draw. Policies ordering the same SKU on the same date share that draw. This construction has no supplier-wide disruption correlation. OIL-041 supplier samples are synthetic and lack purchase-order/date linkage. SPDF supplies nominal lead times only. Neither source identifies this stochastic law.

## Running and inspecting an experiment

```powershell
python -m src.mrp --help
python -m src.mrp --output results/local/daily
python -m src.mrp --train-periods 60 --warmup-periods 30 --output results/local/warm
python -m src.mrp --offshore-only --lead-time-cv 0.5 --seed 42 --output results/local/offshore
python -m src.mrp --source spdf-oil --returns clip --output results/local/monthly
```

Use a new output directory each time. The CLI checks balances before publishing the run. `--safety-stock`, `--minimum-order`, `--order-multiple` and `--fixed-order` set common scenario quantities in part units. The item schema can represent SKU-specific constraints. Validate any such rules against source knowledge before interpreting performance.

| Output | Interpretation |
|---|---|
| `items.csv` | Effective item parameters alongside descriptive source fields. Zero OIL-041 snapshot totals with `inventory_rows=0` indicate missing coverage. |
| `demand.csv` | Canonical demand, dates and split. SPDF includes signed source net consumption and cell treatment. Blank source cells remain blank. |
| `initial_plan.csv` | Complete feasible first plan. Offset zero is the first simulated review, which can be a warmup review. |
| `decisions.csv` | Full-calendar review dates, policy-supplied forecast, release and effective horizon. |
| `orders.csv` | Committed quantities, release periods, promised and actual due periods, and terminal receipt flags. Actual due dates are ex-post audit information. |
| `inventory.csv` | Beginning and ending stock, pipeline and backlog, receipts, releases, immediate and delayed fulfilment, value and split. |
| `metrics.json`, `item_metrics.csv` | Portfolio scores and the same measures by SKU for joining with source attributes. |
| `run.json` | Effective configuration, arguments, source and implementation byte hashes, Python/reader versions, transformation counts, scored dates and maximum balance residuals. |

**Service.** Unit fill is immediately served units divided by requested units. Mean item fill averages that ratio equally across items with positive evaluation demand. Demand-period service measures fully served positive-demand SKU-periods. A zero denominator yields JSON null or an empty CSV field. Stockout item-periods count periods with unmet new demand. Backlog item-periods also include periods with carryover shortages. These measures use aggregated buckets. Event service, replenishment-cycle service and elapsed waiting time need different observations.

**Inventory and orders.** Sum closing on-hand units or their source-price value across the cohort, then average across evaluation periods. Quantities can represent heterogeneous parts. Zero-priced inventory contributes units but no value. Opening and ending stock, backlog and pipeline expose boundary effects. For each SKU, opening pipeline plus released units equals receipts plus ending pipeline. Retain this identity when comparing runs with warmup. Emergency orders are always zero under the regular-order model.

## Future policy comparison

The engine owns the calendar, arrivals, lot constraints, fulfilment, inventory and common scoring. A policy implements `release(item, state) -> Decision(quantity, forecast=None)`. The state provides the absolute review period, past demand within the configured lookback, usable stock, residual backlog, nominal scheduled quantities by offset, and review horizon. Each call receives its own schedule mapping. The engine records the policy's optional forecast. Raw source attributes, future demand and realized lead times stay outside this interface. `MRPPolicy.decide(...)` remains available for detailed MRP audits. Existing policies using the original seven-argument `decide` API can still return a quantity and audit rows. They receive the full observed history. Their logged forecast is empty because that API does not identify the forecast used.

1. Freeze the cohort or sampling design, observation calendar, proxy treatments, shared information, lot rules, costs and supply scenarios. Compare each policy on the same holdout dates. Predeclare whether initialization measures startup or ongoing operation. A warmup gives each policy the same opportunity to build its own state.
2. Use successive training/validation origins to choose forecast windows, MRP safety stocks, and eventually DDMRP buffer parameters. Give both policies equivalent search budgets and objectives. Reserve the final holdout and independent supply seeds for scoring. An observed MRP buffer or a zero SPDF buffer is one scenario choice. Record the service–inventory trade-off for each method.
3. Within each scenario, calculate paired KPI differences. Report them by dataset and period unit, with denominators, criticality or training-defined activity groups, and opening/terminal exposure. Replicate supply seeds to estimate uncertainty conditional on the demand path. Additional seeds on one path give no estimate of demand-history uncertainty.
4. Preserve shared fulfilment rules when extending the policy interface. Material requirements from a BOM, a facility network, known maintenance orders or confirmed supplier updates require documented new inputs. Inventory value alone cannot establish total cost or downtime benefits.

The [review results](../results/README.md) quantify the effects measured on these files.
