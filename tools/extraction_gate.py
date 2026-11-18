"""Freeze the paper's output scope and check workbook coverage."""
import argparse
import json
import math
import re
from pathlib import Path
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from datetime import date, time, timedelta

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries

from run_task import read, sha

PRINTED_NUMBER = re.compile(r'[+-]?(?:(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?%?')


@contextmanager
def attempt_log(gate_path, pdf_digest):
    path = Path(gate_path).parent / f'figure-attempts-{pdf_digest[:16]}.sqlite3'
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS attempts ('
                   'source TEXT, fingerprint TEXT, status TEXT, note TEXT, '
                   'PRIMARY KEY (source, fingerprint))')
        db.execute('BEGIN IMMEDIATE')
        yield db


def attempt_limit(db, source):
    recent = db.execute('SELECT status FROM attempts WHERE source=? ORDER BY rowid DESC',
                        (source,)).fetchall()
    failures = 0
    for (status,) in recent:
        if status == 'confirmed':
            break
        if status == 'failed':
            failures += 1
    if failures >= 2:
        raise ValueError(f'{source}: two failed automatic attempts; use manual point review')


def record_attempt(db, source, fingerprint, status, note=''):
    db.execute('INSERT INTO attempts VALUES (?, ?, ?, ?) ON CONFLICT(source,fingerprint) '
               'DO UPDATE SET status=excluded.status,note=excluded.note '
               "WHERE attempts.status <> 'failed'",
               (source, fingerprint, status, note))


def required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} is required')


def data_bounds(table):
    left, top, right, bottom = range_boundaries(table['range'])
    if not all((left, top, right, bottom)) or left <= 1 or top <= 1 or right < left or bottom < top:
        raise ValueError('use a data rectangle with headers above and labels on the left')
    if len(table['columns']) != right - left + 1 or len(table['rows']) != bottom - top + 1:
        raise ValueError(f"{table['sheet']}: declared labels do not match the number of rows and columns in range")
    return left, top, right, bottom


def rectangles(sheet, table):
    left, top, right, bottom = data_bounds(table)
    columns = [sheet.cell(top - 1, col).value for col in range(left, right + 1)]
    rows = [sheet.cell(row, left - 1).value for row in range(top, bottom + 1)]
    return columns, rows, [cell for row in sheet.iter_rows(min_row=top, max_row=bottom,
                    min_col=left, max_col=right) for cell in row]


def is_complete_transpose(sheet, table):
    left, top, _, _ = data_bounds(table)
    width = len(table['rows'])
    height = len(table['columns'])
    headers = [sheet.cell(top - 1, left + offset).value for offset in range(width)]
    labels = [sheet.cell(top + offset, left - 1).value for offset in range(height)]
    return (headers == table['rows'] and labels == table['columns'] and
            all(value is not None and value != '' for row in sheet.iter_rows(
                min_row=top, max_row=top + height - 1, min_col=left,
                max_col=left + width - 1, values_only=True) for value in row))


def cell_bindings(table, table_id, field='points'):
    lo, top, hi, bottom = data_bounds(table)
    if field == 'points' and 'grid' in table:
        grid = table['grid']
        if 'points' in table or set(grid) - {'part', 'x', 'series'}:
            raise ValueError(f'{table_id}: use either grid or explicit points')
        required_text(grid['part'], f'{table_id}: grid part')
        series = grid.get('series', table['rows'])
        xs = grid.get('x')
        if xs is None:
            xs = []
            for label in table['columns']:
                if type(label) in (int, float):
                    xs.append(label)
                elif isinstance(label, str) and PRINTED_NUMBER.fullmatch(label.strip().replace('−', '-')):
                    xs.append(float(label.strip().replace('−', '-').replace(',', '').rstrip('%')))
                else:
                    raise ValueError(f'{table_id}: nonnumeric column labels require explicit grid.x')
        if len(series) != bottom - top + 1 or len(xs) != hi - lo + 1:
            raise ValueError(f'{table_id}: grid shape differs from frozen data rectangle')
        if any(not isinstance(s, str) or not s.strip() for s in series) or len(set(series)) != len(series):
            raise ValueError(f'{table_id}: grid series must be distinct named rows')
        if any(type(x) not in (int, float, str) or
               (isinstance(x, str) and not x.strip()) or
               (type(x) in (int, float) and not math.isfinite(x)) for x in xs) or len(set(xs)) != len(xs):
            raise ValueError(f'{table_id}: grid x values must be distinct and usable')
        return {f'{get_column_letter(col)}{row}': {'part': grid['part'], 'series': name, 'x': x}
                for row, name in enumerate(series, top)
                for col, x in enumerate(xs, lo)}
    expected = {f'{get_column_letter(col)}{row}' for row in range(top, bottom + 1)
                for col in range(lo, hi + 1)}
    bindings = table.get(field, {})
    if not expected or set(bindings) != expected:
        raise ValueError(f'{table_id}: bind every expected cell in {field}')
    return bindings


