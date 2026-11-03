"""One-command drag loop: numbered overlay, data moves, zoom limit, live table."""
import csv
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz
from openpyxl import load_workbook
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from drag_points import main
from review_points import axis_fit, data_to_pixel, output_xy, overlay_scale, value_at
from run_task import Runner, read, sha, write


class AxisMathTests(unittest.TestCase):
    def test_known_ticks_move_only_the_calibrated_axis(self):
        calibration = axis_fit({'coordinate': 'y', 'ticks': [[10, 100], [90, 0]], 'unit': '%'})
        moved = data_to_pixel([3, 5], calibration, 100)
        self.assertEqual(moved[0], 3)
        self.assertAlmostEqual(moved[1], 10)
        self.assertAlmostEqual(value_at(moved, calibration), 100)
        moved = data_to_pixel([3, 5], calibration, 0)
        self.assertAlmostEqual(moved[1], 90)
        with self.assertRaises(ValueError):
            data_to_pixel([3, 5], calibration, True)

    def test_large_panel_overview_shrinks_and_small_crops_enlarge(self):
        scale = overlay_scale(2880, 2320)
        self.assertLess(scale, 1)
        self.assertGreater(scale * 2880, 900)
        self.assertLess(scale * 2880, 1300)
        self.assertGreaterEqual(overlay_scale(98, 98), 2)
        self.assertEqual(overlay_scale(120, 80, zoom=6), 6)


class DragTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with fitz.open() as doc:
            doc.new_page()
            doc.save(self.root / 'paper.pdf')
        image = Image.new('RGB', (100, 100), 'white')
        ImageDraw.Draw(image).line((5, 40, 60, 40), fill='black', width=2)
        image.save(self.root / 'source.png')
        job = {'schema_version': 1, 'sources': {'s': {'image': 'source.png'}}, 'panels': {'p': {
            'source': 's', 'bounds': [1, 1, 98, 98],
            'axis': {'coordinate': 'y', 'ticks': [[10, 100], [90, 0]], 'unit': '%'}}}, 'items': []}
        for index, x in enumerate((20, 50, 80)):
            job['items'].append({'id': f'p{index}', 'panel': 'p', 'identity': {'series': 'curve', 'x': x},
                                 'anchor': [x, 40], 'roi': [1, 1, 98, 98],
                                 'selector': {'mode': 'gray', 'range': [0, 80]}})
        write(self.root / 'job.json', job)
        write(self.root / 'task.json', {'version': 1, 'pdf': 'paper.pdf', 'work_dir': 'run', 'parts': [
            {'id': 'a', 'method': 'bitmap_points', 'job': 'job.json', 'trial_job': 'job.json',
             'scope': {'series': ['curve'], 'sampling': 'three original points; synthetic drag only'}}]})
        self.runner = Runner(self.root / 'task.json')
        self.assertTrue(self.act('trial')['ok'])
        self.attest()
        self.assertTrue(self.act('run')['ok'])
        self.executions = self.runner.state['executions']

    def act(self, action, **kwargs):
        return self.runner.dispatch({'action': action, 'part': 'a', **kwargs})

    def attest(self):
        record = self.runner.state['parts']['a']['trial']
        write(self.root / 'attest.json', {'part': 'a', 'fingerprint': record['fingerprint'],
              'images': {path: sha(path) for path in record['images']}, 'identity_checked': True,
              'scope_checked': True, 'note': 'Synthetic contract attestation; not actual paper review.'})
        self.assertTrue(self.act('approve', decision='attest.json')['ok'])

    def cli(self, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main([str(self.root / 'task.json'), *args])
        reply = json.loads(buffer.getvalue())
        return code, reply

    def table(self, path):
        book = load_workbook(path)
        sheet = book['points']
        header = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
        rows = []
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if values[0] is None:
                continue
            rows.append(dict(zip(header, values)))
        return rows

    def test_show_numbers_grid_and_live_table(self):
        code, reply = self.cli('show', '--part', 'a')
        self.assertEqual(code, 0, reply)
        self.assertEqual([row['n'] for row in reply['points']], [1, 2, 3])
        self.assertEqual([row['id'] for row in reply['points']], ['p0', 'p1', 'p2'])
        self.assertTrue(all(row['in_view'] for row in reply['points']))
        self.assertGreater(reply['grids'][0]['step'], 0)
        self.assertEqual(reply['axes']['p']['axis'], 'y')
        self.assertEqual(reply['extraction_executions'], self.executions)
        panel = reply['placement']['panels'][0]
        point = reply['points'][0]
        self.assertIsNotNone(point['pixel'])
        ix, iy = output_xy(point['pixel'], panel['bounds'], panel['origin'], panel['scale_x'], panel['scale_y'])
        with Image.open(reply['overlay']) as image:
            self.assertEqual(list(image.size), reply['placement']['size'])
            self.assertGreater(image.width, (panel['bounds'][2] - panel['bounds'][0] + 1) * panel['scale_x'])
            red = image.getpixel((ix, iy))
            gutter = image.crop((0, 0, panel['origin'][0] - 1, image.height))
            gutter_pixels = list(gutter.get_flattened_data())
            self.assertTrue(any(pixel != (255, 255, 255) for pixel in gutter_pixels))
        self.assertGreater(red[0], 180)
        self.assertLess(red[1], 80)
        self.assertLess(red[2], 80)
        stored = self.table(reply['table'])
        self.assertEqual(stored[0]['id'], 'p0')
        self.assertAlmostEqual(float(stored[0]['value']), point['value'])
        with open(reply['csv'], encoding='utf-8', newline='') as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(exported[0]['id'], 'p0')
        self.assertAlmostEqual(float(exported[0]['pixel_y']), point['pixel'][1])

    def test_move_by_pixel_and_by_data_updates_table(self):
        code, shown = self.cli('show', '--part', 'a')
        self.assertEqual(code, 0, shown)
        original_x = shown['points'][0]['pixel'][0]
        code, reply = self.cli('move', '--part', 'a', 'p0', '--data', '100')
        self.assertEqual(code, 0, reply)
        moved = next(row for row in reply['points'] if row['id'] == 'p0')
        self.assertAlmostEqual(moved['value'], 100)
        self.assertAlmostEqual(moved['pixel'][1], 10)
        self.assertAlmostEqual(moved['pixel'][0], original_x)
        match = next(row for row in self.table(reply['table']) if row['id'] == 'p0')
        self.assertAlmostEqual(float(match['value']), 100)
        self.assertAlmostEqual(float(match['pixel_y']), 10)
        code, reply = self.cli('move', '--part', 'a', '1', '--pixel', '22', '42')
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply['extraction_executions'], self.executions)
        self.assertNotEqual(reply['overlay'], shown['overlay'])
        moved = next(row for row in reply['points'] if row['n'] == 1)
        self.assertEqual(moved['pixel'], [22.0, 42.0])
        self.assertEqual(moved['status'], 'reviewed')
        self.assertNotAlmostEqual(moved['value'], shown['points'][0]['value'])
        match = next(row for row in self.table(reply['table']) if row['id'] == 'p0')
        self.assertAlmostEqual(float(match['pixel_x']), 22)
        self.assertAlmostEqual(float(match['pixel_y']), 42)
        self.assertAlmostEqual(float(match['value']), moved['value'])
        self.assertIn(reply['overlay'], Runner(self.root / 'task.json').state['parts']['a']['run']['images'])
        note = load_workbook(reply['table'])['note']['A1'].value
        self.assertIn('not the delivery workbook', note)

    def test_zoom_blocks_unseen_destination_and_allows_a_local_move(self):
        code, zoomed = self.cli('zoom', '--part', 'a', '1', '--pad', '12')
        self.assertEqual(code, 0, zoomed)
        self.assertEqual(zoomed['placement']['panels'][0]['resample'], 'nearest')
        before = zoomed['points'][0]['pixel']
        code, refused = self.cli('move', '--part', 'a', '1', '--pixel', '90', '90')
        self.assertEqual(code, 1, refused)
        self.assertIn('outside', refused['error'])
        code, again = self.cli('show', '--part', 'a')
        self.assertEqual(code, 0, again)
        self.assertEqual(again['points'][0]['pixel'], before)
        code, reply = self.cli('zoom', '--part', 'a', '1', '--pad', '30')
        self.assertEqual(code, 0, reply)
        code, reply = self.cli('move', '--part', 'a', '1', '--pixel', '22', '42')
        self.assertEqual(code, 0, reply)
        self.assertEqual(next(row for row in reply['points'] if row['n'] == 1)['pixel'], [22.0, 42.0])
        code, away = self.cli('zoom', '--part', 'a', '--box', '70,70,98,98', '--panel', 'p')
        self.assertEqual(code, 0, away)
        code, refused = self.cli('move', '--part', 'a', '1', '--pixel', '90', '90')
        self.assertEqual(code, 1)
        self.assertIn('not on the current overlay', refused['error'])

    def test_add_delete_and_undo_keep_the_inventory(self):
        self.assertEqual(self.cli('show', '--part', 'a')[0], 0)
        code, reply = self.cli('add', '--part', 'a', 'extra', '--panel', 'p', '--series', 'new', '--pixel', '30', '45')
        self.assertEqual(code, 0, reply)
        self.assertTrue(any(row['id'] == 'extra' for row in reply['points']))
        code, reply = self.cli('undo', '--part', 'a')
        self.assertEqual(code, 0, reply)
        self.assertFalse(any(row['id'] == 'extra' for row in reply['points']))
        self.assertEqual(len(reply['points']), 3)
        code, reply = self.cli('add', '--part', 'a', 'extra', '--panel', 'p', '--series', 'new', '--pixel', '30', '45')
        self.assertEqual(code, 0, reply)
        code, reply = self.cli('delete', '--part', 'a', 'extra')
        self.assertEqual(code, 0, reply)
        self.assertEqual([row['id'] for row in reply['points']], ['p0', 'p1', 'p2'])
        code, reply = self.cli('delete', '--part', 'a', '1')
        self.assertEqual(code, 0, reply)
        self.assertEqual(len(reply['points']), 3)
        missing = next(row for row in reply['points'] if row['id'] == 'p0')
        self.assertEqual(missing['status'], 'explicit_missing')
        self.assertIsNone(missing['value'])
        code, reply = self.cli('move', '--part', 'a', '1', '--pixel', '22', '42')
        self.assertEqual(code, 0, reply)
        restored = next(row for row in reply['points'] if row['id'] == 'p0')
        self.assertEqual(restored['pixel'], [22.0, 42.0])
        self.assertEqual(restored['status'], 'reviewed')

    def test_refusals_do_not_need_a_decision_file(self):
        code, reply = self.cli('move', '--part', 'a', '1', '--pixel', '22', '42')
        self.assertEqual(code, 1)
        self.assertIn('overlay', reply['error'])
        refused = self.runner.dispatch({'action': 'drag', 'part': 'a', 'command': 'move', 'target': '1',
                                        'pixel': [22, 42], 'nope': 1})
        self.assertFalse(refused['ok'])
        self.assertIn('unknown drag field', refused['error'])
        code, reply = self.cli('show', '--part', 'a')
        self.assertEqual(code, 0, reply)
        code, reply = self.cli('move', '--part', 'a', '1', '--pixel', '22', '42', '--data', '10')
        self.assertEqual(code, 1)
        self.assertIn('not both', reply['error'])
        self.assertEqual(Runner(self.root / 'task.json').state['executions'], self.executions)

    def test_cli_process_show(self):
        script = Path(__file__).resolve().parents[1] / 'tools' / 'drag_points.py'
        completed = subprocess.run([sys.executable, str(script), str(self.root / 'task.json'), 'show', '--part', 'a'],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        reply = json.loads(completed.stdout)
        self.assertTrue(reply['ok'])
        self.assertTrue(Path(reply['overlay']).is_file())
        self.assertEqual(reply['points'][0]['id'], 'p0')


if __name__ == '__main__':
    unittest.main()
