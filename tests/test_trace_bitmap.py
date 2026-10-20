"""Known-geometry ink paths and ambiguity, not paper-wide accuracy."""
from pathlib import Path
import sys
import unittest
import tempfile
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from trace_bitmap import anchored_path,trace
from review_points import write_json,read_json,prepare


class TraceTests(unittest.TestCase):
    def test_crossing_uses_both_sides_and_keeps_source_ink(self):
        cols={x:sorted(set([(x,x),(100-x,100-x)])) for x in range(10,91)}
        r=anchored_path(cols,[[10,10],[90,90]],max_step=1)
        self.assertTrue(all(r[x]['pixel']==[x,float(x)] for x in r))
        self.assertIn('branch_count_changed',r[50]['flags'])

    def test_real_spike_not_smoothed(self):
        cols={x:[(20,20)] for x in range(10)};cols[5]=[(50,50)]
        r=anchored_path(cols,[[0,20],[9,20]],max_step=31)
        self.assertEqual(r[5]['pixel'],[5,50.0])

    def test_parallel_ink_can_hide_spike_until_local_anchor_is_added(self):
        cols={x:[(20,20)] for x in range(10)};cols[5].append((50,50))
        proposal=anchored_path(cols,[[0,20],[9,20]],max_step=31)
        self.assertIn('multiple_ink_runs',proposal[5]['flags'])
        corrected=anchored_path(cols,[[0,20],[5,50],[9,20]],max_step=31)
        self.assertEqual(corrected[5]['pixel'],[5,50.0])

    def test_equal_cost_routes_are_not_certainty(self):
        cols={0:[(50,50)],1:[(40,40),(60,60)],2:[(50,50)]}
        r=anchored_path(cols,[[0,50],[2,50]],max_step=20)
        self.assertEqual(len(r[1]['candidates']),2)
        self.assertIn('competing_paths',r[1]['flags'])

    def test_additional_anchor_resolves_competing_branch(self):
        cols={0:[(50,50)],1:[(40,40),(60,60)],2:[(50,50)]}
        r=anchored_path(cols,[[0,50],[1,60],[2,50]],max_step=20,anchor_tolerance=0)
        self.assertEqual(r[1]['candidates'],[[1,60.0]])

    def test_missing_ink_is_not_invented(self):
        cols={0:[(20,20)],1:[],2:[(20,20)]}
        r=anchored_path(cols,[[0,20],[2,20]])
        self.assertIsNone(r[1]['pixel'])
        self.assertIn('no_ink',r[1]['flags'])

    def test_impossible_anchor_and_jump_do_not_force_a_line(self):
        with self.assertRaises(ValueError):anchored_path({0:[(20,20)],1:[(20,20)]},[[0,90],[1,20]])
        r=anchored_path({0:[(20,20)],1:[(80,80)]},[[0,20],[1,80]],max_step=2)
        self.assertIn('disconnected_path',r[0]['flags'])

    def test_shared_overlap_is_shared_ink_not_two_recovered_values(self):
        cols={x:[(20,22)] for x in range(5)}
        r=anchored_path(cols,[[0,21],[4,21]])
        self.assertTrue(all(v['candidates']==[[x,21.0]] for x,v in r.items()))

    def test_pipeline_to_review_does_not_alter_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);im=Image.new('RGB',(110,110),'white');d=ImageDraw.Draw(im)
            d.line((10,10,90,90),fill='black');d.line((10,90,90,10),fill='black')
            im.save(p/'figure.png');before=(p/'figure.png').read_bytes()
            job={'sources':{'s':{'image':'figure.png'}},'panels':{'a':{'source':'s','bounds':[1,1,108,108],
                'axis':{'coordinate':'y','ticks':[[10,100],[90,0]],'unit':'%'}}},'traces':[{
                    'id':'rising','panel':'a','identity':{'series':'known synthetic diagonal'},
                    'roi':[5,5,100,100],'selector':{'mode':'gray','range':[0,80]},
                    'anchors':[[10,10],[90,90]],'max_step_px':1,'sample_x':[10,30,50,70,90]}]}
            write_json(p/'job.json',job);r=trace(p/'job.json',p/'trace')
            reviewed=prepare(r['job'],p/'review',sample_every=1)
            self.assertTrue(Path(reviewed['manifest']).exists())
            self.assertEqual((p/'figure.png').read_bytes(),before)
            data=read_json(r['job']);self.assertEqual(len(data['items']),5)
            self.assertEqual(data['items'][3]['roles']['value'],[[70,70.0]])


if __name__=='__main__':unittest.main()
