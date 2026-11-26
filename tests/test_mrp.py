import csv
from dataclasses import replace
import json
import random
import statistics
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime

from src.data.schema import Dataset, Item
from src.data.adapters import oil041, select_ids, spdf_oil
from src.mrp.planning import MRPPolicy, lot_size, plan
from src.mrp.__main__ import main
from src.simulation.engine import Config, Decision, Review, realized_lead_time, simulate
from src.evaluation.metrics import item_summaries, summarize
from src.evaluation.validation import check_balances


def dataset(values, item=None):
    item = item or Item('A', 10, 2)
    return Dataset({'A': item}, {'A': values}, [f'{i:03}' for i in range(len(values))], {})


class PlanningTests(unittest.TestCase):
    def test_hand_calculated_netting_and_offset(self):
        item = Item('A', 5, 2, initial_stock=12, safety_stock=2, order_multiple=5)
        rows = plan(item, [4, 8, 10, 3], {1: 5})
        self.assertEqual([r.net_requirements for r in rows], [0, 0, 7, 0])
        self.assertEqual([r.planned_receipts for r in rows], [0, 0, 10, 0])
        self.assertEqual([r.projected_available for r in rows], [8, 5, 5, 2])
        self.assertEqual(rows[0].planned_releases, 10)
        self.assertEqual(rows[2].release_period, 0)

    def test_lot_sizes(self):
        i = Item('A', 1, 1, minimum_order=10, order_multiple=6)
        self.assertEqual(lot_size(1, i), 12)
        self.assertEqual(lot_size(0, i), 0)
        self.assertEqual(lot_size(21, replace(i, fixed_order=20, order_multiple=1)), 40)
        self.assertEqual(lot_size(21, replace(i, fixed_order=20)), 60)
        self.assertAlmostEqual(lot_size(.31, Item('A', 1, 1, fixed_order=.3, order_multiple=.2)), .6)
        self.assertEqual(lot_size(1e-10, Item('A', 1, 1)), 1)

    def test_rolling_release_matches_complete_feasible_plan(self):
        rng = random.Random(19)
        policy = MRPPolicy()
        for _ in range(300):
            lead = rng.randint(1, 40)
            item = Item('A', 1, lead, safety_stock=rng.randrange(5),
                        minimum_order=rng.choice([0, 4]), order_multiple=rng.choice([1, 2, .25]))
            scheduled = {offset: rng.randrange(5) for offset in (1, lead, lead + 1)}
            history = tuple(rng.choices([0, 1, 7, 17], k=rng.randint(1, 60)))
            state = Review(60, history, rng.randrange(12), rng.randrange(12), scheduled, lead + 5, 60)
            release, _ = policy.decide(item, history, state.on_hand, state.backlog, scheduled,
                                       state.horizon, state.window)
            self.assertEqual(policy.release(item, state).quantity, release)

    def test_past_due_explicit(self):
        r = plan(Item('A', 1, 3), [5])[0]
        self.assertTrue(r.past_due_release)
        self.assertEqual(r.release_period, -3)

    def test_outstanding_supply_prevents_duplicate_shortage_orders(self):
        item = Item('A', 1, 3, safety_stock=10)
        qty, rows = MRPPolicy().decide(item, [0, 0], 0, 0, {2: 10}, 5, 2)
        self.assertEqual(qty, 0)
        self.assertEqual(rows[0]['projected_available'], 0)

    def test_feasible_receipt_carries_shortfall(self):
        rows = plan(Item('A', 1, 2), [4, 4, 4], earliest_receipt=2)
        self.assertEqual([r.planned_receipts for r in rows], [0, 0, 12])
        self.assertEqual([r.projected_available for r in rows], [-4, -8, 0])

    def test_decimal_receipts_do_not_generate_an_extra_lot(self):
        item = Item('A', 1, 100, order_multiple=.1)
        scheduled = {t: .1 for t in range(1, 101)}
        state = Review(3, (.1,), .1, 0, scheduled, 101, 1)
        full, _ = MRPPolicy().decide(item, state.history, .1, 0, scheduled, 101, 1)
        self.assertEqual(full, 0)
        self.assertEqual(MRPPolicy().release(item, state).quantity, full)

    def test_backlog_and_safety_stock(self):
        r = plan(Item('A', 1, 1, safety_stock=4), [0], opening=2, backlog=3)[0]
        self.assertEqual(r.net_requirements, 5)
        self.assertEqual(r.projected_available, 4)

    def test_validation(self):
        for kwargs in ({'lead_time': 0}, {'unit_cost': float('nan')}, {'order_multiple': 0}):
            args = dict(sku='A', unit_cost=1, lead_time=1); args.update(kwargs)
            with self.assertRaises(ValueError): Item(**args)
        with self.assertRaises(ValueError): plan(Item('A', 1, 1), [-1])
        with self.assertRaises(ValueError): plan(Item('A', 1, 1), [1], {-1: 5})


