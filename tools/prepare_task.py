"""PDF-only preparation; sparse decisions -> existing fixed extraction runner.

python tools/prepare_task.py paper.pdf [--out package] [--reuse old_task.json]
python tools/prepare_task.py build package
python tools/prepare_task.py configure package --part ID --method vector_lines
python tools/prepare_task.py ocr package --part ID --image figure.png --bounds L T R B
python tools/prepare_task.py ocr-apply package --part ID --decision confirmation.json
python tools/prepare_task.py trial package
python tools/prepare_task.py status package

Local OCR assistance is explicit; no model calls, inferred identities or workbook writes.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time

import fitz
from scan_pages import caption_matches, scan_page
from run_task import (Runner, METHODS, EXTERNAL, REPO, TOOLS, digest, inside,
                      read, readonly_tools, sha, write)
from review_points import axis_fit, axis_forward
from extraction_gate import PRINTED_NUMBER, printed_token
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string, get_column_letter

def utc():
    return datetime.now(timezone.utc).isoformat()


def absolute(value, base):
    return str((Path(base) / value).resolve())


def default_output(pdf):
    cwd = Path.cwd()
    for root in (cwd, *cwd.parents):
        if (root / '00_项目入口.md').is_file():
            return root / '90_历史归档' / 'Agent生成过程' / 'PDF任务准备' / f'{pdf.stem}_{sha(pdf)[:10]}'
    return cwd / 'figure_tasks' / f'{pdf.stem}_{sha(pdf)[:10]}'


def check_package(root):
    root = Path(root).resolve()
    package = read(root / 'package.json')
    if sha(package['pdf']) != package['pdf_sha256']:
        raise ValueError('source PDF changed; prepare a new package')
    for rel, expected in package['files'].items():
        path = root / rel
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f'prepared evidence changed or missing: {rel}')
    return root, package


def normalize_job(job, base, pdf):
    job = copy.deepcopy(job)
    if 'pdf' in job:
        if absolute(job['pdf'], base) != str(pdf):
            raise ValueError('reused job PDF does not match source')
        job['pdf'] = str(pdf)
    for src in job.get('sources', {}).values():
        src['image'] = absolute(src['image'], base)
    for key in ('export', 'review_image'):
        if key in job:
            job[key] = absolute(job[key], base)
    job.pop('prefix', None)
    return job


def input_hashes(job):
    paths = [s['image'] for s in job.get('sources', {}).values()]
    paths += [job[k] for k in ('export', 'review_image') if k in job]
    return {p: sha(p) for p in paths}


def prepare(pdf, out=None, reuse=()):
    start = time.perf_counter()
    pdf = Path(pdf).resolve()
    if not pdf.is_file():
        raise ValueError('PDF missing')
    root = Path(out).resolve() if out else default_output(pdf)
    if inside(root, REPO) or inside(REPO, root):
        raise ValueError('package must be separate from tool repository')
    reuse_paths = list(reuse)
    if not reuse_paths and (root / 'package.json').exists():
        reuse_paths = list(read(root / 'package.json')['request']['reuse'])
    request = {'pdf': str(pdf), 'pdf_sha256': sha(pdf),
               'generator': digest({p.name: sha(p) for p in
                    (Path(__file__), TOOLS / 'scan_pages.py', TOOLS / 'calib.py')}),
               'reuse': {str(Path(p).resolve()): sha(p) for p in reuse_paths}}
    if (root / 'package.json').exists():
        _, existing = check_package(root)
        if existing['request'] != request:
            raise ValueError('preparation inputs changed: use a new output directory')
        return {'ok': True, 'cached': True, 'package': str(root),
                'operation_seconds': round(time.perf_counter() - start, 3), **status(root)}
    if root.exists() and any(root.iterdir()):
        raise ValueError('nonempty output retained; choose a new directory')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'pages').mkdir()
    (root / 'images').mkdir()
    created = utc()
    pages, candidates, warnings, page_texts = [], [], [], {}
    decisions = {'version': 1, 'parts': []}
    with fitz.open(pdf) as doc:
        if doc.needs_pass:
            raise ValueError('encrypted PDF needs an accessible source')
        for index, page in enumerate(doc):
            number = index + 1
            scan = scan_page(page)
            blocks = [b for b in page.get_text('blocks') if b[6] == 0]
            text = '\n'.join(b[4].strip() for b in blocks)
            page_texts[number] = text
            text_path = root / 'pages' / f'p{number:03}.txt'
            text_path.write_text(text, encoding='utf-8')
            captions = list(caption_matches(blocks))
            sparse = len(re.sub(r'\s', '', text)) < 40
            if sparse:
                warnings.append({'page': number, 'reason': 'text_sparse_OCR_or_visual_needed'})
            assets = []
            seen = set()
            for im in scan['images']:
                if im['xref'] in seen or min(im['px']) < 64:
                    continue
                seen.add(im['xref'])
                raw = doc.extract_image(im['xref'])
                asset = root / 'images' / f'p{number:03}_xref{im["xref"]}.{raw["ext"]}'
                asset.write_bytes(raw['image'])
                assets.append({'image': str(asset), 'size': im['px'],
                               'placements_pt': im['placements_pt']})
            preview = None
            if captions or scan['images'] or scan['drawing_count'] or sparse:
                target = root / 'pages' / f'p{number:03}.png'
                page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False).save(target)
                preview = str(target)
            pages.append({'page': number, 'text': str(text_path), 'preview': preview,
                          'assets': assets, 'captions': captions, 'scan': scan})
            page_candidates = captions or ([{'kind': 'unidentified', 'label': str(number),
                'text': '', 'bbox_pt': None}] if assets or scan['drawing_count'] or sparse else [])
            for n, cap in enumerate(page_candidates):
                cid = f'p{number:03}_{cap["kind"]}_{cap["label"]}_{n}'
                candidate = {'id': cid, 'page': number, **cap, 'preview': preview,
                             'assets': assets, 'text': str(text_path), 'context': []}
                candidates.append(candidate)
                decisions['parts'].append({'id': cid, 'candidate_id': cid, 'action': 'pending',
                    'method': None, 'scope': {'series': [], 'sampling': ''},
                    'conditions': {}, 'parameters': {}, 'note': ''})
        # Return only snippets for caption references; full page text stays on disk.
        for c in candidates:
            if c['kind'] == 'unidentified':
                continue
            prefix = r'(?:Fig(?:ure)?\.?|图)' if c['kind'] == 'figure' else r'(?:Table|表)'
            pattern = re.compile(prefix + r'\s*' + re.escape(c['label']) + r'(?![\w])', re.I)
            for p in pages:
                text = page_texts[p['page']]
                hits = list(pattern.finditer(text))
                if hits:
                    c['context'].append({'page': p['page'], 'text': p['text'],
                        'snippets': [text[max(0, h.start()-120):h.end()+360] for h in hits[:3]]})
    reused = []
    for manifest in reuse_paths:
        manifest = Path(manifest).resolve()
        old = read(manifest)
        old_pdf = Path(absolute(old['pdf'], manifest.parent))
        if sha(old_pdf) != request['pdf_sha256']:
            raise ValueError('reuse rejected: different PDF content')
        for part in old['parts']:
            full_path = Path(absolute(part['job'], manifest.parent))
            trial_path = Path(absolute(part['trial_job'], manifest.parent))
            full = normalize_job(read(full_path), full_path.parent, old_pdf)
            trial = normalize_job(read(trial_path), trial_path.parent, old_pdf)
            if 'pdf' in full: full['pdf'] = str(pdf)
            if 'pdf' in trial: trial['pdf'] = str(pdf)
            pid = f'reuse_{len(reused)+1}_{part["id"]}'
            if len(pid) > 48: pid = f'reuse_{len(reused)+1}'
            decision = {'id': pid, 'candidate_id': None, 'action': 'pending',
                'method': part['method'], 'scope': copy.deepcopy(part['scope']),
                'conditions': {}, 'job': full, 'trial_job': trial,
                'note': 'Imported parameters only; identity/sampling and visual approvals not inherited.'}
            decisions['parts'].append(decision)
            reused.append({'id': pid, 'manifest': str(manifest),
                           'input_hashes': {**input_hashes(full), **input_hashes(trial)}})
    write(root / 'inventory.json', {'pages': pages, 'candidates': candidates, 'warnings': warnings})
    write(root / 'decisions.json', decisions)
    (root / 'review.md').write_text('# 待确认的图表候选\n\n'
        '候选不等于完整图表清单；扫描件需OCR或视觉定位。先查表格/正文及补充材料。\n'
        '只修改 decisions.json；未知项保留 pending。程序不会自动确认图例或研究口径。\n\n'
        + '\n'.join(f'- {c["id"]}: PDF第{c["page"]}页，{c["kind"]} {c["label"]}；'
                     f'{c["text"][:160].replace(chr(10), " ")}\n  预览：{c["preview"]}' for c in candidates), encoding='utf-8')
    files = {str(p.relative_to(root)): sha(p) for p in root.rglob('*')
             if p.is_file() and p.name != 'decisions.json'}
    write(root / 'package.json', {'version': 1, **request, 'request': request,
        'created_at': created, 'prepare_seconds': time.perf_counter()-start,
        'files': files, 'reused': reused})
    return {'ok': True, 'cached': False, 'package': str(root), **status(root)}


def compact_job(part, pdf, page, base):
    method, p = part['method'], part['parameters']
    samples = p.get('sample_at', [])
    if not samples or any(type(x) not in (int, float) or not math.isfinite(x) for x in samples) or len(set(samples)) != len(samples):
        raise ValueError('nonempty finite unique sample_at required')
    axes = {}
    for coordinate in ('x', 'y'):
        axis = {'coordinate': coordinate, 'ticks': p[f'{coordinate}_ticks'],
                'unit': p.get('x_unit' if coordinate == 'x' else 'unit', ''),
                'kind': p.get(f'{coordinate}_kind', 'linear')}
        for key in ('check_ticks', 'max_residual_px'):
            if f'{coordinate}_{key}' in p:
                axis[key] = p[f'{coordinate}_{key}']
        axes[coordinate] = axis
    xfit = axis_fit(axes['x'])
    axis_fit(axes['y'])
    if method == 'vector_lines':
        job = {'pdf': str(pdf), 'page': page, 'figure': part['candidate_id'],
               'conditions': part['conditions'], 'frame': p['frame'],
               **axes,
               'sample_at': p['sample_at']}
        for key in ('color_map', 'series_styles', 'insets'):
            if key in p: job[key] = p[key]
        names = list(p.get('color_map', {}).values()) or [s['name'] for s in p.get('series_styles', [])]
        if set(names) != set(part['scope']['series']):
            raise ValueError('series mapping must match declared scope')
        return job
    if method in ('bitmap_points', 'manual_points'):
        bounds = p['bounds']
        if any(type(v) not in (float,int) or not math.isfinite(v) or int(v) != v for v in bounds):
            raise ValueError('bitmap bounds must use integer source-pixel coordinates')
        bounds = [int(v) for v in bounds]
        if set(s['name'] for s in p['series']) != set(part['scope']['series']):
            raise ValueError('series selectors must match declared scope')
        items = []
        for si, series in enumerate(p['series']):
            for xi, x in enumerate(p['sample_at']):
                axis_value = axis_forward(x, xfit['kind'])
                column = round((axis_value-xfit['intercept'])/xfit['slope'])
                left, top, right, bottom = bounds
                if not left < column < right:
                    raise ValueError('sampling column requires room on both sides inside bounds')
                items.append({'id': f's{si}_x{xi}', 'panel': 'p',
                    'identity': {**part['conditions'], 'series': series['name'], 'x': x,
                                 'figure': part['candidate_id'], 'pdf_page': page},
                    'anchor': [column, (top+bottom)/2],
                    'roi': [column-1, top, column+1, bottom],
                    'selector': series['selector'], 'neighbor_px': 0,
                    'limitations': ['Ink-run centers are curve candidates, not bar tips or confirmed series.']})
        return {'schema_version': 1, 'sources': {'s': {'image': absolute(p['image'], base)}},
                'gate_axes': {'x': axes['x']},
                'panels': {'p': {'source': 's', 'bounds': bounds, 'axis': axes['y']}}, 'items': items}
    raise ValueError('compact template supports vector_lines/bitmap_points; use existing job for other methods')


def configure(root, part_id, method=None, bounds=None):
    """Select an imported job, or fill an empty part from a bounded template."""
    root, package = check_package(root)
    decisions = read(root / 'decisions.json')
    part = next(p for p in decisions['parts'] if p['id'] == part_id)
    if method:
        if part.get('parameters') or 'job' in part:
            raise ValueError('existing parameters retained; edit key fields instead of overwriting')
        inv = read(root / 'inventory.json')
        c = next(c for c in inv['candidates'] if c['id'] == part['candidate_id'])
        page = next(p for p in inv['pages'] if p['page'] == c['page'])
        selection = None
        if method == 'auto':
            if decisions.get('gate'):
                from extraction_gate import validate_scope
                scope = read(absolute(decisions['gate'], root))
                entries = [entry for entry in scope['inventory'].values()
                           if (entry['page'], entry['kind'], str(entry.get('label', '')).casefold()) ==
                           (c['page'], c['kind'], str(c['label']).casefold())]
                if len(entries) == 1 and entries[0]['action'] == 'transcribe':
                    verified = validate_scope(scope, package['pdf'])
                    names = {scope['tables'][key]['sheet'] for key in entries[0]['tables']}
                    count = sum(key.rsplit('!', 1)[0] in names for key in verified)
                    if not count:
                        raise ValueError('direct source has no verified numeric bindings')
                    return {'ok': True, 'part': part_id, 'method': 'printed_source',
                            'verified_cells': count, 'source_page': c['page'],
                            'next': 'Build the workbook using the frozen printed bindings.'}
            if c['kind'] == 'table':
                raise ValueError('printed tables use printed-grid and verified printed bindings')
            selection = route_panel(root, c['page'], bounds)
            if selection['geometry'] == 'vector_first':
                method = 'vector_lines'
            elif selection['geometry'] == 'native_bitmap_first' and len(selection['images']) == 1:
                method = 'bitmap_points'
            else:
                raise ValueError(f"{selection['geometry']}: confirm target series geometry before choosing a method")
        if method == 'vector_lines':
            p = {'frame': page['scan']['frames'][0] if len(page['scan']['frames']) == 1 else None,
                 'x_ticks': [], 'y_ticks': [], 'sample_at': [], 'color_map': {}}
        elif method in ('bitmap_points', 'manual_points'):
            p = {'image': c['assets'][0]['image'] if len(c['assets']) == 1 else None,
                 'bounds': None, 'x_ticks': [], 'x_unit': '', 'y_ticks': [], 'unit': '',
                 'sample_at': [], 'series': []}
        else:
            raise ValueError('compact templates cover vector_lines/bitmap_points; other methods use existing job')
        if selection is not None:
            if method == 'vector_lines':
                p['frame'] = bounds
            else:
                xref = selection['images'][0]['xref']
                assets = [a['image'] for a in c['assets'] if Path(a['image']).stem.endswith(f'_xref{xref}')]
                if len(assets) != 1:
                    raise ValueError('target bitmap has no unique prepared native image')
                p['image'] = assets[0]
            part['method_selection'] = selection
        part.update(method=method, parameters=p)
    elif 'job' not in part and not part.get('parameters'):
        raise ValueError('choose a template method or provide an existing job')
    part['action'] = 'extract'
    before = sha(root / 'decisions.json')
    backup = root / 'decision_history' / f'{before}.json'
    backup.parent.mkdir(exist_ok=True)
    if not backup.exists():
        backup.write_bytes((root / 'decisions.json').read_bytes())
    write(root / 'decisions.json', decisions)
    return {'ok': True, 'part': part_id, 'method': part['method'],
            'method_selection': part.get('method_selection'), 'decisions': str(root / 'decisions.json'),
            'next': 'Confirm scope and fill key parameters; build rejects incomplete fields.'}


def build(root, require_gate=False):
    start = time.perf_counter()
    root, package = check_package(root)
    decisions = read(root / 'decisions.json')
    if require_gate and not decisions.get('gate'):
        raise ValueError('set decisions.json gate to the frozen inventory, baseline and figure cards')
    inventory = read(root / 'inventory.json')
    candidates = {c['id']: c for c in inventory['candidates']}
    parts, jobs, inputs, pending = [], {}, {}, []
    ocr_checks, method_selections = {}, {}
    workbook = decisions.get('workbook')
    ids = set()
    for part in decisions['parts']:
        pid = part['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', pid) or pid in ids:
            raise ValueError('unique safe part IDs required')
        ids.add(pid)
        action = part['action']
        if action != 'extract':
            if action not in ('pending', 'reference', 'defer'):
                raise ValueError('action must be pending/extract/reference/defer')
            if action != 'pending' and not part.get('note', '').strip():
                raise ValueError('reference/defer requires an evidence note')
            pending.append({'id': pid, 'action': action, 'note': part.get('note', '')})
            continue
        if part.get('method') not in METHODS or not part['scope']['series'] or not part['scope']['sampling'].strip():
            raise ValueError(f'{pid}: method, series and actual sampling required')
        if len(set(part['scope']['series'])) != len(part['scope']['series']):
            raise ValueError('duplicate series in scope')
        evidence = part.get('conditions', {}).get('ocr_calibration_evidence')
        if evidence:
            from ocr_assist import confirmed
            if sha(evidence['analysis']) != evidence['analysis_sha256'] or sha(evidence['confirmation']) != evidence['confirmation_sha256']:
                raise ValueError('OCR confirmation evidence changed; reconfirm current source')
            c = confirmed(evidence['analysis'], evidence['confirmation'])
            recorded_checks = part.get('conditions', {}).get('ocr_independent_tick_checks')
            if recorded_checks is not None and recorded_checks != {
                'status': c['independent_tick_status'],
                'x': c['x_check_ticks'], 'y': c['y_check_ticks']
            }:
                raise ValueError('OCR independent tick checks changed; reconfirm current source')
            for key in ('x_ticks','y_ticks','unit'):
                if c[key] and c[key] != part['parameters'].get(key):
                    raise ValueError('OCR-applied calibration changed; reconfirm the correction')
            ocr_checks[pid] = {'x': c['x_check_ticks'], 'y': c['y_check_ticks']}
        candidate = candidates.get(part.get('candidate_id'))
        if selection := part.get('method_selection'):
            if not candidate or selection != route_panel(root, candidate['page'], selection['bounds_pt']):
                raise ValueError(f'{pid}: panel method evidence differs from the source PDF')
            method_selections[pid] = {'method': part['method'], 'evidence': selection}
        if 'job_file' in part:
            if not decisions.get('gate') or part['method'] != 'review_candidates' or 'job' in part or 'trial_job' in part:
                raise ValueError(f'{pid}: job_file requires a gated candidate import with one job')
            path = Path(absolute(part['job_file'], root))
            inputs[str(path)] = sha(path)
            full = normalize_job(read(path), path.parent, Path(package['pdf']))
        elif 'job' in part:
            full = normalize_job(part['job'], root, Path(package['pdf']))
        elif candidate:
            full = compact_job(part, Path(package['pdf']), candidate['page'], root)
        else:
            raise ValueError(f'{pid}: candidate_id or explicit job required')
        trial = normalize_job(part.get('trial_job', full), root, Path(package['pdf']))
        full = normalize_job(full, root, Path(package['pdf']))
        if selection:
            if selection['geometry'] == 'vector_first':
                if (part['method'] != 'vector_lines' or full.get('frame') != selection['bounds_pt']
                        or trial.get('frame') != selection['bounds_pt']):
                    raise ValueError(f'{pid}: reroute the changed vector frame or method')
            else:
                xref = selection['images'][0]['xref']
                assets = {absolute(a['image'], root) for a in candidate['assets']
                          if Path(a['image']).stem.endswith(f'_xref{xref}')}
                if (part['method'] != 'bitmap_points' or len(assets) != 1
                        or any({src['image'] for src in job['sources'].values()} != assets
                               for job in (full, trial))):
                    raise ValueError(f'{pid}: reroute the changed bitmap source or method')
        for job in (full, trial): inputs.update(input_hashes(job))
        jobs[pid] = (full, trial)
        parts.append({'id': pid, 'method': part['method'], 'job': f'{pid}_full.json',
                      'trial_job': f'{pid}_trial.json', 'scope': part['scope']})
    gate = None
    printed = {}
    printed_sources = {}
    if 'gate' in decisions:
        from extraction_gate import check_cards, check_configured_coverage, check_job, validate_scope
        gate_path = absolute(decisions['gate'], root)
        scope = read(gate_path)
        printed = validate_scope(scope, package['pdf'])
        printed_sources = {source_id: {'page': entry['page'], 'kind': entry['kind'],
                                      'label': entry.get('label'), 'tables': entry['tables']}
                           for source_id, entry in scope['inventory'].items()
                           if entry['action'] == 'transcribe'}
        source_keys = {(r['page'], r['kind'], str(r['label']).casefold())
                       for r in printed_sources.values() if r['label'] is not None}
        completed_candidates = {c['id'] for c in candidates.values()
                                if (c['page'], c['kind'], str(c['label']).casefold()) in source_keys}
        covered_pending = {p['id'] for p in decisions['parts'] if p.get('candidate_id') in completed_candidates}
        pending = [p for p in pending if p['id'] not in covered_pending]
        check_cards(scope, parts, allow_subset=not workbook)
        for part in parts:
            card = scope['cards'][part['id']]
            for axis, checks in ocr_checks.get(part['id'], {}).items():
                if any(tick not in card['axes'][axis]['check_ticks'] for tick in checks):
                    raise ValueError(f"{part['id']}: frozen {axis} checks omit confirmed OCR evidence")
            for job in jobs[part['id']]:
                check_job(card, job, part['method'], bind=True)
                check_configured_coverage(scope, part['id'], job, part['method'])
        gate = {'file': gate_path, 'sha256': sha(gate_path)}
        inputs[gate_path] = gate['sha256']
    if not parts and not (printed and workbook):
        raise ValueError('no extraction part or source table workbook ready; fill key fields in decisions.json')
    if workbook:
        if set(workbook) != {'plan', 'checks', 'audit'}:
            raise ValueError('workbook requires plan/checks/audit paths')
        workbook = {k: absolute(v, root) for k, v in workbook.items()}
        inputs.update({p: sha(p) for p in workbook.values()})
    for old in package['reused']:
        if old['id'] in jobs:
            for path, h in old['input_hashes'].items():
                if sha(path) != h:
                    raise ValueError('reused image/export changed; prepare a new package')
    # Binding all tool files prevents reopening an old run after tool maintenance.
    from run_task import code_files
    fingerprint = digest({'decisions': decisions, 'inputs': inputs,
        'pdf': package['pdf_sha256'], 'tools': {str(p.relative_to(TOOLS)): sha(p) for p in code_files()}})
    folder = root / 'built' / fingerprint
    if folder.exists():
        record = read(folder / 'build.json')
        if any(not Path(p).is_file() or sha(p) != h for p, h in record['files'].items()):
            raise ValueError('compiled configuration changed; no silent overwrite')
    else:
        folder.mkdir(parents=True)
        for pid, (full, trial) in jobs.items():
            write(folder / f'{pid}_full.json', full)
            write(folder / f'{pid}_trial.json', trial)
        task = {'version': 1, 'pdf': package['pdf'], 'work_dir': 'run', 'parts': parts}
        if gate:
            task.update(version=2, gate=gate)
        if workbook: task['workbook'] = workbook
        write(folder / 'task.json', task)
        write(folder / 'scope.json', {'selected': [p['id'] for p in parts], 'remaining': pending,
              'printed_sources': printed_sources,
              'method_selections': method_selections,
              'warning': 'Compiled scope is not whole-paper completion; pending candidates remain visible.'})
        write(folder / 'build.json', {'files': {str(p): sha(p) for p in folder.iterdir() if p.is_file()}})
    write(root / 'latest.json', {'task': str(folder / 'task.json'), 'fingerprint': fingerprint})
    return {'ok': True, 'task': str(folder / 'task.json'), 'selected_parts': len(parts),
            'remaining_candidates': len(pending), 'operation_seconds': round(time.perf_counter()-start, 3)}


def status(root):
    root, package = check_package(root)
    inventory, decisions = read(root / 'inventory.json'), read(root / 'decisions.json')
    result = {'pages': len(inventory['pages']), 'candidates': len(inventory['candidates']),
        'text_sparse_pages': [w['page'] for w in inventory['warnings']],
        'prepare_seconds': round(package['prepare_seconds'], 3),
        'elapsed_since_prepare_seconds_including_wait': round((datetime.now(timezone.utc)
             - datetime.fromisoformat(package['created_at'])).total_seconds(), 3),
        'decisions': str(root / 'decisions.json'), 'inventory': str(root / 'inventory.json'),
        'pending_parts': sum(p['action'] == 'pending' for p in decisions['parts']),
        'next': 'Fill key fields in decisions.json, then build; visual approval remains required.'}
    if (root / 'latest.json').is_file(): result['latest'] = read(root / 'latest.json')
    return result


def ocr_labels(root, part_id, image=None, bounds=None, engine='auto', language=None):
    from ocr_assist import recognize
    root, _ = check_package(root)
    decisions = read(root / 'decisions.json')
    part = next(p for p in decisions['parts'] if p['id'] == part_id)
    p = part.get('parameters', {})
    image = image or p.get('image')
    bounds = bounds or p.get('bounds')
    if not image:
        inventory = read(root / 'inventory.json')
        c = next(c for c in inventory['candidates'] if c['id'] == part['candidate_id'])
        if len(c['assets']) != 1:
            raise ValueError('choose source image explicitly: multiple/no original image assets; page preview may use different coordinates')
        image = c['assets'][0]['image']
    image = absolute(image, root)
    result = recognize(image, root / 'ocr', bounds, engine, language)
    index = read(root / 'ocr_links.json') if (root / 'ocr_links.json').is_file() else {}
    index[part_id] = {'analysis': result['analysis'], 'image': image, 'sha256': sha(image)}
    write(root / 'ocr_links.json', index)
    return result


def apply_ocr(root, part_id, decision):
    from ocr_assist import confirmed
    root, _ = check_package(root)
    link = read(root / 'ocr_links.json')[part_id]
    c = confirmed(link['analysis'], decision)
    if c['image'] != link['image'] or c['source_sha256'] != link['sha256']:
        raise ValueError('OCR source/part mismatch')
    decisions = read(root / 'decisions.json')
    part = next(p for p in decisions['parts'] if p['id'] == part_id)
    if part['method'] not in ('bitmap_points', 'manual_points') or 'job' in part:
        raise ValueError('automatic OCR merge supports compact bitmap_points; other jobs use confirmed config explicitly')
    p = part['parameters']
    if p.get('image') and absolute(p['image'], root) != c['image']:
        raise ValueError('configured image differs from OCR source')
    p['image'] = c['image']
    if not p.get('bounds') and c['bounds']: p['bounds'] = c['bounds']
    for key in ('x_ticks', 'y_ticks', 'unit', 'x_check_ticks', 'y_check_ticks'):
        if c[key]: p[key] = c[key]
    # OCR names do not establish pen style/selector or the requested series scope.
    part['conditions']['ocr_confirmed_series_labels'] = c['series_labels']
    part['conditions']['ocr_calibration_evidence'] = c['evidence']
    part['conditions']['ocr_independent_tick_checks'] = {
        'status': c['independent_tick_status'], 'x': c['x_check_ticks'], 'y': c['y_check_ticks']}
    before = sha(root / 'decisions.json')
    backup = root / 'decision_history' / f'{before}.json'; backup.parent.mkdir(exist_ok=True)
    if not backup.exists(): backup.write_bytes((root / 'decisions.json').read_bytes())
    write(root / 'decisions.json', decisions)
    return {'ok': True, 'part': part_id, 'decisions': str(root / 'decisions.json'),
            'x_ticks': len(c['x_ticks']), 'y_ticks': len(c['y_ticks']), 'unit': c['unit'],
            'independent_tick_status': c['independent_tick_status'],
            'next': 'Confirm pen/series mapping, sampling and remaining fields before trial.'}


def trial(root, require_gate=False):
    start = time.perf_counter()
    compiled = build(root, require_gate=require_gate)
    replies = []
    with readonly_tools():
        runner = Runner(compiled['task'])
        for part in runner.parts:
            reply = runner.dispatch({'action': 'trial', 'part': part['id']})
            replies.append({'part': part['id'], **reply})
        metrics = {k: runner.state[k] for k in ('computer_seconds', 'executions', 'cache_hits')}
    ok = all(r['ok'] for r in replies)
    result = {'ok': ok, 'task': compiled['task'], 'parts': replies,
        'operation_seconds': round(time.perf_counter()-start, 3), 'metrics': metrics,
        'status': 'unreviewed_candidates' if ok else 'incomplete',
        'next': 'View returned check images; approve only after actual review.' if ok
                else 'Inspect failed parts; correct configuration or use a supported method. No automatic fallback.'}
    write(Path(root) / 'trial_summary.json', result)
    return result


def route_panel(root, page_number, bounds):
    """Choose a first geometry pass from objects inside the supplied panel."""
    root, package = check_package(root)
    if not page_number or not bounds:
        raise ValueError('route requires --page and --bounds in PDF points')
    with fitz.open(package['request']['pdf']) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError('PDF page outside source')
        page = document[page_number - 1]
        panel = fitz.Rect(bounds)
        if not panel.is_valid or panel.is_empty or not (page.rect * page.derotation_matrix).contains(panel):
            raise ValueError('panel bounds outside PDF page')
        images = []
        image_boxes = []
        paints = page.get_bboxlog()
        for info in page.get_image_info(xrefs=True):
            image_box = fitz.Rect(info['bbox'])
            overlap = image_box & panel
            if not overlap.is_empty:
                image_boxes.append(image_box)
                images.append({'xref': info['xref'], 'intersection_pt': list(overlap),
                               'size_px': [info['width'], info['height']],
                               'image_bounds_pt': list(image_box),
                               'paint_order_candidates': [seq for seq, (kind, box) in enumerate(paints)
                                                          if kind == 'fill-image' and fitz.Rect(box) == image_box]})
        # Axis rules can have zero height or width. Only non-degenerate paths
        # with a footprint in both dimensions count as vector evidence here.
        vectors = []
        frames = []
        backdrops = []
        axis_aligned = []
        image_paints = [(seq, fitz.Rect(box)) for seq, (kind, box) in enumerate(paints)
                        if kind == 'fill-image' and any(fitz.Rect(box) == image for image in image_boxes)]
        for drawing in page.get_drawings():
            rect = drawing['rect']
            overlap = rect & panel
            if not overlap.is_empty:
                item = {'intersection_pt': list(overlap), 'segments': len(drawing['items']),
                        'path_bounds_pt': list(rect), 'paint_order': drawing['seqno'],
                        'paint_type': drawing['type']}
                rectangle = len(drawing['items']) == 1 and drawing['items'][0][0] == 're'
                if (rectangle and drawing['type'] == 'f' and drawing.get('fill') == (1., 1., 1.)
                        and any(drawing['seqno'] < seq and rect.contains(box) for seq, box in image_paints)):
                    backdrops.append(item)
                elif (drawing['type'] == 's' and rectangle
                        and drawing['items'][0][0] == 're'
                        and any(all(abs(a-b) <= .75 for a,b in zip(rect, box))
                                for box in (panel, *image_boxes))):
                    frames.append(item)
                else:
                    vectors.append(item)
            elif rect.width == 0 < rect.height and panel.x0 <= rect.x0 <= panel.x1:
                top, bottom = max(rect.y0, panel.y0), min(rect.y1, panel.y1)
                if top < bottom:
                    axis_aligned.append([rect.x0, top, rect.x1, bottom])
            elif rect.height == 0 < rect.width and panel.y0 <= rect.y0 <= panel.y1:
                left, right = max(rect.x0, panel.x0), min(rect.x1, panel.x1)
                if left < right:
                    axis_aligned.append([left, rect.y0, right, rect.y1])
        if vectors and images:
            geometry = 'mixed_check_vector_coverage'
        elif vectors:
            geometry = 'vector_first'
        elif images:
            geometry = 'native_bitmap_first'
        elif axis_aligned:
            geometry = 'axis_aligned_check_identity'
        else:
            geometry = 'no_geometry_found'
        printed_percent_labels = []
        for x0, y0, x1, y1, text, *_ in page.get_text('words', clip=panel, sort=True):
            token = printed_token(text)
            if token.endswith('%') and PRINTED_NUMBER.fullmatch(token):
                printed_percent_labels.append({'text': text, 'bbox_pt': [x0, y0, x1, y1]})
        return {'ok': True, 'page': page_number, 'bounds_pt': bounds, 'geometry': geometry,
                'source_priority': ['verified_published_data', 'panel_geometry'],
                'printed_percent_labels': printed_percent_labels,
                'images': images, 'vector_paths': vectors, 'matching_frame_paths': frames,
                'bitmap_backdrop_paths': backdrops,
                'axis_aligned_paths': axis_aligned}


def printed_grid(root, page_number, bounds, destination, row_edges=None, column_edges=None):
    """Propose gate-ready bindings for a selected printed table region."""
    _, package = check_package(root)
    if not page_number or not bounds or not destination:
        raise ValueError('printed-grid requires --page, --bounds and --cell')
    first_col, first_row = coordinate_from_string(destination)
    first_col = column_index_from_string(first_col)
    with fitz.open(package['request']['pdf']) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError('PDF page outside source')
        page = document[page_number - 1]
        region = fitz.Rect(bounds)
        if region.is_empty or not (page.rect * page.derotation_matrix).contains(region):
            raise ValueError('table bounds outside PDF page')
        if row_edges or column_edges:
            if not row_edges or not column_edges:
                raise ValueError('supply both row and column edges')
            for edges, lo, hi in ((row_edges, region.y0, region.y1),
                                  (column_edges, region.x0, region.x1)):
                if len(edges) < 2 or not all(lo <= a < b <= hi for a, b in zip(edges, edges[1:])):
                    raise ValueError('table edges must be ordered inside bounds')
            rows = [[(x0,y0,x1,y1) for x0,x1 in zip(column_edges,column_edges[1:])]
                    for y0,y1 in zip(row_edges,row_edges[1:])]
        else:
            tables = page.find_tables(clip=region).tables
            if len(tables) != 1:
                raise ValueError('select one ruled table, or provide explicit row and column edges')
            rows = [row.cells for row in tables[0].rows]
        bindings, unresolved = {}, []
        for y, row in enumerate(rows, first_row):
            for x, box in enumerate(row, first_col):
                address = f'{get_column_letter(x)}{y}'
                text = page.get_text('text', clip=box).strip() if box else ''
                if box and PRINTED_NUMBER.fullmatch(printed_token(text)):
                    bindings[address] = {'page': page_number, 'bbox': list(box), 'text': text, 'mode': 'text'}
                else:
                    unresolved.append({'cell': address, 'text': text, 'bbox': list(box) if box else None})
    return {'ok': True, 'page': page_number, 'printed': bindings, 'unresolved': unresolved}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', help='PDF path, or build/trial/status')
    ap.add_argument('package', nargs='?')
    ap.add_argument('--out')
    ap.add_argument('--reuse', action='append', default=[])
    ap.add_argument('--part')
    ap.add_argument('--page', type=int)
    ap.add_argument('--cell')
    ap.add_argument('--row-edges', type=float, nargs='+')
    ap.add_argument('--column-edges', type=float, nargs='+')
    ap.add_argument('--method')
    ap.add_argument('--image')
    ap.add_argument('--bounds', type=float, nargs=4)
    ap.add_argument('--engine', default='auto')
    ap.add_argument('--language')
    ap.add_argument('--decision')
    ap.add_argument('--legacy-ungated', action='store_true', help='replay a historical package without the new gates')
    args = ap.parse_args()
    try:
        if args.input in ('build', 'trial', 'status', 'configure', 'ocr', 'ocr-apply', 'route', 'printed-grid'):
            if not args.package: raise ValueError('package directory required')
            if args.input == 'printed-grid':
                reply = printed_grid(args.package, args.page, args.bounds, args.cell,
                                     args.row_edges, args.column_edges)
            elif args.input == 'route':
                reply = route_panel(args.package, args.page, args.bounds)
            elif args.input == 'configure':
                reply = configure(args.package, args.part, args.method, args.bounds)
            elif args.input == 'ocr':
                reply = ocr_labels(args.package, args.part, args.image, args.bounds, args.engine, args.language)
            elif args.input == 'ocr-apply':
                reply = apply_ocr(args.package, args.part, args.decision)
            elif args.input in ('build', 'trial'):
                reply = {'build': build, 'trial': trial}[args.input](
                    args.package, require_gate=not args.legacy_ungated)
            else:
                reply = status(args.package)
        else:
            reply = prepare(args.input, args.out, args.reuse)
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if reply.get('ok', True) else 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
