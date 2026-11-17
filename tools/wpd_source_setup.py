"""Copy and verify an already obtained official WPD source tree; never downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    'mathFunctions.js': 'javascript/core/mathFunctions.js',
    'color.js': 'javascript/core/color.js',
    'dateConversion.js': 'javascript/core/dateConversion.js',
    'inputParser.js': 'javascript/core/inputParser.js',
    'calibration.js': 'javascript/core/calibration.js',
    'axes_xy.js': 'javascript/core/axes/xy.js',
    'dataset.js': 'javascript/core/dataset.js',
    'autoDetection.js': 'javascript/core/autoDetection.js',
    'curve_detection_customIndependents.js': 'javascript/core/curve_detection/customIndependents.js',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(source, output, manifest=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents or output == ROOT or ROOT in output.parents:
        raise ValueError('Output must be outside the source and tool directories')
    if output.exists():
        raise ValueError('Output exists; choose a new directory')
    spec = json.loads(Path(manifest or ROOT / 'tools/wpd_sources.json').read_text(encoding='utf-8'))
    expected = {item['file']: item['sha256'] for item in spec['files']}
    if set(expected) != set(PATHS):
        raise ValueError('Source mapping does not match the checksum manifest')
    inputs = {name: source / relative for name, relative in PATHS.items()}
    for name, file in inputs.items():
        if not file.is_file() or digest(file) != expected[name]:
            raise ValueError(f'Missing or mismatched source: {file}')
    output.mkdir(parents=True)
    for name, file in inputs.items():
        shutil.copyfile(file, output / name)
        if digest(output / name) != expected[name]:
            raise ValueError(f'Copied source failed verification: {name}')
    return {'engine_sources_dir': str(output), 'verified_files': len(inputs)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='already obtained WPD source checkout')
    parser.add_argument('output', help='new directory for verified engine files')
    args = parser.parse_args()
    print(json.dumps(setup(args.source, args.output), ensure_ascii=False))