def check_old_cells(sheet, table):
    left, top, right, bottom = data_bounds(table)
    covered = [(left - 1, top - 1, right, bottom)]
    for item in table.get('non_data_ranges', []):
        required_text(item.get('reason'), f'{sheet.title}: reason for non-data range')
        limits = range_boundaries(item['range'])
        if not all(limits) or limits[0] > limits[2] or limits[1] > limits[3]:
            raise ValueError(f'{sheet.title}: provide a bounded non-data range')
        covered.append(limits)
    sheet.reset_dimensions()
    unlisted = []
    for row in sheet.rows:
        for cell in row:
            value = cell.value
            is_data = (cell.data_type == 'f' or type(value) in (int, float) or
                       isinstance(value, (date, time, timedelta)))
            if isinstance(value, str):
                token = value.strip().replace('−', '-').lstrip('≈~').strip()
                is_data = is_data or token.casefold() in {'—', '–', 'n/a', '未能分辨'} or bool(PRINTED_NUMBER.fullmatch(token))
            if is_data and not any(l <= cell.column <= r and t <= cell.row <= b for l, t, r, b in covered):
                unlisted.append(cell.coordinate)
                if len(unlisted) == 8:
                    break
        if len(unlisted) == 8:
            break
    if unlisted:
        raise ValueError(f"{sheet.title}: old workbook cells outside frozen baseline: {', '.join(unlisted)}")


def inspect_workbook(path, tables, final=False, missing=None, point_values=None):
    book = load_workbook(path, read_only=False, data_only=True, keep_links=False)
    missing = missing or {}
    errors = []
    seen_missing = set()
    try:
        for table_id, table in tables.items():
            name = table['sheet']
            if name not in book:
                errors.append(f'{table_id}: missing sheet {name}')
                continue
            sheet = book[name]
            columns, rows, cells = rectangles(sheet, table)
            if columns != table['columns'] or rows != table['rows']:
                if is_complete_transpose(sheet, table):
                    errors.append(f'{table_id}: rows and columns are transposed; '
                                  'use horizontal-axis values as columns and legend series as rows')
                    continue
                errors.append(f'{table_id}: rows or columns differ from frozen scope')
            if not final:
                continue
            numeric = 0
            for cell in cells:
                key = f'{name}!{cell.coordinate}'
                value = cell.value
                if point_values is not None and key in point_values:
                    reading = point_values[key]
                    if reading['value'] is None:
                        if value != '—' or key not in missing:
                            errors.append(f'{key}: reviewed source gap must be written as — with evidence')
                    elif type(value) not in (float, int) or not math.isclose(
                            value, reading['value'], rel_tol=0, abs_tol=reading['tolerance']):
                        errors.append(f"{key}: workbook value {value!r} differs from reviewed reading {reading['value']}")
                if type(value) in (int, float) and math.isfinite(value):
                    numeric += 1
                elif value == '—' and key in missing:
                    seen_missing.add(key)
                else:
                    errors.append(f'{table_id}: empty, nonnumeric or unsupported missing value at {key}')
            if not numeric:
                errors.append(f'{table_id}: the entire table has no numeric results')
    finally:
        book.close()
    if set(missing) - seen_missing and final:
        errors.append('missing-value evidence does not match the workbook cells')
    if errors:
        raise ValueError('; '.join(errors[:20]))


