"""Time-phased MRP for independently replenished items."""
from dataclasses import asdict, dataclass, replace
import math

from src.data.schema import validate_demand
from src.simulation.engine import Decision


def lot_size(net, item):
    if not math.isfinite(net):
        raise ValueError('Net requirement must be finite')
    if net <= 0:
        return 0.0
    increment = item.lot_increment
    lots = max(net, item.minimum_order) / increment
    nearest = round(lots)
    # Allow roundoff at positive integral boundaries, including decimal package sizes.
    if nearest > 0 and abs(lots - nearest) <= min(4 * math.ulp(lots), 1e-9):
        return nearest * increment
    return math.ceil(lots) * increment


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


def _running_totals(values):
    """Compensated cumulative sums for repeated fractional quantities."""
    total = correction = 0.0
    for value in values:
        adjusted = value - correction
        updated = total + adjusted
        correction = (updated - total) - adjusted
        total = updated
        yield total


def _net(gross, safety, backlog, opening, scheduled, planned=0.0):
    terms = (gross, safety, backlog, -opening, -scheduled, -planned)
    shortage = math.fsum(terms)
    # At a cancellation boundary, the input totals themselves are rounded floats.
    return shortage if shortage > min(math.ulp(max(map(abs, terms))), 1e-9) else 0.0


def plan(item, gross, scheduled=None, opening=None, backlog=0, earliest_receipt=0):
    validate_demand(gross)
    scheduled = {} if scheduled is None else dict(scheduled)
    if type(earliest_receipt) is not int or earliest_receipt < 0:
        raise ValueError('earliest_receipt must be a nonnegative integer')
    for t, q in scheduled.items():
        if type(t) is not int or t < 0 or not math.isfinite(q) or q < 0:
            raise ValueError('Invalid scheduled receipt')
    opening = item.initial_stock if opening is None else opening
    if any(not math.isfinite(x) or x < 0 for x in (opening, backlog)):
        raise ValueError('Opening stock/backlog must be nonnegative and finite')

    # Cumulative requirements allow a single balance calculation even before the fence.
    # Compensated running sums prevent repeated fractional forecasts drifting across a lot.
    gross_totals = _running_totals(gross)
    receipt_totals = _running_totals(scheduled.get(t, 0) for t in range(len(gross)))
    planned_lots = 0
    rows = []
    for t, (gross_total, committed) in enumerate(zip(gross_totals, receipt_totals)):
        demand = gross[t]
        receipt = scheduled.get(t, 0)
        net = _net(gross_total, item.safety_stock, backlog, opening, committed, planned_lots * item.lot_increment)
        planned = lot_size(net, item) if t >= earliest_receipt else 0.0
        planned_lots += round(planned / item.lot_increment)
        available = math.fsum((opening, -backlog, committed, planned_lots * item.lot_increment, -gross_total))
        release = t - item.lead_time if planned else None
        rows.append(PlanRow(t, demand, receipt, net, planned, 0.0, available,
                            release, release is not None and release < 0))
    for t in range(len(rows) - item.lead_time):
        rows[t] = replace(rows[t], planned_releases=rows[t + item.lead_time].planned_receipts)
    return rows


def trailing_mean(history, window):
    recent = history[-window:]
    return math.fsum(recent) / len(recent) if recent else 0.0


class MRPPolicy:
    """Only the receipt at offset L can authorize a release now."""
    name = 'mrp'

    def release(self, item, state):
        forecast = trailing_mean(state.history, state.window)
        committed = math.fsum(q for offset, q in state.scheduled.items()
                              if offset <= item.lead_time)
        net = _net((item.lead_time + 1) * forecast, item.safety_stock, state.backlog,
                   state.on_hand, committed)
        return Decision(lot_size(net, item), forecast)

    def decide(self, item, history, on_hand, backlog, scheduled, horizon, window):
        """Detailed plan at a single origin for inspection and existing callers."""
        forecast = trailing_mean(history, window)
        rows = plan(item, [forecast] * horizon, scheduled, on_hand, backlog,
                    earliest_receipt=item.lead_time)
        return rows[0].planned_releases, [asdict(r) for r in rows]
