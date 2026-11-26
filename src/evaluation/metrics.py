"""Service at request time, physical stock and committed orders over the scored interval."""
from collections import defaultdict
from statistics import fmean


def summarize(ledger, orders):
    rows = [r for r in ledger if r.get('split', 'evaluation') == 'evaluation']
    if not rows:
        raise ValueError('Empty evaluation ledger')
    periods = {r['period'] for r in rows}
    first, last = min(periods), max(periods)
    opening = [r for r in rows if r['period'] == first]
    closing = [r for r in rows if r['period'] == last]
    demand_by_sku = defaultdict(float)
    filled_by_sku = defaultdict(float)
    for r in rows:
        demand_by_sku[r['sku']] += r['demand']
        filled_by_sku[r['sku']] += r['immediate_fulfilled']
    demand = sum(demand_by_sku.values())
    item_rates = [filled_by_sku[sku] / qty for sku, qty in demand_by_sku.items() if qty > 0]
    positive = sum(r['demand'] > 0 for r in rows)
    stockouts = sum(r['unmet_demand'] > 0 for r in rows)
    released = [o for o in orders if o['release_period'] in periods and o['sku'] in demand_by_sku]
    return {
        'total_demand': demand,
        'unit_fill_rate': sum(filled_by_sku.values()) / demand if demand else None,
        'mean_item_fill_rate': fmean(item_rates) if item_rates else None,
        'items_with_demand': len(item_rates),
        'positive_demand_item_periods': positive,
        'demand_period_service_rate': (positive - stockouts) / positive if positive else None,
        'stockout_item_periods': stockouts,
        'backlog_item_periods': sum(r['closing_backlog'] > 0 for r in rows),
        'unmet_units_at_request': sum(r['unmet_demand'] for r in rows),
        'opening_stock': sum(r['opening_stock'] for r in opening),
        'opening_backlog': sum(r['opening_backlog'] for r in opening),
        'opening_pipeline_units': sum(r.get('opening_pipeline', 0) for r in opening),
        'ending_stock': sum(r['closing_stock'] for r in closing),
        'ending_backlog': sum(r['closing_backlog'] for r in closing),
        'ending_pipeline_units': sum(r['pipeline_qty'] for r in closing),
        'average_inventory_units': sum(r['closing_stock'] for r in rows) / len(periods),
        'average_inventory_value': sum(r['inventory_value'] for r in rows) / len(periods),
        'regular_orders': sum(not o['emergency'] for o in released),
        'emergency_orders': sum(o['emergency'] for o in released),
        'order_units': sum(o['quantity'] for o in released),
        'evaluation_periods': len(periods),
        'items': len(demand_by_sku),
    }


def item_summaries(ledger, orders):
    by_item, orders_by_item = defaultdict(list), defaultdict(list)
    for row in ledger:
        by_item[row['sku']].append(row)
    for order in orders:
        orders_by_item[order['sku']].append(order)
    return [{'sku': sku, **summarize(rows, orders_by_item[sku])}
            for sku, rows in by_item.items()]
