"""Run a chronological MRP experiment and publish a new self-contained audit directory."""
import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory

from src.data.adapters import fingerprints, oil041, spdf_oil
from src.evaluation.metrics import item_summaries, summarize
from src.evaluation.validation import check_balances
from src.mrp.planning import MRPPolicy
from src.simulation.engine import Config, simulate


ORDER_FIELDS = ('order_id', 'sku', 'release_period', 'nominal_due', 'actual_due', 'quantity',
                'actual_lead_time', 'emergency', 'received_within_horizon')


def write_csv(path, rows, fields=None):
    rows = iter(rows)
    with path.open('w', encoding='utf-8', newline='') as stream:
        first = next(rows, None)
        writer = csv.DictWriter(stream, fieldnames=fields if fields else list(first))
        writer.writeheader()
        if first is not None:
            writer.writerow(first)
        writer.writerows(rows)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def demand_rows(data, cfg):
    for sku, series in data.demand.items():
        for t, qty in enumerate(series):
            row = {'sku': sku, 'period': t, 'date': data.periods[t], 'demand': qty,
                   'split': cfg.split(t)}
            if data.metadata['source'] == 'spdf_oil':
                status, observed = data.demand_adjustments.get((sku, t), ('observed', qty))
                row.update(source_net_consumption=observed, treatment=status)
            yield row


def first_plans(data, policy, cfg):
    for sku, item in sorted(data.items.items()):
        horizon = max(cfg.planning_horizon, item.lead_time + 1)
        _, rows = policy.decide(item, data.demand[sku][:cfg.train_periods],
                                item.initial_stock, 0, {}, horizon, cfg.forecast_window)
        for row in rows:
            yield {'sku': sku, **row}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['oil041', 'spdf-oil'], default='oil041')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-items', type=int, default=50)
    parser.add_argument('--selection-seed', type=int, help='Sample master IDs uniformly without replacement')
    parser.add_argument('--offshore-only', action='store_true')
    parser.add_argument('--source-stock', action='store_true')
    parser.add_argument('--returns', choices=['reject', 'clip'], default='reject')
    parser.add_argument('--train-periods', type=int)
    parser.add_argument('--warmup-periods', type=int, default=0, help='Unscored reviews after training')
    parser.add_argument('--forecast-window', type=int)
    parser.add_argument('--planning-horizon', type=int, default=60)
    parser.add_argument('--lead-time-cv', type=float, default=0.0)
    parser.add_argument('--seed', type=int, default=42, help='Supply scenario seed')
    parser.add_argument('--safety-stock', type=float, help='Override all SKU safety stocks, in units')
    parser.add_argument('--minimum-order', type=float, default=0)
    parser.add_argument('--order-multiple', type=float, default=1)
    parser.add_argument('--fixed-order', type=float, default=0)
    args = parser.parse_args(argv)
    is_daily = args.source == 'oil041'
    if not is_daily and args.offshore_only:
        parser.error('SPDF has no verified offshore facility mapping')
    if is_daily and args.returns != 'reject':
        parser.error('--returns clip applies to SPDF net consumption')
    root = args.input or Path('data/temp_downloads') / ('oil041' if is_daily else 'spdf-main')
    output = args.output or Path('results') / ('mrp_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    if output.exists():
        raise FileExistsError(output)
    if is_daily:
        data = oil041(root, args.max_items, args.offshore_only, args.source_stock, args.selection_seed)
    else:
        data = spdf_oil(root, args.max_items, args.source_stock, args.returns, args.selection_seed)
    data.items = {
        sku: replace(item, safety_stock=item.safety_stock if args.safety_stock is None else args.safety_stock,
                     minimum_order=args.minimum_order, order_multiple=args.order_multiple, fixed_order=args.fixed_order)
        for sku, item in data.items.items()
    }
    cfg = Config(
        train_periods=args.train_periods if args.train_periods is not None else (90 if is_daily else 24),
        forecast_window=args.forecast_window if args.forecast_window is not None else (60 if is_daily else 12),
        planning_horizon=args.planning_horizon, lead_time_cv=args.lead_time_cv, seed=args.seed,
        warmup_periods=args.warmup_periods)
    policy = MRPPolicy()
    ledger, orders, decisions = simulate(data, policy, cfg)
    residuals = check_balances(ledger, orders)
    metrics = summarize(ledger, orders)
    src = Path(__file__).resolve().parents[1]
    manifest = {
        'format_version': 2, 'policy': policy.name, 'config': asdict(cfg), 'source': data.metadata,
        'evaluation_start': data.periods[cfg.evaluation_start], 'evaluation_end': data.periods[-1],
        'python': platform.python_version(), 'xlrd': None if is_daily else version('xlrd'),
        'implementation_sha256': fingerprints(sorted(src.rglob('*.py')), src.parent),
        'arguments': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        'max_absolute_balance_residuals': residuals,
        'assumptions': [
            'Consumption is the demand proxy. One pooled stockpoint per SKU. Full backordering.',
            'No initial backlog or pipeline. Training supplies history. Warmup carries all state into evaluation.',
            'Receipts serve backlog first. Decisions use past demand and nominal outstanding due dates.',
            'Late outstanding orders are provisionally due next period. Regular replenishment only.',
            'Constant trailing mean forecast through the nominal lead time plus the review bucket.',
            'Positive integer supply delays with nominal mean. CV targets pre-round variability for L > 1.',
            'Average value uses end-period on-hand stock. Terminal orders and unmet backlog remain visible.',
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp) / 'run'
        stage.mkdir()
        write_csv(stage / 'items.csv', ({**asdict(item), **data.item_attributes[sku]}
                                        for sku, item in data.items.items()))
        write_csv(stage / 'demand.csv', demand_rows(data, cfg))
        write_csv(stage / 'initial_plan.csv', first_plans(data, policy, cfg))
        write_csv(stage / 'inventory.csv', ledger)
        write_csv(stage / 'decisions.csv', decisions)
        write_csv(stage / 'orders.csv', orders, ORDER_FIELDS)
        write_csv(stage / 'item_metrics.csv', item_summaries(ledger, orders))
        write_json(stage / 'metrics.json', metrics)
        write_json(stage / 'run.json', manifest)
        stage.rename(output)
    print(json.dumps({'output': str(output), **metrics}, indent=2))
    return output


if __name__ == '__main__':
    main()