def check_cards(scope, parts, *, allow_subset=False):
    cards = scope['cards']
    by_id = {part['id']: part for part in parts}
    if set(by_id) - set(cards) or (not allow_subset and set(cards) - set(by_id)):
        raise ValueError('each extraction part requires one figure card')
    for source, entry in scope['inventory'].items():
        if entry['action'] == 'read':
            for table_id in entry['tables']:
                table = scope['tables'][table_id]
                if 'grid' in table:
                    grid = table['grid']
                    legend = iter(cards[grid['part']]['series'])
                    if not all(series in legend for series in grid.get('series', table['rows'])):
                        raise ValueError(f'{table_id}: grid rows must follow the frozen legend order')
                for ref in cell_bindings(table, table_id).values():
                    if cards.get(ref.get('part'), {}).get('source') != source:
                        raise ValueError(f'{table_id}: cell binding requires a part from the same source figure')
    for part_id, card in cards.items():
        entry = scope['inventory'][card['source']]
        if (entry['action'] != 'read' or
                (part_id in by_id and card['series'] != by_id[part_id]['scope']['series'])):
            raise ValueError(f'{part_id}: source disposition or legend order differs')
        required_text(card.get('zero'), 'zero baseline or visual reason it is absent')
        required_text(card.get('coordinates'), 'source pixel coordinate system')
        for axis in ('x', 'y'):
            spec = card['axes'][axis]
            required_text(spec.get('quantity'), f'{axis} axis quantity')
            required_text(spec.get('unit'), f'{axis} axis unit')
            if spec['kind'] not in ('linear', 'log10', 'category', 'printed_ticks'):
                raise ValueError(f'{axis}: unsupported scale without a source tick map')


def check_discrete_x(spec, job, factor):
    positions = {}
    for position, label in spec['ticks']:
        if (type(position) not in (int, float) or not math.isfinite(position) or
                type(label) not in (str, int, float) or label in positions or
                (positions and position <= next(reversed(positions.values())))):
            raise ValueError('x: discrete ticks require unique labels in left-to-right order')
        positions[label] = position
    checks = spec['check_ticks']
    if not checks or any(type(p) not in (int, float) or not math.isfinite(p) or
                         label not in positions or abs(positions[label] - p) * factor > 2
                         for p, label in checks):
        raise ValueError('x: independent printed label or tick position differs by more than 2 source pixels')
    for item in job['items']:
        label = item['identity'].get('x')
        anchor_x = item['anchor'][0]
        if label not in positions or abs(positions[label] - anchor_x) * factor > 2:
            raise ValueError(f"{item['id']}: manual target does not match a declared printed X position")


def check_job(card, job, method):
    from review_points import axis_fit

    # Bind an axis check to the exact calibration used by the extractor.
    axes = job.get('axes') or {a: job[a] for a in ('x', 'y') if a in job}
    if method in ('bitmap_points', 'manual_points', 'review_candidates', 'bitmap_lines',
                  'bitmap_curve_identity', 'bitmap_bar_ranges'):
        panels = list(job.get('panels', {}).values())
        if len(panels) != 1:
            raise ValueError('one calibration card per bitmap panel')
        axes = {**job.get('gate_axes', job.get('wpd_import_contract', {}).get('axes', {})),
                'y': panels[0]['axis']}
    if not axes:
        raise ValueError(f'{method}: no explicit compatible preflight axes')
    for axis in ('x', 'y'):
        spec = card['axes'][axis]
        actual = axes.get(axis, {})
        if actual.get('kind', 'linear') != spec['kind'] or actual.get('ticks') != spec['ticks']:
            raise ValueError(f'{axis}: reader configuration differs from checked ticks')
        factor = spec.get('native_px_per_unit', 1)
        if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0:
            raise ValueError(f'{axis}: invalid native pixel scale')
        if axis == 'x' and method == 'manual_points' and spec['kind'] in ('category', 'printed_ticks'):
            check_discrete_x(spec, job, factor)
            continue
        checks = spec['check_ticks']
        if not checks or any(c[0] == f[0] or c[1] == f[1] for c in checks for f in spec['ticks']):
            raise ValueError(f'{axis}: independent check ticks required')
        axis_fit({**spec, 'coordinate': axis, 'max_residual_px': 2 / factor})


