"""Drag a mis-digitized point, then redraw. No handwritten JSON or pixel math.

python tools/drag_points.py task.json show --part panel_a
python tools/drag_points.py task.json zoom --part panel_a 3
python tools/drag_points.py task.json zoom --part panel_a --box 100,40,180,120 --panel a
python tools/drag_points.py task.json move --part panel_a 3 --pixel 120 64
python tools/drag_points.py task.json move --part panel_a 3 --data 12.5
python tools/drag_points.py task.json add --part panel_a new-id --panel a --series Observed --pixel 30 45
python tools/drag_points.py task.json delete --part panel_a 4
python tools/drag_points.py task.json undo --part panel_a
python tools/drag_points.py task.json copy-series --part panel_a --from "50 mg/L" --to "100 mg/L" --to "150 mg/L"

Each command prints one JSON object. `overlay` is the picture to look at.
`table` / `csv` are the live point table (not the delivery workbook).
Grid labels and `pixel` are source-image pixels, top-left origin, y down.
`--data` moves only the calibrated axis. A move must land inside the overlay
you just looked at; otherwise zoom or show that region first.

The same fields work as one JSON line to `run_task.py task.json serve`:
{"action":"drag","part":"panel_a","command":"move","target":"3","data":12.5}
Do not run this CLI against a work_dir while that serve process is mid-command.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import re
import sys
import time
from pathlib import Path

from openpyxl import Workbook

from review_points import data_to_pixel, inside, pair, point_catalog, render_numbered_overlay, summary
from run_task import Runner, read, sha, write, readonly_tools


FIELDS = {
    'show': set(),
    'zoom': {'target', 'box', 'panel', 'pad', 'zoom'},
    'move': {'target', 'pixel', 'data', 'x', 'y', 'role'},
    'add': {'target', 'pixel', 'data', 'x', 'y', 'panel', 'series', 'role'},
    'delete': {'target', 'role'},
    'undo': set(),
    'copy-series': {'source', 'targets'},
}

TABLE_NOTE = (
    'Live digitizing table. pixel_x and pixel_y are source-image pixels '
    '(top-left origin, y down). value is the reading on the single linear axis '
    'named in the axis column, not a second coordinate. This file is rewritten '
    'after show, zoom, move, add, delete, and undo. It is not the delivery workbook; '
    'publishing still uses accept, draft, and save. A correction is a visual '
    'self-check, not an independent validation.'
)


def finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a finite number')
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f'{label} must be a finite number') from None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def reject_extra(request, command):
    extra = set(request) - {'action', 'part', 'command'} - FIELDS[command]
    if extra:
        raise ValueError(f"unexpected fields for {command}: {', '.join(sorted(extra))}")


def part_or_raise(runner, part_id):
    try:
        return runner.part(part_id)
    except StopIteration:
        raise ValueError(f'unknown part {part_id}') from None


def integer_box(bounds):
    box = [int(math.floor(bounds[0])), int(math.floor(bounds[1])),
           int(math.floor(bounds[2])), int(math.floor(bounds[3]))]
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('panel box is empty')
    return box


def parse_box(value):
    parts = value.split(',') if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)) or len(parts) != 4:
        raise ValueError('box needs four source pixels: x0,y0,x1,y1')
    return [finite(part, 'box') for part in parts]


def check_zoom_size(box):
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        raise ValueError('zoom box is smaller than 8 source pixels')
    if box[2] - box[0] > 220 or box[3] - box[1] > 220:
        raise ValueError('zoom covers at most 220 source pixels per side; use a smaller --pad or --box')


def clip_pad(panel_bounds, raw):
    x0 = max(math.floor(panel_bounds[0]), math.floor(min(raw[0], raw[2])))
    y0 = max(math.floor(panel_bounds[1]), math.floor(min(raw[1], raw[3])))
    x1 = min(math.floor(panel_bounds[2]), math.ceil(max(raw[0], raw[2])))
    y1 = min(math.floor(panel_bounds[3]), math.ceil(max(raw[1], raw[3])))
    box = [int(x0), int(y0), int(x1), int(y1)]
    check_zoom_size(box)
    return box


def explicit_box(spec, panels):
    if 'panel' not in spec:
        raise ValueError('zoom --box also needs --panel')
    if spec['panel'] not in panels:
        raise ValueError(f"unknown panel {spec['panel']}")
    box = parse_box(spec['box'])
    bounds = panels[spec['panel']]['bounds']
    if not (bounds[0] <= box[0] < box[2] <= bounds[2] and bounds[1] <= box[1] < box[3] <= bounds[3]):
        shown = [round(float(v), 1) for v in bounds]
        raise ValueError(f'box is outside panel {spec["panel"]} bounds {shown}; coordinates are source pixels, y down')
    pixel = [int(math.floor(box[0])), int(math.floor(box[1])),
             int(math.ceil(box[2])), int(math.ceil(box[3]))]
    pixel[2] = min(pixel[2], int(math.floor(bounds[2])))
    pixel[3] = min(pixel[3], int(math.floor(bounds[3])))
    check_zoom_size(pixel)
    return [{'panel': spec['panel'], 'bounds': pixel}]


def pad_value(spec):
    pad = finite(spec.get('pad', 40), 'pad')
    if not 8 <= pad <= 120:
        raise ValueError('pad must be between 8 and 120 source pixels')
    return pad


def zoom_value(spec):
    if 'zoom' not in spec:
        return None
    zoom = finite(spec['zoom'], 'zoom')
    if not 1 <= zoom <= 8:
        raise ValueError('zoom must be between 1 and 8')
    return zoom


def resolve_target(rows, target, role=None):
    if target is None or not str(target).strip():
        raise ValueError('point number or id required')
    token = str(target).strip()
    if token.isdigit():
        hits = [row for row in rows if row['n'] == int(token)]
    elif '::' in token:
        ident, role_name = token.split('::', 1)
        hits = [row for row in rows if row['id'] == ident and row['role'] == role_name]
    else:
        hits = [row for row in rows if row['id'] == token and (role is None or row['role'] == role)]
        if role is None and len({row['role'] for row in hits}) > 1:
            raise ValueError(f'{token} has more than one role; pass --role or the point number')
    if len(hits) != 1:
        raise ValueError(f'unknown point {token}; use n or id from the latest show')
    return hits[0]


def new_ident(token):
    if not isinstance(token, str) or not token.strip() or any(ch.isspace() for ch in token):
        raise ValueError('new point id must be a nonempty string without spaces')
    token = token.strip()
    if token.isdigit():
        raise ValueError('new point id cannot be only digits; those are point numbers')
    if len(token) > 80:
        raise ValueError('point id is longer than 80 characters')
    return token


def role_name(spec):
    role = spec.get('role') or 'value'
    if not isinstance(role, str) or not role.strip():
        raise ValueError('role must be a nonempty string')
    return role.strip()


def destination_pixel(spec, base, calibration):
    if 'pixel' in spec and ('data' in spec or 'x' in spec or 'y' in spec):
        raise ValueError('pass --pixel alone, or --data with the uncalibrated axis, not both')
    if 'pixel' in spec:
        return pair(spec['pixel'])
    axis = calibration['coordinate']
    if 'data' in spec and axis == 'y' and 'y' in spec:
        raise ValueError('y is the calibrated axis; pass --data for it, not --y')
    if 'data' in spec and axis == 'x' and 'x' in spec:
        raise ValueError('x is the calibrated axis; pass --data for it, not --x')
    point = pair(base)
    if 'x' in spec:
        point[0] = finite(spec['x'], 'x')
    if 'y' in spec:
        point[1] = finite(spec['y'], 'y')
    if 'data' in spec:
        point = data_to_pixel(point, calibration, spec['data'])
    elif 'x' not in spec and 'y' not in spec:
        raise ValueError('move needs --pixel X Y or --data VALUE')
    return point


def add_pixel(spec, calibration):
    if 'pixel' in spec:
        if any(key in spec for key in ('data', 'x', 'y')):
            raise ValueError('add --pixel sets both source coordinates; do not also pass --data, --x, or --y')
        return pair(spec['pixel'])
    if 'data' not in spec:
        raise ValueError('add needs --pixel X Y, or --data VALUE plus the other source pixel (--x or --y)')
    axis = calibration['coordinate']
    other = 'y' if axis == 'x' else 'x'
    if other not in spec:
        raise ValueError(f'add --data moves the {axis} axis; also pass --{other} as a source pixel')
    if axis == 'y' and 'y' in spec:
        raise ValueError('y is calibrated; pass --data, not --y')
    if axis == 'x' and 'x' in spec:
        raise ValueError('x is calibrated; pass --data, not --x')
    point = [finite(spec['x'], 'x') if 'x' in spec else 0.0,
             finite(spec['y'], 'y') if 'y' in spec else 0.0]
    return data_to_pixel(point, calibration, spec['data'])


def mark_visible(rows, boxes):
    for row in rows:
        loc = row['pixel'] or row['anchor']
        row['in_view'] = any(row['panel'] == box['panel'] and inside(loc, box['bounds']) for box in boxes)


def write_tables(folder, rows, meta):
    folder.mkdir(parents=True, exist_ok=True)
    fields = ['n', 'id', 'role', 'panel', 'series', 'pixel_x', 'pixel_y', 'value', 'unit',
              'axis', 'status', 'in_view', 'coincident_with', 'identity']
    csv_path = folder / 'points.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            pixel = row['pixel'] or [None, None]
            writer.writerow({
                'n': row['n'], 'id': row['id'], 'role': row['role'], 'panel': row['panel'],
                'series': row['series'], 'pixel_x': pixel[0], 'pixel_y': pixel[1],
                'value': row['value'], 'unit': row['unit'], 'axis': row['axis'],
                'status': row['status'], 'in_view': row['in_view'],
                'coincident_with': row.get('coincident_with') or '',
                'identity': json.dumps(row['identity'], ensure_ascii=False),
            })
    xlsx_path = folder / 'points.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = 'points'
    sheet.append(fields)
    for row in rows:
        pixel = row['pixel'] or [None, None]
        sheet.append([row['n'], row['id'], row['role'], row['panel'], row['series'],
                      pixel[0], pixel[1], row['value'], row['unit'], row['axis'],
                      row['status'], bool(row['in_view']), row.get('coincident_with') or '',
                      json.dumps(row['identity'], ensure_ascii=False)])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip('ABCDEFGHIJKLMN', (6, 24, 14, 12, 18, 12, 12, 14, 10, 8, 22, 12, 28, 40)):
        sheet.column_dimensions[column].width = width
    note = book.create_sheet('note')
    note['A1'] = TABLE_NOTE
    note['A3'] = meta.get('overlay')
    note['A4'] = meta.get('revision')
    note.column_dimensions['A'].width = 120
    book.save(xlsx_path)
    return str(xlsx_path), str(csv_path)


def observation_views(rows, boxes):
    views = []
    for box in boxes:
        views.append({'id': None, 'role': None, 'panel': box['panel'], 'bounds': list(box['bounds'])})
        for row in rows:
            loc = row['pixel'] or row['anchor']
            if row['panel'] == box['panel'] and inside(loc, box['bounds']):
                views.append({'id': row['id'], 'role': row['role'], 'panel': box['panel'],
                              'bounds': list(box['bounds'])})
    return views


def publish(runner, part_id, result, revision, boxes, zoom, command, record=None, warning=None):
    boxes = [{'panel': box['panel'], 'bounds': [int(v) for v in box['bounds']]} for box in boxes]
    rows = point_catalog(result)
    mark_visible(rows, boxes)
    folder = runner.output(f'point_views/{part_id}/{time.time_ns()}')
    folder.mkdir(parents=True, exist_ok=True)
    image_path = folder / 'overlay.png'
    placement = render_numbered_overlay(result, image_path, boxes, rows, zoom)
    image_hash = sha(image_path)
    views = observation_views(rows, boxes)
    view_path = folder / 'view.json'
    write(view_path, {
        'version': 1, 'revision': revision, 'job_sha256': result['job_sha256'],
        'images': {str(image_path): image_hash}, 'views': views, 'view_mode': 'points',
    })
    table, csv_path = write_tables(runner.output(f'drag/{part_id}'), rows, {
        'overlay': str(image_path), 'revision': revision,
    })
    if record is not None:
        state = runner.state['parts'][part_id]
        original = state.setdefault('original_check_images', list(record['images']))
        record['images'] = list(original) + [str(image_path)]
        record['files'][str(image_path)] = image_hash
    runner.state.setdefault('drag', {})[part_id] = {
        'view': str(view_path), 'boxes': boxes, 'zoom': zoom,
    }
    sample = next((row['n'] for row in rows if row['in_view']), rows[0]['n'] if rows else 1)
    task = str(runner.manifest)
    tool = 'python tools/drag_points.py'
    payload = {
        'command': command, 'part': part_id, 'overlay': str(image_path), 'table': table, 'csv': csv_path,
        'revision': revision, 'summary': summary(result), 'points': rows, 'view_bounds': boxes,
        'grids': [{'panel': item['panel'], 'step': item['grid_step'], 'bounds': item['bounds']}
                  for item in placement['panels']],
        'placement': placement,
        'axes': {name: {'axis': panel['calibration']['coordinate'], 'unit': panel['calibration'].get('unit')}
                 for name, panel in result['panels'].items()},
        'extraction_executions': runner.state['executions'],
        'coordinates': 'source pixels, top-left origin, y down',
        'scope': 'visual self-check; drag updates the live point table, not the delivery workbook',
        'note': ('Grid labels and pixel fields are source-image pixels, not PNG pixels. '
                 '--data moves only the calibrated axis. Look at overlay, then send one command. '
                 'Numbers are reassigned after add or delete; ids stay put.'),
        'next': [
            f'{tool} {task} show --part {part_id}',
            f'{tool} {task} zoom --part {part_id} {sample}',
            f'{tool} {task} move --part {part_id} {sample} --pixel X Y',
            f'{tool} {task} move --part {part_id} {sample} --data VALUE',
            f'{tool} {task} undo --part {part_id}',
        ],
    }
    if warning:
        payload['warning'] = warning
    return payload


def session(runner, part_id):
    return runner.state.setdefault('drag', {}).get(part_id) or {}


def require_overlay(runner, part_id, revision):
    info = session(runner, part_id)
    view_path = info.get('view')
    if not view_path or not Path(view_path).is_file():
        raise ValueError('no overlay yet; run show or zoom and look at the image before move, add, or delete')
    observation = read(view_path)
    if observation.get('revision') != revision:
        raise ValueError('overlay is stale; run show or zoom again and look at the new image')
    if any(sha(path) != digest for path, digest in observation.get('images', {}).items()):
        raise ValueError('overlay file changed; run show or zoom again')
    return info, observation, view_path


def covered(observation, panel, pixel):
    return any(view.get('panel') == panel and view.get('bounds') and inside(pixel, view['bounds'])
               for view in observation['views'])


def suggest_box(pixel, bounds):
    pad = 36
    x0 = max(math.floor(bounds[0]), math.floor(pixel[0] - pad))
    y0 = max(math.floor(bounds[1]), math.floor(pixel[1] - pad))
    x1 = min(math.floor(bounds[2]), math.ceil(pixel[0] + pad))
    y1 = min(math.floor(bounds[3]), math.ceil(pixel[1] + pad))
    return f'{int(x0)},{int(y0)},{int(x1)},{int(y1)}'


def region_for(command, request, result, panels):
    if command == 'show':
        return [{'panel': name, 'bounds': integer_box(panel['bounds'])} for name, panel in panels.items()], None
    if ('target' in request) == ('box' in request):
        raise ValueError('zoom needs a point number or id, or --box x0,y0,x1,y1 with --panel')
    zoom = zoom_value(request)
    if 'box' in request:
        if 'pad' in request:
            raise ValueError('use --pad when zooming on a point, or --box on its own')
        return explicit_box(request, panels), zoom
    rows = point_catalog(result)
    row = resolve_target(rows, request.get('target'), request.get('role'))
    loc = row['pixel'] or row['anchor']
    box = clip_pad(panels[row['panel']]['bounds'],
                   [loc[0] - pad_value(request), loc[1] - pad_value(request),
                    loc[0] + pad_value(request), loc[1] + pad_value(request)])
    return [{'panel': row['panel'], 'bounds': box}], zoom


def left_view(rows, ident, role):
    row = next((item for item in rows if item['id'] == ident and item['role'] == role), None)
    if row and not row['in_view']:
        return f"point {row['n']} {ident} is outside the current crop; run show or zoom {row['n']}"
    return None


def series_id(name):
    slug = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')
    if not slug:
        raise ValueError('series name needs at least one letter or digit')
    return slug


def copy_coincident(result, source_name, target_names):
    """Repeat one series onto legend series that share the same marks.

    Values are copied, not remeasured. Each copy is its own row and is flagged
    coincident_with the other series. This does not merge series or drop one.
    """
    if not isinstance(source_name, str) or not source_name.strip():
        raise ValueError('copy-series needs --from, the series you already measured')
    source_name = source_name.strip()
    if not target_names or not isinstance(target_names, (list, tuple)):
        raise ValueError('copy-series needs at least one --to series from the legend')
    targets = []
    for name in target_names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError('each --to series needs a name')
        name = name.strip()
        if name in targets:
            raise ValueError(f'duplicate --to series {name}')
        targets.append(name)
    if source_name in targets:
        raise ValueError('a series cannot be copied onto itself')
    present = {(item.get('identity') or {}).get('series') for item in result['items']}
    already = [name for name in targets if name in present]
    if already:
        raise ValueError('series already present, not overwritten: ' + ', '.join(already))
    sources = [item for item in result['items'] if (item.get('identity') or {}).get('series') == source_name]
    if not sources:
        raise ValueError(f'no points with series {source_name}')
    ids = {item['id'] for item in result['items']}
    group = [source_name, *targets]
    created = []
    for name in targets:
        others = [item for item in group if item != name]
        for item in sources:
            clone = copy.deepcopy(item)
            clone['id'] = f"{item['id']}__{series_id(name)}"
            if clone['id'] in ids:
                raise ValueError(f"point id {clone['id']} already exists")
            ids.add(clone['id'])
            clone['identity'] = {**(item.get('identity') or {}), 'series': name}
            clone['added_by_edit'] = True
            clone['coincident_with'] = others
            flags = list(clone.get('flags') or [])
            flags.append('coincident_with=' + ';'.join(others))
            clone['flags'] = flags
            result['items'].append(clone)
            created.append(clone['id'])
    for item in sources:
        others = [name for name in group if name != source_name]
        item['coincident_with'] = others
        flags = list(item.get('flags') or [])
        flags.append('coincident_with=' + ';'.join(others))
        item['flags'] = flags
    result['history'].append({
        'kind': 'copy_coincident_series',
        'source': source_name,
        'targets': targets,
        'ids': created,
        'note': 'same measured point kept once per legend series; not a second reading',
    })
    result['summary'] = summary(result)
    return created


def perform_drag(runner, request):
    command = request.get('command')
    if command not in FIELDS:
        raise ValueError('drag command must be show, zoom, move, add, delete, or undo')
    part_id = request.get('part')
    if not isinstance(part_id, str) or not part_id.strip():
        raise ValueError('part required')
    reject_extra(request, command)
    part = part_or_raise(runner, part_id)
    record = runner.current(part, 'run')
    job = Path(record['folder']) / 'candidates' / 'review_job.json'
    if not job.is_file():
        raise ValueError(
            'this part has no bitmap point candidates to drag. '
            'Environmental-science figures are usually scans or embedded bitmaps; '
            'an empty vector extraction is not a reason to write a new extractor. '
            'Use bitmap_points or bitmap_lines, or review_candidates, then show / move / add / delete.'
        )
    _state, _prior, _jp, _j, _sources, panels, result, revision = runner.load_points(part, job)
    if command in ('show', 'zoom'):
        boxes, zoom = region_for(command, request, result, panels)
        return publish(runner, part_id, result, revision, boxes, zoom, command)
    if command == 'copy-series':
        copy_coincident(result, request.get('source'), request.get('targets'))
        target = runner.output(f'results/{part_id}_{time.time_ns()}.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target, result)
        new_revision = sha(target)
        state = runner.state['parts'][part_id]
        state.setdefault('point_undo', []).append({'previous': _prior, 'affected': [
            {'id': item['id'], 'role': role} for item in result['items'] if item.get('added_by_edit')
            for role in item['roles']]})
        state['result'] = {'file': str(target), 'sha256': new_revision}
        state.pop('pending_review', None)
        state.pop('run_approval', None)
        boxes, zoom = region_for('show', request, result, panels)
        return publish(runner, part_id, result, new_revision, boxes, zoom, command, record=record)
    if command == 'undo':
        decision = runner.output(f'drag/{part_id}/decision.json')
        decision.parent.mkdir(parents=True, exist_ok=True)
        write(decision, {'base_revision': revision, 'note': 'drag undo'})
        outcome = runner.point_action('undo', part, record, job, str(decision), None, None, None, redraw=False)
        restored = read(outcome['result']['file'])
        info = session(runner, part_id)
        boxes = info.get('boxes') or [{'panel': name, 'bounds': integer_box(panel['bounds'])}
                                      for name, panel in restored['panels'].items()]
        return publish(runner, part_id, restored, outcome['revision'], boxes, info.get('zoom'), command, record=record)
    return edit_one(runner, part, record, job, part_id, request, result, revision, panels)


def edit_one(runner, part, record, job, part_id, request, result, revision, panels):
    command = request['command']
    info, observation, view_path = require_overlay(runner, part_id, revision)
    rows = point_catalog(result)
    if command == 'add':
        ident = new_ident(request.get('target'))
        if any(row['id'] == ident for row in rows):
            raise ValueError('point id already exists; use move')
        panel_name = request.get('panel')
        if panel_name not in panels:
            raise ValueError('add requires --panel, one of: ' + ', '.join(panels))
        series = request.get('series')
        if not isinstance(series, str) or not series.strip():
            raise ValueError('add requires --series, the legend name you are assigning')
        role = role_name(request)
        point = add_pixel(request, panels[panel_name]['calibration'])
        if not inside(point, panels[panel_name]['bounds']) or not covered(observation, panel_name, point):
            hint = suggest_box(point, panels[panel_name]['bounds'])
            raise ValueError(f'destination is outside the current overlay; look there first: zoom --box {hint} --panel {panel_name}')
        note = f"drag add {ident} {role} at source pixel {point[0]:.4g},{point[1]:.4g}"
        op = {'op': 'add', 'id': ident, 'role': role, 'panel': panel_name,
              'identity': {'series': series.strip()}, 'pixel': [point[0], point[1]], 'note': note}
        focus = (ident, role)
    else:
        if 'target' not in request:
            raise ValueError('point number or id required')
        row = resolve_target(rows, request.get('target'), request.get('role'))
        shown = {(view.get('id'), view.get('role')) for view in observation['views']}
        if (row['id'], row['role']) not in shown:
            raise ValueError(f"point {row['n']} {row['id']} is not on the current overlay; run zoom {row['n']} or show")
        if command == 'delete':
            note = f"drag delete {row['id']} {row['role']}"
            op = {'op': 'delete', 'id': row['id'], 'role': row['role'], 'note': note}
        else:
            calibration = panels[row['panel']]['calibration']
            point = destination_pixel(request, row['pixel'] or row['anchor'], calibration)
            if not inside(point, panels[row['panel']]['bounds']) or not covered(observation, row['panel'], point):
                hint = suggest_box(point, panels[row['panel']]['bounds'])
                raise ValueError(
                    'destination is outside the current overlay; '
                    f'look there first: zoom --box {hint} --panel {row["panel"]}')
            note = (f"drag move {row['id']} {row['role']} to source pixel {point[0]:.4g},{point[1]:.4g}")
            if 'data' in request:
                note += f" (data {float(request['data']):.6g} on {calibration['coordinate']})"
            op = {'op': 'move', 'id': row['id'], 'role': row['role'], 'pixel': [point[0], point[1]], 'note': note}
        focus = (row['id'], row['role'])
    decision = runner.output(f'drag/{part_id}/decision.json')
    decision.parent.mkdir(parents=True, exist_ok=True)
    write(decision, {'base_revision': revision, 'images': observation['images'], 'viewed': True, 'note': note, 'ops': [op]})
    outcome = runner.point_action('edit', part, record, job, str(decision), str(view_path), None, None, redraw=False)
    updated = read(outcome['result']['file'])
    kept = session(runner, part_id)
    payload = publish(runner, part_id, updated, outcome['revision'], kept['boxes'], kept.get('zoom'),
                      command, record=record, warning=None)
    warning = left_view(payload['points'], *focus)
    if warning:
        payload['warning'] = warning
    return payload


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--part', required=True)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('task')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show', parents=[common])
    zoom = commands.add_parser('zoom', parents=[common])
    zoom.add_argument('target', nargs='?')
    zoom.add_argument('--box')
    zoom.add_argument('--panel')
    zoom.add_argument('--pad', type=float)
    zoom.add_argument('--zoom', type=float)
    move = commands.add_parser('move', parents=[common])
    move.add_argument('target')
    move.add_argument('--pixel', nargs=2, type=float)
    move.add_argument('--data', type=float)
    move.add_argument('--x', type=float)
    move.add_argument('--y', type=float)
    move.add_argument('--role')
    add = commands.add_parser('add', parents=[common])
    add.add_argument('target')
    add.add_argument('--panel')
    add.add_argument('--series')
    add.add_argument('--pixel', nargs=2, type=float)
    add.add_argument('--data', type=float)
    add.add_argument('--x', type=float)
    add.add_argument('--y', type=float)
    add.add_argument('--role')
    delete = commands.add_parser('delete', parents=[common])
    delete.add_argument('target')
    delete.add_argument('--role')
    commands.add_parser('undo', parents=[common])
    copy_cmd = commands.add_parser('copy-series', parents=[common])
    copy_cmd.add_argument('--from', dest='source', required=True)
    copy_cmd.add_argument('--to', dest='targets', action='append', required=True)
    return parser


def specification(args):
    spec = {'action': 'drag', 'part': args.part, 'command': args.command}

    def put(name, value):
        if value is not None:
            spec[name] = value

    if args.command == 'zoom':
        put('target', args.target)
        put('box', args.box)
        put('panel', args.panel)
        put('pad', args.pad)
        put('zoom', args.zoom)
    elif args.command == 'move':
        put('target', args.target)
        if args.pixel is not None:
            spec['pixel'] = [float(args.pixel[0]), float(args.pixel[1])]
        put('data', args.data)
        put('x', args.x)
        put('y', args.y)
        put('role', args.role)
    elif args.command == 'add':
        put('target', args.target)
        put('panel', args.panel)
        put('series', args.series)
        if args.pixel is not None:
            spec['pixel'] = [float(args.pixel[0]), float(args.pixel[1])]
        put('data', args.data)
        put('x', args.x)
        put('y', args.y)
        put('role', args.role)
    elif args.command == 'delete':
        put('target', args.target)
        put('role', args.role)
    elif args.command == 'copy-series':
        put('source', args.source)
        spec['targets'] = list(args.targets)
    return spec


def main(argv=None):
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        with readonly_tools():
            runner = Runner(args.task)
            reply = runner.dispatch(specification(args))
    except Exception as exc:
        reply = {'ok': False, 'error': str(exc)}
    print(json.dumps(reply, ensure_ascii=False))
    return 0 if reply.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