class SimulationTests(unittest.TestCase):
    def test_fractional_stock_and_legacy_decision_interface(self):
        histories = []

        def decide(item, history, on_hand, backlog, scheduled, horizon, window):
            histories.append(history)
            return 0, []

        item = Item('A', 1, 1, initial_stock=.3)
        ledger, orders, _ = simulate(dataset([0, .1, .2], item), SimpleNamespace(decide=decide),
                                     Config(1, 1, 1))
        self.assertEqual(histories, [(0,), (0, .1)])
        self.assertEqual(summarize(ledger, orders)['stockout_item_periods'], 0)
        self.assertEqual(ledger[-1]['closing_backlog'], 0)
        check_balances(ledger, orders)

    def test_late_order_keeps_nominal_promise_in_review(self):
        reviews = []

        def release(item, state):
            reviews.append(state)
            return Decision(5 if state.period == 1 else 0)

        with patch('src.simulation.engine.realized_lead_time', return_value=4):
            ledger, _, _ = simulate(dataset([0] * 6), SimpleNamespace(release=release),
                                    Config(1, 1, 3))
        self.assertEqual([r.scheduled for r in reviews], [{}, {1: 5}, {1: 5}, {1: 5}, {}])
        self.assertEqual([r['receipts'] for r in ledger], [0, 0, 0, 0, 5])

    def test_warmup_carries_stock_backlog_and_orders_across_boundary(self):
        data = dataset([2] * 10)
        cold = simulate(data, MRPPolicy(), Config(2, 2, 3))
        warm = simulate(data, MRPPolicy(), Config(2, 2, 3, warmup_periods=2))
        ledger, orders, _ = warm
        self.assertEqual(orders, cold[1])
        self.assertEqual([r['closing_stock'] for r in ledger], [r['closing_stock'] for r in cold[0]])
        metrics = summarize(ledger, orders)
        self.assertEqual(metrics['evaluation_periods'], 6)
        self.assertEqual(metrics['opening_backlog'], 4)
        self.assertEqual(metrics['opening_pipeline_units'], 8)
        self.assertEqual(metrics['unit_fill_rate'], 1)
        self.assertEqual(metrics['regular_orders'], 6)
        self.assertEqual(max(check_balances(ledger, orders).values()), 0)
        with self.assertRaises(ValueError):
            simulate(data, MRPPolicy(), Config(8, 2, 3, warmup_periods=2))

    def test_policy_specific_forecast_and_common_lot_constraints(self):
        class InspectPolicy:
            def release(self, item, state):
                self.state = state
                return Decision(0)

        policy = InspectPolicy()
        _, _, decisions = simulate(dataset([1, 2, 3, 99]), policy, Config(2, 2, 3))
        self.assertIsNone(decisions[-1]['forecast_per_period'])
        self.assertEqual(policy.state.history, (2, 3))
        item = Item('A', 1, 2, order_multiple=5)
        for q in (1, float('nan'), -1, float('inf')):
            with self.subTest(q=q), self.assertRaises(ValueError):
                simulate(dataset([1, 2, 3], item), SimpleNamespace(release=lambda *_: Decision(q)),
                         Config(2, 2, 3))

    def test_inventory_metrics_with_different_volume_and_dormant_skus(self):
        d = Dataset({'A': Item('A', 2, 1, initial_stock=10), 'B': Item('B', 2, 1),
                     'C': Item('C', 3, 1)}, {'A': [0, 10], 'B': [0, 2], 'C': [0, 0]}, ['0', '1'], {})
        ledger, orders, _ = simulate(d, MRPPolicy(), Config(1, 1, 1))
        m = summarize(ledger, orders)
        self.assertEqual(m['unit_fill_rate'], 10 / 12)
        self.assertEqual(m['mean_item_fill_rate'], .5)
        self.assertEqual(m['positive_demand_item_periods'], 2)
        self.assertEqual(m['items_with_demand'], 2)
        self.assertIsNone(item_summaries(ledger, orders)[2]['unit_fill_rate'])
        self.assertEqual(m['ending_backlog'], 2)

    def test_lead_scenario_preserves_nominal_mean_and_one_period_floor(self):
        cfg = Config(2, 2, 3, .5, 42)
        self.assertEqual(realized_lead_time(Item('A', 1, 1), 0, cfg), 1)
        item = Item('A', 1, 2)
        draws = [realized_lead_time(item, t, cfg) for t in range(10000)]
        self.assertGreaterEqual(min(draws), 1)
        self.assertAlmostEqual(statistics.mean(draws), 2, delta=.06)
        self.assertNotEqual(draws[:50], [realized_lead_time(item, t, replace(cfg, seed=2))
                                         for t in range(50)])

    def test_regular_delay_no_magic_receipt(self):
        d = dataset([5] * 8)
        ledger, orders, _ = simulate(d, MRPPolicy(), Config(2, 2, 4))
        self.assertEqual(orders[0]['release_period'], 2)
        self.assertEqual(orders[0]['actual_due'], 4)
        self.assertEqual(ledger[0]['receipts'], 0)
        self.assertEqual(ledger[1]['receipts'], 0)
        self.assertGreater(ledger[2]['receipts'], 0)

    def test_audit_detects_incorrect_arrival_even_if_totals_match(self):
        ledger, orders, _ = simulate(dataset([5] * 8), MRPPolicy(), Config(2, 2, 4))
        changed = [dict(o) for o in orders]
        changed[0]['actual_due'] += 1
        changed[0]['actual_lead_time'] += 1
        with self.assertRaisesRegex(ValueError, 'receipts'):
            check_balances(ledger, changed)

    def test_inventory_and_backlog_conservation(self):
        ledger, orders, _ = simulate(dataset([2, 3, 10, 0, 20, 1, 0, 5, 4, 2]), MRPPolicy(), Config(2, 2, 4, .5))
        for r in ledger:
            self.assertAlmostEqual(r['closing_stock'], r['opening_stock'] + r['receipts'] - r['backlog_fulfilled'] - r['immediate_fulfilled'])
            self.assertAlmostEqual(r['closing_backlog'], r['opening_backlog'] - r['backlog_fulfilled'] + r['unmet_demand'])
            self.assertGreaterEqual(r['closing_stock'], 0)
            self.assertGreaterEqual(r['closing_backlog'], 0)
        self.assertAlmostEqual(sum(o['quantity'] for o in orders), sum(r['receipts'] for r in ledger) + ledger[-1]['pipeline_qty'])

    def test_no_future_demand_leakage(self):
        cfg = Config(2, 2, 4)
        a = simulate(dataset([1, 1, 3, 4, 5, 6]), MRPPolicy(), cfg)
        b = simulate(dataset([1, 1, 3, 4, 500, 600]), MRPPolicy(), cfg)
        self.assertEqual(a[2][:3], b[2][:3])  # includes decision before changed current demand

    def test_forecast_uses_observed_past(self):
        _, _, decisions = simulate(dataset([2, 4, 10, 0]), MRPPolicy(), Config(2, 2, 3))
        self.assertEqual([r['forecast_per_period'] for r in decisions], [3, 7])

    def test_reproducible_shocks(self):
        i = Item('A', 1, 10); c = Config(2, 2, 3, .6, 42)
        self.assertEqual([realized_lead_time(i, t, c) for t in range(10)], [realized_lead_time(i, t, c) for t in range(10)])
        self.assertTrue(all(realized_lead_time(i, t, c) >= 1 for t in range(10)))

    def test_zero_demand_metrics(self):
        ledger, orders, _ = simulate(dataset([0] * 6), MRPPolicy(), Config(2, 2, 4))
        m = summarize(ledger, orders)
        self.assertIsNone(m['unit_fill_rate'])
        self.assertIsNone(m['demand_period_service_rate'])
        self.assertEqual(m['regular_orders'], 0)

    def test_fill_rate_not_eventually_fulfilled_rate(self):
        ledger, orders, _ = simulate(dataset([5] * 8), MRPPolicy(), Config(2, 2, 4))
        m = summarize(ledger, orders)
        self.assertLess(m['unit_fill_rate'], 1)
        self.assertEqual(m['emergency_orders'], 0)

    def test_training_boundaries(self):
        with self.assertRaises(ValueError): simulate(dataset([1, 2]), MRPPolicy(), Config(2, 2, 4))