def configured_points(job, method):
    """Read the selection fields consumed by each fixed reader."""
    if method in ('bitmap_points', 'manual_points', 'review_candidates'):
        for item in job['items']:
            identity = item['identity']
            for role in item.get('roles') or [item.get('role', 'value')]:
                yield item['id'], identity.get('series'), identity.get('x'), role
    elif method in ('bitmap_lines', 'bitmap_curve_identity'):
        for trace in job['traces']:
            for x in trace['sample_x']:
                yield f"{trace['id']}-{x}", trace['identity'].get('series'), x, 'value'
    elif method == 'bitmap_bar_ranges':
        for item in job.get('bars', []):
            roles = ('bar_tip', 'uncapped_top', 'uncapped_bottom') if item.get('range_expected', True) else ('bar_tip',)
            for role in roles:
                yield item['id'], item['identity'].get('series'), item['identity'].get('x'), role
        for item in job.get('capped_ranges', []):
            for role in ('capped_top', 'capped_bottom'):
                yield item['id'], item['identity'].get('series'), item['identity'].get('x'), role
    elif method in ('wpd', 'vector_lines'):
        names = ([s['name'] for s in job['series']] if method == 'wpd' else
                 [s['name'] for s in job.get('series_styles', [])] or list(job.get('color_map', {}).values()))
        for series_index, name in enumerate(names):
            for index, x in enumerate(job['sample_at']):
                ident = f"{job['series'][series_index]['id']}__s{index:04d}" if method == 'wpd' else None
                yield ident, name, x, 'value'
    elif method == 'vector_window_stats':
        for series in job['series']:
            for window in job['windows']:
                for statistic in window['statistics']:
                    yield (f"{series['id']}__{window['id']}__{statistic}", series['name'],
                           window.get('label', window['start']), 'value')
    else:
        raise ValueError(f'{method}: cannot prove configured output scope; use a supported reader or manual points')


def check_configured_coverage(scope, part_id, job, method):
    card = scope['cards'][part_id]
    source = scope['inventory'][card['source']]
    if method == 'review_candidates' and 'wpd_import_contract' in job:
        contract = job['wpd_import_contract']
        if (contract.get('pdf_sha256') != scope['pdf_sha256'] or
                contract.get('source_id') != card['source'] or contract.get('part_id') != part_id):
            raise ValueError(f'{part_id}: WPD candidate package belongs to another paper or figure')
        evidence = [(contract['config'], contract['config_sha256']),
                    (contract['engine_result'], contract['engine_result_sha256']),
                    (contract['source']['image'], contract['source']['image_sha256']),
                    *[(job['sources'][panel['source']]['image'], contract['source']['image_sha256'])
                      for panel in job['panels'].values()]]
        if any(sha(path) != expected for path, expected in evidence):
            raise ValueError(f'{part_id}: WPD input or result changed; review must use the recorded evidence')
    if 'page' in job and job['page'] not in [source['page'], *source.get('continuation_pages', [])]:
        raise ValueError(f'{part_id}: reader page differs from frozen source')
    points = list(configured_points(job, method))
    series = {point[1] for point in points}
    if series != set(card['series']):
        raise ValueError(f'{part_id}: configured series differ from frozen figure card')
    by_id, by_series_x = defaultdict(list), defaultdict(list)
    for i, (ident, name, x, role) in enumerate(points):
        if ident is not None:
            by_id[ident, role].append(i)
        if type(x) in (int, float, str):
            by_series_x[name, x, role].append(i)
    used = set()
    for table_id in source['tables']:
        table = scope['tables'][table_id]
        for address, ref in cell_bindings(table, table_id).items():
            if ref['part'] != part_id:
                continue
            role = ref.get('role', 'value')
            if 'id' in ref:
                indexes = by_id[ref['id'], role]
            else:
                indexes = by_series_x[ref['series'], ref['x'], role]
            indexes = [i for i in indexes if all(points[i][j] == ref[key]
                       for j, key in ((1, 'series'), (2, 'x')) if key in ref)]
            if len(indexes) != 1 or indexes[0] in used:
                raise ValueError(f"{table['sheet']}!{address}: missing, duplicate or ambiguous configured reading")
            used.add(indexes[0])
    if len(used) != len(points):
        raise ValueError(f'{part_id}: {len(points)-len(used)} configured readings have no frozen output cell')


