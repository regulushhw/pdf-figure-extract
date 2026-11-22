import sys
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import fitz
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from extraction_gate import cell_bindings, check_cards, check_configured_coverage, check_delivery, check_job, compare_current_cells, coverage_report, expand_figure_previews, inspect_workbook, validate_scope
from review_points import axis_fit
from extraction_gate import coarse_source_rounding
from run_task import Runner, changed_previews, sha, write


class ExtractionGateTests(unittest.TestCase):
    def test_source_rounding_uses_coarsest_supported_value(self):
        self.assertEqual(coarse_source_rounding(2943.38,[2930,2959])['output_value'],2940)
        self.assertEqual(coarse_source_rounding(2943.38,[2941,2959]),{})
        self.assertEqual(coarse_source_rounding(2943.38,[2930,3100])['output_value'],2940)
        with self.assertRaisesRegex(ValueError,'invalid source measurement bounds'):
            coarse_source_rounding(2943.38,[2950,3100])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pdf = self.root / 'paper.pdf'
        with fitz.open() as doc:
            doc.new_page()
            doc.save(self.pdf)
        self.old = self.root / 'old.xlsx'
        self.workbook(self.old)
        tables = {name: {'sheet': name, 'range': 'B2:C2', 'columns': [70, 71], 'rows': ['observed']}
                  for name in ['Fig6-1', 'Fig6-2']}
        self.scope = {
            'pdf_sha256': sha(self.pdf), 'pages_reviewed': [1],
            'inventory': {'Fig6': {'kind': 'figure', 'page': 1, 'action': 'read', 'tables': list(tables)}},
            'tables': tables, 'baseline': {'file': str(self.old), 'sha256': sha(self.old),
                                         'tables': {key: dict(value) for key, value in tables.items()}},
        }

    def workbook(self, path, second=True, last=2):
        book = Workbook()
        book.remove(book.active)
        for name in ['Fig6-1', 'Fig6-2'] if second else ['Fig6-1']:
            sheet = book.create_sheet(name)
            sheet.append([None, 70, 71])
            sheet.append(['observed', 1, last])
        book.save(path)
        book.close()

    def test_merge_or_shrink_old_table_fails_before_read(self):
        validate_scope(self.scope, self.pdf)
        self.scope['inventory']['Fig6']['tables'].remove('Fig6-2')
        self.scope['tables'].pop('Fig6-2')
        with self.assertRaisesRegex(ValueError, 'baseline output table'):
            validate_scope(self.scope, self.pdf)
        self.scope['baseline']['tables'].pop('Fig6-2')
        with self.assertRaisesRegex(ValueError, 'classify every old worksheet'):
            validate_scope(self.scope, self.pdf)

    def test_coverage_separates_unrun_cells_and_reviewed_gap(self):
        self.scope['tables']['Fig6-1']['grid'] = {'part': 'first'}
        self.scope['tables']['Fig6-2']['grid'] = {'part': 'second'}
        result = {'items': [
            {'id': 'end', 'identity': {'series': 'observed', 'x': 70}, 'roles': {
                'value': {'value': None, 'status': 'explicit_missing',
                          'review': {'missing_evidence': {'kind': 'source_uncovered'}}}}},
            {'identity': {'series': 'observed', 'x': 71}, 'roles': {
                'value': {'value': 5, 'status': 'automatic_candidate'}}},
        ]}
        report = coverage_report(self.scope, {'first': result}, {})
        self.assertEqual(report['counts'],
                         {'source_uncovered': 1, 'numeric_candidate': 1, 'not_extracted': 2})
        self.assertEqual(report['cells']['Fig6-2!B2']['reference']['x'], 70)
        self.assertEqual(report['cells']['Fig6-1!B2']['review_target'], {'id': 'end', 'role': 'value'})
        compare_current_cells(report, self.old)
        self.assertEqual(report['current_workbook']['changes'], {'existing_number_has_source_gap': 1})
        self.assertEqual(report['cells']['Fig6-1!B2']['current_value'], 1)

    def test_figure_preview_includes_print_area_but_retains_other_detail_ranges(self):
        image = BytesIO()
        Image.new('RGB', (20, 20), 'white').save(image, format='PNG')
        workbook = Workbook()
        figure = workbook.active
        figure.title = "Author's figure"
        figure.add_image(WorkbookImage(image), 'H5')
        figure.print_area = 'A1:M20'
        workbook.create_sheet('Data')
        target = self.root / 'preview.xlsx'
        workbook.save(target)
        workbook.close()
        views = [{'sheet': figure.title, 'range': 'A1:E8'},
                 {'sheet': figure.title, 'range': 'H5:J9'},
                 {'sheet': 'Data', 'range': 'A1:E8'}]
        expand_figure_previews({'target': target, 'previews': views})
        self.assertEqual([view['range'] for view in views], ['A1:M20', 'H5:J9', 'A1:E8'])

    def test_matching_but_shortened_old_and_new_ranges_fail(self):
        for tables in (self.scope['baseline']['tables'], self.scope['tables']):
            tables['Fig6-1'].update(range='B2:B2', columns=[70])
        with self.assertRaisesRegex(ValueError, 'Fig6-1: old workbook cells outside frozen baseline: C1, C2'):
            validate_scope(self.scope, self.pdf)

    def test_review_regions_can_share_sheet_without_overlapping(self):
        self.scope['baseline'] = None
        self.scope['no_previous_workbook_reason'] = 'Scoped correction draft'
        second = self.scope['tables']['Fig6-2']
        second.update(sheet='Fig6-1', range='D2:E2')
        with self.assertRaisesRegex(ValueError, 'preserve separate sheets'):
            validate_scope(self.scope, self.pdf)
        self.scope['review_regions'] = True
        validate_scope(self.scope, self.pdf)
        second['range'] = 'C2:D2'
        with self.assertRaisesRegex(ValueError, 'cannot overlap'):
            validate_scope(self.scope, self.pdf)

    def test_caption_absent_from_old_workbook_still_must_be_in_inventory(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 50), 'Fig. 5. Peak river discharge.')
            page.insert_text((40, 80), 'Fig. 6. Flow duration.')
            page = doc.new_page()
            page.insert_text((40, 50), 'Fig. 6. Continued.')
            doc.save(self.pdf)
        self.scope['pdf_sha256'] = sha(self.pdf)
        self.scope['pages_reviewed'] = [1, 2]
        self.scope['inventory']['Fig6']['continuation_pages'] = [2]
        with self.assertRaisesRegex(ValueError, 'PDF 1: figure 5'):
            validate_scope(self.scope, self.pdf)
        self.scope['inventory']['Fig5'] = {'kind': 'figure', 'page': 1, 'action': 'exclude',
                                           'reason': 'Outside the synthetic fixture scope'}
        validate_scope(self.scope, self.pdf)
        del self.scope['inventory']['Fig6']['continuation_pages']
        with self.assertRaisesRegex(ValueError, 'PDF 2: figure 6'):
            validate_scope(self.scope, self.pdf)

    def test_missing_sheet_or_cell_blocks_delivery(self):
        draft = self.root / 'draft.xlsx'
        self.workbook(draft)
        check_delivery(self.scope, draft, {})
        self.workbook(draft, second=False)
        with self.assertRaisesRegex(ValueError, 'missing sheet'):
            check_delivery(self.scope, draft, {})
        for missing in (None, '—'):
            self.workbook(draft, last=missing)
            with self.assertRaisesRegex(ValueError, 'unsupported missing value at Fig6-1!C2'):
                check_delivery(self.scope, draft, {})

    def assert_table_diagnosis(self, rows, expected_message):
        path = self.root / 'layout.xlsx'
        book = Workbook()
        for row in rows:
            book.active.append(row)
        book.save(path)
        book.close()
        table = {'sheet': 'Sheet', 'range': 'B2:D3',
                 'columns': [70, 80, 90], 'rows': ['observed', 'simulated']}
        with self.assertRaisesRegex(ValueError, f'^Fig6: {expected_message}'):
            inspect_workbook(path, {'Fig6': table}, final=True)

    def test_complete_transpose_has_specific_diagnostic(self):
        self.assert_table_diagnosis([
            [None, 'observed', 'simulated'], [70, 1, 4], [80, 2, 5], [90, 3, 6]
        ], 'rows and columns are transposed')

    def test_transpose_with_missing_column_is_not_a_complete_transpose(self):
        for rows in (
            [[None, 'observed'], [70, 1], [80, 2], [90, 3]],
            [[None, 'observed', 'simulated'], [70, 1, 4], [80, 2, 5]],
            [[None, 'observed', 'simulated'], [70, 1, 4], [80, 2, None], [90, 3, 6]],
        ):
            with self.subTest(rows=rows):
                self.assert_table_diagnosis(rows, 'rows or columns differ from frozen scope')

    def test_regular_missing_column_keeps_scope_diagnostic(self):
        self.assert_table_diagnosis([
            [None, 70, 90], ['observed', 1, 3], ['simulated', 4, 6]
        ], 'rows or columns differ from frozen scope')

    def test_only_changed_saved_previews_require_visual_review(self):
        original = Image.new('RGBA', (6, 6), 'white')
        drafts = []
        for label in ('stable', 'changed'):
            before = self.root / f'A2_preview_draft_{label}.png'
            after = self.root / f'A2_preview_saved_{label}.png'
            original.save(before)
            saved = original.copy()
            if label == 'changed':
                saved.putpixel((3, 3), (255, 255, 255, 128))
            saved.save(after, compress_level=0)
            drafts.append(str(before))
        self.assertEqual(changed_previews(drafts, 2), [str(after)])

    def test_workbook_values_and_source_gap_match_latest_review(self):
        image = self.root / 'evidence.png'
        Image.new('RGB', (30, 30), 'white').save(image)
        receipt = {'kind': 'source_uncovered', 'reason': 'curve ends before this X',
                   'image': str(image), 'sha256': sha(image), 'bbox': [10, 10, 12, 12]}
        self.scope['cards'] = {'p': {'source': 'Fig6'}}
        result = {'items': [], 'sources': {'s': {'sha256': sha(image)}},
                  'panels': {'panel': {'source': 's'}}}
        evidence = {'missing': {'Fig6-1!C2': receipt, 'Fig6-2!C2': receipt}}
        for table_id, table in self.scope['tables'].items():
            table['points'] = {}
            for address in ('B2', 'C2'):
                point = f'{table_id}-{address}'
                table['points'][address] = {'part': 'p', 'id': point}
                reading = ({'status': 'reviewed', 'value': 1, 'pixel': [11, 11]} if address == 'B2'
                           else {'status': 'explicit_missing', 'value': None,
                                 'review': {'note': receipt['reason']}})
                result['items'].append({'id': point, 'roles': {'value': reading},
                                        'roi': [5, 5, 15, 15], 'panel': 'panel'})
        draft = self.root / 'draft.xlsx'
        self.workbook(draft, last='—')
        check_delivery(self.scope, draft, evidence, {'p': result})
        for index in (1,3):
            result['items'][index]['roles']['value']['review']['missing_evidence'] = dict(receipt)
        check_delivery(self.scope, draft, {}, {'p': result})
        with self.assertRaisesRegex(ValueError, 'conflicting source gap evidence'):
            check_delivery(self.scope,draft,{'missing':{'Fig6-1!C2':{**receipt,'reason':'different'}}},
                           {'p':result})
        result['items'][0]['roles']['value']['value'] = 2
        with self.assertRaisesRegex(ValueError, 'Fig6-1!B2: workbook value'):
            check_delivery(self.scope, draft, evidence, {'p': result})
        result['items'][0]['roles']['value']['value'] = 1
        for index in (1,3):
            del result['items'][index]['roles']['value']['review']['missing_evidence']
        receipt['bbox'] = [20, 20, 22, 22]
        with self.assertRaisesRegex(ValueError, 'missing evidence must match'):
            check_delivery(self.scope, draft, evidence, {'p': result})

    def test_printed_source_wrong_cell_and_wrong_location_fail(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 50), '1')
            page.insert_text((90, 50), '2')
            boxes = [list(page.search_for(token)[0]) for token in ('1', '2')]
            doc.save(self.pdf)
        self.scope['pdf_sha256'] = sha(self.pdf)
        self.scope['inventory']['Fig6']['action'] = 'transcribe'
        self.scope['cards'] = {}
        for table in self.scope['tables'].values():
            table['printed'] = {
                address: {'text': str(i + 1), 'mode': 'text', 'bbox': boxes[i]}
                for i, address in enumerate(('B2', 'C2'))}
        printed = validate_scope(self.scope, self.pdf)
        draft = self.root / 'draft.xlsx'
        self.workbook(draft)
        check_delivery(self.scope, draft, {}, results={}, printed=printed)
        self.workbook(draft, last=3)
        with self.assertRaisesRegex(ValueError, 'Fig6-1!C2: workbook value'):
            check_delivery(self.scope, draft, {}, results={}, printed=printed)
        self.scope['tables']['Fig6-1']['printed']['C2']['bbox'] = boxes[0]
        with self.assertRaisesRegex(ValueError, 'printed token differs'):
            validate_scope(self.scope, self.pdf)

    def test_independent_tick_blocks_reader(self):
        axis = {'kind': 'linear', 'unit': '%', 'ticks': [[0, 0], [20, 20]], 'check_ticks': [[10, 10]]}
        card = {'axes': {'x': dict(axis), 'y': dict(axis)}}
        job = {'axes': {'x': axis, 'y': axis}}
        check_job(card, job, 'vector_window_stats')
        card['axes']['x']['check_ticks'] = [[13, 10]]
        with self.assertRaisesRegex(ValueError, 'independent tick residual'):
            check_job(card, job, 'vector_window_stats')

    def test_mixed_printed_and_geometry_cells_share_one_frozen_table(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 50), '1')
            box = list(page.search_for('1')[0])
            doc.save(self.pdf)
        self.scope['pdf_sha256'] = sha(self.pdf)
        self.scope['cards'] = {'p': {'source': 'Fig6'}}
        for index, table in enumerate(self.scope['tables'].values()):
            table['printed'] = {'B2': {'text':'1','mode':'text','bbox':box}}
            table['points'] = {'C2': {'part':'p','id':f'point-{index}'}}
        printed = validate_scope(self.scope,self.pdf)
        results = {'p': {'_evidence': {'records': [
            {'id':f'point-{index}','value':2,'pdf_xy':[70,50]} for index in range(2)]}}}
        check_delivery(self.scope,self.old,{},results=results,printed=printed)
        report=coverage_report(self.scope,results,printed)
        self.assertEqual(report['counts'],{'numeric_candidate':2,'printed_source':2})
        self.scope['tables']['Fig6-1']['points']['B2']={'part':'p','id':'collision'}
        with self.assertRaisesRegex(ValueError,'partition'):
            validate_scope(self.scope,self.pdf)

    def test_axis_fit_rejects_duplicate_values_at_distinct_positions(self):
        for kind, values in (('linear', [1, 1, 21]), ('log10', [1, 1, 100])):
            with self.subTest(kind=kind):
                axis = {'kind': kind, 'coordinate': 'x', 'unit': '%',
                        'ticks': list(zip([0, 1, 20], values)), 'max_residual_px': 2}
                with self.assertRaisesRegex(ValueError, 'duplicate fitted tick values'):
                    axis_fit(axis)

    def test_configuration_shortened_axis_and_missing_series_fail_before_read(self):
        self.scope['cards'] = {'p': {'source': 'Fig6', 'series': ['observed', 'simulated']}}
        for name, series in [('Fig6-1', 'observed'), ('Fig6-2', 'simulated')]:
            self.scope['tables'][name]['points'] = {
                'B2': {'part': 'p', 'series': series, 'x': 70},
                'C2': {'part': 'p', 'series': series, 'x': 71}}
        job = {'page': 1, 'color_map': {'0,0,0': 'observed', '1,0,0': 'simulated'}, 'sample_at': [70, 71]}
        check_configured_coverage(self.scope, 'p', job, 'vector_lines')
        job['sample_at'] = [70]
        with self.assertRaisesRegex(ValueError, 'Fig6-1!C2'):
            check_configured_coverage(self.scope, 'p', job, 'vector_lines')
        del job['color_map']['1,0,0']
        with self.assertRaisesRegex(ValueError, 'configured series'):
            check_configured_coverage(self.scope, 'p', job, 'vector_lines')

    def test_regular_grid_pairs_horizontal_sampling_and_legend_order(self):
        table = {'sheet': 'Fig6-1', 'range': 'B6:C7', 'columns': ['70', '71'],
                 'rows': ['observed', 'simulated'], 'grid': {'part': 'p'}}
        self.scope['tables'] = {'Fig6-1': table}
        self.scope['inventory']['Fig6']['tables'] = ['Fig6-1']
        axis = {'quantity': 'flow', 'unit': '%', 'kind': 'linear'}
        self.scope['cards'] = {'p': {'source': 'Fig6', 'series': table['rows'],
                                     'zero': 'axis', 'coordinates': 'native pixels',
                                     'axes': {'x': axis, 'y': axis}}}
        part = {'id': 'p', 'scope': {'series': table['rows']}}
        check_cards(self.scope, [part])
        self.assertEqual(cell_bindings(table, 'Fig6-1'), {
            'B6': {'part': 'p', 'series': 'observed', 'x': 70},
            'C6': {'part': 'p', 'series': 'observed', 'x': 71},
            'B7': {'part': 'p', 'series': 'simulated', 'x': 70},
            'C7': {'part': 'p', 'series': 'simulated', 'x': 71}})
        table['grid']['series'] = ['simulated', 'observed']
        with self.assertRaisesRegex(ValueError, 'frozen legend order'):
            check_cards(self.scope, [part])

    def test_two_failures_block_new_task_and_manual_points_remains_available(self):
        part = {'id': 'p', 'method': 'bitmap_points'}
        scope = {'pdf_sha256': sha(self.pdf), 'cards': {'p': {'source': 'Fig6'}}}
        for fingerprint in ('first', 'second', 'third'):
            runner = Runner.__new__(Runner)
            runner.scope = scope
            runner.gate_path = self.root / f'{fingerprint}.json'
            with patch.object(runner, 'command', side_effect=ValueError('engine error')) as command:
                error = 'two failed automatic attempts' if fingerprint == 'third' else 'engine error'
                with self.assertRaisesRegex(ValueError, error):
                    runner.run_extractor(part, fingerprint, [], self.root)
                self.assertEqual(command.call_count, int(fingerprint != 'third'))
        axis = {'kind': 'linear', 'unit': '%', 'ticks': [[0, 0], [20, 20]], 'check_ticks': [[10, 10]]}
        check_job({'axes': {'x': axis, 'y': axis}},
                  {'gate_axes': {'x': axis}, 'panels': {'p': {'axis': axis}}}, 'manual_points')

    def test_manual_category_targets_must_match_independent_printed_positions(self):
        x = {'kind': 'category', 'ticks': [[10, 'Jan'], [25, 'Apr']],
             'check_ticks': [[25, 'Apr']]}
        y = {'kind': 'linear', 'unit': '%', 'ticks': [[0, 0], [20, 20]],
             'check_ticks': [[10, 10]]}
        card = {'axes': {'x': x, 'y': y}}
        job = {'gate_axes': {'x': x}, 'panels': {'p': {'axis': y}},
               'items': [{'id': 'apr', 'identity': {'x': 'Apr'}, 'anchor': [25, 10]}]}
        check_job(card, job, 'manual_points')
        job['items'][0]['anchor'][0] = 21
        with self.assertRaisesRegex(ValueError, 'manual target'):
            check_job(card, job, 'manual_points')
        x['check_ticks'] = [[22, 'Apr']]
        with self.assertRaisesRegex(ValueError, 'independent printed label'):
            check_job(card, job, 'manual_points')

    def test_boxplot_category_allows_offset_but_rejects_adjacent_slot(self):
        x = {'kind': 'category', 'ticks': [[100, 'spring'], [200, 'summer']],
             'check_ticks': [[101, 'spring'], [200, 'summer']]}
        y = {'kind': 'linear', 'unit': '%', 'ticks': [[0, 0], [20, 20]],
             'check_ticks': [[10, 10]]}
        card = {'axes': {'x': x, 'y': y}}
        box = {'id': 'summer-flow', 'identity': {'x': 'summer'}, 'x': 175,
               'roi': [160, 5, 190, 40]}
        job = {'gate_axes': {'x': x}, 'panels': {'p': {'axis': y}}, 'boxplots': [box]}
        check_job(card, job, 'bitmap_bar_ranges')
        box['roi'][0] = 145
        with self.assertRaisesRegex(ValueError, 'crosses its category boundary'):
            check_job(card, job, 'bitmap_bar_ranges')
        box['roi'][0] = 160
        box['identity']['x'] = 'autumn'
        with self.assertRaisesRegex(ValueError, 'unknown boxplot category'):
            check_job(card, job, 'bitmap_bar_ranges')

    def test_manual_trial_keeps_frozen_positions_without_an_extractor(self):
        path = self.root / 'source.png'
        Image.new('RGB', (25, 25), 'white').save(path)
        axis = {'kind': 'linear', 'quantity': 'flow', 'unit': '%',
                'ticks': [[1, 0], [21, 20]], 'check_ticks': [[11, 10]]}
        self.scope['cards'] = {'p': {'source': 'Fig6', 'series': ['observed'],
                                   'zero': 'printed', 'coordinates': 'native pixels',
                                   'axes': {'x': axis, 'y': axis}}}
        for table, points in zip(self.scope['tables'].values(), [(5, 8), (15, 18)]):
            table['points'] = {address: {'part': 'p', 'id': f'p{x}'}
                               for address, x in zip(('B2', 'C2'), points)}
        gate = self.root / 'scope.json'
        write(gate, self.scope)
        part = {'id': 'p', 'method': 'manual_points', 'job': 'job.json', 'trial_job': 'job.json',
                'scope': {'series': ['observed'], 'sampling': 'four positions'}}
        job = {'schema_version': 1, 'sources': {'s': {'image': str(path)}},
               'gate_axes': {'x': axis}, 'panels': {'p': {'source': 's', 'bounds': [1, 1, 22, 22],
                                                       'axis': {**axis, 'coordinate': 'y'}}},
               'items': [{'id': f'p{x}', 'panel': 'p', 'roi': [x - 1, 1, x + 1, 22], 'anchor': [x, 10],
                          'identity': {'series': 'observed', 'x': x}, 'roles': {'value': [[x, 10]]}}
                         for x in (5, 8, 15, 18)]}
        write(self.root / 'job.json', job)
        task = self.root / 'task.json'
        write(task, {'version': 2, 'work_dir': 'work', 'pdf': str(self.pdf), 'parts': [part],
                     'gate': {'file': str(gate), 'sha256': sha(gate)}})
        runner = Runner(task)
        trial = runner.dispatch({'action': 'trial', 'part': 'p'})
        self.assertTrue(trial['ok'], trial)
        from run_task import read
        roles = read(Path(trial['folder']) / 'candidates/review_job.json')['items']
        self.assertEqual([(item['id'], item['roles']) for item in roles],
                         [(f'p{x}', {'value': []}) for x in (5, 8, 15, 18)])
        self.assertEqual(runner.state['executions'], 0)
