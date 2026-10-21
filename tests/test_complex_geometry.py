"""Known synthetic geometry and fixed-runner integration, no paper values."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
import fitz

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from complex_bars import analyze_bar,analyze_capped,fill_bar_interiors,locate_group_slots,run as bar_run
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

    def test_closed_outline_interior_supports_body_without_filling_background(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[60:91,40]=True;mask[60:91,50]=True
        mask[60,40:51]=True;mask[90,40:51]=True
        filled=fill_bar_interiors(mask,10)
        self.assertTrue(filled[70,45]);self.assertFalse(filled[70,30])
        spec={**self.spec(),'range_expected':False}
        roles,_,_=analyze_bar(mask,spec,90,filled)
        self.assertEqual(roles['bar_tip'],[[45,60.]])
        self.assertFalse(mask[70,45])

    def test_open_outline_and_wide_plot_background_not_filled(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[20:111,10]=True;mask[20:111,100]=True
        mask[20,10:101]=True;mask[110,10:101]=True
        filled=fill_bar_interiors(mask,10)
        self.assertFalse(filled[70,45])
        mask[60:91,40]=True;mask[60:91,50]=True;mask[90,40:51]=True
        filled=fill_bar_interiors(mask,10)
        self.assertFalse(filled[70,45])

    def test_fill_width_must_be_explicit_and_bounded(self):
        for width in (True,1,41,3.5):
            with self.assertRaises(ValueError):fill_bar_interiors(np.zeros((10,10),dtype=bool),width)

    def test_bar_only_near_edge_does_not_truncate_to_shorter_neighbour(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[35:91,40:51]=True;mask[60:91,51:59]=True
        spec={**self.spec(),'x':49,'roi':[45,10,53,130],'range_expected':False,'min_body_width':3}
        roles,_,_=analyze_bar(mask,spec,90)
        self.assertEqual(roles['bar_tip'],[[49,35.]])
        # Range-bearing charts keep their separate symmetric stem rule.
        roles,_,_=analyze_bar(mask,{**spec,'range_expected':True},90)
        self.assertEqual(roles['bar_tip'],[[49,60.]])

    def test_bar_only_negative_near_edge_reaches_actual_tip(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[90:126,40:51]=True;mask[90:106,51:59]=True
        roles,_,_=analyze_bar(mask,{**self.spec(),'x':49,'roi':[45,10,53,130],
            'range_expected':False,'min_body_width':3},90)
        self.assertEqual(roles['bar_tip'],[[49,125.]])

    def grouped(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[40:101,10:35]=True;mask[40:101,60:85]=True
        panel={'bounds':[1,1,128,138],'zero_y':70}
        spec={'left':5,'right':110,'groups':[['a0','a1','a2'],['b0','b1','b2']],
              'within_group':'equal_width','min_group_width':20}
        return mask,panel,spec

    def test_group_slots_use_detected_spans_and_explicit_id_order(self):
        mask,panel,spec=self.grouped();slots=locate_group_slots(mask,panel,spec)
        self.assertEqual(list(slots),['a0','a1','a2','b0','b1','b2'])
        self.assertEqual(slots['a0']['group_span'],[10,34])
        self.assertAlmostEqual(slots['a0']['x'],13.667)
        self.assertEqual(slots['a2']['group_span'],[10,34])

    def test_group_count_or_width_mismatch_refused(self):
        mask,panel,spec=self.grouped()
        with self.assertRaises(ValueError):locate_group_slots(mask,panel,{**spec,'groups':[['one','two']]})
        with self.assertRaises(ValueError):locate_group_slots(mask,panel,{**spec,'min_bar_width':10})

    def test_group_membership_does_not_infer_equal_width_or_repeat_ids(self):
        mask,panel,spec=self.grouped()
        with self.assertRaises(ValueError):locate_group_slots(mask,panel,{**spec,'within_group':'guess'})
        with self.assertRaises(ValueError):locate_group_slots(mask,panel,{**spec,'groups':[['a','a'],['b','c']]})

    def test_grouped_run_reuses_mask_and_preserves_source_and_slots(self):
        from propose_bitmap_points import select_pixels
        from review_points import digest
        mask,panel,layout=self.grouped()
        mask[71:]=False
        image=self.root/'groups.png';Image.fromarray(np.where(mask,0,255).astype('uint8')).convert('RGB').save(image)
        before=digest(image)
        panel.update(source='s',axis={'coordinate':'y','ticks':[[10,100],[130,0]],'unit':'%'})
        bars=[{'id':ident,'panel':'p','identity':{'series':ident},'x':20,
               'roi':[5,5,110,130],'range_expected':False,'min_body_width':3}
              for group in layout['groups'] for ident in group]
        job=self.root/'group_job.json';write(job,{'sources':{'s':{'image':str(image)}},
            'panels':{'p':panel},'bars':bars,'fill_outline_max_width':8,
            'bar_groups':[{**layout,'panel':'p'}]})
        with patch('complex_bars.select_pixels',wraps=select_pixels) as select:
            bar_run(job,self.root/'group_result');self.assertEqual(select.call_count,1)
        result=read(self.root/'group_result/review_job.json')
        self.assertEqual([i['id'] for i in result['items']],[b['id'] for b in bars])
        self.assertTrue(all(i['roles']['bar_tip'][0][1]==40 for i in result['items']))
        self.assertEqual(result['items'][-1]['complex_evidence']['slot_location']['previous_x'],20)
        self.assertEqual(digest(image),before)

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
