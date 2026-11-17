import sys
import unittest
import json
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from vector_windows import window_stats,straight_segments


class WindowTests(unittest.TestCase):
    def test_nonuniform_triangle_mean_is_time_weighted(self):
        r=window_stats([((0,0),(2,10)),((2,10),(10,0))],0,10)
        self.assertEqual(r['status'],'complete')
        self.assertEqual((r['min'],r['max'],r['mean']),(0,10,5))

    def test_vertical_ink_changes_extreme_not_integral(self):
        r=window_stats([((0,2),(5,2)),((5,2),(5,20)),((5,20),(5,2)),((5,2),(10,2))],0,10)
        self.assertEqual(r['max'],20);self.assertEqual(r['mean'],2)

    def test_gap_and_partial_window_never_fill(self):
        for segs in [[((0,1),(4,1)),((6,1),(10,1))],[((1,1),(10,1))],[]]:
            r=window_stats(segs,0,10)
            self.assertEqual(r['status'],'source_window_uncovered')
            self.assertIsNone(r['mean']);self.assertIsNone(r['max'])

    def test_different_overlapping_curves_are_ambiguous(self):
        r=window_stats([((0,1),(10,1)),((0,3),(10,3))],0,10)
        self.assertEqual(r['status'],'ambiguous_curve');self.assertIsNone(r['min'])

    def test_duplicate_segments_do_not_double_integrate(self):
        r=window_stats([((0,1),(10,3)),((10,3),(0,1))],0,10)
        self.assertEqual(r['mean'],2)

    def test_clipping_uses_boundary_intersections(self):
        r=window_stats([((-2,-4),(3,6))],0,1)
        self.assertEqual((r['min'],r['max'],r['mean']),(0,2,1))

    def test_bad_window_and_nonfinite_input_rejected(self):
        for start,stop in [(1,1),(2,1),(0,float('nan'))]:
            with self.assertRaises(ValueError):window_stats([],start,stop)
        with self.assertRaises(ValueError):window_stats([((0,float('inf')),(1,1))],0,1)

    def test_only_collinear_quads_are_supported(self):
        import fitz
        q=fitz.Quad(fitz.Point(2,1),fitz.Point(2,3),fitz.Point(2,8),fitz.Point(2,4))
        s=straight_segments({'items':[('qu',q)]})
        self.assertEqual(set(s[0]),{(2,1),(2,8)})
        with self.assertRaises(ValueError):straight_segments({'items':[('qu',fitz.Rect(0,0,2,3).quad)]})
        with self.assertRaises(ValueError):straight_segments({'items':[('c',fitz.Point(0,0))]})

    def test_real_pdf_and_fixed_runner_preserve_all_statistics(self):
        import fitz
        from vector_windows import run
        from run_task import Runner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf=root/'curves.pdf';doc=fitz.open();page=doc.new_page(width=150,height=110)
            shape=page.new_shape();shape.draw_polyline([(10,90),(60,10),(110,90)]);shape.finish(color=(0,0,0),width=1,closePath=False);shape.commit()
            page.draw_line((10,70),(110,70),color=(.5,.5,.5),width=2);doc.save(pdf);doc.close()
            job={'pdf':str(pdf),'page':1,'frame':[10,10,110,90],
                 'axes':{'x':{'kind':'linear','unit':'time','ticks':[[10,0],[110,10]]},
                         'y':{'kind':'linear','unit':'flow','ticks':[[90,0],[10,8]]}},
                 'series':[{'id':'black','name':'black','color':'0,0,0','width_range':[.9,1.1],'line_style':'solid'},
                           {'id':'gray','name':'gray','color':'.5,.5,.5','width_range':[1.9,2.1],'line_style':'solid'}],
                 'windows':[{'id':'all','identity':{'period':'all'},'start':0,'stop':10,'statistics':['min','max','mean']}],
                 'prefix':str(root/'direct')}
            source=root/'job.json';source.write_text(json.dumps(job),encoding='utf8');reply=run(source)
            self.assertEqual(reply['complete'],6)
            data=json.loads((root/'direct.json').read_text());values={(r['series'],r['statistic']):r['value'] for r in data['records']}
            self.assertAlmostEqual(values['black','mean'],4);self.assertAlmostEqual(values['black','max'],8)
            self.assertAlmostEqual(values['gray','mean'],2)
            task={'version':1,'pdf':str(pdf),'work_dir':'runner','parts':[{'id':'p','method':'vector_window_stats',
                  'job':str(source),'trial_job':str(source),'scope':{'series':['black','gray'],'sampling':'whole curve min/max/mean'}}]}
            taskfile=root/'task.json';taskfile.write_text(json.dumps(task),encoding='utf8')
            o=Runner(taskfile).dispatch({'action':'trial','part':'p'});self.assertTrue(o['ok'],o)
            routed=json.loads((Path(o['folder'])/'data.json').read_text());self.assertEqual(len(routed['_evidence']['records']),6)
            job['series'][0]['color']='1,0,0';source.write_text(json.dumps(job),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'no source paths'):run(source)


if __name__=='__main__':unittest.main()