def validate_scope(scope, pdf):
    import fitz
    from scan_pages import caption_matches

    if scope['pdf_sha256'] != sha(pdf):
        raise ValueError('scope PDF has changed')
    with fitz.open(pdf) as doc:
        if scope['pages_reviewed'] != list(range(1, len(doc) + 1)):
            raise ValueError('require full-paper page review before extraction')
        page_count = len(doc)
        captions = {(page.number + 1, item['kind'], item['label'].casefold())
                    for page in doc for item in caption_matches(
                        block for block in page.get_text('blocks') if block[6] == 0)}
    figures = scope['inventory']
    tables = scope['tables']
    if not figures or not tables:
        raise ValueError('full-paper inventory and output table baseline required')
    assigned = []
    listed_captions = set()
    for source_id, entry in figures.items():
        required_text(source_id, 'figure/table ID')
        if entry['kind'] not in ('figure', 'table') or not 1 <= entry['page'] <= page_count:
            raise ValueError(f'{source_id}: invalid source kind or page')
        label = entry.get('label')
        if label is None:
            match = re.fullmatch(r'(?:fig(?:ure)?\.?|table|图|表)[\s_-]*([0-9]+[a-z]?|[ivxlcdm]+)',
                                 source_id, re.I)
            label = match[1] if match else None
        if label is not None:
            required_text(label, f'{source_id}: printed caption label')
        pages = [entry['page'], *entry.get('continuation_pages', [])]
        if any(type(page) is not int or not 1 <= page <= page_count for page in pages):
            raise ValueError(f'{source_id}: invalid source or continuation page')
        if label is not None:
            covered = {(page, entry['kind'], label.strip().casefold()) for page in pages}
            if covered & listed_captions:
                raise ValueError(f'{source_id}: duplicate source caption in inventory')
            listed_captions.update(covered)
        if entry['action'] == 'exclude':
            required_text(entry.get('reason'), f'{source_id}: exclusion reason')
            if entry.get('tables'):
                raise ValueError(f'{source_id}: excluded source has output tables')
        elif entry['action'] in ('read', 'transcribe'):
            if not entry.get('tables'):
                raise ValueError(f'{source_id}: no output table mapped')
            assigned.extend(entry['tables'])
        else:
            raise ValueError(f'{source_id}: decide its disposition before extraction')
    dismissed = set()
    for decision in scope.get('caption_false_positives', []):
        required_text(decision.get('reason'), 'caption rejection reason')
        key = (decision['page'], decision['kind'], decision['label'].strip().casefold())
        if key not in captions or key in listed_captions or key in dismissed:
            raise ValueError('caption rejection must identify one unmatched detected caption')
        dismissed.add(key)
    unlisted = captions - listed_captions - dismissed
    if unlisted:
        details = ', '.join(f'PDF {page}: {kind} {label}' for page, kind, label in sorted(unlisted))
        raise ValueError(f'caption missing from full-paper inventory: {details}')
    if sorted(assigned) != sorted(tables):
        raise ValueError('each expected output table must be mapped exactly once')
    for table_id, table in tables.items():
        if not table['columns'] or not table['rows'] or any(v is None or v == '' for v in table['columns'] + table['rows']):
            raise ValueError(f'{table_id}: freeze nonempty column and row labels')
        data_bounds(table)
    if len({t['sheet'] for t in tables.values()}) != len(tables):
        raise ValueError('preserve separate sheets for the frozen output tables')
    old = scope['baseline']
    if old is None:
        required_text(scope.get('no_previous_workbook_reason'), 'why this is a new workbook')
    else:
        if sha(old['file']) != old['sha256']:
            raise ValueError('old workbook baseline has changed')
        if not old['tables'] or set(old['tables']) - set(tables):
            raise ValueError('a baseline output table is missing from the new scope')
        book = load_workbook(old['file'], read_only=True, data_only=False, keep_links=False)
        try:
            mapped = {t['sheet'] for t in old['tables'].values()}
            metadata = old.get('metadata_sheets', {})
            if mapped & set(metadata) or mapped | set(metadata) != set(book.sheetnames):
                raise ValueError('classify every old worksheet as a data table or metadata')
            for name, reason in metadata.items():
                required_text(reason, f'{name}: metadata exclusion reason')
            for table in old['tables'].values():
                check_old_cells(book[table['sheet']], table)
        finally:
            book.close()
        for key, old_table in old['tables'].items():
            for axis in ('columns', 'rows'):
                new_labels = iter(tables[key][axis])
                if not all(label in new_labels for label in old_table[axis]):
                    raise ValueError(f'{key}: frozen {axis} removed or reordered')
        inspect_workbook(old['file'], old['tables'])
    return printed_readings(scope, pdf) if any(x['action'] == 'transcribe' for x in figures.values()) else {}


