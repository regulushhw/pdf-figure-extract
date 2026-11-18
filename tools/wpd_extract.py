"""Optional WPD core -> existing review_candidates task; no workbook writes.

python tools/wpd_extract.py prepare config.json new_output_dir --pdf paper.pdf
python tools/wpd_extract.py export task.json part_id current.csv
External WPD files must match wpd_sources.json. Never downloads or installs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import wpd_bridge as bridge
from contextlib import nullcontext


def prepare_task(config, output, pdf, gate_path=None, part_id='figure'):
    config, output, pdf = map(lambda p: Path(p).resolve(), (config, output, pdf))
    if output.exists():
        raise ValueError('Output exists: resume its task, or use a new output directory for changed configuration')
    if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
        raise ValueError('Existing source PDF required')
    _, cfg, *_ = bridge.config_load(config)
    part = {'id': part_id, 'method': 'review_candidates', 'job': 'review_job.json',
            'trial_job': 'review_job.json', 'scope': {'series': [s['name'] for s in cfg['series']],
            'sampling': f"{len(cfg['sample_at'])} frozen positions per series",
            'sample_at': cfg['sample_at']}}
    ledger = nullcontext()
    if gate_path:
        from extraction_gate import (attempt_limit, attempt_log, check_cards, check_configured_coverage,
                                     check_job, record_attempt, validate_scope)
        from run_task import read
        gate_path = Path(gate_path).resolve()
        scope = read(gate_path)
        validate_scope(scope, pdf)
        check_cards(scope, [part], allow_subset=True)
        card = scope['cards'][part_id]
        check_job(card, cfg, 'wpd')
        check_configured_coverage(scope, part_id, cfg, 'wpd')
        source = card['source']
        ledger = attempt_log(gate_path, scope['pdf_sha256'])
    node = os.environ.get('FIGURE_NODE') or shutil.which('node')
    if not node:
        raise ValueError('Node runtime unavailable; configure FIGURE_NODE')
    output.mkdir(parents=True)
    started = time.perf_counter()
    try:
        prepared = bridge.prepare(config, output / 'prepared')
        command = [node, str(Path(__file__).with_name('wpd_engine.mjs')),
                   prepared['engine_input'], str(output / 'engine-result.json')]
        failure = None
        fingerprint = prepared['config_sha256']
        with ledger as db:
            if db is not None:
                attempt_limit(db, source)
                if db.execute('SELECT 1 FROM attempts WHERE source=? AND fingerprint=?',
                              (source, fingerprint)).fetchone():
                    raise ValueError('identical WPD configuration already tried; resume its output')
            try:
                run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                     timeout=300, shell=False)
                (output / 'engine.log').write_text(run.stdout + run.stderr, encoding='utf-8')
                if run.returncode:
                    raise ValueError(f'WPD failed; inspect {output / "engine.log"}')
                imported = bridge.bridge(config, output / 'engine-result.json', output / 'review_job.json')
                if not imported['candidates']:
                    raise ValueError('WPD produced no candidates')
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                failure = exc
            if db is not None:
                record_attempt(db, source, fingerprint, 'failed' if failure else 'candidate',
                               str(failure) if failure else '')
        if failure:
            raise failure
        if gate_path:
            review_job = output / 'review_job.json'
            candidate = bridge.read_json(review_job)
            candidate['wpd_import_contract'].update(
                pdf_sha256=scope['pdf_sha256'], source_id=source, part_id=part_id)
            bridge.write_json(review_job, candidate)
        task = {'version': 1, 'pdf': str(pdf), 'work_dir': 'run', 'parts': [part]}
        if gate_path:
            task.update(version=2, gate={'file': str(gate_path), 'sha256': bridge.sha(gate_path)})
        bridge.write_json(output / 'task.json', task)
        summary = {'task': str(output / 'task.json'), 'part': part_id,
                   'expected_positions': imported['items'], 'candidates': imported['candidates'],
                   'missing_candidates': imported['needs_review'],
                   'visual_review_positions': imported['items'],
                   'preparation_seconds': round(time.perf_counter()-started, 4),
                   'tokens': None, 'status': 'unreviewed_candidates',
                   'next': 'Use run_task.py trial, view source/overlay, approve and run; correct with drag_points.py.',
                   'cost_scope': 'Local preparation only; excludes configuration, review and delivery.'}
        bridge.write_json(output / 'summary.json', summary)
        return summary
    except Exception as exc:
        bridge.write_json(output / 'failure.json', {'error': str(exc), 'status': 'incomplete_output_retained'})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('config'); p.add_argument('output'); p.add_argument('--pdf', required=True)
    p.add_argument('--gate'); p.add_argument('--part', default='figure')
    p.add_argument('--legacy-ungated', action='store_true')
    p = commands.add_parser('export')
    p.add_argument('task'); p.add_argument('part'); p.add_argument('csv')
    args = parser.parse_args()
    if args.command == 'prepare':
        if not args.gate and not args.legacy_ungated:
            parser.error('prepare requires --gate for new work')
        result = prepare_task(args.config, args.output, args.pdf, args.gate, args.part)
    else:
        result = bridge.export_results(args.task, args.part, args.csv)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
