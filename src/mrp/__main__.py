import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from src.data.adapters import oil041, spdf_oil, fingerprints
from src.mrp.planning import MRPPolicy, plan
from src.simulation.engine import Config, simulate
from src.evaluation.metrics import summarize


def write_csv(path, rows, fields=None):
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description='MRP baseline: immutable source -> canonical data -> plan -> simulation')
    parser.add_argument('--source', choices=['oil041', 'spdf-oil'], default='oil041')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-items', type=int, default=50)
    parser.add_argument('--offshore-only', action='store_true')
    parser.add_argument('--source-stock', action='store_true')
    parser.add_argument('--returns', choices=['reject', 'clip'], default='reject')
    parser.add_argument('--train-periods', type=int)
    parser.add_argument('--forecast-window', type=int)
    parser.add_argument('--planning-horizon', type=int, default=60)
    parser.add_argument('--lead-time-cv', type=float, default=0.0)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--safety-stock', type=float, help='Override all SKU safety stocks, in units')
    parser.add_argument('--minimum-order', type=float, default=0)
    parser.add_argument('--order-multiple', type=float, default=1)
    parser.add_argument('--fixed-order', type=float, default=0)
    args = parser.parse_args(argv)
    if args.max_items < 1:
        parser.error('--max-items must be positive')
    if args.source == 'spdf-oil' and args.offshore_only:
        parser.error('SPDF has no verified offshore facility mapping')
    if args.source == 'oil041':
        data = oil041(args.input or Path('data/temp_downloads/oil041'), args.max_items, args.offshore_only, args.source_stock)
    else:
        data = spdf_oil(args.input or Path('data/temp_downloads/spdf-main'), args.max_items, args.source_stock, args.returns)
    data.items = {sku: replace(item, safety_stock=item.safety_stock if args.safety_stock is None else args.safety_stock,
                              minimum_order=args.minimum_order, order_multiple=args.order_multiple,
                              fixed_order=args.fixed_order) for sku, item in data.items.items()}
    cfg = Config(args.train_periods if args.train_periods is not None else (90 if args.source == 'oil041' else 24),
                 args.forecast_window if args.forecast_window is not None else (60 if args.source == 'oil041' else 12),
                 args.planning_horizon, args.lead_time_cv, args.seed)
    ledger, orders, decisions = simulate(data, MRPPolicy(), cfg)
    initial_plan = []
    for sku, item in sorted(data.items.items()):
        history = data.demand[sku][:cfg.train_periods][-cfg.forecast_window:]
        gross = [sum(history) / len(history)] * max(cfg.planning_horizon, item.lead_time + 1)
        initial_plan.extend({'sku': sku, **asdict(row)} for row in plan(item, gross))
    # New directories only: never silently overwrite a prior experiment.
    output = args.output or Path('results') / ('mrp_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / 'items.csv', [asdict(item) for item in data.items.values()])
    write_csv(output / 'demand.csv', [{'sku': sku, 'period': t, 'date': date, 'demand': data.demand[sku][t],
                                     'split': 'train' if t < cfg.train_periods else 'evaluation'}
                                    for sku in data.items for t, date in enumerate(data.periods)])
    write_csv(output / 'initial_plan.csv', initial_plan)
    write_csv(output / 'inventory.csv', ledger)
    write_csv(output / 'decisions.csv', decisions)
    write_csv(output / 'orders.csv', orders, ['order_id', 'sku', 'release_period', 'nominal_due', 'actual_due', 'quantity', 'actual_lead_time', 'emergency', 'received_within_horizon'])
    metrics = summarize(ledger, orders)
    (output / 'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (output / 'run.json').write_text(json.dumps({'policy': 'mrp', 'config': asdict(cfg), 'source': data.metadata,
        'implementation_sha256': fingerprints(sorted(Path(__file__).resolve().parents[1].rglob('*.py'))),
        'arguments': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        'assumptions': ['Backorders, FIFO aggregate backlog first; no lost sales.',
            'Receive then decide without current demand, then fulfil current demand.',
            'Trailing mean forecast; training/evaluation separated chronologically.',
            'No emergency fulfilment; late planned releases use regular lead time.',
            'Nominal outstanding due dates visible; actual future arrival shocks hidden.',
            'No warmup excluded: cold-start effects included; terminal pipeline/backlog reported.',
            'End-period on-hand valuation; no holding-cost rate or unverified currency conversion.']}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), **metrics}, indent=2))
    return output


if __name__ == '__main__':
    main()