def printed_readings(scope, pdf):
    import fitz

    result = {}
    with fitz.open(pdf) as doc:
        for entry in scope['inventory'].values():
            if entry['action'] != 'transcribe':
                continue
            pages = [entry['page'], *entry.get('continuation_pages', [])]
            for table_id in entry['tables']:
                table = scope['tables'][table_id]
                for address, ref in cell_bindings(table, table_id, 'printed').items():
                    key = f"{table['sheet']}!{address}"
                    page_number = ref.get('page', entry['page'])
                    if type(page_number) is not int or page_number not in pages or not 1 <= page_number <= len(doc):
                        raise ValueError(f'{key}: printed source page does not belong to this item')
                    page = doc[page_number - 1]
                    box = fitz.Rect(ref['bbox'])
                    if box.is_empty or not (page.rect * page.derotation_matrix).contains(box):
                        raise ValueError(f'{key}: printed source region outside page')
                    token = ref['text'].strip().replace('−', '-')
                    if ref['mode'] == 'text':
                        actual = page.get_text('text', clip=box).strip().replace('−', '-')
                        if token != actual:
                            raise ValueError(f'{key}: printed token differs from the PDF text at its recorded location')
                    elif ref['mode'] == 'visual':
                        required_text(ref.get('note'), f'{key}: visual transcription evidence')
                    else:
                        raise ValueError(f'{key}: use text or visual source evidence')
                    if not PRINTED_NUMBER.fullmatch(token):
                        raise ValueError(f'{key}: expected a single printed numeric token; resolve other source formats explicitly')
                    value = float(token.rstrip('%').replace(',', ''))
                    if not math.isfinite(value):
                        raise ValueError(f'{key}: printed number is not finite')
                    result[key] = {'value': value, 'tolerance': max(math.ulp(value) * 4, 1e-12)}
    return result


