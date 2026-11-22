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
from complex_bars import analyze_bar,analyze_boxplot,analyze_capped,body_baseline_at,fill_bar_interiors,locate_group_slots,run as bar_run
from complex_curves import track,shared_ink,run as curve_run
from propose_bitmap_points import select_pixels
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

    def test_boxplot_requires_connected_whiskers(self):
        mask = np.zeros((75, 55), dtype=bool)
        mask[[20, 30, 41], 15:36] = True
        mask[20:42, [15, 35]] = True
        mask[9:21, 25] = True
        mask[[9, 55], 20:31] = True
        roles, flags, _ = analyze_boxplot(mask, {'roi': [11, 5, 39, 60], 'x': 25, 'box_half_width': 10})
        self.assertEqual(roles['median'], [[25, 30]])
        self.assertEqual(roles['upper_whisker'], [[25, 9]])
        self.assertEqual(roles['lower_whisker'], [])
        self.assertEqual(flags, ['lower_whisker_ambiguous'])

    def test_boxplot_separates_colored_roles(self):
        outline = np.zeros((75, 55), dtype=bool)
        outline[[20, 41], 15:36] = True
        outline[20:42, [15, 35]] = True
        median = np.zeros_like(outline)
        median[30, 15:36] = True
        whiskers = np.zeros_like(outline)
        whiskers[9:20, 25] = whiskers[42:56, 25] = True
        whiskers[[9, 55], 20:31] = True
        spec = {'roi': [11, 5, 39, 60], 'x': 25, 'box_half_width': 10}
        roles, flags, _ = analyze_boxplot(outline, spec, median, whiskers)
        self.assertEqual(roles['median'], [[25, 30]])
        self.assertEqual(roles['lower_whisker'], [[25, 55]])
        self.assertFalse(flags)
        median[33, 15:36] = True
        roles, flags, _ = analyze_boxplot(outline, spec, median, whiskers)
        self.assertEqual(roles['median'], [])
        self.assertEqual(flags, ['box_horizontal_bands_ambiguous'])

    def test_boxplot_color_selector_keeps_antialiased_ink(self):
        rgb = np.array([[[0, 0, 255], [128, 128, 255], [250, 250, 255],
                         [255, 0, 0], [0, 0, 0]]], dtype=np.uint8)
        result = select_pixels(rgb, {'mode': 'dominant_channel', 'channel': 'blue', 'margin': 81})
        self.assertEqual(result.tolist(), [[True, True, False, False, False]])

    def test_positive_bar_tip_separate_from_stem_endpoints(self):
        mask,_=self.mask();roles,flags,evidence=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[[45,60.]])
        self.assertEqual(roles['uncapped_top'],[[45,40.]])
        self.assertEqual(roles['uncapped_bottom'],[[45,120.]])
        self.assertFalse(flags)
        self.assertEqual(evidence['role_y_bounds_px'],
                         {'bar_tip':[60,60], 'uncapped_top':[40,40], 'uncapped_bottom':[120,120]})

    def test_negative_bar_tip(self):
        mask,_=self.mask(negative=True);roles,_,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[[45,110.]])

    def test_uncapped_guide_returns_original_source_endpoint_pixels(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[60:91,54:67]=True
        for y in range(40,121):
            x=round(62-.08*(y-45))
            mask[y,x]=True
        spec={'roi':[48,20,73,130],'x':60,'stem_guide':[[62,45],[57.2,105]]}
        roles,flags,evidence=analyze_bar(mask,spec,90)
        self.assertEqual(roles['bar_tip'],[[60,60.]])
        self.assertEqual(roles['uncapped_top'],[[62,40.]])
        self.assertEqual(roles['uncapped_bottom'],[[56,120.]])
        self.assertFalse(flags)
        self.assertEqual(evidence['stem_guide'],spec['stem_guide'])

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

    def test_thick_uncapped_stem_does_not_supply_its_own_cap(self):
        mask,_ = self.mask()
        mask[40:61,43:48] = True
        mask[40,43] = mask[40,47] = False
        roles,flags,_ = analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['uncapped_top'], [[45,40.]])
        self.assertNotIn('uncapped_top_has_cap_requires_review', flags)

    def test_thick_capped_stroke_is_one_pair_but_parallel_stems_remain_separate(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[40:121,40:51]=True
        mask[40:43,35:56]=True
        mask[118:121,35:56]=True
        spec={'roi':[34,20,56,130],'crosses_zero':True}
        roles,flags,evidence=analyze_capped(mask,spec,90)
        self.assertEqual(len(roles['capped_top']),1)
        self.assertFalse(flags)
        self.assertGreater(len(evidence['pairs'][0]['contributing_stem_columns']),1)
        self.assertEqual(evidence['role_y_bounds_px'], {'capped_top':[40,42], 'capped_bottom':[118,120]})
        mask[41:120,42:48]=False
        roles,flags,_=analyze_capped(mask,spec,90)
        self.assertEqual(len(roles['capped_top']),2)
        self.assertIn('capped_pair_missing_or_ambiguous',flags)
        roles, flags, evidence = analyze_capped(mask,{**spec,'stem_x_window':[39,42]},90)
        self.assertEqual(len(roles['capped_top']),1)
        self.assertTrue(39 <= roles['capped_top'][0][0] <= 42)
        self.assertFalse(flags)
        self.assertEqual(len(evidence['outside_stem_window']),1)

    def test_stem_alone_not_bar_or_zero(self):
        mask=np.zeros((140,130),dtype=bool);mask[30:120,45]=True
        roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['bar_tip'],[])
        self.assertIn('bar_body_missing_or_two_sided',flags)

    def test_skewed_body_contact_keeps_original_tip_geometry(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[60:85,40:51]=True
        spec={**self.spec(),'range_expected':False}
        panel={'bounds':[1,1,128,138],'body_baseline':[[5,94],[85,76]]}
        self.assertFalse(analyze_bar(mask,spec,90)[0]['bar_tip'])
        self.assertEqual(analyze_bar(mask,spec,body_baseline_at(panel,45))[0]['bar_tip'],[[45,60.]])

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
        mask,spec=self.curve(True)
        for pattern in ('dashed', 'dotted'):
            with self.subTest(pattern=pattern):
                result = track(mask, {**spec,'style':{**spec['style'],'pattern':pattern}})
                self.assertEqual(result['status'],'proposed_unreviewed')
                gap=next(s for s in result['samples'] if s['x']==18)
                self.assertIsNone(gap['selected']);self.assertFalse(gap['candidates'])
                self.assertIn('dash_gap_unmeasured',gap['flags'])

    def test_dash_fragment_route_recovers_bracketed_gap_beside_solid_line(self):
        mask,spec=self.curve(True)
        mask[44,10:88]=True
        spec.update(dash_fragment_span_px=8,interpolate_dash_gaps=True)
        result=track(mask,spec)
        gap=next(s for s in result['samples'] if s['x']==18)
        self.assertTrue(result['dash_fragment_path'])
        self.assertEqual(gap['selected'],[18,40])
        self.assertIn('local_dash_interpolation_review',gap['flags'])
        self.assertTrue(gap['interpolation_bracket'])

    def test_auto_anchors_keep_uncovered_positions_on_native_large_mask(self):
        mask=np.zeros((700,900),dtype=bool)
        mask[315,100:801]=True
        spec={'roi':[1,1,898,698],'auto_anchors':True,'sample_x':[70,100,450,800,830]}
        result=track(mask,spec)
        self.assertEqual(result['anchors'],[[100,315],[800,315]])
        self.assertEqual([s['selected'] for s in result['samples']],
                         [None,[100,315],[450,315],[800,315],None])

    def test_auto_anchors_refuse_two_unseparated_identities(self):
        mask,spec=self.curve()
        mask[90,10:91]=True
        spec.pop('anchors')
        spec['auto_anchors']=True
        result=track(mask,spec)
        self.assertEqual(result['status'],'disconnected')
        self.assertTrue(all(s['flags']==['no_isolated_ink_anchors'] for s in result['samples']))

    def test_auto_anchor_uses_track_width_to_reject_neighbour(self):
        mask=np.zeros((80,130),dtype=bool)
        mask[30,10:121]=True
        mask[52:58,10:121]=True
        spec={'roi':[1,1,128,78],'auto_anchors':True,'sample_x':[10,65,120],
              'style':{'pattern':'solid','width_range':[1,2]}}
        self.assertEqual([s['selected'] for s in track(mask,spec)['samples']],
                         [[10,30],[65,30],[120,30]])

    def test_component_span_removes_isolated_gray_fragments_before_tracking(self):
        mask=np.zeros((80,130),dtype=bool)
        mask[30,10:121]=True
        for x in range(10,121,5):
            mask[52,x:x+4]=True
        spec={'roi':[1,1,128,78],'auto_anchors':True,'sample_x':[10,65,120],
              'min_component_span_px':10}
        self.assertEqual([s['selected'] for s in track(mask,spec)['samples']],
                         [[10,30],[65,30],[120,30]])

    def test_horizontal_stroke_detection_requires_broad_ink_and_does_not_erase_it(self):
        mask=np.zeros((80,130),dtype=bool)
        mask[30,10:121]=True
        mask[45,1:129]=True
        mask[61,12:32]=True
        from complex_curves import find_horizontal_strokes
        rows = find_horizontal_strokes(mask,[1,1,128,78],[46,61])
        self.assertEqual(rows,[45])
        self.assertTrue(mask[45,50])

    def test_flat_data_curve_is_retained_even_at_a_suspected_rule_row(self):
        mask=np.zeros((80,130),dtype=bool)
        mask[45,1:129]=True
        common={'roi':[1,1,128,78],'auto_anchors':True,'sample_x':[10,65,120]}
        before=track(mask,common)
        after=track(mask,{**common,'axis_rule_rows':[45]})
        self.assertEqual(after['horizontal_stroke_rows'],[45])
        self.assertEqual([s['selected'] for s in before['samples']],
                         [[10,45],[65,45],[120,45]])
        self.assertEqual([s['selected'] for s in after['samples']],
                         [s['selected'] for s in before['samples']])
        self.assertTrue(all('horizontal_stroke_identity_review' in s['flags'] for s in after['samples']))

    def test_automatic_dash_does_not_offer_continuous_neighbour(self):
        mask,spec=self.curve()
        spec.pop('anchors')
        spec.update(auto_anchors=True,style={'pattern':'dashed','max_gap_px':4})
        result=track(mask,spec)
        self.assertTrue(all(not s['candidates'] and 'dash_pattern_not_observed' in s['flags']
                            for s in result['samples']))

    def test_auto_trace_keeps_both_sides_of_a_long_gap(self):
        mask=np.zeros((80,220),dtype=bool)
        mask[30,10:90]=True
        mask[30,120:211]=True
        spec={'roi':[1,1,218,78],'auto_anchors':True,'sample_x':[10,50,100,150,210]}
        result=track(mask,spec)
        self.assertEqual(result['status'],'partial_unreviewed')
        self.assertEqual([s['selected'] for s in result['samples']],
                         [[10,30],[50,30],None,[150,30],[210,30]])
        self.assertIn('recovered_from_right_anchor_review',result['samples'][3]['flags'])

    def test_gap_limit_stops_unconnected_trace(self):
        mask,spec=self.curve(True);spec['style']['max_gap_px']=3
        self.assertEqual(track(mask,spec)['status'],'disconnected')

    def test_optional_dash_interpolation_keeps_ink_bracket(self):
        mask,spec=self.curve(True)
        spec['interpolate_dash_gaps']=True
        gap=next(s for s in track(mask,spec)['samples'] if s['x']==18)
        self.assertEqual(gap['selected'],[18,40])
        self.assertEqual(gap['interpolation_bracket'],[[15,40],[22,40]])
        self.assertIn('local_dash_interpolation_review',gap['flags'])

    def test_solid_does_not_skip_visible_ink_to_lower_cost(self):
        mask,spec=self.curve();spec['style']={'pattern':'solid','max_gap_px':2}
        result=track(mask,spec)
        self.assertEqual(result['missing_x'],[])

    def test_range_can_lie_wholly_below_zero_inside_bar(self):
        mask=np.zeros((140,130),dtype=bool)
        mask[90:111,40:51]=True;mask[97:126,45]=True
        roles,flags,_=analyze_bar(mask,self.spec(),90)
        self.assertEqual(roles['uncapped_bottom'],[[45,125.]])

    def test_wide_shared_ink_keeps_both_anchored_paths_reviewable(self):
        mask=np.zeros((50,70),dtype=bool)
        mask[10,5:61]=mask[30,5:61]=True
        mask[10:31,20:36]=True
        traces=[]
        for y in (10,30):
            spec={'roi':[4,4,62,40],'anchors':[[5,y],[60,y]],
                  'sample_x':[15,25,40],'max_step_px':3,'style':{'width_range':[1,3]}}
            traces.append({'id':str(y),'panel':'p','result':track(mask,spec)})
        shared_ink(traces)
        for trace,y in zip(traces,(10,30)):
            left,overlap,right=trace['result']['samples']
            self.assertEqual([item['selected'][1] for item in (left,overlap,right)],[y,y,y])
            self.assertEqual(overlap['observed_ink_y_bounds'],[10,30])
            self.assertIn('merged_ink_or_pen_width_conflict',overlap['flags'])
            self.assertFalse(left.get('shared_ink_spans'))
        # An inferred path is not a shared value when the tracks resolve to
        # different positions in the same broad band.
        self.assertNotEqual(traces[0]['result']['samples'][1]['selected'],
                            traces[1]['result']['samples'][1]['selected'])

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

    def test_shared_range_requires_two_observed_pen_bounds(self):
        def trace(name, bounds, interpolation=False):
            point={'x':5,'selected':[5,10],'flags':[], 'observed_ink_y_bounds':bounds}
            if interpolation:
                point['interpolation_bracket']=[[4,10],[6,10]]
            return {'id':name,'panel':'p','result':{'status':'proposed_unreviewed',
                    'selected_path':[[5,10]],'samples':[point]}}
        for interpolated in (False, True):
            first, second = trace('a',[9,11]), trace('b',[10,12],interpolated)
            shared_ink([first,second])
            span = first['result']['samples'][0]['shared_ink_spans'][0]
            self.assertEqual(span['peer_trace_id'],'b')
            self.assertEqual(span.get('shared_source_y_bounds_px'), None if interpolated else [10,11])

    def test_detected_horizontal_data_remains_a_readable_review_candidate(self):
        mask = np.zeros((110, 110), dtype=bool)
        mask[45, 5:106] = True
        Image.fromarray(np.where(mask,0,255).astype('uint8')).save(self.root/'curve.png')
        job={'sources':{'s':{'image':'curve.png'}},'panels':{'p':{'source':'s','bounds':[1,1,108,108],
             'axis':{'coordinate':'y','ticks':[[10,100],[100,0]],'check_ticks':[[45,61.111111]],'unit':'flow'}}},
             'traces':[{'id':'flat','panel':'p','identity':{'series':'flat'},'roi':[5,10,105,100],
                        'anchors':[[10,45],[100,45]],'sample_x':[55],
                        'selector':{'mode':'gray','range':[0,80]}}]}
        write(self.root/'curve.json',job)
        curve_run(self.root/'curve.json',self.root/'result')
        result=read(self.root/'result/geometry.json')['records'][0]['result']
        self.assertEqual(result['horizontal_review_candidates'][0]['row'],45)
        self.assertEqual(result['samples'][0]['selected'],[55,45])
        self.assertIn('horizontal_stroke_identity_review',result['samples'][0]['flags'])


if __name__=='__main__':unittest.main()
