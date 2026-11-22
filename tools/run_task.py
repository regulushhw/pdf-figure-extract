"""Fixed, resumable extraction runner. JSON data in; bounded summaries out.

No eval, shell commands, executable plugins, network calls or model calls.
`serve` holds Windows read-only handles on tool code until quit. This protects
the tool directory during the session, not other Agent tools or the whole OS.
Point corrections use action `drag` or tools/drag_points.py: one command redraws
the numbered overlay and the live point table. No handwritten decision file.
"""
import argparse
import contextlib
import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
EXTRACTORS = {'vector_bars': 'extract_bars.py', 'vector_lines': 'extract_lines.py',
              'vector_window_stats': 'vector_windows.py',
              'vector_points': 'extract_points.py', 'vector_dashes': 'extract_dashes.py',
              'bitmap_points': 'propose_bitmap_points.py', 'bitmap_lines': 'auto_trace_bitmap.py',
              'bitmap_bar_ranges': 'complex_bars.py', 'bitmap_curve_identity': 'complex_curves.py'}
EXTERNAL = {'unplotter_csv', 'chartdetective_csv', 'webplotdigitizer_csv'}
METHODS = set(EXTRACTORS) | EXTERNAL | {'review_candidates', 'manual_points'}
ACTIONS = {'status', 'scan', 'coverage', 'trial', 'approve', 'run', 'inspect', 'edit', 'undo',
           'review', 'apply', 'accept', 'reject', 'draft', 'save', 'verify', 'drag'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def changed_previews(draft_images, paper):
    from PIL import Image, ImageChops

    changed = []
    draft_prefix = f'A{paper}_preview_draft'
    saved_prefix = f'A{paper}_preview_saved'
    for path in sorted(map(Path, draft_images)):
        saved = path.with_name(saved_prefix + path.name.removeprefix(draft_prefix))
        with Image.open(path) as before, Image.open(saved) as after:
            if (before.size != after.size or
                    any(high for _, high in ImageChops.difference(
                        before.convert('RGBA'), after.convert('RGBA')).getextrema())):
                changed.append(str(saved))
    return changed


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def inside(path, parent):
    return Path(path).resolve().is_relative_to(Path(parent).resolve())


def code_files():
    return sorted(p for p in TOOLS.rglob('*') if p.suffix in {'.py', '.mjs', '.ps1'})


@contextlib.contextmanager
def readonly_tools():
    """Windows denies writes/deletes through other handles while worker lives."""
    handles = []
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            for path in code_files():
                handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), 'cannot lock tool', str(path))
                handles.append(handle)
            yield 'windows_write_delete_denied_while_worker_alive'
        finally:
            for handle in handles:
                api.CloseHandle(handle)
    else:
        yield 'hash_check_only_use_external_sandbox_for_write_denial'


def add_bound_changes(plan, readings):
    """Use verified cell bindings as the default draft writes."""
    from openpyxl.utils.cell import coordinate_from_string, get_column_letter, range_boundaries
    changes = plan.setdefault('changes', [])
    pending = dict(readings)
    for change in changes:
        left, top, _, _ = range_boundaries(change['range'])
        for row, values in enumerate(change['values'], top):
            for column, value in enumerate(values, left):
                key = f"{change['sheet']}!{get_column_letter(column)}{row}"
                reading = readings.get(key)
                if reading is None:
                    continue
                expected = reading.get('output_value', reading['value'])
                matches = value == '—' if expected is None else (
                    type(value) in (int, float) and
                    math.isclose(value, expected, rel_tol=0, abs_tol=reading['tolerance']))
                if change.get('formula') or not matches:
                    raise ValueError(f'{key}: planned write conflicts with bound source reading')
                pending.pop(key, None)
    by_row = defaultdict(list)
    for key, reading in pending.items():
        sheet, address = key.rsplit('!', 1)
        _, row = coordinate_from_string(address)
        column = range_boundaries(address)[0]
        value = reading.get('output_value', reading['value'])
        if value is not None and reading.get('decimals') is not None:
            value = round(value, reading['decimals'])
        by_row[sheet, row].append((column, '—' if value is None else value))
    for (sheet, row), cells in sorted(by_row.items()):
        blocks = []
        for column, value in sorted(cells):
            if not blocks or column != blocks[-1][0] + len(blocks[-1][1]):
                blocks.append((column, []))
            blocks[-1][1].append(value)
        for start, values in blocks:
            first = f'{get_column_letter(start)}{row}'
            last = f'{get_column_letter(start + len(values) - 1)}{row}'
            changes.append({'sheet': sheet, 'range': f'{first}:{last}', 'values': [values]})
    formats = plan.setdefault('number_formats', []) if any(
        item.get('decimals') is not None and item['value'] is not None for item in readings.values()) else []
    for key, item in sorted(readings.items()):
        if item.get('decimals') is None or item['value'] is None:
            continue
        sheet, address = key.rsplit('!', 1)
        spec = {'sheet':sheet, 'range':address, 'decimals':item['decimals']}
        if spec not in formats:
            formats.append(spec)


