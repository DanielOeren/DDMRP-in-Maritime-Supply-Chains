"""Deterministic time-phased netting; negative release dates are explicit exceptions."""
from dataclasses import asdict, dataclass
import math
from src.data.schema import validate_demand


def lot_size(net, item):
    if net <= 1e-9:
        return 0.0
    quantity = max(net, item.minimum_order)
    if item.fixed_order:
        quantity = math.ceil(quantity / item.fixed_order - 1e-12) * item.fixed_order
    quantity = math.ceil(quantity / item.order_multiple - 1e-12) * item.order_multiple
    return float(quantity)


@dataclass(frozen=True)
class PlanRow:
    period: int
    gross_requirements: float
    scheduled_receipts: float
    net_requirements: float
    planned_receipts: float
    planned_releases: float
    projected_available: float
    release_period: int | None
    past_due_release: bool


def plan(item, gross, scheduled=None, opening=None, backlog=0, earliest_receipt=0):
    validate_demand(gross)
    scheduled = {} if scheduled is None else dict(scheduled)
    if not isinstance(earliest_receipt, int) or earliest_receipt < 0:
        raise ValueError('earliest_receipt must be a nonnegative integer')
    for t, q in scheduled.items():
        if not isinstance(t, int) or t < 0 or not math.isfinite(q) or q < 0:
            raise ValueError('Invalid scheduled receipt')
    opening = item.initial_stock if opening is None else opening
    if any(not math.isfinite(x) or x < 0 for x in (opening, backlog)):
        raise ValueError('Opening stock/backlog must be nonnegative and finite')
    available = opening - backlog
    rows, releases = [], {}
    for t, demand in enumerate(gross):
        receipt = scheduled.get(t, 0)
        net = max(0.0, demand + item.safety_stock - available - receipt)
        planned = lot_size(net, item) if t >= earliest_receipt else 0.0
        available += receipt + planned - demand
        release = t - item.lead_time if planned else None
        if release is not None:
            releases[release] = releases.get(release, 0) + planned
        rows.append(dict(period=t, gross_requirements=demand, scheduled_receipts=receipt,
                         net_requirements=net, planned_receipts=planned,
                         projected_available=available, release_period=release,
                         past_due_release=release is not None and release < 0))
    return [PlanRow(**row, planned_releases=releases.get(row['period'], 0)) for row in rows]


class MRPPolicy:
    """Replan with a feasible receipt fence: new orders cannot arrive before lead time."""
    name = 'mrp'

    def decide(self, item, history, on_hand, backlog, scheduled, horizon, window):
        recent = history[-window:]
        forecast = sum(recent) / len(recent) if recent else 0.0
        rows = plan(item, [forecast] * horizon, scheduled, on_hand, backlog,
                    earliest_receipt=item.lead_time)
        # Shortfalls carry forward to the first feasible receipt and are netted against
        # ALL intervening committed receipts; do not repeatedly order overdue shortages.
        due = sum(r.planned_receipts for r in rows if r.release_period is not None and r.release_period <= 0)
        return due, [asdict(r) for r in rows]
