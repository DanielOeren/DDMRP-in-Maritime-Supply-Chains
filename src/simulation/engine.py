"""Shared event order, physical inventory and exogenous supply scenarios."""
from dataclasses import dataclass
import hashlib
import math
import random
from typing import Mapping, Protocol

from src.data.schema import Item


@dataclass(frozen=True)
class Config:
    train_periods: int = 90
    forecast_window: int = 60
    planning_horizon: int = 60
    lead_time_cv: float = 0.0
    seed: int = 42
    warmup_periods: int = 0

    def __post_init__(self):
        for value in (self.train_periods, self.forecast_window, self.planning_horizon):
            if type(value) is not int or value < 1:
                raise ValueError('Train/window/horizon must be positive integers')
        if type(self.warmup_periods) is not int or self.warmup_periods < 0:
            raise ValueError('warmup_periods must be a nonnegative integer')
        if not math.isfinite(self.lead_time_cv) or self.lead_time_cv < 0:
            raise ValueError('lead_time_cv must be finite and nonnegative')
        if type(self.seed) is not int:
            raise ValueError('seed must be an integer')

    @property
    def evaluation_start(self):
        return self.train_periods + self.warmup_periods

    def split(self, period):
        if period < self.train_periods:
            return 'train'
        return 'warmup' if period < self.evaluation_start else 'evaluation'


@dataclass(frozen=True)
class Review:
    period: int
    history: tuple  # Last window of actual demand, up to and excluding period.
    on_hand: float
    backlog: float
    scheduled: Mapping[int, float]  # Nominal receipts by offset from period.
    horizon: int
    window: int


@dataclass(frozen=True)
class Decision:
    quantity: float
    forecast: float | None = None


class OrderingPolicy(Protocol):
    def release(self, item: Item, state: Review) -> Decision: ...


def realized_lead_time(item, period, config):
    """Positive whole periods with nominal mean; shared SKU/release-period draws."""
    lead = item.lead_time
    if config.lead_time_cv == 0 or lead == 1:
        return lead
    digest = hashlib.sha256(f'{config.seed}:{item.sku}:{period}'.encode()).digest()
    rng = random.Random(int.from_bytes(digest, 'big'))
    # X = 1 + (L-1) Z. Choose Var(Z) to target CV(X) before integer rounding.
    sigma_sq = math.log1p((config.lead_time_cv * lead / (lead - 1)) ** 2)
    continuous = 1 + (lead - 1) * rng.lognormvariate(-sigma_sq / 2, math.sqrt(sigma_sq))
    lower = math.floor(continuous)
    return lower + int(rng.random() < continuous - lower)


def _fulfil(stock, requested):
    """Return served, remaining stock and unmet units; avoid roundoff-only stockouts."""
    served = min(stock, requested)
    residual = stock - requested
    if served > 0 and 0 < abs(residual) <= min(4 * math.ulp(max(stock, requested)), 1e-9):
        return requested, 0.0, 0.0
    return served, stock - served, requested - served


def simulate(dataset, policy: OrderingPolicy, config):
    dataset.validate()
    if config.evaluation_start >= len(dataset.periods):
        raise ValueError('Training and warmup must leave at least one evaluation period')
    release = getattr(policy, 'release', None)
    ledger, orders, decisions = [], [], []
    for sku, item in sorted(dataset.items.items()):
        series = dataset.demand[sku]
        on_hand, backlog, pipeline = item.initial_stock, 0.0, []
        horizon = max(config.planning_horizon, item.lead_time + 1)
        for t in range(config.train_periods, len(series)):
            opening, old_backlog = on_hand, backlog
            opening_pipeline = sum(o['quantity'] for o in pipeline)
            receipt = sum(o['quantity'] for o in pipeline if o['actual_due'] == t)
            pipeline = [o for o in pipeline if o['actual_due'] != t]
            fulfilled_backlog, on_hand, backlog = _fulfil(on_hand + receipt, backlog)

            scheduled = {}
            for order in pipeline:
                offset = max(1, order['nominal_due'] - t)
                scheduled[offset] = scheduled.get(offset, 0) + order['quantity']
            if release is None:
                # Original decision API received all observed history and returned (qty, audit).
                qty, _ = policy.decide(item, tuple(series[:t]), on_hand, backlog, scheduled,
                                       horizon, config.forecast_window)
                decision = Decision(qty)
            else:
                history = tuple(series[max(0, t - config.forecast_window):t])
                state = Review(t, history, on_hand, backlog, scheduled, horizon, config.forecast_window)
                decision = release(item, state)
            quantity = decision.quantity
            if not math.isfinite(quantity) or quantity < 0:
                raise ValueError('Policy returned invalid order quantity')
            if quantity:
                lots = quantity / item.lot_increment
                below_minimum = item.minimum_order - quantity > min(4 * math.ulp(item.minimum_order), 1e-9)
                fractional_lot = abs(lots - round(lots)) > min(4 * math.ulp(lots), 1e-9)
                if below_minimum or round(lots) == 0 or fractional_lot:
                    raise ValueError(f'Policy release violates lot constraints for {sku}')
            if decision.forecast is not None and (
                not math.isfinite(decision.forecast) or decision.forecast < 0
            ):
                raise ValueError('Policy returned invalid forecast')
            key = {'sku': sku, 'period': t, 'date': dataset.periods[t], 'split': config.split(t)}
            decisions.append({**key, 'forecast_per_period': decision.forecast,
                              'release_qty': quantity, 'planning_horizon': horizon})
            if quantity:
                lead = realized_lead_time(item, t, config)
                order = {'order_id': f'{sku}:{t}', 'sku': sku, 'release_period': t,
                         'nominal_due': t + item.lead_time, 'actual_due': t + lead,
                         'quantity': quantity, 'actual_lead_time': lead, 'emergency': False,
                         'received_within_horizon': t + lead < len(series)}
                pipeline.append(order)
                orders.append(order)

            demand = series[t]
            immediate, on_hand, unmet = _fulfil(on_hand, demand)
            backlog += unmet
            ledger.append({**key, 'opening_stock': opening, 'receipts': receipt,
                           'opening_backlog': old_backlog, 'backlog_fulfilled': fulfilled_backlog,
                           'demand': demand, 'immediate_fulfilled': immediate, 'unmet_demand': unmet,
                           'closing_stock': on_hand, 'closing_backlog': backlog,
                           'inventory_value': on_hand * item.unit_cost,
                           'opening_pipeline': opening_pipeline,
                           'pipeline_qty': sum(o['quantity'] for o in pipeline), 'released_qty': quantity})
    return ledger, orders, decisions
