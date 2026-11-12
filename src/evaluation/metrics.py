def summarize(ledger, orders):
    if not ledger:
        raise ValueError('Empty evaluation ledger')
    periods = {r['period'] for r in ledger}
    total_demand = sum(r['demand'] for r in ledger)
    positive = [r for r in ledger if r['demand'] > 0]
    last = max(periods)
    return {
        'total_demand': total_demand,
        'unit_fill_rate': sum(r['immediate_fulfilled'] for r in ledger) / total_demand if total_demand else None,
        'demand_period_service_rate': sum(r['unmet_demand'] == 0 for r in positive) / len(positive) if positive else None,
        'stockout_item_periods': sum(r['unmet_demand'] > 0 for r in ledger),
        'backlog_item_periods': sum(r['closing_backlog'] > 0 for r in ledger),
        'unmet_units_at_request': sum(r['unmet_demand'] for r in ledger),
        'ending_backlog': sum(r['closing_backlog'] for r in ledger if r['period'] == last),
        'average_inventory_units': sum(r['closing_stock'] for r in ledger) / len(periods),
        'average_inventory_value': sum(r['inventory_value'] for r in ledger) / len(periods),
        'ending_pipeline_units': sum(r['pipeline_qty'] for r in ledger if r['period'] == last),
        'regular_orders': sum(not o['emergency'] for o in orders),
        'emergency_orders': sum(o['emergency'] for o in orders),
        'order_units': sum(o['quantity'] for o in orders),
        'evaluation_periods': len(periods),
        'items': len({r['sku'] for r in ledger}),
    }
