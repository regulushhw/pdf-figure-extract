"""Resident point edits, sparse redraw, rollback and compatibility contracts."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import fitz
from PIL import Image,ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_task import Runner,read,write,sha


class PointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        with fitz.open() as doc:doc.new_page();doc.save(self.root/'paper.pdf')
        image=Image.new('RGB',(100,100),'white');ImageDraw.Draw(image).line((5,40,60,40),fill='black',width=2)
        image.save(self.root/'source.png')
        job={'schema_version':1,'sources':{'s':{'image':'source.png'}},'panels':{'p':{'source':'s',
            'bounds':[1,1,98,98],'axis':{'coordinate':'y','ticks':[[10,100],[90,0]],'unit':'%'}}},'items':[]}
        for i,x in enumerate((20,50,80)):
            job['items'].append({'id':f'p{i}','panel':'p','identity':{'series':'curve','x':x},
                'anchor':[x,40],'roi':[1,1,98,98],'selector':{'mode':'gray','range':[0,80]}})
        write(self.root/'job.json',job)
        write(self.root/'task.json',{'version':1,'pdf':'paper.pdf','work_dir':'run','parts':[
            {'id':'a','method':'bitmap_points','job':'job.json','trial_job':'job.json',
             'scope':{'series':['curve'],'sampling':'three original points; synthetic edits only'}}]})
        self.runner=Runner(self.root/'task.json')
        self.assertTrue(self.act('trial')['ok']);self.attest('approve');self.assertTrue(self.act('run')['ok'])

    def act(self,action,**kw):return self.runner.dispatch({'action':action,'part':'a',**kw})

    def attest(self,action):
        record=self.runner.state['parts']['a']['trial' if action=='approve' else 'run']
        write(self.root/'attest.json',{'part':'a','fingerprint':record['fingerprint'],
            'images':{p:sha(p) for p in record['images']},'identity_checked':True,'scope_checked':True,
            'note':'Synthetic contract attestation; not actual paper review.'})
        result=self.act(action,decision='attest.json')
        if action=='approve':self.assertTrue(result['ok'],result)
        return result

    def inspect(self,ids=('p0','p1','p2')):
        r=self.act('inspect',points=[{'id':i,'role':'value'} for i in ids])
        self.assertTrue(r['ok'],r);return r

    def edit(self,view,ops,**extra):
        manifest=read(view['view'])
        write(self.root/'edit.json',{'base_revision':view['revision'],'images':manifest['images'],
            'viewed':True,'note':'synthetic local point operation','ops':ops,**extra})
        return self.act('edit',batch=view['view'],decision='edit.json')

    def result(self):return read(self.runner.state['parts']['a']['result']['file'])

    def undo(self,view):
        write(self.root/'undo.json',{'base_revision':view['revision'],'note':'undo synthetic batch'})
        return self.act('undo',decision='undo.json')

    def test_batch_move_delete_fill_and_no_extraction_rerun(self):
        view=self.inspect();r=self.edit(view,[{'op':'move','id':'p0','pixel':[22,42]},
            {'op':'delete','id':'p1'},{'op':'add','id':'p2','pixel':[80,40]}])
        self.assertTrue(r['ok'],r);self.assertEqual(r['summary']['reviewed'],2)
        self.assertEqual(r['summary']['explicit_missing'],1)
        self.assertEqual(r['extraction_executions'],1)
        self.assertEqual(len(r['images']),1)
        with Image.open(r['images'][0]) as image:
            self.assertLess(image.width,1500)

    def test_new_point_reassign_delete_and_resume(self):
        r=self.edit(self.inspect(),[{'op':'add','id':'extra','panel':'p','identity':{'series':'new'},'pixel':[30,45]}])
        self.assertTrue(r['ok'],r)
        r=self.edit(r,[{'op':'reassign','id':'extra','series':'corrected'}]);self.assertTrue(r['ok'],r)
        self.runner=Runner(self.root/'task.json')
        r=self.inspect(('extra',));r=self.edit(r,[{'op':'delete','id':'extra'}]);self.assertTrue(r['ok'],r)
        self.assertEqual(len(self.result()['items']),3)

    def test_required_deleted_point_does_not_shrink_inventory(self):
        r=self.edit(self.inspect(),[{'op':'delete','id':'p0'}]);self.assertTrue(r['ok'],r)
        self.assertEqual(len(self.result()['items']),3)
        self.assertFalse(self.attest('accept')['ok'])

    def test_undo_restores_before_batch_and_preview(self):
        v=self.inspect();r=self.edit(v,[{'op':'move','id':'p0','pixel':[22,45]}]);self.assertTrue(r['ok'],r)
        u=self.undo(r);self.assertTrue(u['ok'],u)
        p=self.result()['items'][0]['roles']['value']
        self.assertEqual(p['pixel'],[20.,40.5]);self.assertEqual(p['status'],'automatic_candidate')

    def test_undo_added_point_uses_local_ghost_without_exporting_it(self):
        r=self.edit(self.inspect(),[{'op':'add','id':'extra','panel':'p','identity':{'series':'new'},'pixel':[30,45]}])
        u=self.undo(r);self.assertTrue(u['ok'],u)
        self.assertEqual(len(self.result()['items']),3)
        self.assertTrue(u['images'])

    def test_atomic_failure_keeps_previous_revision(self):
        v=self.inspect();r=self.edit(v,[{'op':'move','id':'p0','pixel':[21,42]}]);self.assertTrue(r['ok'],r)
        before=copy.deepcopy(self.runner.state['parts']['a']['result'])
        failed=self.edit(r,[{'op':'move','id':'p0','pixel':[23,44]},{'op':'move','id':'p1','pixel':[99,99]}])
        self.assertFalse(failed['ok']);self.assertEqual(before,self.runner.state['parts']['a']['result'])

    def test_stale_view_and_unviewed_changes_refused(self):
        v=self.inspect();r=self.edit(v,[{'op':'move','id':'p0','pixel':[22,42]}]);self.assertTrue(r['ok'],r)
        self.assertFalse(self.edit(v,[{'op':'move','id':'p0','pixel':[23,43]}])['ok'])
        self.assertFalse(self.edit(r,[{'op':'move','id':'p0','pixel':[23,43]}],viewed=False)['ok'])

    def test_destination_requires_observed_region(self):
        v=self.inspect(('p0',));self.assertFalse(self.edit(v,[{'op':'move','id':'p0','pixel':[90,90]}])['ok'])
        v=self.act('inspect',points=[{'id':'p0','role':'value'}],regions=[{'panel':'p','bounds':[70,70,98,98]}])
        r=self.edit(v,[{'op':'move','id':'p0','pixel':[90,90]}]);self.assertTrue(r['ok'],r)

    def test_reassign_preserves_original_identity_and_legacy_resume(self):
        r=self.edit(self.inspect(),[{'op':'reassign','id':'p0','series':'changed'}]);self.assertTrue(r['ok'],r)
        self.runner=Runner(self.root/'task.json');self.inspect(('p0',))
        p=self.result()['items'][0];self.assertEqual(p['identity']['series'],'changed')
        self.assertEqual(p['original_identity']['series'],'curve')
        self.assertTrue(self.act('review')['ok'])

    def test_images_reused_and_unrelated_points_unchanged(self):
        v=self.inspect();cache=self.runner._point_images['a']['s']
        r=self.edit(v,[{'op':'move','id':'p0','pixel':[22,42]}]);self.assertTrue(r['ok'],r)
        self.assertIs(cache,self.runner._point_images['a']['s'])
        self.assertEqual(self.result()['items'][1]['roles']['value']['status'],'automatic_candidate')
        self.assertEqual(self.result()['items'][1]['roles']['value']['pixel'],[50.,40.5])

    def test_current_corrected_overlay_required_by_acceptance(self):
        r=self.edit(self.inspect(),[{'op':'accept','id':'p0'},{'op':'accept','id':'p1'},
                                  {'op':'add','id':'p2','pixel':[80,40]}]);self.assertTrue(r['ok'],r)
        self.assertTrue(self.attest('accept')['ok'])
        r=self.edit(r,[{'op':'move','id':'p0','pixel':[23,43]}]);self.assertTrue(r['ok'],r)
        self.assertNotIn('run_approval',self.runner.state['parts']['a'])

    def test_existing_point_cannot_be_added_twice(self):
        v=self.inspect();self.assertFalse(self.edit(v,[{'op':'add','id':'p0','pixel':[22,42]}])['ok'])

    def test_legacy_apply_invalidates_fast_undo_stack(self):
        v=self.edit(self.inspect(),[{'op':'move','id':'p0','pixel':[22,42]}]);self.assertTrue(v['ok'],v)
        batch=self.act('review');manifest=read(batch['manifest']);card=manifest['items'][0]
        role=next(iter(card['roles']))
        write(self.root/'legacy.json',{'batch_id':manifest['batch_id'],
            'reviewer':{'kind':'agent','name':'synthetic'},'decisions':[
                {'id':card['id'],'role':role,'action':'set','pixel':[80,40],'note':'synthetic legacy fill'}]})
        reply=self.act('apply',batch=batch['manifest'],decision='legacy.json');self.assertTrue(reply['ok'],reply)
        current=self.runner.state['parts']['a']['result']['sha256']
        write(self.root/'undo.json',{'base_revision':current,'note':'must not undo legacy edits'})
        self.assertFalse(self.act('undo',decision='undo.json')['ok'])


if __name__=='__main__':unittest.main()
