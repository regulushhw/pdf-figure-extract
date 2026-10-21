"""Whole-object observation and axis-only edits with reused candidates."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

import fitz
from PIL import Image,ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from review_points import load_job,load_result,inspect_points,edit_points,read_json
from run_task import Runner,write,sha


class ObjectReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        im=Image.new('RGB',(100,300),'white');d=ImageDraw.Draw(im)
        d.line((20,40,20,240),fill='black',width=2)
        d.line((40,90,40,210),fill='black',width=3)
        im.save(self.root/'source.png')
        with fitz.open() as pdf:pdf.new_page();pdf.save(self.root/'paper.pdf')
        write(self.root/'job.json',{'schema_version':1,'sources':{'s':{'image':'source.png'}},
            'panels':{'p':{'source':'s','bounds':[1,1,98,298],
                'axis':{'coordinate':'y','ticks':[[10,100],[290,0]],'unit':'%'}}},
            'items':[{'id':'future','panel':'p','identity':{'series':'SD','month':1},
                'roi':[10,5,30,290],'anchor':[20,150],
                'roles':{'upper':[],'lower':[[20,240]]}},
                {'id':'baseline','panel':'p','identity':{'statistic':'baseline','month':1},
                'roi':[32,5,48,290],'anchor':[40,150],
                'roles':{'upper':[[40,90]],'lower':[[40,210]]}}]})
        jp,j,s,p=load_job(self.root/'job.json');self.result=load_result(jp,j,s,p)
        self.refs=[{'id':i['id'],'role':role} for i in self.result['items'] for role in i['roles']]

    def view(self,mode='objects',refs=None):
        return inspect_points(self.result,self.root/f'view_{mode}','revision',
                              refs=self.refs if refs is None else refs,view_mode=mode)

    def edit(self,view,ops):
        obs=read_json(view['view'])
        return edit_points(self.result,{'viewed':True,'note':'synthetic explicit observation',
            'images':obs['images'],'ops':ops},obs)[0]

    def test_missing_endpoint_observed_with_full_object_without_custom_crop(self):
        refs=[self.refs[0]]
        point=self.view('points',refs)
        with self.assertRaisesRegex(ValueError,'outside current shown'):
            self.edit(point,[{'op':'set_axis','id':'future','role':'upper','position':40}])
        whole=self.view('objects',refs)
        result=self.edit(whole,[{'op':'set_axis','id':'future','role':'upper','position':40}])
        state=result['items'][0]['roles']['upper']
        self.assertEqual(state['pixel'],[20.,40.])
        self.assertAlmostEqual(state['value'],100-(40-10)*100/280)
        self.assertEqual(result['items'][1],self.result['items'][1])

    def test_labels_map_all_endpoints_without_series_inference(self):
        v=self.view();self.assertEqual([x['label'] for x in v['points']],[1,2,3,4])
        self.assertEqual([(x['id'],x['role']) for x in v['points']],
                         [(x['id'],x['role']) for x in self.refs])
        self.assertEqual(len(v['images']),1)
        with Image.open(v['images'][0]) as image:
            self.assertLess(image.width,900);self.assertLess(image.height,1600)
        self.assertEqual(self.result['items'][0]['roles']['upper']['status'],'needs_review')

    def test_focus_keeps_full_roi_escape_and_rejects_unseen_edits(self):
        focus=self.view('objects');full=self.view('objects_full')
        fv=read_json(focus['view'])['views'][0];wv=read_json(full['view'])['views'][0]
        self.assertGreater(fv['bounds'][1],wv['bounds'][1])
        self.assertLess(fv['bounds'][3],wv['bounds'][3])
        self.assertEqual(fv['full_bounds'],wv['bounds'])
        with self.assertRaisesRegex(ValueError,'outside current shown'):
            self.edit(focus,[{'op':'set_axis','id':'future','role':'upper','position':6}])
        # Full-range fallback exposes the destination; the explicit edit is
        # possible, but remains a user's decision rather than detected ink.
        r=self.edit(full,[{'op':'set_axis','id':'future','role':'upper','position':6}])
        self.assertEqual(r['items'][0]['roles']['upper']['pixel'][1],6)

    def test_generated_template_cannot_silently_accept_candidates(self):
        v=self.view();t=read_json(v['decision_template'])
        self.assertFalse(t['viewed']);self.assertEqual(t['note'],'')
        self.assertTrue(all(o['position'] is None for o in t['ops']))
        with self.assertRaisesRegex(ValueError,'must be viewed'):
            edit_points(self.result,t,read_json(v['view']))

    def test_invalid_axis_position_or_unobserved_destination_is_atomic(self):
        v=self.view();before=copy.deepcopy(self.result)
        for position in (None,True,'40',float('nan'),299):
            with self.assertRaises(ValueError):
                self.edit(v,[{'op':'set_axis','id':'future','role':'upper','position':position}])
        self.assertEqual(self.result,before)

    def test_explicit_missing_keeps_original_inventory(self):
        r=self.edit(self.view(),[{'op':'delete','id':'future','role':'upper','note':'not distinguishable'}])
        self.assertEqual(len(r['items']),2)
        self.assertIsNone(r['items'][0]['roles']['upper']['value'])
        self.assertEqual(r['items'][0]['roles']['upper']['status'],'explicit_missing')

    def runner(self):
        write(self.root/'task.json',{'version':1,'pdf':'paper.pdf','work_dir':'run','parts':[
            {'id':'month1','method':'review_candidates','job':'job.json','trial_job':'job.json',
             'scope':{'series':['SD','baseline'],'sampling':'four synthetic endpoints'}}]})
        r=Runner(self.root/'task.json');trial=r.dispatch({'action':'trial','part':'month1'})
        self.assertTrue(trial['ok'],trial)
        record=r.state['parts']['month1']['trial']
        write(self.root/'approve.json',{'part':'month1','fingerprint':record['fingerprint'],
            'images':{p:sha(p) for p in record['images']},'identity_checked':True,'scope_checked':True,
            'note':'Synthetic test attestation, not paper review'})
        self.assertTrue(r.dispatch({'action':'approve','part':'month1','decision':'approve.json'})['ok'])
        self.assertTrue(r.dispatch({'action':'run','part':'month1'})['ok']);return r

    def test_runner_reuses_candidates_and_edits_without_extractor(self):
        r=self.runner()
        view=r.dispatch({'action':'inspect','part':'month1','points':self.refs,'view_mode':'objects'})
        self.assertTrue(view['ok'],view)
        t=read_json(view['decision_template']);t.update(viewed=True,note='Explicit synthetic coordinate')
        t['ops']=[{'op':'set_axis','id':'future','role':'upper','position':40}]
        write(self.root/'edit.json',t)
        reply=r.dispatch({'action':'edit','part':'month1','batch':view['view'],'decision':'edit.json'})
        self.assertTrue(reply['ok'],reply);self.assertEqual(reply['extraction_executions'],0)
        self.assertEqual(read_json(reply['view'])['view_mode'],'objects')
        write(self.root/'undo.json',{'base_revision':reply['revision'],'note':'undo explicit synthetic coordinate'})
        undo=r.dispatch({'action':'undo','part':'month1','decision':'undo.json'})
        self.assertTrue(undo['ok'],undo)
        self.assertEqual(read_json(undo['result']['file'])['items'][0]['roles']['upper']['status'],'needs_review')

    def test_imported_source_change_invalidates_previous_views(self):
        r=self.runner();Image.new('RGB',(100,300),'gray').save(self.root/'source.png')
        reply=r.dispatch({'action':'inspect','part':'month1','points':self.refs,'view_mode':'objects'})
        self.assertFalse(reply['ok']);self.assertIn('stale',reply['error'])

    def test_invalid_view_mode_refused_before_edit(self):
        r=self.runner()
        self.assertFalse(r.dispatch({'action':'inspect','part':'month1','view_mode':'auto_guess'})['ok'])


if __name__=='__main__':unittest.main()
