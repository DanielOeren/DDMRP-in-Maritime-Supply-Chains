import csv
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime

from src.data.schema import Dataset, Item
from src.data.adapters import oil041, spdf_oil
from src.mrp.planning import MRPPolicy, lot_size, plan
from src.mrp.__main__ import main
from src.simulation.engine import Config, realized_lead_time, simulate
from src.evaluation.metrics import summarize


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
    def test_regular_delay_no_magic_receipt(self):
        d = dataset([5] * 8)
        ledger, orders, _ = simulate(d, MRPPolicy(), Config(2, 2, 4))
        self.assertEqual(orders[0]['release_period'], 2)
        self.assertEqual(orders[0]['actual_due'], 4)
        self.assertEqual(ledger[0]['receipts'], 0)
        self.assertEqual(ledger[1]['receipts'], 0)
        self.assertGreater(ledger[2]['receipts'], 0)

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

    def test_end_to_end_and_no_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.glob('*.csv')}
            output = main(['--input', str(root), '--output', str(root / 'run'), '--train-periods', '2'])
            for name in ('run.json', 'metrics.json', 'items.csv', 'demand.csv', 'initial_plan.csv', 'orders.csv', 'inventory.csv', 'decisions.csv'):
                self.assertTrue((output / name).exists())
            self.assertEqual(json.loads((output / 'metrics.json').read_text())['evaluation_periods'], 363)
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
        sheet = SimpleNamespace(nrows=2, cell_value=lambda r, c: months[c - 10], row_values=lambda r: list(row))
        empty = SimpleNamespace(nrows=1, cell_value=sheet.cell_value)
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
            row[10] = ''
            with self.assertRaisesRegex(ValueError, 'partial-year blank'): spdf_oil(root, returns='clip')


if __name__ == '__main__':
    unittest.main()