def bound_readings(scope, results, missing):
    values = {}
    used = set()
    indexes = {}
    for part, result in results.items():
        by_id, by_series_x = defaultdict(list), defaultdict(list)
        rows = result.get('items', result.get('_evidence', {}).get('records', []))
        for index, row in enumerate(rows):
            if 'id' in row:
                by_id[row['id']].append(index)
            identity = row.get('identity', {})
            series, x = (row.get(k, identity.get(k)) for k in ('series', 'x'))
            if isinstance(series, str) and type(x) in (str, int, float):
                by_series_x[series, x].append(index)
        indexes[part] = (rows, by_id, by_series_x)
    for source, entry in scope['inventory'].items():
        if entry['action'] != 'read':
            continue
        for table_id in entry['tables']:
            table = scope['tables'][table_id]
            bindings = cell_bindings(table, table_id)
            for address, ref in bindings.items():
                key = f"{table['sheet']}!{address}"
                part = ref['part']
                if scope['cards'][part]['source'] != source or part not in results:
                    raise ValueError(f'{key}: use a completed part from the same source figure')
                selector = {k: v for k, v in ref.items() if k in ('id', 'series', 'x')}
                if not selector or ('id' not in selector and set(selector) != {'series', 'x'}):
                    raise ValueError(f'{key}: identify reading by stable ID or series and X')
                role = ref.get('role', 'value')
                result = results[part]
                rows, by_id, by_series_x = indexes[part]
                candidates = by_id[selector['id']] if 'id' in selector else by_series_x[selector['series'], selector['x']]
                matches = [i for i in candidates if all(
                    rows[i].get(k, rows[i].get('identity', {}).get(k)) == v for k, v in selector.items())]
                if len(matches) != 1:
                    raise ValueError(f'{key}: reading reference has {len(matches)} matches')
                index = matches[0]
                if (part, index, role) in used:
                    raise ValueError(f'{key}: reading already bound to another cell')
                used.add((part, index, role))
                row = rows[index]
                if 'roles' in row:
                    reading = row['roles'][role]
                    status = reading['status']
                    if status == 'explicit_missing':
                        item = missing.get(key, {})
                        src = result['sources'][result['panels'][row['panel']]['source']]
                        bbox = item.get('bbox', [])
                        roi = row['roi']
                        if (item.get('sha256') != src['sha256'] or len(bbox) != 4 or
                                item.get('reason') != (reading.get('review') or {}).get('note') or
                                not (roi[0] <= bbox[0] < bbox[2] <= roi[2] and
                                     roi[1] <= bbox[1] < bbox[3] <= roi[3])):
                            raise ValueError(f'{key}: missing evidence must match this reading and source region')
                    elif status != 'reviewed' or reading.get('pixel') is None:
                        raise ValueError(f'{key}: reading is not visually resolved')
                else:
                    interval = row.get('pdf_window')
                    window_evidence = (
                        result.get('_evidence', {}).get('method') == 'vector_window_stats'
                        and row.get('status') == 'complete' and row.get('statistic') == 'mean'
                        and interval and interval['x'][0] < interval['x'][1]
                        and interval['path_indices']
                    )
                    if role != 'value' or (row.get('pdf_xy') is None and not window_evidence):
                        raise ValueError(f'{key}: vector reading requires a source coordinate')
                    reading = row
                decimals = table.get('decimals')
                if decimals is not None and (type(decimals) is not int or not 0 <= decimals <= 15):
                    raise ValueError(f'{key}: invalid frozen decimal precision')
                values[key] = {'value': reading['value'],
                               'tolerance': 1e-8 if decimals is None else 0.5 * 10 ** -decimals + 1e-10}
    return values


def check_delivery(scope, path, evidence, results=None, printed=None):
    missing = evidence.get('missing', {})
    for key, item in missing.items():
        required_text(item.get('reason'), f'{key}: missing-value reason')
        if item['kind'] not in ('source_unreadable', 'source_uncovered') or sha(item['image']) != item['sha256']:
            raise ValueError(f'{key}: missing value requires unchanged source image and source criterion')
        from PIL import Image
        with Image.open(item['image']) as image:
            left, top, right, bottom = item['bbox']
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError(f'{key}: missing-value evidence outside source')
    point_values = None if results is None else bound_readings(scope, results, missing)
    if printed is not None:
        point_values = {**(point_values or {}), **printed}
    inspect_workbook(path, scope['tables'], final=True, missing=missing, point_values=point_values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope')
    parser.add_argument('pdf')
    parser.add_argument('--workbook')
    parser.add_argument('--evidence')
    args = parser.parse_args()
    try:
        scope = read(args.scope)
        printed = validate_scope(scope, args.pdf)
        if args.workbook:
            check_delivery(scope, args.workbook, read(args.evidence) if args.evidence else {}, printed=printed)
        tables = scope['tables'].values()
        print(json.dumps({'ok': True, 'figures': sum(e['kind'] == 'figure' for e in scope['inventory'].values()),
                          'source_tables': sum(e['kind'] == 'table' for e in scope['inventory'].values()),
                          'tables': len(tables), 'data_columns': sum(len(t['columns']) for t in tables),
                          'data_cells': sum(len(t['columns']) * len(t['rows']) for t in tables),
                          'scope': 'structure_printed_values_and_missing_evidence' if args.workbook else 'frozen_scope'}))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
