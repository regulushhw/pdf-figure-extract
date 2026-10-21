"""Synthetic preparation and runner integration; no research workbook writes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_task import prepare, build, trial, status, configure, read, write, sha


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'paper.pdf'
        self.out = self.root / 'package'
        with fitz.open() as doc:
            p = doc.new_page(width=400, height=400)
            p.insert_text((20, 25), 'Figure 1. Synthetic discharge curve and caption.')
            p.draw_line((40, 280), (300, 100), color=(0, 0, 0))
            p.insert_text((20, 370), 'See Figure 1 for historical flow; unit and series require confirmation.')
            p = doc.new_page(width=400, height=400)
            p.insert_text((20, 30), 'Table IV. Printed summary statistics, check before reading figures.')
            doc.new_page(width=400, height=400)  # no text: OCR/visual candidate
            doc.save(self.pdf)

    def prepare(self, **kwargs):
        return prepare(self.pdf, self.out, **kwargs)

    def vector(self):
        d = read(self.out / 'decisions.json')
        p = next(p for p in d['parts'] if 'figure' in p['id'])
        p.update(action='extract', method='vector_lines', scope={'series': ['flow'], 'sampling': 'x=0,5,10'},
                 parameters={'frame': [30, 80, 310, 290], 'x_ticks': [[40, 0], [170, 5], [300, 10]],
                     'y_ticks': [[280, 0], [190, 50], [100, 100]], 'sample_at': [0, 5, 10], 'color_map': {'0,0,0': 'flow'}})
        write(self.out / 'decisions.json', d)
        return d

    def test_pdf_only_inventory_context_and_cache(self):
        before = sha(self.pdf)
        result = self.prepare()
        self.assertFalse(result['cached'])
        inv = read(self.out / 'inventory.json')
        self.assertEqual(len(inv['candidates']), 3)
        self.assertEqual(inv['candidates'][1]['kind'], 'table')
        self.assertEqual(result['text_sparse_pages'], [3])
        self.assertTrue(inv['candidates'][0]['context'])
        self.assertTrue(Path(inv['candidates'][0]['preview']).is_file())
        self.assertTrue(self.prepare()['cached'])
        self.assertEqual(before, sha(self.pdf))

    def test_no_invented_ready_job(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'no extraction part'):
            build(self.out)
        self.assertFalse((self.out / 'latest.json').exists())

    def test_compact_vector_to_real_runner_and_cache(self):
        self.prepare(); self.vector()
        first = build(self.out)
        self.assertEqual(first['selected_parts'], 1)
        self.assertEqual(first['remaining_candidates'], 2)
        r = trial(self.out)
        self.assertTrue(r['ok'], r)
        folder = Path(r['parts'][0]['folder'])
        values = read(folder / 'data.json')['series']['flow']
        for point, expected in zip(values, [0, 50, 100]):
            self.assertAlmostEqual(point[1], expected)
        r2 = trial(self.out)
        self.assertEqual(r2['metrics']['executions'], 1)
        self.assertEqual(r2['metrics']['cache_hits'], 1)
        self.assertEqual(r2['status'], 'unreviewed_candidates')

    def test_changed_decisions_new_build_preserves_evidence(self):
        self.prepare(); d = self.vector(); first = build(self.out)
        d['parts'][0]['parameters']['sample_at'] = [0, 10]
        write(self.out / 'decisions.json', d)
        second = build(self.out)
        self.assertNotEqual(first['task'], second['task'])
        self.assertTrue(Path(first['task']).is_file())

    def test_tampered_preparation_and_compilation_rejected(self):
        self.prepare(); self.vector(); compiled = build(self.out)
        Path(compiled['task']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'compiled configuration changed'): build(self.out)
        (self.out / 'inventory.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'): status(self.out)

    def test_series_mismatch_and_reference_without_note_rejected(self):
        self.prepare(); d = self.vector()
        d['parts'][0]['scope']['series'] = ['wrong']
        write(self.out / 'decisions.json', d)
        with self.assertRaisesRegex(ValueError, 'series mapping'): build(self.out)
        d = self.vector(); d['parts'][1]['action'] = 'reference'
        write(self.out / 'decisions.json', d)
        with self.assertRaisesRegex(ValueError, 'evidence note'): build(self.out)

    def test_reuse_no_approval_and_pdf_only_resume(self):
        job = {'pdf': str(self.pdf), 'page': 1, 'frame': [30, 80, 310, 290],
               'x': {'ticks': [[40, 0], [170, 5], [300, 10]]}, 'y': {'ticks': [[280, 0], [190, 50], [100, 100]]},
               'sample_at': [0, 5, 10], 'color_map': {'0,0,0': 'flow'}}
        write(self.root / 'job.json', job)
        write(self.root / 'old.json', {'version': 1, 'pdf': str(self.pdf), 'parts': [
            {'id': 'old', 'method': 'vector_lines', 'job': 'job.json', 'trial_job': 'job.json',
             'scope': {'series': ['flow'], 'sampling': '0,5,10'}}]})
        self.prepare(reuse=[self.root / 'old.json'])
        d = read(self.out / 'decisions.json'); part = d['parts'][-1]
        self.assertEqual(part['action'], 'pending')
        self.assertNotIn('approval', part)
        part['action'] = 'extract'; write(self.out / 'decisions.json', d)
        self.assertTrue(trial(self.out)['ok'])
        self.assertTrue(self.prepare()['cached'])

    def test_different_pdf_reuse_rejected(self):
        other = self.root / 'other.pdf'
        with fitz.open() as doc:
            doc.new_page(); doc.save(other)
        write(self.root / 'old.json', {'pdf': str(other), 'parts': []})
        with self.assertRaisesRegex(ValueError, 'different PDF'):
            self.prepare(reuse=[self.root / 'old.json'])

    def test_compact_bitmap_expands_series_and_sampling(self):
        self.prepare()
        image = self.root / 'curve.png'
        im = Image.new('RGB', (100, 100), 'white')
        ImageDraw.Draw(im).line((10, 40, 90, 40), fill='black', width=3); im.save(image)
        d = read(self.out / 'decisions.json')
        d['parts'][0].update(action='extract', method='bitmap_points',
            scope={'series': ['flow'], 'sampling': 'x=0,5,10'},
            parameters={'image': str(image), 'bounds': [1, 1, 98, 98],
                'x_ticks': [[10, 0], [90, 10]], 'x_unit': 'probability',
                'y_ticks': [[10, 100], [90, 0]], 'unit': 'Ml/d', 'sample_at': [0, 5, 10],
                'series': [{'name': 'flow', 'selector': {'mode': 'gray', 'range': [0, 80]}}]})
        write(self.out / 'decisions.json', d)
        compiled = build(self.out)
        task = read(compiled['task'])
        job = read(Path(compiled['task']).parent / task['parts'][0]['job'])
        self.assertEqual([i['anchor'][0] for i in job['items']], [10, 50, 90])
        result = trial(self.out)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'unreviewed_candidates')

    def test_caption_only_roman_headers_not_body_references(self):
        pdf = self.root / 'roman.pdf'
        with fitz.open() as doc:
            p = doc.new_page()
            p.insert_text((20, 30), 'TABLE III')
            p.insert_text((20, 60), 'Summary statistics')
            p.insert_text((20, 90), 'Figure 11 shows future flows, a body reference only.')
            doc.save(pdf)
        out = self.root / 'roman_package'
        prepare(pdf, out)
        inv = read(out / 'inventory.json')
        self.assertEqual([(c['kind'], c['label']) for c in inv['candidates']], [('table', 'III')])

    def test_configure_template_and_no_parameter_overwrite(self):
        self.prepare()
        pid = read(self.out / 'decisions.json')['parts'][0]['id']
        configure(self.out, pid, 'vector_lines')
        part = read(self.out / 'decisions.json')['parts'][0]
        self.assertEqual(part['parameters']['x_ticks'], [])
        self.assertTrue(list((self.out / 'decision_history').glob('*.json')))
        with self.assertRaisesRegex(ValueError, 'existing parameters retained'):
            configure(self.out, pid, 'bitmap_points')
        with self.assertRaisesRegex(ValueError, 'method, series'): build(self.out)


if __name__ == '__main__':
    unittest.main()
