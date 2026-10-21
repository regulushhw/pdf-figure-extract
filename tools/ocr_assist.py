"""Local OCR labels with source boxes -> reviewed linear calibration data.

recognize image output_root [--bounds L T R B] [--engine auto/windows/tesseract]
confirm analysis.json confirmation.json output.json
No model, cloud, installation, automatic legend identity or numeric correction.
"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from PIL import Image, ImageDraw
from review_points import axis_fit
from run_task import REPO, digest, inside, read, sha, write

NUMBER = re.compile(r'^[+\-−–]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?%?$')
UNIT = re.compile(r'%|\b(?:mm|cm|days?|hours?|Ml\s*/\s*d|m[lI]\s*/\s*d|m3\s*/\s*s|m³\s*/\s*s|kg)\b', re.I)


def numeric(text):
    raw = text.strip()
    if not NUMBER.fullmatch(raw):
        return None
    value = float(raw.replace('−', '-').replace('–', '-').rstrip('%'))
    return value if math.isfinite(value) else None


def inverse_box(box, rotation, size, scale):
    """Bounding-edge coordinates: clockwise rotated/enlarged image -> source."""
    w, h = size
    x0, y0, x1, y1 = [float(v)/scale for v in box]
    if rotation == 0: return [x0, y0, x1, y1]
    if rotation == 90: return [y0, h-x1, y1, h-x0]
    if rotation == 180: return [w-x1, h-y1, w-x0, h-y0]
    if rotation == 270: return [w-y1, x0, w-y0, x1]
    raise ValueError('rotation must be 0/90/180/270')


def parse_tsv(text):
    """Tesseract official TSV fields; preserve engine confidence, not accuracy."""
    lines = {}
    for r in csv.DictReader(io.StringIO(text), delimiter='\t'):
        if r['level'] != '5' or not r['text'].strip(): continue
        key = tuple(r[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
        x, y, w, h = [float(r[k]) for k in ('left', 'top', 'width', 'height')]
        lines.setdefault(key, []).append({'text': r['text'], 'bbox': [x, y, x+w, y+h],
                                          'confidence': float(r['conf'])})
    return [{'text': ' '.join(w['text'] for w in words), 'words': words} for words in lines.values()]


def normalize(raw, size, scale):
    words, lines, warnings = [], [], []
    for result in raw['results']:
        angle = result.get('text_angle') or 0
        # Windows documents boxes in the deskewed image for nonzero TextAngle.
        # Do not guess a deskew transform or use those boxes for calibration.
        reliable = abs(float(angle)) < 1e-6
        if not reliable: warnings.append(f'rotation {result["rotation"]}: nonzero TextAngle={angle}; boxes require deskew review')
        for li, line in enumerate(result['lines']):
            lid = f'r{result["rotation"]}_l{li}'
            ids, boxes = [], []
            for wi, word in enumerate(line['words']):
                box = inverse_box(word['bbox'], result['rotation'], size, scale)
                if len(box) != 4 or not all(math.isfinite(v) for v in box):
                    raise ValueError('invalid OCR box')
                wid = f'{lid}_w{wi}'
                words.append({'id': wid, 'line_id': lid, 'text': word['text'],
                    'bbox': box, 'confidence': word.get('confidence'),
                    'rotation': result['rotation'], 'coordinate_reliable': reliable,
                    'numeric_candidate': numeric(word['text'])})
                ids.append(wid); boxes.append(box)
            if boxes:
                lines.append({'id': lid, 'text': line['text'], 'word_ids': ids,
                    'bbox': [min(b[0] for b in boxes), min(b[1] for b in boxes),
                             max(b[2] for b in boxes), max(b[3] for b in boxes)],
                    'rotation': result['rotation'], 'coordinate_reliable': reliable})
    return words, lines, warnings


def suggestions(words, lines, bounds):
    result = {'axis_groups': [], 'label_candidates': [],
              'warning': 'Text centers only: tick positions, values, units and series require confirmation.'}
    if bounds:
        left, top, right, bottom = bounds
        margin = max(35, min(100, (right-left)*.18))
        groups = []
        for axis, side in (('y', 'left'), ('y', 'right'), ('x', 'bottom'), ('x', 'top')):
            candidates = []
            for w in words:
                if w['numeric_candidate'] is None or not w['coordinate_reliable'] or w['rotation'] != 0: continue
                a, b, c, d = w['bbox']; x, y = (a+c)/2, (b+d)/2
                near = ((axis == 'y' and top-5 <= y <= bottom+5 and
                         (left-margin <= c <= left+5 if side == 'left' else right-5 <= a <= right+margin)) or
                        (axis == 'x' and left-5 <= x <= right+5 and
                         (bottom-5 <= b <= bottom+margin if side == 'bottom' else top-margin <= d <= top+5)))
                if near:
                    candidates.append({'word_id': w['id'], 'position': y if axis == 'y' else x,
                        'value': w['numeric_candidate'], 'alignment': c if side == 'left' else a if side == 'right' else b})
            clusters = []
            for candidate in sorted(candidates, key=lambda c: c['alignment']):
                if not clusters or abs(candidate['alignment']-clusters[-1][0]['alignment']) > 9:
                    clusters.append([])
                clusters[-1].append(candidate)
            for cluster in clusters:
                if len(cluster) < 2: continue
                ticks = sorted(cluster, key=lambda c: c['position'])
                try:
                    fit = axis_fit({'coordinate': axis, 'ticks': [[t['position'], t['value']] for t in ticks],
                                    'unit': 'unconfirmed', 'max_residual_px': 2})
                    fit_status = 'linear_candidate_unconfirmed'
                except ValueError as exc:
                    fit, fit_status = None, f'conflict_or_nonlinear: {exc}'
                groups.append({'axis': axis, 'side': side, 'ticks': ticks,
                               'fit_status': fit_status, 'fit': fit})
        result['axis_groups'] = groups
    for line in lines:
        b = line['bbox']
        if line['rotation'] in (90,270) and b[3]-b[1] < 1.5*(b[2]-b[0]): continue
        if any(ch.isalpha() or '\u4e00' <= ch <= '\u9fff' or ch == '%' for ch in line['text']):
            result['label_candidates'].append({'line_id': line['id'], 'text': line['text'],
                'bbox': line['bbox'], 'kind': 'unit_or_axis_label' if UNIT.search(line['text']) or line['rotation'] in (90,270) else 'legend_or_other_text'})
    return result


def recognize(image, out, bounds=None, engine='auto', language=None, rotations=(0, 90), scale=2):
    start = time.perf_counter()
    image, out = Path(image).resolve(), Path(out).resolve()
    if inside(out, REPO) or inside(REPO, out): raise ValueError('OCR output must be outside tool repository')
    if not rotations or len(set(rotations)) != len(rotations) or any(r not in (0,90,180,270) for r in rotations):
        raise ValueError('unique right-angle rotations required')
    if not 1 <= scale <= 4: raise ValueError('scale must be 1..4')
    if engine == 'auto': engine = 'windows' if os.name == 'nt' else 'tesseract'
    if engine not in ('windows', 'tesseract'): raise ValueError('unsupported engine')
    language = language or ('en-US' if engine == 'windows' else 'eng')
    with Image.open(image) as im: source = im.convert('RGB')
    size = source.size
    if bounds and (len(bounds) != 4 or not (0 <= bounds[0] < bounds[2] <= size[0] and 0 <= bounds[1] < bounds[3] <= size[1])):
        raise ValueError('panel bounds outside source image')
    code = {p.name: sha(p) for p in (Path(__file__), Path(__file__).with_name('ocr_windows.ps1'))}
    request = {'image': str(image), 'source_sha256': sha(image), 'size': list(size), 'bounds': bounds,
               'engine': engine, 'language': language, 'rotations': list(rotations), 'scale': scale, 'code': code}
    fingerprint = digest(request)
    folder = out / fingerprint[:20]
    if (folder / 'analysis.json').is_file():
        a = validate(folder / 'analysis.json')
        return {'ok': True, 'cached': True, 'analysis': str(folder / 'analysis.json'),
                'review_image': a['review_image'], 'words': len(a['words']),
                'operation_seconds': round(time.perf_counter()-start, 3)}
    if folder.exists(): raise ValueError('incomplete OCR evidence retained; inspect before retry')
    folder.mkdir(parents=True)
    inputs = []
    for rotation in rotations:
        target = folder / f'input_{rotation}.png'
        processed = source.resize((size[0]*scale, size[1]*scale), Image.Resampling.LANCZOS).rotate(-rotation, expand=True)
        processed.save(target)
        inputs.append({'image': str(target), 'rotation': rotation})
    write(folder / 'request.json', {'language': language, 'images': inputs})
    if engine == 'windows':
        if os.name != 'nt': raise ValueError('Windows OCR requires Windows')
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(Path(__file__).with_name('ocr_windows.ps1')), '-Request', str(folder / 'request.json'),
                   '-Output', str(folder / 'raw.json')]
        run = subprocess.run(command, capture_output=True, timeout=90, shell=False)
        (folder / 'engine.log').write_bytes(run.stdout + run.stderr)
        if run.returncode: raise ValueError(f'Windows OCR failed; see {folder / "engine.log"}')
        raw = read(folder / 'raw.json')
    else:
        exe = os.environ.get('FIGURE_TESSERACT') or shutil.which('tesseract')
        if not exe: raise ValueError('Tesseract unavailable; no automatic installation')
        raw = {'engine': engine, 'language': language, 'results': []}
        for item in inputs:
            run = subprocess.run([exe, item['image'], 'stdout', '-l', language, '--psm', '11', 'tsv'],
                                 capture_output=True, timeout=90, shell=False)
            (folder / f'engine_{item["rotation"]}.log').write_bytes(run.stderr)
            if run.returncode: raise ValueError('Tesseract failed; inspect engine log')
            tsv = run.stdout.decode('utf-8-sig')
            (folder / f'raw_{item["rotation"]}.tsv').write_text(tsv, encoding='utf-8')
            raw['results'].append({**item, 'text_angle': 0, 'lines': parse_tsv(tsv)})
        write(folder / 'raw.json', raw)
    words, lines, warnings = normalize(raw, size, scale)
    hints = suggestions(words, lines, bounds)
    marked = source.resize((size[0]*2, size[1]*2))
    draw = ImageDraw.Draw(marked)
    visible_lines = {l['line_id'] for l in hints['label_candidates']}
    for w in words:
        if w['rotation'] in (90,270) and w['line_id'] not in visible_lines: continue
        b = [v*2 for v in w['bbox']]
        color = 'red' if w['numeric_candidate'] is not None else 'blue'
        draw.rectangle(b, outline=color, width=1)
        draw.text((b[0], max(0,b[1]-10)), w['id'], fill=color)
    if bounds: draw.rectangle([v*2 for v in bounds], outline='green', width=2)
    review = folder / 'labels_check.png'; marked.save(review)
    template = {'analysis_fingerprint': fingerprint, 'source_sha256': request['source_sha256'],
        'review_image_sha256': sha(review), 'viewed': False, 'note': '',
        'x': [], 'y': [], 'unit': None, 'series': []}
    for axis in ('x','y'):
        groups = [g for g in hints['axis_groups'] if g['axis'] == axis and g['fit']]
        if len(groups) == 1:
            template[axis] = [{'word_id': t['word_id']} for t in groups[0]['ticks']]
    write(folder / 'confirmation_template.json', template)
    (folder / 'labels.tsv').write_text('id\ttext\tx0\ty0\tx1\ty1\n' + '\n'.join(
        w['id']+'\t'+w['text'].replace('\t',' ')+'\t'+'\t'.join(f'{v:.2f}' for v in w['bbox']) for w in words), encoding='utf-8')
    files = {str(p): sha(p) for p in folder.iterdir() if p.is_file() and p.name != 'confirmation_template.json'}
    write(folder / 'analysis.json', {'version': 1, 'fingerprint': fingerprint, **request,
        'words': words, 'lines': lines, 'suggestions': hints, 'warnings': warnings,
        'review_image': str(review), 'files': files, 'operation_seconds': time.perf_counter()-start})
    return {'ok': True, 'cached': False, 'analysis': str(folder / 'analysis.json'),
        'review_image': str(review), 'confirmation_template': str(folder / 'confirmation_template.json'),
        'words': len(words), 'axis_groups': len(hints['axis_groups']),
        'warnings': warnings, 'operation_seconds': round(time.perf_counter()-start,3)}


def validate(path):
    a = read(path)
    if sha(a['image']) != a['source_sha256']: raise ValueError('OCR source image changed')
    if any(not Path(p).is_file() or sha(p) != h for p,h in a['files'].items()):
        raise ValueError('OCR evidence changed or missing')
    return a


def confirmed(analysis, confirmation):
    a, d = validate(analysis), read(confirmation)
    if (d.get('analysis_fingerprint') != a['fingerprint'] or d.get('source_sha256') != a['source_sha256']
            or d.get('review_image_sha256') != sha(a['review_image']) or d.get('viewed') is not True or not d.get('note','').strip()):
        raise ValueError('explicit current-image review and note required')
    words = {w['id']: w for w in a['words']}; lines = {l['id']: l for l in a['lines']}
    result = {'image': a['image'], 'source_sha256': a['source_sha256'], 'bounds': a['bounds'],
              'x_ticks': [], 'y_ticks': [], 'series_labels': [], 'unit': None,
              'evidence': {'analysis': str(Path(analysis).resolve()), 'confirmation': str(Path(confirmation).resolve()),
                           'analysis_sha256': sha(analysis),
                           'confirmation_sha256': sha(confirmation), 'note': d['note'], 'status': 'Agent visual self-check, not independent validation'}}
    for axis in ('x','y'):
        used = set()
        for tick in d.get(axis, []):
            w = words[tick['word_id']]
            if not w['coordinate_reliable'] or w['id'] in used: raise ValueError('unreliable or duplicate tick box')
            used.add(w['id'])
            value = tick.get('value', w['numeric_candidate'])
            if type(value) not in (float,int) or not math.isfinite(value): raise ValueError('explicit numeric tick value required')
            b = w['bbox']; position = tick.get('position', (b[0]+b[2])/2 if axis=='x' else (b[1]+b[3])/2)
            if not math.isfinite(position) or not 0 <= position <= a['size'][0 if axis=='x' else 1]:
                raise ValueError('tick position outside source')
            if (value != w['numeric_candidate'] or 'position' in tick) and not tick.get('reason','').strip():
                raise ValueError('corrected value/tick position requires reason')
            result[f'{axis}_ticks'].append([position, value])
        if result[f'{axis}_ticks']:
            axis_fit({'coordinate': axis, 'ticks': result[f'{axis}_ticks'], 'unit': 'confirmed', 'max_residual_px': 2})
    if d.get('unit'):
        unit = d['unit']; lines[unit['line_id']]
        if not lines[unit['line_id']]['coordinate_reliable']: raise ValueError('unit box coordinates require deskew review')
        if not unit['text'].strip(): raise ValueError('unit cannot be empty')
        if unit['text'] not in lines[unit['line_id']]['text'] and not unit.get('reason','').strip():
            raise ValueError('corrected unit text requires reason')
        result['unit'] = unit['text']
    for series in d.get('series', []):
        line = lines[series['line_id']]
        if not line['coordinate_reliable']: raise ValueError('series box coordinates require deskew review')
        if not series['name'].strip(): raise ValueError('series label cannot be empty')
        result['series_labels'].append({'name': series['name'], 'line_id': line['id'], 'bbox': line['bbox']})
    if not result['x_ticks'] and not result['y_ticks'] and not result['series_labels'] and not result['unit']:
        raise ValueError('no confirmed labels')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='action', required=True)
    p = sub.add_parser('recognize'); p.add_argument('image'); p.add_argument('out')
    p.add_argument('--bounds', type=float, nargs=4); p.add_argument('--engine', default='auto')
    p.add_argument('--language'); p.add_argument('--rotations', type=int, nargs='+', default=[0,90]); p.add_argument('--scale', type=int, default=2)
    p = sub.add_parser('confirm'); p.add_argument('analysis'); p.add_argument('confirmation'); p.add_argument('output')
    args = ap.parse_args()
    try:
        if args.action == 'recognize':
            reply = recognize(args.image,args.out,args.bounds,args.engine,args.language,args.rotations,args.scale)
        else:
            target = Path(args.output).resolve()
            if target.exists(): raise ValueError('confirmed output already exists; preserve prior evidence')
            write(target, confirmed(args.analysis,args.confirmation)); reply = {'ok':True,'confirmed':str(target)}
        print(json.dumps(reply,ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
