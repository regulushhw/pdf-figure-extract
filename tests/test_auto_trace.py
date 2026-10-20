"""Routing and known-geometry checks; not universal chart accuracy."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from auto_trace_bitmap import route_trace,graph_path,run
from review_points import write_json,prepare


class AutoTraceTests(unittest.TestCase):
    def fixture(self,kind):
        im=Image.new('RGB',(110,110),'white');d=ImageDraw.Draw(im)
        if kind=='spike':d.line([(10,60),(40,60),(50,15),(60,60),(90,60)],fill=(145,145,145),width=1)
        else:d.line((10,60,90,60),fill=(145,145,145),width=1)
        if kind=='occlusion':d.line((50,30,50,90),fill='black',width=3)
        if kind=='white_gap':d.rectangle((45,59,55,61),fill='white')
        if kind=='large_occlusion':d.rectangle((35,58,65,62),fill='black')
        t={'id':'known','panel':'a','identity':{'series':'synthetic gray'},'roi':[5,5,100,100],
           'selector':{'mode':'gray','range':[130,160]},'anchors':[[10,60],[90,60]],
           'max_step_px':2,'sample_x':[10,30,50,70,90]}
        return np.array(im),t

    def test_clean_selects_columns_and_known_values(self):
        rgb,t=self.fixture('clean');r=route_trace(rgb,t)
        self.assertEqual(r['selected_method'],'column_path')
        self.assertTrue(all(s['candidates']==[[s['x'],60.]] for s in r['samples']))

    def test_vertical_spike_uses_graph_without_flattening(self):
        rgb,t=self.fixture('spike');r=route_trace(rgb,t)
        self.assertEqual(r['selected_method'],'ink_graph')
        self.assertLessEqual(abs(r['samples'][2]['candidates'][0][1]-15),1)

    def test_dark_occlusion_routes_and_flags_instead_of_silent_fill(self):
        rgb,t=self.fixture('occlusion');r=route_trace(rgb,t)
        self.assertEqual(r['selected_method'],'occlusion_graph')
        self.assertLessEqual(abs(r['samples'][2]['candidates'][0][1]-60),1)
        self.assertIn('dark_occlusion_route_requires_review',r['samples'][2]['flags'])
        self.assertGreater(r['methods']['occlusion_graph']['nonpreferred_pixels'],0)

    def test_white_gap_is_not_interpolated(self):
        rgb,t=self.fixture('white_gap');r=route_trace(rgb,t)
        self.assertIsNone(r['selected_method'])
        self.assertEqual(r['samples'][2]['candidates'],[])

    def test_large_occlusion_rejected(self):
        rgb,t=self.fixture('large_occlusion');r=route_trace(rgb,t)
        self.assertIsNone(r['selected_method'])
        self.assertEqual(r['methods']['occlusion_graph']['status'],'excess_occlusion')

    def test_masks_apply_to_all_methods(self):
        rgb,t=self.fixture('clean');t['exclude_boxes']=[[45,5,55,100]]
        self.assertIsNone(route_trace(rgb,t)['selected_method'])

    def test_invalid_graph_bounds_and_anchor_rejected(self):
        rgb,t=self.fixture('clean')
        with self.assertRaises(ValueError):graph_path(rgb,[-1,5,100,100],t['selector'],t['anchors'])
        with self.assertRaises(ValueError):graph_path(rgb,t['roi'],t['selector'],[[10,60],[120,60]])

    def test_end_to_end_preserves_image_and_connects_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);rgb,t=self.fixture('occlusion');Image.fromarray(rgb).save(p/'figure.png')
            before=(p/'figure.png').read_bytes()
            job={'sources':{'s':{'image':'figure.png'}},'panels':{'a':{'source':'s','bounds':[1,1,108,108],
                  'axis':{'coordinate':'y','ticks':[[10,100],[90,0]],'unit':'%'}}},'traces':[t]}
            write_json(p/'job.json',job);r=run(p/'job.json',p/'auto')
            self.assertEqual(r['routes'][0]['selected'],'occlusion_graph')
            batch=prepare(r['job'],p/'review',sample_every=1)
            self.assertTrue(Path(batch['manifest']).exists())
            self.assertEqual((p/'figure.png').read_bytes(),before)
            with self.assertRaises(ValueError):run(p/'job.json',p/'auto')


if __name__=='__main__':unittest.main()
