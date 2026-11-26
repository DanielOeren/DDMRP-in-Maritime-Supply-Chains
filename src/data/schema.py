"""Canonical, independent-demand inputs in units of the dataset's planning period."""
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cached_property
import math


@dataclass(frozen=True)
class Item:
    sku: str
    unit_cost: float
    lead_time: int
    initial_stock: float = 0
    safety_stock: float = 0
    minimum_order: float = 0
    order_multiple: float = 1
    fixed_order: float = 0

    def __post_init__(self):
        if not isinstance(self.sku, str) or not self.sku.strip():
            raise ValueError('A nonempty SKU is required')
        if type(self.lead_time) is not int or self.lead_time < 1:
            raise ValueError('lead_time must be a positive integer')
        for name in ('unit_cost', 'initial_stock', 'safety_stock', 'minimum_order',
                     'order_multiple', 'fixed_order'):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.order_multiple == 0:
            raise ValueError('order_multiple must be positive')

    @cached_property
    def lot_increment(self):
        """Smallest quantity satisfying both decimal lot constraints."""
        if not self.fixed_order:
            return self.order_multiple
        pack = Fraction(str(self.order_multiple))
        lot = Fraction(str(self.fixed_order))
        return float(Fraction(math.lcm(pack.numerator, lot.numerator),
                              math.gcd(pack.denominator, lot.denominator)))


def validate_demand(demand):
    if not demand or any(not math.isfinite(x) or x < 0 for x in demand):
        raise ValueError('Demand must be nonempty, finite and nonnegative')


@dataclass
class Dataset:
    items: dict
    demand: dict
    periods: list
    metadata: dict
    # Descriptive source data is available for analysis outside the decision interface.
    item_attributes: dict = field(default_factory=dict)
    demand_adjustments: dict = field(default_factory=dict)

    def validate(self):
        if not self.items or self.items.keys() != self.demand.keys():
            raise ValueError('Items and demand must have identical nonempty keys')
        if len(set(self.periods)) != len(self.periods) or self.periods != sorted(self.periods):
            raise ValueError('Periods must be unique and chronological')
        for sku, series in self.demand.items():
            if sku != self.items[sku].sku or len(series) != len(self.periods):
                raise ValueError('SKU or period dimension mismatch')
            validate_demand(series)
        return self
