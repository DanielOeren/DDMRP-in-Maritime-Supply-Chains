"""Read-only adapters. Source attributes and proxy adjustments accompany model inputs."""
import csv
from datetime import date, datetime, timedelta
import hashlib
import math
from pathlib import Path
import random

from src.data.schema import Dataset, Item


def records(path, required=()):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        missing = set(required).difference(fields)
        if missing or len(fields) != len(set(fields)):
            raise ValueError(f'Invalid columns in {Path(path).name}, missing {sorted(missing)} or duplicate names')
        return list(reader)


def fingerprints(paths, relative_to=None):
    return {p.relative_to(relative_to).as_posix() if relative_to else str(p):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def nonnegative(value, field):
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f'Missing or invalid {field}: {value!r}') from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{field} must be finite and nonnegative: {value!r}')
    return number


def index_by(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if not value or not value.strip() or value in result:
            raise ValueError(f'Empty or duplicate {key}: {value!r}')
        result[value] = row
    return result


def select_ids(frame, limit, seed):
    if type(limit) is not int or limit < 1:
        raise ValueError('max_items must be a positive integer')
    ids = sorted(frame)
    if seed is None:
        return ids[:limit]
    return sorted(random.Random(seed).sample(ids, min(limit, len(ids))))


def oil041(root, max_items=50, offshore=False, source_stock=False, selection_seed=None):
    root = Path(root)
    paths = [root / f'{name}.csv' for name in (
        'spare_parts_master', 'facility_master', 'maintenance_consumption', 'inventory_levels')]
    required = [
        ('part_id', 'standard_lead_time_days', 'unit_cost_usd'),
        ('facility_id', 'offshore_flag'),
        ('work_order_id', 'part_id', 'facility_id', 'event_timestamp', 'consumption_qty'),
        ('inventory_id', 'warehouse_id', 'part_id', 'facility_id', 'stock_qty', 'reserved_qty', 'safety_stock_qty'),
    ]
    parts, facilities, consumption, inventory = map(records, paths, required)
    parts = index_by(parts, 'part_id')
    facilities = index_by(facilities, 'facility_id')
    if any(r['offshore_flag'] not in ('0', '1') for r in facilities.values()):
        raise ValueError('Unknown offshore_flag')
    allowed = {key for key, r in facilities.items() if not offshore or r['offshore_flag'] == '1'}
    if not allowed:
        raise ValueError('No facilities in requested scope')
    selected = select_ids(parts, max_items, selection_seed)
    start = date(2021, 1, 1)
    periods = [(start + timedelta(days=i)).isoformat() for i in range(365)]
    demand = {sku: [0.0] * len(periods) for sku in selected}
    attributes = {}
    for sku in selected:
        part = parts[sku]
        attributes[sku] = {
            'source_lead_days': nonnegative(part['standard_lead_time_days'], 'standard_lead_time_days'),
            'criticality': part.get('criticality'), 'part_category': part.get('part_category'),
            'abc_class': part.get('abc_class'), 'repairable_flag': part.get('repairable_flag'),
            'inventory_rows': 0, 'snapshot_stock': 0.0, 'snapshot_reserved': 0.0,
            'snapshot_available': 0.0, 'snapshot_safety': 0.0, 'over_reserved_rows': 0,
        }

    def item_in_scope(row):
        sku, facility = row['part_id'], row['facility_id']
        if sku not in parts or facility not in facilities:
            raise ValueError(f'Unknown part or facility: {sku!r}, {facility!r}')
        return sku in demand and facility in allowed

    event_count = 0
    for row in index_by(consumption, 'work_order_id').values():
        in_scope = item_in_scope(row)
        t = (datetime.fromisoformat(row['event_timestamp']).date() - start).days
        if not 0 <= t < len(periods):
            raise ValueError('Consumption outside documented 2021 horizon')
        quantity = nonnegative(row['consumption_qty'], 'consumption_qty')
        if in_scope:
            demand[row['part_id']][t] += quantity
            event_count += 1

    pairs = set()
    for row in index_by(inventory, 'inventory_id').values():
        in_scope = item_in_scope(row)
        pair = row['part_id'], row['warehouse_id']
        if not pair[1] or pair in pairs:
            raise ValueError(f'Missing warehouse or duplicate inventory snapshot pair: {pair}')
        pairs.add(pair)
        stock = nonnegative(row['stock_qty'], 'stock_qty')
        reserved = nonnegative(row['reserved_qty'], 'reserved_qty')
        safety = nonnegative(row['safety_stock_qty'], 'safety_stock_qty')
        if in_scope:
            a = attributes[row['part_id']]
            a['inventory_rows'] += 1
            a['snapshot_stock'] += stock
            a['snapshot_reserved'] += reserved
            a['snapshot_available'] += max(0, stock - reserved)
            a['snapshot_safety'] += safety
            a['over_reserved_rows'] += int(reserved > stock)

    items = {}
    for sku, a in attributes.items():
        items[sku] = Item(sku, nonnegative(parts[sku]['unit_cost_usd'], 'unit_cost_usd'),
                          max(1, math.ceil(a['source_lead_days'])),
                          a['snapshot_available'] if source_stock else 0, a['snapshot_safety'])
    metadata = {
        'source': 'oil041', 'period_unit': 'day', 'currency': 'USD', 'synthetic': True,
        'sha256': fingerprints(paths, root), 'offshore_only': offshore,
        'candidate_items': len(parts), 'selection_seed': selection_seed,
        'facilities_in_scope': len(allowed), 'selected_consumption_rows': event_count,
        'items_without_snapshot': sum(a['inventory_rows'] == 0 for a in attributes.values()),
        'over_reserved_rows_selected': sum(a['over_reserved_rows'] for a in attributes.values()),
        'initial_stock': 'undated snapshot scenario' if source_stock else 'zero scenario',
        'transformations': [
            'Pooled facility consumption by SKU/day. Absent events represent zero proxy demand.',
            'Lead days rounded upward with a one-period floor.',
            'Summed snapshot safety stock. Missing pairs contribute zero scenario units.',
            'Available snapshot stock sums max(stock minus reservations, 0) per warehouse.',
        ],
    }
    return Dataset(items, demand, periods, metadata, attributes).validate()


def spdf_oil(root, max_items=50, source_stock=False, returns='reject', selection_seed=None):
    if returns not in ('reject', 'clip'):
        raise ValueError('returns must be reject or clip')
    try:
        import xlrd
    except ImportError as error:
        raise RuntimeError('SPDF import requires python -m pip install -r requirements.txt') from error
    root = Path(root)
    paths = [root / 'Data_deel1a.xls', root / 'Data _deel1b.xls']
    expected = [date(y, m, 1).isoformat() for y in range(1997, 2002) for m in range(1, 13)
                if (y, m) <= (2001, 8)]
    materials = {}
    columns = ['Material', 'Del. time', 'Min', 'Max', 'Stock', 'Price', 'H', 'M', 'L', 'Total']
    for path in paths:
        book = xlrd.open_workbook(path)
        sheet = book.sheet_by_index(1)
        if sheet.ncols != 66 or sheet.row_values(0)[:10] != columns:
            raise ValueError(f'Unexpected oil workbook layout in {path.name}')
        dates = [xlrd.xldate_as_datetime(sheet.cell_value(0, j), book.datemode).date().isoformat()
                 for j in range(10, 66)]
        if dates != expected[::-1]:
            raise ValueError(f'Expected monthly columns Aug 2001 through Jan 1997 in {path.name}')
        for i in range(1, sheet.nrows):
            material = nonnegative(sheet.cell_value(i, 0), 'Material')
            if not material.is_integer() or int(material) in materials:
                raise ValueError(f'Non-integral or duplicate material ID: {material}')
            materials[int(material)] = (sheet, i)

    periods = [d for d in expected if d >= '1997-12-01']
    items, demand, attributes, adjustments = {}, {}, {}, {}
    negatives = blanks = 0
    clipped_units = 0.0
    for material in select_ids(materials, max_items, selection_seed):
        sheet, i = materials[material]
        row = sheet.row_values(i)
        sku = str(material)
        chronological = row[10:][::-1]
        years_with_values = {d[:4] for d, value in zip(expected, chronological) if value != ''}
        series = []
        for t, (d, value) in enumerate(zip(periods, chronological[-len(periods):])):
            if value == '':
                if d[:4] in years_with_values:
                    raise ValueError(f'Unexplained partial-year blank: material {sku}, {d}')
                blanks += 1
                adjustments[sku, t] = ('whole_year_blank', None)
                value = 0
            quantity = float(value)
            if not math.isfinite(quantity):
                raise ValueError(f'Nonfinite net consumption for {sku}, {d}')
            if quantity < 0:
                if returns == 'reject':
                    raise ValueError(f'Negative net consumption for {sku}, {d}; use --returns clip for a proxy')
                adjustments[sku, t] = ('clipped_negative', quantity)
                negatives += 1
                clipped_units -= quantity
                quantity = 0.0
            series.append(quantity)
        lead = nonnegative(row[1], 'Del. time')
        stock = nonnegative(row[4], 'Stock')
        items[sku] = Item(sku, nonnegative(row[5], 'Price'), max(1, math.ceil(lead / 30.4375)),
                          stock if source_stock else 0)
        attributes[sku] = {'source_lead_days': lead, 'source_reorder_point': row[2],
                           'source_max_stock': row[3], 'snapshot_stock': stock}
        attributes[sku].update(zip(('installed_high', 'installed_medium', 'installed_low',
                                    'installed_total'), row[6:10]))
        demand[sku] = series
    metadata = {
        'source': 'spdf_oil', 'period_unit': 'month', 'currency': 'source currency unspecified',
        'synthetic': False, 'sha256': fingerprints(paths, root),
        'candidate_items': len(materials), 'selection_seed': selection_seed,
        'initial_stock': 'undated snapshot scenario' if source_stock else 'zero scenario',
        'negative_cells_clipped': negatives, 'negative_units_clipped': clipped_units,
        'documented_blank_cells_zeroed': blanks,
        'zero_price_items': sum(item.unit_cost == 0 for item in items.values()),
        'transformations': [
            'Original halves, chronological contiguous segment after unreliable November 1997.',
            'Whole-calendar-year blanks converted to zero. Signed proxy changes recorded by cell.',
            'Calendar days divided by 30.4375, rounded upward with a one-month floor.',
            'Zero scenario safety stock. Source Min is a reorder point.',
        ],
    }
    return Dataset(items, demand, periods, metadata, attributes, adjustments).validate()
