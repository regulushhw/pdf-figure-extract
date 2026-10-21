"""Known synthetic geometry and fixed-runner integration, no paper values."""
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image,ImageDraw
import fitz

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from complex_bars import analyze_bar,analyze_capped,run as bar_run
from complex_curves import track,shared_ink,run as curve_run
from run_task import Runner,write,read


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def mask(self,negative=False,caps=False):
        im=Image.new('RGB',(130,140),'white');d=ImageDraw.Draw(im)
        d.rectangle((40,90 if negative else 60,50,110 if negative else 90),fill='black')
        d.line((45,40,45,120),fill='black')
        if caps:
            d.line((39,40,51,40),fill='black');d.line((39,120,51,120),fill='black')
        return np.asarray(im)[:,:,0]<100,im

    def spec(self):return {'id':'bar','panel':'p','identity':{'series':'s'},'roi':[35,10,55,130],'x':45}

    def test_positive_bar_tip_separate_from_stem_endpoints(self):
        mask,_=self.mask();roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[[45,60.]])
        self.assertEqual(roles['uncapped_top'],[[45,40.]])
        self.assertEqual(roles['uncapped_bottom'],[[45,120.]])
        self.assertFalse(flags)

    def test_negative_bar_tip(self):
        mask,_=self.mask(negative=True);roles,_,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[[45,110.]])

    def test_capped_stem_not_mislabeled_as_uncapped(self):
        mask,_=self.mask(caps=True);roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['uncapped_top'],[])
        self.assertTrue(any('has_cap' in f for f in flags))

    def test_capped_pair_requires_two_caps(self):
        mask,_=self.mask(caps=True)
        spec={**self.spec(),'crosses_zero':True}
        roles,flags,_=analyze_capped(mask,spec,90)
        self.assertEqual(len(roles['capped_top']),1)
        mask[120,39:52]=False
        roles,flags,_=analyze_capped(mask,spec,90)
        self.assertFalse(roles['capped_top']);self.assertTrue(flags)

    def test_stem_alone_not_bar_or_zero(self):
        mask=np.zeros((140,130),dtype=bool);mask[30:120,45]=True
        roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[])
        self.assertIn('bar_body_missing_or_two_sided',flags)

    def test_coincident_endpoint_not_invented(self):
        mask,_=self.mask();mask[10:60,:]=False
        roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertFalse(roles['uncapped_top'])
        self.assertIn('uncapped_top_merged_with_body_or_zero',flags)

    def curve(self,dashed=False):
        mask=np.zeros((110,110),dtype=bool)
        if dashed:
            for x in range(10,88,12):mask[40,x:x+6]=True
            spec={'roi':[1,1,108,108],'anchors':[[10,40],[87,40]],'sample_x':[13,18,25,87],
                  'style':{'pattern':'dashed','max_gap_px':6,'width_range':[1,2]}}
        else:
            for x in range(10,91):mask[round(20+x*.5),x]=True
            spec={'roi':[1,1,108,108],'anchors':[[10,25],[90,65]],'sample_x':[10,30,50,70,90]}
        return mask,spec

    def test_clean_solid_does_not_flag_unrelated_ink(self):
        mask,spec=self.curve();mask[90,10:91]=True
        result=track(mask,spec)
        self.assertEqual(result['status'],'proposed_unreviewed')
        self.assertTrue(all(not s['flags'] for s in result['samples']))

    def test_dashed_gap_remains_missing_not_interpolated(self):
        mask,spec=self.curve(True);result=track(mask,spec)
        self.assertEqual(result['status'],'proposed_unreviewed')
        gap=next(s for s in result['samples'] if s['x']==18)
        self.assertIsNone(gap['selected']);self.assertFalse(gap['candidates'])
        self.assertIn('dash_gap_unmeasured',gap['flags'])

    def test_gap_limit_stops_unconnected_trace(self):
        mask,spec=self.curve(True);spec['style']['max_gap_px']=3
        self.assertEqual(track(mask,spec)['status'],'disconnected')

    def test_solid_does_not_skip_visible_ink_to_lower_cost(self):
        mask,spec=self.curve();spec['style']={'pattern':'solid','max_gap_px':2}
        result=track(mask,spec)
        self.assertEqual(result['missing_x'],[])

    def test_range_can_lie_wholly_below_zero_inside_bar(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[90:111,40:51]=True;mask[97:126,45]=True
        roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['uncapped_bottom'],[[45,125.]])

    def test_crossing_preserves_end_anchor_and_marks_shared_ink(self):
        mask,spec=self.curve()
        for x in range(10,91):mask[round(70-x*.5),x]=True
        a={'id':'a','panel':'p','result':track(mask,spec)}
        other={**spec,'anchors':[[10,65],[90,25]]}
        b={'id':'b','panel':'p','result':track(mask,other)}
        shared_ink([a,b])
        for s in a['result']['samples']:
            self.assertAlmostEqual(s['selected'][1],round(20+s['x']*.5),delta=1)
        self.assertTrue(any('shared_ink_identity_review' in s['flags'] for s in a['result']['samples']))

    def test_shared_pixels_never_assert_equal_series(self):
        mask,spec=self.curve();a={'panel':'p','result':track(mask,spec)};b={'panel':'p','result':track(mask,spec)}
        shared_ink([a,b]);self.assertTrue(all('shared_ink_identity_review' in s['flags'] for s in a['result']['samples']))

    def test_dash_style_on_continuous_line_is_not_verified(self):
        mask,spec=self.curve();spec['style']={'pattern':'dashed','max_gap_px':4}
        result=track(mask,spec)
        self.assertTrue(all('dash_pattern_not_observed' in s['flags'] for s in result['samples']))

    def test_missing_anchor_or_invalid_sampling_refused(self):
        mask,spec=self.curve();spec['sample_x']=[109]
        with self.assertRaisesRegex(ValueError,'sample_x'):track(mask,spec)

    def common_job(self,image):
        image.save(self.root/'source.png')
        return {'sources':{'s':{'image':'source.png'}},'panels':{'p':{'source':'s','bounds':[1,1,128,138],
                 'zero_y':90,'axis':{'coordinate':'y','ticks':[[40,50],[90,0],[120,-30]],'unit':'%'}}}}

    def test_bars_fixed_runner_emits_review_contract(self):
        _,image=self.mask();job=self.common_job(image);job['bars']=[self.spec()]
        write(self.root/'job.json',job)
        with fitz.open() as doc:doc.new_page();doc.save(self.root/'paper.pdf')
        write(self.root/'task.json',{'version':1,'pdf':'paper.pdf','work_dir':'run','parts':[
            {'id':'p','method':'bitmap_bar_ranges','job':'job.json','trial_job':'job.json',
             'scope':{'series':['s'],'sampling':'one synthetic bar'}}]})
        result=Runner(self.root/'task.json').dispatch({'action':'trial','part':'p'})
        self.assertTrue(result['ok'],result)
        review=read(Path(result['folder'])/'candidates/review_job.json')
        self.assertEqual(review['items'][0]['roles']['bar_tip'],[[45,60.]])

    def test_curves_emit_missing_role_in_gap(self):
        mask,spec=self.curve(True)
        image=Image.fromarray(np.where(mask,0,255).astype('uint8')).convert('RGB')
        image.save(self.root/'curve.png')
        job={'sources':{'s':{'image':'curve.png'}},'panels':{'p':{'source':'s','bounds':[1,1,108,108],
             'axis':{'coordinate':'y','ticks':[[10,100],[100,0]],'unit':'flow'}}},
             'traces':[{**spec,'id':'d','panel':'p','identity':{'series':'d'},'selector':{'mode':'gray','range':[0,80]}}]}
        write(self.root/'curve.json',job);summary=curve_run(self.root/'curve.json',self.root/'result')
        self.assertEqual(summary['missing_roles'],1)


if __name__=='__main__':unittest.main()
