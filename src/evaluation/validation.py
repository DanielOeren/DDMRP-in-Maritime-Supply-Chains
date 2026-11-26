"""Reconcile the ledger against committed orders, including the warmup and terminal pipeline."""
from collections import defaultdict
import math


def check_balances(ledger, orders):
    if not ledger:
        raise ValueError('Empty inventory ledger')
    last_period = max(row['period'] for row in ledger)
    released, due = defaultdict(float), defaultdict(float)
    for order in orders:
        start, end = order['release_period'], order['actual_due']
        if end <= start or end - start != order['actual_lead_time']:
            raise ValueError(f"Invalid transit time for {order['order_id']}")
        key = order['sku'], start
        if key in released or not math.isfinite(order['quantity']) or order['quantity'] <= 0:
            raise ValueError(f"Invalid or duplicate release for {key}")
        if order['received_within_horizon'] != (end <= last_period):
            raise ValueError(f"Incorrect terminal receipt flag for {key}")
        released[key] = order['quantity']
        due[order['sku'], end] += order['quantity']

    prior = {}
    maxima = defaultdict(float)
    for row in ledger:
        sku, t = row['sku'], row['period']
        stock, backlog, pipeline = row['closing_stock'], row['closing_backlog'], row['pipeline_qty']
        if min(stock, backlog, pipeline) < 0 or min(stock, backlog) > 0:
            raise ValueError(f'Invalid physical state for {sku} at {t}')
        available = row['opening_stock'] + row['receipts']
        backlog_first = min(available, row['opening_backlog'])
        expected = {
            'receipts': due.get((sku, t), 0),
            'released_qty': released.pop((sku, t), 0),
            'backlog_fulfilled': backlog_first,
            'immediate_fulfilled': min(row['demand'], available - backlog_first),
            'demand': row['immediate_fulfilled'] + row['unmet_demand'],
            'closing_stock': row['opening_stock'] + row['receipts']
                             - row['backlog_fulfilled'] - row['immediate_fulfilled'],
            'closing_backlog': row['opening_backlog'] - row['backlog_fulfilled'] + row['unmet_demand'],
            'pipeline_qty': row['opening_pipeline'] + row['released_qty'] - row['receipts'],
        }
        if sku in prior:
            previous = prior[sku]
            if previous['period'] + 1 != t:
                raise ValueError(f'Nonconsecutive ledger for {sku} at {t}')
            expected.update(opening_stock=previous['closing_stock'], opening_backlog=previous['closing_backlog'],
                            opening_pipeline=previous['pipeline_qty'])
        else:
            expected.update(opening_backlog=0, opening_pipeline=0)
        for field, value in expected.items():
            observed = row[field]
            maxima[field] = max(maxima[field], abs(observed - value))
            if not math.isclose(observed, value, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError(f'{sku} at {t}: {field} = {observed}, expected {value}')
        prior[sku] = row
    if released:
        raise ValueError('Order releases outside the inventory ledger')
    return dict(maxima)
