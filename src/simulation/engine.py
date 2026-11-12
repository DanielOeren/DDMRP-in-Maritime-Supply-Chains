"""Receive, plan/release, then fulfil demand. All shortages are backordered."""
from dataclasses import dataclass
import hashlib
import math
import random


@dataclass(frozen=True)
class Config:
    train_periods: int = 90
    forecast_window: int = 60
    planning_horizon: int = 60
    lead_time_cv: float = 0.0
    seed: int = 42

    def __post_init__(self):
        for value in (self.train_periods, self.forecast_window, self.planning_horizon):
            if not isinstance(value, int) or value < 1:
                raise ValueError('Train/window/horizon must be positive integers')
        if not math.isfinite(self.lead_time_cv) or self.lead_time_cv < 0:
            raise ValueError('lead_time_cv must be finite and nonnegative')


def realized_lead_time(item, period, config):
    """Policy-independent SKU/calendar keyed shocks (same-day orders share shock)."""
    if config.lead_time_cv == 0:
        return item.lead_time
    digest = hashlib.sha256(f'{config.seed}:{item.sku}:{period}'.encode()).digest()
    rng = random.Random(int.from_bytes(digest, 'big'))
    sigma = math.sqrt(math.log1p(config.lead_time_cv ** 2))
    return max(1, math.ceil(item.lead_time * rng.lognormvariate(-sigma * sigma / 2, sigma)))


def simulate(dataset, policy, config):
    dataset.validate()
    if config.train_periods >= len(dataset.periods):
        raise ValueError('Training must leave at least one evaluation period')
    ledger, orders, decisions = [], [], []
    for sku, item in sorted(dataset.items.items()):
        history = list(dataset.demand[sku][:config.train_periods])
        on_hand, backlog, pipeline = item.initial_stock, 0.0, []
        for t in range(config.train_periods, len(dataset.periods)):
            opening, old_backlog = on_hand, backlog
            arrivals = [o for o in pipeline if o['actual_due'] == t]
            receipt = sum(o['quantity'] for o in arrivals)
            pipeline = [o for o in pipeline if o['actual_due'] != t]
            on_hand += receipt
            fulfilled_backlog = min(on_hand, backlog)
            on_hand -= fulfilled_backlog
            backlog -= fulfilled_backlog
            scheduled = {}
            for order in pipeline:
                # Only nominal promises are visible; late outstanding orders assumed next period.
                offset = max(1, order['nominal_due'] - t)
                scheduled[offset] = scheduled.get(offset, 0) + order['quantity']
            horizon = max(config.planning_horizon, item.lead_time + 1)
            quantity, audit = policy.decide(item, tuple(history), on_hand, backlog, dict(scheduled), horizon, config.forecast_window)
            if not math.isfinite(quantity) or quantity < 0:
                raise ValueError('Policy returned invalid order quantity')
            decisions.append({'sku': sku, 'period': t, 'date': dataset.periods[t],
                              'forecast_per_period': sum(history[-config.forecast_window:]) / len(history[-config.forecast_window:]),
                              'release_qty': quantity, 'planning_horizon': horizon,
                              'past_due_planned_qty': sum(r['planned_receipts'] for r in audit if r.get('past_due_release'))})
            if quantity > 0:
                lead = realized_lead_time(item, t, config)
                order = {'order_id': f'{sku}:{t}', 'sku': sku, 'release_period': t,
                         'nominal_due': t + item.lead_time, 'actual_due': t + lead,
                         'quantity': quantity, 'actual_lead_time': lead, 'emergency': False,
                         'received_within_horizon': t + lead < len(dataset.periods)}
                pipeline.append(order)
                orders.append(order)
            demand = dataset.demand[sku][t]
            immediate = min(on_hand, demand)
            on_hand -= immediate
            unmet = demand - immediate
            backlog += unmet
            ledger.append({'sku': sku, 'period': t, 'date': dataset.periods[t],
                           'opening_stock': opening, 'receipts': receipt,
                           'opening_backlog': old_backlog, 'backlog_fulfilled': fulfilled_backlog,
                           'demand': demand, 'immediate_fulfilled': immediate, 'unmet_demand': unmet,
                           'closing_stock': on_hand, 'closing_backlog': backlog,
                           'inventory_value': on_hand * item.unit_cost,
                           'pipeline_qty': sum(o['quantity'] for o in pipeline), 'released_qty': quantity})
            history.append(demand)
    return ledger, orders, decisions