class Runner:
    def __init__(self, manifest):
        self.manifest = Path(manifest).resolve()
        self.task = read(self.manifest)
        allowed = {'version', 'work_dir', 'pdf', 'parts', 'workbook', 'gate'}
        version = self.task.get('version')
        if set(self.task) - allowed or version not in (1, 2) or ('gate' in self.task) != (version == 2):
            raise ValueError('fixed manifest fields required; version 2 requires gate')
        self.base = self.manifest.parent
        self.work = self.path(self.task['work_dir'])
        if inside(self.work, REPO) or inside(REPO, self.work):
            raise ValueError('work_dir must be separate from the tool repository')
        self.pdf = self.path(self.task['pdf'])
        if not self.pdf.is_file():
            raise ValueError('source PDF missing')
        self.parts = self.task['parts']
        ids = [p['id'] for p in self.parts]
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', x) for x in ids):
            raise ValueError('unique safe part IDs required')
        for part in self.parts:
            if set(part) - {'id', 'method', 'job', 'trial_job', 'scope'}:
                raise ValueError('unknown part fields')
            if not part.get('scope', {}).get('series') or not part['scope'].get('sampling'):
                raise ValueError('declare all series and actual sampling before extraction')
        self.scope = None
        self.printed = {}
        if version == 2:
            from extraction_gate import check_cards, validate_scope
            gate = self.task['gate']
            self.gate_path = self.path(gate['file'])
            if sha(self.gate_path) != gate['sha256']:
                raise ValueError('frozen scope changed; prepare a new task')
            self.scope = read(self.gate_path)
            self.printed = validate_scope(self.scope, self.pdf)
            check_cards(self.scope, self.parts, allow_subset='workbook' not in self.task)
        if not ids and not (self.printed and self.task.get('workbook')):
            raise ValueError('requires extraction parts or reviewed printed values for a workbook')
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_path = self.work / 'task_state.json'
        signature = digest(self.task)
        tool_hash = digest({str(p.relative_to(TOOLS)): sha(p) for p in code_files()})
        self.state = read(self.state_path) if self.state_path.exists() else {
            'manifest': signature, 'tools': tool_hash, 'parts': {}, 'cache': {},
            'computer_seconds': 0, 'executions': 0, 'cache_hits': 0}
        if self.state['manifest'] != signature or self.state['tools'] != tool_hash:
            raise ValueError('task definition or tool code changed: use a new work_dir; old evidence retained')

    def path(self, value):
        return (self.base / value).resolve()

    def output(self, value):
        path = (self.work / value).resolve()
        if not inside(path, self.work):
            raise ValueError('output escapes work_dir')
        return path

    def part(self, name):
        return next(p for p in self.parts if p['id'] == name)

    def command(self, script, args, folder, executable=None, env=None):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        command = ([str(executable)] if executable else [sys.executable, '-B']) + [str(TOOLS / script)] + list(map(str, args))
        if executable and str(script).endswith('.ps1'):
            command[1:1] = ['-NoProfile', '-File']
        runtime_env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONINSPECT'}}
        runtime_env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        runtime_env.update(env or {})
        stamp = time.time_ns()
        start = time.perf_counter()
        # Windows CreateProcess rejects very long working directories even when
        # Python can create them. Jobs and workbook work folders use absolute paths.
        run_cwd = TOOLS if os.name == 'nt' and len(str(folder)) >= 240 else folder
        with (folder / f'{stamp}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=run_cwd, env=runtime_env, stdout=log,
                                    stderr=subprocess.STDOUT, shell=False, timeout=300)
        self.state['computer_seconds'] += time.perf_counter() - start
        self.state['executions'] += 1
        if result.returncode:
            log_path = folder / f'{stamp}.log'
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-700:]
            raise ValueError(f'{script} failed: {tail}; full log {log_path}; no automatic fallback')

    def normalized(self, part, trial=False):
        source = self.path(part['trial_job'] if trial else part['job'])
        job = read(source)
        inputs = {str(self.pdf): sha(self.pdf)}
        if 'pdf' in job:
            job['pdf'] = str((source.parent / job['pdf']).resolve())
            if Path(job['pdf']) != self.pdf:
                raise ValueError('job PDF must match task PDF')
        for src in job.get('sources', {}).values():
            src['image'] = str((source.parent / src['image']).resolve())
            inputs[src['image']] = sha(src['image'])
        if part['method'] in EXTERNAL:
            for field in ['export', 'review_image']:
                job[field] = str((source.parent / job[field]).resolve())
                inputs[job[field]] = sha(job[field])
        job.pop('prefix', None)
        if self.scope is not None and part['method'] in METHODS:
            from extraction_gate import check_job
            check_job(self.scope['cards'][part['id']], job, part['method'], bind=True)
        key = digest({'job': job, 'inputs': inputs, 'method': part['method'],
                      'scope': part['scope'], 'tools': self.state['tools']})
        return job, inputs, key

    def valid(self, record):
        return bool(record) and all(Path(p).is_file() and sha(p) == h for p, h in record['files'].items())

    def extract(self, part, trial=False):
        method = part['method']
        if method not in METHODS:
            raise ValueError(f'unsupported method {method}: maintenance required; scope remains incomplete')
        job, inputs, key = self.normalized(part, trial)
        if self.scope is not None:
            from extraction_gate import check_configured_coverage
            check_configured_coverage(self.scope, part['id'], job, method)
        cached = self.state['cache'].get(key)
        if cached:
            if not self.valid(cached):
                raise ValueError('cached evidence changed or missing; do not silently regenerate')
            self.state['cache_hits'] += 1
            return copy.deepcopy(cached)
        folder = self.output(f'artifacts/{key}')
        if folder.exists():
            raise ValueError(f'incomplete artifact directory retained for diagnosis: {folder}')
        folder.mkdir(parents=True)
        job['prefix'] = str(folder / 'data')
        write(folder / 'job.json', job)
        if method in {'review_candidates', 'manual_points'}:
            # Import the existing candidate contract, never rerun an extractor
            # or inherit somebody else's reviewed/accepted status.
            from review_points import load_job
            from PIL import Image,ImageDraw
            if method == 'manual_points':
                for item in job['items']:
                    item['roles'] = {role: [] for role in item.get('roles', [item.get('role', 'value')])}
                    item.pop('origin', None)
            candidate_folder=folder/'candidates';candidate_folder.mkdir()
            candidate=candidate_folder/'review_job.json';write(candidate,job)
            _,_,sources,panels=load_job(candidate)
            for n,(source_id,src) in enumerate(sources.items()):
                with Image.open(src['image']) as image:overview=image.convert('RGB')
                draw=ImageDraw.Draw(overview)
                for item in job['items']:
                    if panels[item['panel']]['source']==source_id:
                        draw.rectangle(item['roi'],outline='red',width=2)
                overview.thumbnail((1100,1100))
                overview.save(folder/f'candidate_overview_{n}.png')
            write(folder/'import_summary.json',{'method':'review_candidates','items':len(job['items']),
                'roles':sum(len(i['roles']) for i in job['items']),
                'status':'unreviewed_candidates','extractor_executed':False,
                'note':'Overview locates imported objects; inspect original pixels before editing.'})
        elif method in EXTERNAL:
            if set(job['columns']['series']) != set(part['scope']['series']):
                raise ValueError('external series columns must cover declared scope exactly')
            self.import_csv(job, folder)
        else:
            args = [folder / 'job.json']
            if not method.startswith('vector_'):
                args.append(folder / 'candidates')
            self.run_extractor(part, key, args, folder)
        files = {str(p): sha(p) for p in folder.rglob('*') if p.is_file()}
        images = [p for p in files if p.endswith('.png') and any(s in Path(p).name for s in ['check', 'overlay','overview'])]
        record = {'fingerprint': key, 'folder': str(folder), 'files': files, 'images': images,
                  'inputs': inputs, 'status': 'unreviewed_candidates'}
        self.state['cache'][key] = record
        return copy.deepcopy(record)

    def run_extractor(self, part, fingerprint, args, folder):
        script = EXTRACTORS[part['method']]
        if self.scope is None:
            self.command(script, args, folder)
            return
        from extraction_gate import attempt_limit, attempt_log, record_attempt
        source = self.scope['cards'][part['id']]['source']
        failure = None
        with attempt_log(self.gate_path, self.scope['pdf_sha256']) as db:
            attempt_limit(db, source)
            try:
                self.command(script, args, folder)
                if part['method'].startswith('vector_'):
                    rows = read(folder / 'data.json').get('_evidence', {}).get('records', [])
                    have_candidate = any(row.get('value') is not None for row in rows)
                else:
                    items = read(folder / 'candidates' / 'review_job.json')['items']
                    have_candidate = any(points for item in items for points in item['roles'].values())
                if not have_candidate:
                    raise ValueError('automatic extraction produced no numeric candidates')
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                failure = exc
            record_attempt(db, source, fingerprint, 'failed' if failure else 'candidate',
                           str(failure) if failure else '')
        if failure:
            raise failure

    def review_attempt(self, part, fingerprint, status, note):
        if self.scope is None:
            return
        if part['method'] == 'review_candidates':
            job = read(self.output(f'artifacts/{fingerprint}/job.json'))
            fingerprint = job.get('wpd_import_contract', {}).get('config_sha256')
            if not fingerprint:
                return
        elif part['method'] not in EXTRACTORS:
            return
        from extraction_gate import attempt_log, record_attempt
        source = self.scope['cards'][part['id']]['source']
        with attempt_log(self.gate_path, self.scope['pdf_sha256']) as db:
            current = db.execute('SELECT status FROM attempts WHERE source=? AND fingerprint=?',
                                 (source, fingerprint)).fetchone()
            if current == ('failed',) and status == 'confirmed':
                raise ValueError('rejected automatic output cannot be approved')
            record_attempt(db, source, fingerprint, status, note)

    @staticmethod
    def import_csv(job, folder):
        """Explicit export columns only: no inferred series, calibration or locale."""
        fields = {'export', 'review_image', 'columns', 'sample_at', 'prefix'}
        if set(job) - fields:
            raise ValueError('external job accepts only export/review_image/columns/sample_at')
        columns = job['columns']
        records = []
        with Path(job['export']).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        if not rows or len({row[columns['x']] for row in rows}) != len(rows):
            raise ValueError('empty or duplicate X export; split series explicitly')
        for row in rows:
            x = float(row[columns['x']])
            for name, col in columns['series'].items():
                raw = row[col].strip()
                y = float(raw) if raw else None
                if not math.isfinite(x) or y is not None and not math.isfinite(y):
                    raise ValueError('nonfinite external value')
                records.append({'series': name, 'x': x, 'value': y,
                                'status': 'missing' if y is None else 'extracted_unreviewed'})
        if [float(row[columns['x']]) for row in rows] != job['sample_at']:
            raise ValueError('export does not cover declared sample_at exactly')
        write(folder / 'data.json', {'_evidence': {'method': 'external_export_unreviewed',
              'export_sha256': sha(job['export']), 'records': records}})
        shutil.copyfile(job['review_image'], folder / 'external_check.png')

    def current(self, part, stage):
        record = self.state['parts'].get(part['id'], {}).get(stage)
        _, _, key = self.normalized(part, stage == 'trial')
        if not self.valid(record) or record['fingerprint'] != key:
            raise ValueError(f'{part["id"]}: {stage} missing, changed or stale')
        return record

    def bind_vector_gaps(self, record, data, decisions):
        evidence = data.get('_evidence', {})
        rows = [r for r in evidence.get('records', []) if r.get('value') is None]
        if not rows and not decisions:
            return
        if self.scope is None or evidence.get('method') != 'vector_lines' or not isinstance(decisions, list):
            raise ValueError('only gated vector lines support explicit vector gaps')
        keys = {(d['series'], d['x']): d for d in decisions}
        if len(keys) != len(decisions) or set(keys) != {(r['series'], r['x']) for r in rows}:
            raise ValueError('review every missing vector reading exactly once')
        image = str(Path(record['folder']) / 'data_source_check.png')
        if image not in record['images']:
            raise ValueError('vector gap requires the original review image')
        for row in rows:
            d = keys[row['series'], row['x']]
            gap = row.get('gap_candidate', {})
            bbox = gap.get('source_bbox_px')
            kind = d.get('kind')
            note = d.get('reason')
            if (not bbox or gap.get('reason') == 'sample_outside_panel'
                    or not isinstance(note, str) or not note.strip()
                    or kind not in ('source_uncovered', 'source_unreadable')
                    or (kind == 'source_uncovered' and gap.get('reason') != 'outside_selected_path_extent')):
                raise ValueError(f'{row["series"]}, x={row["x"]}: invalid vector source gap decision')
            row['review'] = {'missing_evidence': {
                'kind': kind, 'reason': note, 'image': image, 'sha256': record['files'][image], 'bbox': bbox}}

    def attest(self, part, stage, decision):
        record = self.current(part, stage)
        d = read(self.path(decision))
        expected = {p: sha(p) for p in record['images']}
        if (not expected or d.get('part') != part['id'] or d.get('fingerprint') != record['fingerprint']
                or d.get('images') != expected or d.get('identity_checked') is not True
                or d.get('scope_checked') is not True or not d.get('note')):
            raise ValueError('explicit visual decision with current image hashes, identity and scope required')
        if stage == 'run':
            result = self.state['parts'][part['id']].get('result')
            if result:
                result = self.checked_result(result)
                summary = result['summary']
                unresolved = summary['numeric_roles_unresolved']
                if self.scope is not None:
                    unresolved = summary['visual_roles_unreviewed']
                    if any(not (role.get('review') or {}).get('note', '').strip()
                           for item in result['items'] for role in item['roles'].values()
                           if role['status'] == 'explicit_missing'):
                        raise ValueError('every source gap requires a visual reason')
                if unresolved:
                    raise ValueError('unresolved review roles remain; cannot accept')
            else:
                data = Path(record['folder']) / 'data.json'
                if not data.exists():
                    raise ValueError('bitmap candidates require review/apply before accept')
                data = read(data)
                if not data.get('_evidence', {}).get('records'):
                    raise ValueError('no evidence records; cannot accept complete scope')
                self.bind_vector_gaps(record, data, d.get('vector_gaps', []))
        saved = self.output(f'{part["id"]}_{stage}_decision_{digest(d)}.json')
        self.review_attempt(part, record['fingerprint'], 'confirmed', d['note'])
        if not saved.exists():
            write(saved, d)
        self.state['parts'][part['id']][stage + '_approval'] = {
            'file': str(saved), 'sha256': sha(saved),
            'full_fingerprint': self.normalized(part, False)[2]}
        return {'decision': str(saved), 'scope': 'agent visual attestation, not independent validation'}

    def approved(self, part, stage):
        record = self.current(part, stage)
        approval = self.state['parts'][part['id']].get(stage + '_approval', {})
        if not approval or sha(approval['file']) != approval['sha256']:
            raise ValueError('visual approval missing or changed')
        if read(approval['file'])['fingerprint'] != record['fingerprint']:
            raise ValueError('stale visual approval')
        if approval['full_fingerprint'] != self.normalized(part, False)[2]:
            raise ValueError('full extraction configuration changed after visual approval')

    def checked_result(self, record):
        if sha(record['file']) != record['sha256']:
            raise ValueError('review result changed')
        return read(record['file'])

    def workbook(self, action):
        if self.scope is not None and {part['id'] for part in self.parts} != set(self.scope['cards']):
            raise ValueError('workbook delivery requires every extraction part in the frozen inventory')
        for part in self.parts:
            self.approved(part, 'run')
        cfg = self.task['workbook']
        if set(cfg) != {'plan', 'checks', 'audit'}:
            raise ValueError('workbook requires plan/checks/audit paths only')
        source_plan = self.path(cfg['plan'])
        plan = read(source_plan)
        if any(not Path(plan[k]).is_absolute() or inside(plan[k], REPO)
               or Path(plan[k]).suffix.lower() != '.xlsx' for k in ['target', 'backup']):
            raise ValueError('target and backup must be absolute XLSX paths outside tool repository')
        paper = plan['paper']
        if type(paper) is not int or not 1 <= paper <= 9999:
            raise ValueError('invalid paper ID')
        contracts = {str(self.path(cfg[k])):sha(self.path(cfg[k])) for k in ['checks','audit']}
        evidence_path = self.output('delivery_evidence.json')
        results = None
        evidence = {}
        readings = None
        if self.scope is not None:
            from extraction_gate import bound_readings, expand_figure_previews, prefer_printed
            evidence = read(evidence_path) if evidence_path.exists() else {}
            results = {}
            for part in self.parts:
                part_state = self.state['parts'][part['id']]
                if 'result' in part_state:
                    results[part['id']] = self.checked_result(part_state['result'])
                else:
                    record = self.current(part, 'run')
                    data = read(Path(record['folder']) / 'data.json')
                    approval = read(part_state['run_approval']['file'])
                    self.bind_vector_gaps(record, data, approval.get('vector_gaps', []))
                    results[part['id']] = data
            missing = evidence.setdefault('missing', {})
            readings = prefer_printed(bound_readings(self.scope, results, missing), self.printed)
            add_bound_changes(plan, readings)
            expand_figure_previews(plan)
            revision = digest({'plan': plan, 'contracts': contracts,
                               'delivery_evidence': sha(evidence_path) if evidence_path.exists() else None,
                               'accepted': [self.state['parts'][p['id']]['run_approval']['sha256']
                                            for p in self.parts],
                               'results': [self.state['parts'][p['id']].get('result', {}).get('sha256')
                                           for p in self.parts]})
            folder = self.output(f'workbook/{revision[:24]}')
        else:
            folder = self.output('workbook')
        folder.mkdir(parents=True, exist_ok=True)
        plan_path = folder / f'A{paper}_修改计划.json'
        existing = read(plan_path) if plan_path.exists() else None
        without_snapshot = lambda p: {k:v for k,v in p.items() if k != 'source_snapshot'}
        if existing and without_snapshot(existing) != without_snapshot(plan):
            raise ValueError('save plan changed; keep evidence and use a new task')
        if not plan_path.exists():
            write(plan_path, plan)
        if readings is not None:
            bindings_path = folder / 'bound_readings.json'
            if bindings_path.exists():
                if read(bindings_path) != readings:
                    raise ValueError('bound draft readings changed')
            else:
                write(bindings_path, readings)
            missing_path = folder / 'bound_missing_evidence.json'
            if missing:
                if missing_path.exists() and read(missing_path) != missing:
                    raise ValueError('bound source gap evidence changed')
                if not missing_path.exists():
                    write(missing_path, missing)
        old_contracts = self.state.get('workbook_contracts')
        if self.scope is None and old_contracts and old_contracts != contracts:
            raise ValueError('verification contracts changed after draft preparation')
        self.state['workbook_contracts'] = contracts
        env = {'SRDP_TASK_DIR': str(folder),
               'FIGURE_PYTHON': os.environ.get('FIGURE_PYTHON') or sys.executable}
        node = os.environ.get('FIGURE_NODE') or shutil.which('node')
        powershell = shutil.which('pwsh') or shutil.which('powershell')
        if not node or not powershell:
            raise ValueError('configured Node and PowerShell required for workbook pipeline')
        draft = folder / f'A{paper}_保存前.xlsx'
        preview_receipt = folder / 'draft_previews.json'
        def reconcile(book):
            if self.scope is not None:
                from extraction_gate import check_delivery
                check_delivery(self.scope, book, evidence, results, self.printed)

        if action == 'draft':
            if draft.exists():
                if self.scope is None:
                    raise ValueError('draft already exists; use save or new task rather than overwrite')
            else:
                self.command('workbook_save/author.mjs', ['author', paper], folder, node, env)
                self.command('workbook_save/merge_cells.py', [paper], folder, env=env)
            reconcile(draft)
            if self.scope is not None:
                self.command('verify.py', [draft, self.path(cfg['checks'])], folder)
                self.command('audit_workbook.py', [draft, self.path(cfg['audit'])], folder)
            images = list(map(str, folder.glob('*preview_draft*.png')))
            if not preview_receipt.is_file():
                self.command('workbook_save/author.mjs', ['preview_draft', paper], folder, node, env)
                images = list(map(str, folder.glob('*preview_draft*.png')))
                if self.scope is not None:
                    if not images or len(images) != len(plan.get('previews', [])):
                        raise ValueError('draft must render one preview per declared view')
                    write(preview_receipt, {'draft': sha(draft), 'images': {p: sha(p) for p in images}})
            elif read(preview_receipt) != {'draft': sha(draft), 'images': {p: sha(p) for p in images}}:
                raise ValueError('draft or completed preview changed')
            return {'status': 'draft_needs_visual_review', 'draft': str(draft), 'images': images}
        target = Path(plan['target'])
        checkbook = draft if action == 'save' else target
        reconcile(checkbook)
        self.command('verify.py', [checkbook, self.path(cfg['checks'])], folder)
        self.command('audit_workbook.py', [checkbook, self.path(cfg['audit'])], folder)
        if action == 'save':
            decision = self.output('save_decision.json')
            d = read(decision)
            images = {str(p): sha(p) for p in folder.glob('*preview_draft*.png')}
            if self.scope is not None and (not preview_receipt.is_file() or
                    read(preview_receipt) != {'draft': sha(draft), 'images': images}):
                raise ValueError('save requires the completed previews for this draft')
            if (d.get('draft_sha256') != sha(draft) or not images or d.get('images') != images
                    or d.get('notes_checked') is not True or d.get('viewed') is not True):
                raise ValueError('save_decision.json must attest draft, preview images and affected notes')
            self.command('workbook_save/recalculate.ps1', ['-PaperIds', paper, '-BundledEngine'],
                         folder, powershell, env)
            reconcile(target)
            self.command('verify.py', [target, self.path(cfg['checks'])], folder)
            self.command('audit_workbook.py', [target, self.path(cfg['audit'])], folder)
            mode = 'preview_saved' if self.scope is not None else 'preview'
            self.command('workbook_save/author.mjs', [mode, paper], folder, node, env)
            if self.scope is not None:
                changed = changed_previews(images, paper)
                self.state['saved'] = {'target': str(target), 'sha256': sha(target),
                                       'changed_previews': changed}
                return {'status': 'saved_visual_changes_need_review' if changed else 'saved_checks_passed',
                        'workbook': str(target), 'images': changed}
            self.state['saved'] = {'target': str(target), 'sha256': sha(target),
                                   'scope': 'declared task only; saved preview still requires viewing'}
        return {'status': 'saved_checks_passed' if action == 'save' else 'declared_checks_passed',
                'workbook': str(target), 'images': list(map(str, folder.glob('A*_preview_*.png')))}

    def load_points(self,part,job):
        from review_points import load_job,load_result
        state=self.state['parts'][part['id']]
        prior=state.get('result')
        if prior:self.checked_result(prior)
        jp,j,sources,panels=load_job(job)
        result=load_result(jp,j,sources,panels,prior['file'] if prior else None)
        revision=prior['sha256'] if prior else digest(result)
        return state,prior,jp,j,sources,panels,result,revision

    def point_action(self,action,part,record,job,decision,batch,points,regions,view_mode='points',redraw=True, cells=None):
        from review_points import load_result,summary,inspect_points,edit_points
        start=time.perf_counter()
        state,prior,jp,j,sources,panels,result,revision=self.load_points(part,job)
        if cells is not None:
            from extraction_gate import coverage_report

            if self.scope is None:
                raise ValueError('cell inspection requires frozen worksheet bindings')
            report = coverage_report(self.scope, {part['id']: result}, self.printed)['cells']
            points = []
            for cell in cells:
                target = report.get(cell, {}).get('review_target')
                if target is None:
                    raise ValueError(f'{cell}: no uniquely bound bitmap reading in part {part["id"]}')
                points.append(target)
            if len({(p['id'], p['role']) for p in points}) != len(points):
                raise ValueError('cell selection binds the same reading more than once')
        if action=='inspect':redraw=True
        if not hasattr(self,'_point_images'):self._point_images={}
        images=self._point_images.setdefault(part['id'],{})
        ghosts=[];undo_frame=None
        if action=='inspect':
            affected=points
        else:
            request=read(self.path(decision))
            if request.get('base_revision')!=revision or not request.get('note','').strip():
                raise ValueError('current base_revision and operation note required')
            if action=='undo':
                frames=state.get('point_undo',[])
                if not frames:raise ValueError('no point edit to undo')
                undo_frame=frames[-1];affected=undo_frame['affected']
                ghosts=[copy.deepcopy(i) for i in result['items'] if any(r['id']==i['id'] for r in affected)]
                previous=undo_frame['previous']
                if previous:self.checked_result(previous)
                result=load_result(jp,j,sources,panels,previous['file'] if previous else None)
                result['history'].append({'kind':'undo_point_edit','reverted_revision':revision,'note':request['note']})
                result['summary']=summary(result)
            else:
                observation_path=self.path(batch)
                if not inside(observation_path,self.output(f'point_views/{part["id"]}')):
                    raise ValueError('view must belong to this part')
                observation=read(observation_path)
                if observation['revision']!=revision:raise ValueError('view is stale; inspect current revision')
                result,affected,ghosts=edit_points(result,request,observation)
                view_mode=observation.get('view_mode','points')
            target=self.output(f'results/{part["id"]}_{time.time_ns()}.json');target.parent.mkdir(exist_ok=True)
            write(target,result);revision=sha(target)
        render_start=time.perf_counter()
        if redraw:
            folder=self.output(f'point_views/{part["id"]}/{time.time_ns()}')
            view=inspect_points(result,folder,revision,affected,regions,images,ghosts,view_mode)
            if cells is not None:
                view['cell_targets'] = dict(zip(cells, points))
            render_seconds=time.perf_counter()-render_start
        else:
            view={'view':None,'images':[],'image_hashes':{},'revision':revision,'views':[],'points':[],
                  'decision_template':None}
            render_seconds=0.0
        if action!='inspect':
            if action=='undo':state['point_undo'].pop()
            else:state.setdefault('point_undo',[]).append({'previous':prior,'affected':affected})
            state['result']={'file':str(target),'sha256':revision}
            state.pop('pending_review',None);state.pop('run_approval',None)
            original=state.setdefault('original_check_images',list(record['images']))
            record['images']=original+view['images']
            for p in view['images']:record['files'][p]=sha(p)
        return {**view,'summary':summary(result),'operation_seconds':round(time.perf_counter()-start,4),
                'render_seconds':round(render_seconds,4),'extraction_executions':self.state['executions'],
                'result':state.get('result'),'scope':'point data edited; no extraction rerun or workbook write'}

    def drag(self,request):
        from drag_points import perform_drag
        return perform_drag(self,request)

    def coverage(self):
        from extraction_gate import compare_current_cells, coverage_report
        from review_points import initial_result, load_job

        if self.scope is None:
            raise ValueError('coverage requires frozen worksheet bindings')
        results = {}
        for part in self.parts:
            part_id = part['id']
            state = self.state['parts'].get(part_id, {})
            if 'run' not in state:
                continue
            record = self.current(part, 'run')
            folder = Path(record['folder'])
            candidates = folder / 'candidates/review_job.json'
            if 'result' in state:
                results[part_id] = self.checked_result(state['result'])
            elif candidates.is_file():
                results[part_id] = initial_result(*load_job(candidates))
            elif (folder / 'data.json').is_file():
                data = read(folder / 'data.json')
                if 'run_approval' in state:
                    self.approved(part, 'run')
                    approval = read(state['run_approval']['file'])
                    self.bind_vector_gaps(record, data, approval.get('vector_gaps', []))
                    for row in data.get('_evidence', {}).get('records', []):
                        if row.get('value') is not None:
                            row['status'] = 'reviewed'
                results[part_id] = data
        report = coverage_report(self.scope, results, self.printed)
        if self.task.get('workbook'):
            plan = read(self.path(self.task['workbook']['plan']))
            compare_current_cells(report, Path(plan['target']))
        path = self.output('coverage.json')
        write(path, report)
        return {'coverage': str(path), 'counts': report['counts'],
                'current_changes': report.get('current_workbook', {}).get('changes', {}),
                'dash_dispositions': report.get('current_workbook', {}).get('dash_dispositions', {})}

    def execute(self, action, part_id=None, decision=None, batch=None, points=None, regions=None, view_mode='points', cells=None):
        if action not in ACTIONS:
            raise ValueError('unknown action; arbitrary execution forbidden')
        if action == 'status':
            return {'parts': self.state['parts'], 'executions': self.state['executions'],
                    'cache_hits': self.state['cache_hits'], 'computer_seconds': round(self.state['computer_seconds'], 3)}
        if action == 'coverage':
            return self.coverage()
        if action == 'scan':
            key = digest({'pdf': sha(self.pdf), 'tools': self.state['tools']})
            folder = self.output(f'scan/{key}'); output = folder / 'scan.json'
            if output.exists():
                if self.state.get('scan') != sha(output):
                    raise ValueError('scan changed')
                self.state['cache_hits'] += 1
            else:
                self.command('scan_pages.py', [self.pdf, '--json', output], folder)
                self.state['scan'] = sha(output)
            return {'scan': str(output), 'pages': len(read(output)), 'identity': 'agent must inspect original'}
        if action in {'draft', 'save', 'verify'}:
            return self.workbook(action)
        part = self.part(part_id)
        state = self.state['parts'].setdefault(part_id, {})
        if action in {'trial', 'run'}:
            if action == 'run':
                self.approved(part, 'trial')
            record = self.extract(part, action == 'trial')
            state[action] = record
            return {k: record[k] for k in ['fingerprint', 'folder', 'images', 'status']}
        if action in {'approve', 'accept'}:
            return self.attest(part, 'trial' if action == 'approve' else 'run', decision)
        if action == 'reject':
            if self.scope is None or part['method'] not in (set(EXTRACTORS) | {'review_candidates'}):
                raise ValueError('reject requires a gated automatic extraction')
            d = read(self.path(decision))
            stage = d.get('stage')
            if stage not in ('trial', 'run'):
                raise ValueError('reject decision requires trial or run stage')
            record = self.current(part, stage)
            if (d.get('fingerprint') != record['fingerprint'] or
                    d.get('images') != {p: sha(p) for p in record['images']} or
                    not isinstance(d.get('note'), str) or not d['note'].strip()):
                raise ValueError('reject requires current output, image hashes and visual reason')
            self.review_attempt(part, record['fingerprint'], 'failed', d['note'])
            state.pop(stage + '_approval', None)
            if stage == 'trial':
                state.pop('run_approval', None)
            return {'status': 'rejected_automatic_output'}
        record = self.current(part, 'run')
        job = Path(record['folder']) / 'candidates/review_job.json'
        if not job.exists():
            raise ValueError('review/apply supported for bitmap review jobs; vector/export uses accept attestation')
        if action in ('inspect','edit','undo'):
            return self.point_action(action,part,record,job,decision,batch,points,regions,view_mode,cells=cells)
        from review_points import prepare, apply
        result_record = state.get('result')
        if result_record:
            self.checked_result(result_record)
        resume = result_record['file'] if result_record else None
        if action == 'review':
            prior = state.get('pending_review')
            if prior and prior['resume'] == resume and all(sha(p) == h for p,h in prior['files'].items()):
                self.state['cache_hits'] += 1
                return prior['reply']
            reply = prepare(job, self.output(f'review/{part_id}'), limit=1, sample_every=20, zoom=2, resume=resume)
            files = [reply['manifest']] + reply['images']
            state['pending_review'] = {'resume': resume, 'reply': reply, 'files': {p:sha(p) for p in files}}
            return reply
        manifest = self.path(batch)
        if not inside(manifest, self.output(f'review/{part_id}')):
            raise ValueError('batch must belong to this part')
        target = self.output(f'results/{part_id}_{time.time_ns()}.json'); target.parent.mkdir(exist_ok=True)
        result = apply(manifest, self.path(decision), target, resume=resume)
        state['result'] = {'file': str(target), 'sha256': sha(target)}
        # A legacy apply is outside the fast editor's transaction stack.
        # Do not let a later undo silently revert that unrelated operation.
        state.pop('point_undo',None)
        state.pop('pending_review', None)
        # Acceptance must cover the latest corrected overlay, not just original candidates.
        for im in result['check_images']:
            record['images'].append(im['path'])
            record['files'][im['path']] = im['sha256']
        state.pop('run_approval', None)
        return result

    def dispatch(self, request):
        try:
            if self.scope is not None and sha(self.gate_path) != self.task['gate']['sha256']:
                raise ValueError('frozen scope changed; prepare a new task')
            if digest({str(p.relative_to(TOOLS)):sha(p) for p in code_files()}) != self.state['tools']:
                raise ValueError('tool code changed during session; maintenance requires a new task')
            allowed={'action','part','decision','batch','points','regions','view_mode','cells'}
            drag_keys={'command','target','role','pixel','data','x','y','panel','series','box','pad','zoom','source','targets'}
            extra=set(request)-allowed
            if request.get('action')=='drag':
                if extra-drag_keys:raise ValueError('unknown drag field')
            elif extra:
                raise ValueError('only fixed action/part/decision/batch/points/regions/view_mode fields permitted')
            if request.get('view_mode','points') not in ('points','objects','objects_full'):
                raise ValueError('view_mode must be points, objects or objects_full')
            if 'view_mode' in request and request['action'] not in ('inspect','edit'):
                raise ValueError('view_mode is only supported for inspect/edit')
            if 'cells' in request:
                cells = request['cells']
                if (request['action'] != 'inspect' or 'points' in request or
                        not isinstance(cells, list) or not cells or
                        any(not isinstance(cell, str) or not cell for cell in cells)):
                    raise ValueError('cells requires inspect, worksheet addresses, and no point selectors')
            if request.get('action')=='drag':
                result=self.drag(request)
            else:
                result = self.execute(request['action'], request.get('part'), request.get('decision'), request.get('batch'),
                                      request.get('points'),request.get('regions'),request.get('view_mode','points'),
                                      request.get('cells'))
            write(self.state_path, self.state)
            # Keep stdout compact; full state/records remain on disk.
            if request['action'] == 'status':
                result['parts'] = {k: {'stages': list(v), 'candidate_status': v.get('run', {}).get('status')}
                                   for k, v in self.state['parts'].items()}
            return {'ok': True, **result, 'state': str(self.state_path)}
        except Exception as exc:
            write(self.state_path, self.state)
            return {'ok': False, 'error': str(exc), 'state': str(self.state_path), 'status': 'incomplete'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task'); parser.add_argument('action', choices=sorted(ACTIONS | {'serve'}))
    parser.add_argument('--part'); parser.add_argument('--decision'); parser.add_argument('--batch')
    parser.add_argument('--cell', action='append', dest='cells')
    args = parser.parse_args()
    try:
        with readonly_tools() as protection:
            runner = Runner(args.task)
            if args.action == 'serve':
                print(json.dumps({'ready': True, 'protection': protection}), flush=True)
                for line in sys.stdin:
                    if line.strip() == 'quit':
                        break
                    try:
                        reply = runner.dispatch(json.loads(line))
                    except Exception as exc:
                        reply = {'ok': False, 'error': str(exc)}
                    print(json.dumps(reply, ensure_ascii=False), flush=True)
            else:
                request = {'action': args.action, 'part': args.part,
                           'decision': args.decision, 'batch': args.batch}
                if args.cells is not None:
                    request['cells'] = args.cells
                reply = runner.dispatch(request)
                print(json.dumps(reply, ensure_ascii=False))
                return 0 if reply['ok'] else 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
