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


def prepare_task(config, output, pdf):
    config, output, pdf = map(lambda p: Path(p).resolve(), (config, output, pdf))
    if output.exists():
        raise ValueError('Output exists: resume its task, or use a new output directory for changed configuration')
    if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
        raise ValueError('Existing source PDF required')
    _, cfg, *_ = bridge.config_load(config)
    node = os.environ.get('FIGURE_NODE') or shutil.which('node')
    if not node:
        raise ValueError('Node runtime unavailable; configure FIGURE_NODE')
    output.mkdir(parents=True)
    started = time.perf_counter()
    try:
        prepared = bridge.prepare(config, output / 'prepared')
        command = [node, str(Path(__file__).with_name('wpd_engine.mjs')),
                   prepared['engine_input'], str(output / 'engine-result.json')]
        run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                             timeout=300, shell=False)
        (output / 'engine.log').write_text(run.stdout + run.stderr, encoding='utf-8')
        if run.returncode:
            raise ValueError(f'WPD failed; inspect {output / "engine.log"}; no automatic fallback')
        imported = bridge.bridge(config, output / 'engine-result.json', output / 'review_job.json')
        task = {'version': 1, 'pdf': str(pdf), 'work_dir': 'run', 'parts': [
            {'id': 'figure', 'method': 'review_candidates', 'job': 'review_job.json',
             'trial_job': 'review_job.json', 'scope': {'series': [s['name'] for s in cfg['series']],
             'sampling': f"{len(cfg['sample_at'])} frozen positions per series; unreviewed WPD candidates",
             'sample_at': cfg['sample_at']}}]}
        bridge.write_json(output / 'task.json', task)
        summary = {'task': str(output / 'task.json'), 'part': 'figure',
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
    p = commands.add_parser('export')
    p.add_argument('task'); p.add_argument('part'); p.add_argument('csv')
    args = parser.parse_args()
    result = prepare_task(args.config, args.output, args.pdf) if args.command == 'prepare' else bridge.export_results(args.task, args.part, args.csv)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
