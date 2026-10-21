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
from scan_pages import scan_page
from run_task import (Runner, METHODS, EXTERNAL, REPO, TOOLS, digest, inside,
                      read, readonly_tools, sha, write)
from review_points import axis_fit

CAPTION = re.compile(r'^\s*(Figure|Fig\.?|Table|图|表)\s*([0-9]+|[IVXLCDM]+)'
                     r'(?:\s*[.:：](?:\s|$)|\s*$)', re.I)


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
            captions = []
            for b in blocks:
                # A caption may share a text block with the preceding paragraph.
                for line in b[4].splitlines():
                    match = CAPTION.match(line)
                    if match:
                        kind = 'table' if match[1].lower() in ('table', '表') else 'figure'
                        captions.append({'kind': kind, 'label': match[2],
                                         'text': b[4].strip(), 'bbox_pt': list(b[:4])})
                        break
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
    if method == 'vector_lines':
        from calib import fit_axis
        fit_axis([tuple(t) for t in p['x_ticks']])
        fit_axis([tuple(t) for t in p['y_ticks']])
        job = {'pdf': str(pdf), 'page': page, 'figure': part['candidate_id'],
               'conditions': part['conditions'], 'frame': p['frame'],
               'x': {'ticks': p['x_ticks']}, 'y': {'ticks': p['y_ticks']},
               'sample_at': p['sample_at']}
        for key in ('color_map', 'series_styles'):
            if key in p: job[key] = p[key]
        names = list(p.get('color_map', {}).values()) or [s['name'] for s in p.get('series_styles', [])]
        if set(names) != set(part['scope']['series']):
            raise ValueError('series mapping must match declared scope')
        return job
    if method == 'bitmap_points':
        bounds = p['bounds']
        if any(type(v) not in (float,int) or not math.isfinite(v) or int(v) != v for v in bounds):
            raise ValueError('bitmap bounds must use integer source-pixel coordinates')
        bounds = [int(v) for v in bounds]
        xaxis = axis_fit({'coordinate': 'x', 'ticks': p['x_ticks'], 'unit': p['x_unit']})
        axis = {'coordinate': 'y', 'ticks': p['y_ticks'], 'unit': p['unit']}
        axis_fit(axis)
        if set(s['name'] for s in p['series']) != set(part['scope']['series']):
            raise ValueError('series selectors must match declared scope')
        items = []
        for si, series in enumerate(p['series']):
            for xi, x in enumerate(p['sample_at']):
                column = round((x-xaxis['intercept'])/xaxis['slope'])
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
                'panels': {'p': {'source': 's', 'bounds': bounds, 'axis': axis}}, 'items': items}
    raise ValueError('compact template supports vector_lines/bitmap_points; use existing job for other methods')


def configure(root, part_id, method=None):
    """Select an imported job, or fill an empty part from a bounded template."""
    root, _ = check_package(root)
    decisions = read(root / 'decisions.json')
    part = next(p for p in decisions['parts'] if p['id'] == part_id)
    if method:
        if part.get('parameters') or 'job' in part:
            raise ValueError('existing parameters retained; edit key fields instead of overwriting')
        inv = read(root / 'inventory.json')
        c = next(c for c in inv['candidates'] if c['id'] == part['candidate_id'])
        page = next(p for p in inv['pages'] if p['page'] == c['page'])
        if method == 'vector_lines':
            p = {'frame': page['scan']['frames'][0] if len(page['scan']['frames']) == 1 else None,
                 'x_ticks': [], 'y_ticks': [], 'sample_at': [], 'color_map': {}}
        elif method == 'bitmap_points':
            p = {'image': c['assets'][0]['image'] if len(c['assets']) == 1 else None,
                 'bounds': None, 'x_ticks': [], 'x_unit': '', 'y_ticks': [], 'unit': '',
                 'sample_at': [], 'series': []}
        else:
            raise ValueError('compact templates cover vector_lines/bitmap_points; other methods use existing job')
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
    return {'ok': True, 'part': part_id, 'decisions': str(root / 'decisions.json'),
            'next': 'Confirm scope and fill key parameters; build rejects incomplete fields.'}


