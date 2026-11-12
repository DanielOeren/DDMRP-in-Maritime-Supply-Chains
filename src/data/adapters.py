"""Adapters never write to the source directory. Every transformation is documented."""
import csv
from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path
import math
from src.data.schema import Dataset, Item


def records(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def fingerprints(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def oil041(root, max_items=50, offshore=False, source_stock=False):
    root = Path(root)
    paths = [root / f'{name}.csv' for name in ('spare_parts_master', 'facility_master', 'maintenance_consumption', 'inventory_levels')]
    parts, facilities, consumption, inventory = map(records, paths)
    allowed = {r['facility_id'] for r in facilities if not offshore or r['offshore_flag'] == '1'}
    # Fixed calendar origin/end from the published sample, independent of holdout activity.
    start, end = date(2021, 1, 1), date(2021, 12, 31)
    n = (end - start).days + 1
    selected = sorted(parts, key=lambda r: r['part_id'])[:max_items]
    demand = {p['part_id']: [0.0] * n for p in selected}
    for row in consumption:
        if row['part_id'] in demand and row['facility_id'] in allowed:
            t = (datetime.fromisoformat(row['event_timestamp']).date() - start).days
            if not 0 <= t < n:
                raise ValueError('Consumption outside documented 2021 horizon')
            q = float(row['consumption_qty'])
            if q < 0:
                raise ValueError('Negative consumption in OIL041')
            demand[row['part_id']][t] += q
    stock, safety = {}, {}
    for r in inventory:
        if r['facility_id'] in allowed:
            sku = r['part_id']
            stock[sku] = stock.get(sku, 0) + max(0, float(r['stock_qty']) - float(r['reserved_qty']))
            safety[sku] = safety.get(sku, 0) + float(r['safety_stock_qty'])
    items = {p['part_id']: Item(p['part_id'], float(p['unit_cost_usd']), max(1, math.ceil(float(p['standard_lead_time_days']))),
                              stock.get(p['part_id'], 0) if source_stock else 0,
                              safety.get(p['part_id'], 0)) for p in selected}
    return Dataset(items, demand, [(start + timedelta(days=i)).isoformat() for i in range(n)], {
        'source': 'oil041', 'period_unit': 'day', 'currency': 'USD', 'synthetic': True,
        'sha256': fingerprints(paths), 'offshore_only': offshore,
        'initial_stock': 'undated snapshot scenario' if source_stock else 'zero scenario',
        'transformations': ['Parts selected by sorted ID, not future demand.',
            'Daily consumption summed across selected facilities into one pooled stocking point per part.',
            'Absent daily consumption treated as zero within the published event horizon.',
            'Lead times rounded upward to days; minimum one day.',
            'Safety stock summed across selected inventory rows; missing pairs contribute zero.',
            'Snapshot safety stock is a synthetic scenario parameter, not a historically known policy.',
            'No warehouse allocation or transfer network is inferred; no source KPI labels used.']}).validate()


def spdf_oil(root, max_items=50, source_stock=False, returns='reject'):
    try:
        import xlrd
    except ImportError as e:
        raise RuntimeError('SPDF import requires: python -m pip install -r requirements.txt') from e
    root = Path(root)
    paths = [root / 'Data_deel1a.xls', root / 'Data _deel1b.xls']
    all_rows, headers = [], None
    for path in paths:
        book = xlrd.open_workbook(path)
        sheet = book.sheet_by_index(1)
        dates = [xlrd.xldate_as_datetime(sheet.cell_value(0, j), book.datemode).date().isoformat() for j in range(10, 66)]
        if headers is not None and dates != headers:
            raise ValueError('Original oil date axes differ')
        headers = dates
        all_rows.extend(sheet.row_values(i) for i in range(1, sheet.nrows))
    # Start after the unreliable November 1997: contiguous months without an invented observation.
    indices = sorted((i for i, d in enumerate(headers) if d >= '1997-12-01'), key=lambda i: headers[i])
    items, demand, negatives, blanks = {}, {}, 0, 0
    for r in sorted(all_rows, key=lambda r: int(r[0]))[:max_items]:
        sku = str(int(r[0])); series = []
        for i in indices:
            value = r[i + 10]
            if value == '':
                # Only documented whole-calendar-year blanks can become zero.
                year = headers[i][:4]
                if any(r[j + 10] != '' for j, d in enumerate(headers) if d[:4] == year):
                    raise ValueError(f'Unexplained partial-year blank: material {sku}, {headers[i]}')
                blanks += 1
                value = 0
            value = float(value)
            if value < 0:
                negatives += 1
                if returns == 'reject':
                    raise ValueError(f'Negative net consumption for {sku}; explicitly use --returns clip for a proxy scenario')
                if returns != 'clip':
                    raise ValueError('returns must be reject or clip')
                value = 0.0
            series.append(value)
        items[sku] = Item(sku, float(r[5]), max(1, math.ceil(float(r[1]) / 30.4375)), float(r[4]) if source_stock else 0)
        demand[sku] = series
    return Dataset(items, demand, [headers[i] for i in indices], {
        'source': 'spdf_oil', 'period_unit': 'month', 'currency': 'source currency unspecified', 'synthetic': False,
        'sha256': fingerprints(paths), 'initial_stock': 'undated snapshot scenario' if source_stock else 'zero scenario',
        'negative_cells_clipped': negatives, 'documented_blank_cells_zeroed': blanks,
        'transformations': ['Original halves only; chronology reversed; contiguous Dec 1997-Aug 2001 retained.',
            'Calendar-day lead times divided by 30.4375 and rounded upward, minimum one month.',
            'Min is a reorder point, NOT safety stock; safety stock set to zero.',
            'Negative net consumption rejected unless explicit clip proxy assumption selected.',
            'Zero prices retained and flagged by count; consumption is not observed gross demand.'],
        'zero_price_items': sum(i.unit_cost == 0 for i in items.values())}).validate()