class AdapterAndCLITests(unittest.TestCase):
    def fixture(self, root):
        tables = {
            'spare_parts_master': [{'part_id': 'A', 'unit_cost_usd': 10, 'standard_lead_time_days': 2.1}],
            'facility_master': [{'facility_id': 'F', 'offshore_flag': 1}, {'facility_id': 'G', 'offshore_flag': 0}],
            'maintenance_consumption': [
                {'part_id': 'A', 'facility_id': 'F', 'event_timestamp': '2021-01-01 01:00:00', 'consumption_qty': 2},
                {'part_id': 'A', 'facility_id': 'F', 'event_timestamp': '2021-01-01 02:00:00', 'consumption_qty': 3},
                {'part_id': 'A', 'facility_id': 'G', 'event_timestamp': '2021-01-02 01:00:00', 'consumption_qty': 7}],
            'inventory_levels': [{'part_id': 'A', 'facility_id': 'F', 'stock_qty': 9, 'reserved_qty': 2, 'safety_stock_qty': 3}],
        }
        for i, row in enumerate(tables['maintenance_consumption']):
            row['work_order_id'] = str(i)
        tables['inventory_levels'][0].update(inventory_id='I', warehouse_id='W')
        for name, rows in tables.items():
            with (root / f'{name}.csv').open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)

    def test_daily_aggregation_and_offshore_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            d = oil041(root, offshore=True, source_stock=True)
            self.assertEqual(d.demand['A'][:3], [5, 0, 0])
            self.assertEqual(d.items['A'].lead_time, 3)
            self.assertEqual(d.items['A'].initial_stock, 7)
            self.assertEqual(len(d.periods), 365)

    def test_selection_uses_stable_master_frame(self):
        ids = list(range(100))
        sample = select_ids(ids, 17, 42)
        self.assertEqual(len(set(sample)), 17)
        self.assertEqual(sample, select_ids(reversed(ids), 17, 42))
        self.assertNotEqual(sample, select_ids(ids, 17, 13))
        self.assertEqual(select_ids(ids, 3, None), [0, 1, 2])

    def test_oil_invalid_records_and_snapshot_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            path = root / 'inventory_levels.csv'
            original = path.read_text()
            path.write_text(original.replace(',9,2,', ',9,12,'))
            d = oil041(root, source_stock=True)
            self.assertEqual(d.items['A'].initial_stock, 0)
            self.assertEqual(d.item_attributes['A']['snapshot_reserved'], 12)
            self.assertEqual(d.metadata['over_reserved_rows_selected'], 1)
            for bad, message in [(original + original.splitlines()[-1] + '\n', 'duplicate'),
                                 (original.replace('A,F,', 'A,X,'), 'Unknown part or facility'),
                                 (original.replace(',9,2,', ',nan,2,'), 'stock_qty')]:
                path.write_text(bad)
                with self.assertRaisesRegex(ValueError, message):
                    oil041(root)

    def test_end_to_end_and_no_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.glob('*.csv')}
            output = main(['--input', str(root), '--output', str(root / 'run'), '--train-periods', '2'])
            for name in ('run.json', 'metrics.json', 'items.csv', 'demand.csv', 'initial_plan.csv', 'orders.csv', 'inventory.csv', 'decisions.csv'):
                self.assertTrue((output / name).exists())
            self.assertEqual(json.loads((output / 'metrics.json').read_text())['evaluation_periods'], 363)
            with (output / 'initial_plan.csv').open() as plan_file, (output / 'decisions.csv').open() as decisions_file:
                first = next(csv.DictReader(plan_file))
                decision = next(csv.DictReader(decisions_file))
                self.assertEqual(float(first['planned_releases']), float(decision['release_qty']))
                self.assertEqual(float(first['planned_receipts']), 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.csv')})
            with self.assertRaises(FileExistsError):
                main(['--input', str(root), '--output', str(output), '--train-periods', '2'])

    def test_spdf_chronology_returns_and_unknown_blanks(self):
        # Minimal in-memory workbook API with the original 56 reversed monthly columns.
        months = [datetime(y, m, 1) for y in range(1997, 2002) for m in range(1, 13) if (y, m) <= (2001, 8)][::-1]
        row = [1, 45, 10, 20, 5, 2, '', '', '', ''] + [0] * 56
        row[10] = 7  # August 2001
        row[11] = -2
        row[10 + months.index(datetime(1997, 11, 1))] = 99999  # explicitly unreliable month
        headers = ['Material', 'Del. time', 'Min', 'Max', 'Stock', 'Price', 'H', 'M', 'L', 'Total']
        sheet = SimpleNamespace(nrows=2, ncols=66,
                                cell_value=lambda r, c: row[c] if r else months[c - 10],
                                row_values=lambda r: list(row) if r else headers + months)
        empty = SimpleNamespace(nrows=1, ncols=66, cell_value=sheet.cell_value, row_values=sheet.row_values)
        fake = SimpleNamespace(
            open_workbook=lambda p: SimpleNamespace(datemode=0, sheet_by_index=lambda i: sheet if p.name == 'Data_deel1a.xls' else empty),
            xldate_as_datetime=lambda v, mode: v)
        with tempfile.TemporaryDirectory() as tmp, patch.dict('sys.modules', {'xlrd': fake}):
            root = Path(tmp)
            for name in ('Data_deel1a.xls', 'Data _deel1b.xls'): (root / name).write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, 'Negative net consumption'): spdf_oil(root)
            data = spdf_oil(root, returns='clip')
            self.assertEqual((data.periods[0], data.periods[-1]), ('1997-12-01', '2001-08-01'))
            self.assertEqual(data.demand['1'][-2:], [0, 7])
            self.assertEqual(data.items['1'].lead_time, 2)
            self.assertEqual(data.items['1'].safety_stock, 0)  # Min is not safety stock
            self.assertEqual(data.metadata['negative_cells_clipped'], 1)
            self.assertEqual(data.demand_adjustments['1', len(data.periods) - 2], ('clipped_negative', -2))
            self.assertEqual(data.item_attributes['1']['source_reorder_point'], 10)
            row[10] = ''
            with self.assertRaisesRegex(ValueError, 'partial-year blank'): spdf_oil(root, returns='clip')
            row[10:18] = [''] * 8
            data = spdf_oil(root, returns='clip')
            self.assertEqual(data.metadata['documented_blank_cells_zeroed'], 8)
            self.assertEqual(data.demand_adjustments['1', len(data.periods)-1], ('whole_year_blank', None))
            months[0] = months[1]
            with self.assertRaisesRegex(ValueError, 'monthly columns'):
                spdf_oil(root, returns='clip')


if __name__ == '__main__':
    unittest.main()