def build(root):
    start = time.perf_counter()
    root, package = check_package(root)
    decisions = read(root / 'decisions.json')
    inventory = read(root / 'inventory.json')
    candidates = {c['id']: c for c in inventory['candidates']}
    parts, jobs, inputs, pending = [], {}, {}, []
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
            for key in ('x_ticks','y_ticks','unit'):
                if c[key] and c[key] != part['parameters'].get(key):
                    raise ValueError('OCR-applied calibration changed; reconfirm the correction')
        candidate = candidates.get(part.get('candidate_id'))
        if 'job' in part:
            full = normalize_job(part['job'], root, Path(package['pdf']))
        elif candidate:
            full = compact_job(part, Path(package['pdf']), candidate['page'], root)
        else:
            raise ValueError(f'{pid}: candidate_id or explicit job required')
        trial = normalize_job(part.get('trial_job', full), root, Path(package['pdf']))
        full = normalize_job(full, root, Path(package['pdf']))
        for job in (full, trial): inputs.update(input_hashes(job))
        jobs[pid] = (full, trial)
        parts.append({'id': pid, 'method': part['method'], 'job': f'{pid}_full.json',
                      'trial_job': f'{pid}_trial.json', 'scope': part['scope']})
    if not parts:
        raise ValueError('no extraction part ready; fill key fields in decisions.json')
    workbook = decisions.get('workbook')
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
        if workbook: task['workbook'] = workbook
        write(folder / 'task.json', task)
        write(folder / 'scope.json', {'selected': [p['id'] for p in parts], 'remaining': pending,
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
    if part['method'] != 'bitmap_points' or 'job' in part:
        raise ValueError('automatic OCR merge supports compact bitmap_points; other jobs use confirmed config explicitly')
    p = part['parameters']
    if p.get('image') and absolute(p['image'], root) != c['image']:
        raise ValueError('configured image differs from OCR source')
    p['image'] = c['image']
    if not p.get('bounds') and c['bounds']: p['bounds'] = c['bounds']
    for key in ('x_ticks', 'y_ticks', 'unit'):
        if c[key]: p[key] = c[key]
    # OCR names do not establish pen style/selector or the requested series scope.
    part['conditions']['ocr_confirmed_series_labels'] = c['series_labels']
    part['conditions']['ocr_calibration_evidence'] = c['evidence']
    before = sha(root / 'decisions.json')
    backup = root / 'decision_history' / f'{before}.json'; backup.parent.mkdir(exist_ok=True)
    if not backup.exists(): backup.write_bytes((root / 'decisions.json').read_bytes())
    write(root / 'decisions.json', decisions)
    return {'ok': True, 'part': part_id, 'decisions': str(root / 'decisions.json'),
            'x_ticks': len(c['x_ticks']), 'y_ticks': len(c['y_ticks']), 'unit': c['unit'],
            'next': 'Confirm pen/series mapping, sampling and remaining fields before trial.'}


def trial(root):
    start = time.perf_counter()
    compiled = build(root)
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', help='PDF path, or build/trial/status')
    ap.add_argument('package', nargs='?')
    ap.add_argument('--out')
    ap.add_argument('--reuse', action='append', default=[])
    ap.add_argument('--part')
    ap.add_argument('--method')
    ap.add_argument('--image')
    ap.add_argument('--bounds', type=float, nargs=4)
    ap.add_argument('--engine', default='auto')
    ap.add_argument('--language')
    ap.add_argument('--decision')
    args = ap.parse_args()
    try:
        if args.input in ('build', 'trial', 'status', 'configure', 'ocr', 'ocr-apply'):
            if not args.package: raise ValueError('package directory required')
            if args.input == 'configure':
                reply = configure(args.package, args.part, args.method)
            elif args.input == 'ocr':
                reply = ocr_labels(args.package, args.part, args.image, args.bounds, args.engine, args.language)
            elif args.input == 'ocr-apply':
                reply = apply_ocr(args.package, args.part, args.decision)
            else:
                reply = {'build': build, 'trial': trial, 'status': status}[args.input](args.package)
        else:
            reply = prepare(args.input, args.out, args.reuse)
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if reply.get('ok', True) else 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
