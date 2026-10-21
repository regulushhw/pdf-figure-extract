"""Bitmap proposals through existing review interface; synthetic evidence only."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image,ImageDraw
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from propose_bitmap_points import propose,locate_bar_groups
from review_points import read_json,write_json,prepare


class BitmapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        image=Image.new('RGB',(100,100),'white');d=ImageDraw.Draw(image)
        d.line((10,30,90,30),fill='black',width=3)
        d.line((10,70,90,70),fill='black',width=3)
        d.line((10,50,90,50),fill=(150,150,150),width=3)
        image.save(self.root/'figure.png')
        self.job={'schema_version':1,'sources':{'s':{'image':'figure.png','figure':'synthetic'}},
                  'panels':{'p':{'source':'s','bounds':[1,1,98,98],
                                 'axis':{'coordinate':'y','ticks':[[10,100],[90,0]],'unit':'%'}}},
                  'items':[{'id':'point','panel':'p','identity':{'series':'declared from legend'},
                            'anchor':[50,50],'roi':[5,5,95,95],
                            'selector':{'mode':'gray','range':[0,60]}}]}

    def run_propose(self,name='out'):
        jp=self.root/'job.json';write_json(jp,self.job)
        summary=propose(jp,self.root/name)
        return read_json(summary['job']),summary

    def test_two_tracks_never_averaged_to_blank_middle(self):
        job,_=self.run_propose();points=job['items'][0]['roles']['value']
        self.assertEqual(points,[[50,30],[50,70]])
        self.assertIn('multiple_ink_runs',job['items'][0]['flags'])

    def test_exclusion_removes_legend_without_erasing_other_track(self):
        self.job['items'][0]['exclude_boxes']=[[5,65,95,75]]
        job,_=self.run_propose()
        self.assertEqual(job['items'][0]['roles']['value'],[[50,30]])

    def test_gray_selector_preserves_single_candidate_as_unreviewed(self):
        self.job['items'][0]['selector']['range']=[140,160]
        job,result=self.run_propose()
        self.assertEqual(job['items'][0]['roles']['value'],[[50,50]])
        p=prepare(result['job'],self.root/'review',sample_every=1)
        self.assertTrue(Path(p['manifest']).exists())
        self.assertFalse(job['items'][0]['proposal_evidence']['identity_verified'])

    def test_missing_not_filled_and_neighbor_ties_retained(self):
        self.job['items'][0]['anchor']=[9,50]
        job,result=self.run_propose()
        self.assertEqual(result['missing'],1)
        self.assertEqual(job['items'][0]['roles']['value'],[])
        self.job['items'][0]['neighbor_px']=1
        job,_=self.run_propose('near')
        self.assertEqual(job['items'][0]['roles']['value'],[[10,30],[10,70]])
        self.assertIn('neighbor_column_not_target',job['items'][0]['flags'])

    def test_color_and_thick_merged_runs_and_no_overwrite(self):
        item=self.job['items'][0];item['selector']={'mode':'color','rgb':[150,150,150],'tolerance':1}
        item['max_run_px']=2
        job,_=self.run_propose()
        self.assertIn('thick_or_merged_ink',job['items'][0]['flags'])
        with self.assertRaises(ValueError):self.run_propose()

    def test_invalid_roi_rejected_and_original_image_unchanged(self):
        before=(self.root/'figure.png').read_bytes()
        self.job['items'][0]['roi']=[-1,0,95,95]
        with self.assertRaises(ValueError):self.run_propose()
        self.assertEqual((self.root/'figure.png').read_bytes(),before)

    def test_actual_bar_groups_preserve_irregular_spacing(self):
        mask=np.zeros((20,100),dtype=bool)
        for lo,hi in [(3,14),(30,41),(72,85)]:mask[10,lo:hi+1]=True
        # Equal group centres (8.5,44,79.5) place the middle one in blank ink.
        self.assertFalse(mask[10,44])
        spans=locate_bar_groups(mask,row=10,left=1,right=98,expected_count=3)
        self.assertEqual(spans,[[3,14],[30,41],[72,85]])
        self.assertTrue(all(mask[10,round((a+b)/2)] for a,b in spans))

    def test_bar_group_count_mismatch_or_axis_row_fails_before_expansion(self):
        mask=np.zeros((20,100),dtype=bool)
        mask[10,3:15]=True;mask[10,30:42]=True
        mask[15,1:99]=True  # wrong row: continuous horizontal axis
        for row in [9,10,15]:
            with self.assertRaisesRegex(ValueError,'check foot row, ROI'):
                locate_bar_groups(mask,row=row,left=1,right=98,expected_count=3)

    def test_bar_group_validation_and_small_noise(self):
        mask=np.zeros((20,100),dtype=bool)
        mask[10,3:15]=True;mask[10,25:27]=True
        self.assertEqual(locate_bar_groups(mask,row=10,left=1,right=98,expected_count=1),[[3,14]])
        for patch in [{'row':20},{'left':-1},{'right':100},{'expected_count':0},
                      {'min_width':0},{'row':10.0}]:
            args=dict(row=10,left=1,right=98,expected_count=1);args.update(patch)
            with self.assertRaises(ValueError):locate_bar_groups(mask,**args)
        with self.assertRaises(ValueError):
            locate_bar_groups(mask.astype('uint8'),row=10,left=1,right=98,expected_count=1)


if __name__=='__main__':unittest.main()
