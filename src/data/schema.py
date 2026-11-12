from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Item:
    sku: str
    unit_cost: float
    lead_time: int  # whole simulation periods, minimum one
    initial_stock: float = 0
    safety_stock: float = 0
    minimum_order: float = 0
    order_multiple: float = 1
    fixed_order: float = 0  # zero = lot-for-lot; otherwise multiples of fixed lot

    def __post_init__(self):
        if not self.sku or isinstance(self.lead_time, bool) or not isinstance(self.lead_time, int) or self.lead_time < 1:
            raise ValueError('SKU and positive integer lead_time required')
        for name in ('unit_cost', 'initial_stock', 'safety_stock', 'minimum_order', 'order_multiple', 'fixed_order'):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.order_multiple <= 0:
            raise ValueError('order_multiple must be positive')


def validate_demand(demand):
    if not demand or any(not math.isfinite(x) or x < 0 for x in demand):
        raise ValueError('Demand must be nonempty, finite and nonnegative')


@dataclass
class Dataset:
    items: dict
    demand: dict
    periods: list
    metadata: dict

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
