"""Synthetic worker behavior: no project papers or final workbooks changed."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from run_task import Runner, readonly_tools, read, write, sha, TOOLS


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        doc = fitz.open(); page = doc.new_page(width=100, height=100)
        page.draw_line((10, 60), (90, 20), color=(0, 0, 0))
        doc.save(self.root / 'source.pdf'); doc.close()
        im = Image.new('RGB', (100, 100), 'white')
        ImageDraw.Draw(im).line((10, 40, 90, 40), fill='black', width=3)
        im.save(self.root / 'figure.png')
        self.job = {'schema_version':1, 'sources':{'s':{'image':'figure.png'}},
            'panels':{'p':{'source':'s','bounds':[1,1,98,98],
                          'axis':{'coordinate':'y','ticks':[[10,100],[90,0]],'unit':'%'}}},
            'items':[{'id':'v','panel':'p','identity':{'series':'curve'},'anchor':[50,40],
                      'roi':[5,5,95,95],'selector':{'mode':'gray','range':[0,60]}}]}
        write(self.root / 'full.json', self.job)
        write(self.root / 'trial.json', self.job)
        self.task = {'version':1, 'work_dir':'work', 'pdf':'source.pdf',
          'parts':[{'id':'a','method':'bitmap_points','job':'full.json','trial_job':'trial.json',
                    'scope':{'series':['curve'],'sampling':'one source pixel x=50'}}]}
        write(self.root / 'task.json', self.task)
        self.runner = Runner(self.root / 'task.json')

    def act(self, action, **kwargs):
        return self.runner.dispatch({'action':action,'part':'a',**kwargs})

    def approve(self, stage='trial'):
        record = self.runner.state['parts']['a'][stage]
        decision = {'part':'a','fingerprint':record['fingerprint'],
             'images':{p:sha(p) for p in record['images']},'identity_checked':True,
             'scope_checked':True,'note':'synthetic test attestation; not actual visual validation'}
        write(self.root / 'decision.json', decision)
        reply = self.act('approve' if stage=='trial' else 'accept', decision='decision.json')
        self.assertTrue(reply['ok'], reply)

    def test_gate_and_identical_trial_full_cache(self):
        self.assertFalse(self.act('run')['ok'])
        self.assertTrue(self.act('trial')['ok'])
        self.approve()
        self.assertTrue(self.act('run')['ok'])
        self.assertEqual(self.runner.state['executions'], 1)
        self.assertEqual(self.runner.state['cache_hits'], 1)
        self.assertEqual(self.runner.state['parts']['a']['run']['status'], 'unreviewed_candidates')

    def test_full_configuration_change_invalidates_trial_approval(self):
        self.act('trial'); self.approve()
        job = copy.deepcopy(self.job); job['items'][0]['anchor']=[60,40]
        write(self.root/'full.json',job)
        reply = self.act('run')
        self.assertFalse(reply['ok']); self.assertIn('changed',reply['error'])

    def test_source_change_invalidates_without_overwrite(self):
        first=self.act('trial'); self.approve()
        Image.new('RGB',(100,100),'white').save(self.root/'figure.png')
        self.assertFalse(self.act('run')['ok'])
        self.assertTrue(Path(first['folder']).exists())

    def test_corrupt_cache_is_not_silently_regenerated(self):
        result=self.act('trial'); p=Path(result['folder'])/'candidates/review_job.json'
        p.write_text('{}',encoding='utf-8')
        reply=self.act('trial')
        self.assertFalse(reply['ok']); self.assertIn('cached evidence',reply['error'])

    def test_fixed_commands_and_output_boundaries(self):
        self.assertFalse(self.runner.dispatch({'action':'status','command':'anything'})['ok'])
        self.assertFalse(self.act('shell')['ok'])
        with self.assertRaises(ValueError):self.runner.output('../escape')
        bad=copy.deepcopy(self.task);bad['work_dir']=str(TOOLS/'bad')
        write(self.root/'bad.json',bad)
        with self.assertRaises(ValueError):Runner(self.root/'bad.json')

    def test_unsupported_method_remains_incomplete(self):
        self.runner.parts[0]['method']='log_axis_unavailable'
        r=self.act('trial')
        self.assertFalse(r['ok']);self.assertEqual(r['status'],'incomplete')
        self.assertIn('maintenance',r['error'])

    def test_bitmap_review_apply_resume_and_accept(self):
        self.act('trial');self.approve();self.act('run')
        one=self.act('review');two=self.act('review')
        self.assertTrue(one['ok'],one);self.assertEqual(one['manifest'],two['manifest'])
        batch=read(one['manifest'])
        d={'batch_id':batch['batch_id'],'reviewer':{'kind':'agent','name':'synthetic'},
           'decisions':[{'id':'v','role':'value','action':'accept','candidate_index':0}]}
        write(self.root/'points.json',d)
        applied=self.act('apply',decision='points.json',batch=one['manifest'])
        self.assertTrue(applied['ok'],applied)
        self.assertEqual(applied['summary']['numeric_roles_reviewed'],1)
        self.approve('run')
        new=Runner(self.root/'task.json')
        self.assertIn('run_approval',new.state['parts']['a'])

    def test_cannot_accept_bitmap_candidates_without_review(self):
        self.act('trial');self.approve();self.act('run')
        record=self.runner.state['parts']['a']['run']
        d={'part':'a','fingerprint':record['fingerprint'],'images':{p:sha(p) for p in record['images']},
           'identity_checked':True,'scope_checked':True,'note':'test'}
        write(self.root/'decision.json',d)
        self.assertFalse(self.act('accept',decision='decision.json')['ok'])

    def test_vector_pipeline_preserves_expected_numbers(self):
        self.runner.parts[0]['method']='vector_lines'
        job={'pdf':'source.pdf','page':1,'frame':[5,5,95,95], 'color_map':{'0,0,0':'curve'},
             'x':{'ticks':[[10,0],[50,5],[90,10]]},'y':{'ticks':[[90,0],[50,50],[10,100]]},'sample_at':[0,5,10]}
        write(self.root/'trial.json',job);write(self.root/'full.json',job)
        r=self.act('trial');self.assertTrue(r['ok'],r);self.approve();r=self.act('run')
        self.assertTrue(r['ok'],r)
        values=read(Path(r['folder'])/'data.json')['series']['curve']
        for got,want in zip(values,[(0,37.5),(5,62.5),(10,87.5)]):
            self.assertAlmostEqual(got[0],want[0]);self.assertAlmostEqual(got[1],want[1])
        self.approve('run')

    def external(self, content):
        (self.root/'export.csv').write_text(content,encoding='utf-8')
        self.runner.parts[0]['method']='unplotter_csv'
        job={'export':'export.csv','review_image':'figure.png',
             'columns':{'x':'X','series':{'curve':'Y'}},'sample_at':[0,1]}
        write(self.root/'full.json',job);write(self.root/'trial.json',job)
        return self.act('trial')

    def test_external_exports_keep_blanks_and_bind_source(self):
        r=self.external('X,Y\n0,12\n1,\n');self.assertTrue(r['ok'],r)
        data=read(Path(r['folder'])/'data.json')['_evidence']
        self.assertEqual(data['records'][0]['value'],12)
        self.assertIsNone(data['records'][1]['value'])
        self.approve();self.act('run')
        record=self.runner.state['parts']['a']['run']
        d={'part':'a','fingerprint':record['fingerprint'],'images':{p:sha(p) for p in record['images']},
           'identity_checked':True,'scope_checked':True,'note':'test'}
        write(self.root/'decision.json',d)
        self.assertFalse(self.act('accept',decision='decision.json')['ok'])

    def test_external_incorrect_sampling_rejected(self):
        r=self.external('X,Y\n0,12\n2,14\n')
        self.assertFalse(r['ok']);self.assertIn('sample_at',r['error'])

    def test_save_denied_before_all_parts_approved(self):
        reply=self.runner.dispatch({'action':'save'})
        self.assertFalse(reply['ok'])
        self.assertFalse((self.runner.work/'workbook').exists())

    def test_scan_reuse_does_not_repeat_subprocess(self):
        one=self.act('scan');two=self.act('scan')
        self.assertTrue(one['ok'],one);self.assertEqual(one['scan'],two['scan'])
        self.assertEqual(self.runner.state['executions'],1)

    def test_vector_bar_dispatch_uses_actual_slots(self):
        doc=fitz.open();p=doc.new_page(width=160,height=160)
        for y,label in [(30,'100'),(80,'50'),(130,'0')]:
            # Align the text span centre to the intended tick.
            p.insert_text((15,y+2.5),label,fontsize=6)
        p.draw_rect(fitz.Rect(60,60,65,130),fill=(0,0,0),color=None)
        p.draw_rect(fitz.Rect(100,90,105,130),fill=(0,0,0),color=None)
        doc.save(self.root/'source.pdf');doc.close()
        job={'pdf':'source.pdf','page':1,'frames':[[40,30,130,130]],'names':['p'],
             'color_map':{'0,0,0':'curve'},'slot_labels':['one','two'],'slot_edges':[40,80,130],'tick_window':30}
        write(self.root/'full.json',job);write(self.root/'trial.json',job)
        self.runner.parts[0]['method']='vector_bars'
        r=self.act('trial');self.assertTrue(r['ok'],r)
        data=read(Path(r['folder'])/'data.json')
        self.assertEqual(len(data['p']['records']),2)
        self.assertTrue(all(r['value']>0 for r in data['p']['records']))

    @unittest.skipUnless(os.environ.get('FIGURE_SAVE_INTEGRATION')=='1','configured spreadsheet runtime required')
    def test_runner_full_workbook_pipeline_and_failed_preview_gate(self):
        import io
        import openpyxl
        from openpyxl.drawing.image import Image as SheetImage
        # First approve a genuine synthetic extraction through normal gates.
        self.runner.parts[0]['method']='vector_lines'
        job={'pdf':'source.pdf','page':1,'frame':[5,5,95,95],'color_map':{'0,0,0':'curve'},
             'x':{'ticks':[[10,0],[50,5],[90,10]]},'y':{'ticks':[[90,0],[50,50],[10,100]]},'sample_at':[0,5,10]}
        write(self.root/'full.json',job);write(self.root/'trial.json',job)
        self.assertTrue(self.act('trial')['ok']);self.approve();r=self.act('run');self.approve('run')
        source=self.root/'book.xlsx';book=openpyxl.Workbook();s=book.active;s.title='Data'
        s['A1']=1;s['B1']=2
        s.add_image(SheetImage(io.BytesIO((self.root/'figure.png').read_bytes())),'E5')
        book.save(source);book.close()
        extracted=str(Path(r['folder'])/'data.json')
        plan={'paper':1,'target':str(source),'backup':str(self.root/'backup.xlsx'),'input_sha256':sha(source),
              'changes':[{'sheet':'Data','range':'A1','values':[[62.5]]}],
              'previews':[{'sheet':'Data','range':'A1:B2','label':'test'}]}
        write(self.root/'plan.json',plan)
        write(self.root/'checks.json',{'ranges':[{'sheet':'Data','range':'A1'}],
          'checks':[{'sheet':'Data','cell':'A1','json':extracted,'path':['series','curve',1,1]}]})
        write(self.root/'audit.json',{'rules':[{'kind':'cell','sheet':'Data','cell':'A1','expected':62.5,'source':'synthetic PDF curve at x=5'}]})
        self.runner.task['workbook']={'plan':'plan.json','checks':'checks.json','audit':'audit.json'}
        draft=self.runner.dispatch({'action':'draft'});self.assertTrue(draft['ok'],draft)
        before=sha(source)
        denied=self.runner.dispatch({'action':'save'})
        self.assertFalse(denied['ok']);self.assertEqual(sha(source),before)
        decision={'draft_sha256':sha(self.runner.work/'workbook/A1_保存前.xlsx'),
                  'images':{p:sha(p) for p in draft['images']},'viewed':True,'notes_checked':True}
        write(self.runner.work/'save_decision.json',decision)
        saved=self.runner.dispatch({'action':'save'});self.assertTrue(saved['ok'],saved)
        result=openpyxl.load_workbook(source)
        self.assertEqual(result['Data']['A1'].value,62.5)
        self.assertEqual(result['Data']['B1'].value,2)
        self.assertEqual(len(result['Data']._images),1);result.close()
        self.assertTrue(self.runner.dispatch({'action':'verify'})['ok'])

    @unittest.skipUnless(os.name=='nt','Windows file sharing protection')
    def test_worker_locks_actual_tool_against_other_process(self):
        # A zero-write open proves write access denied, without changing any tool bytes.
        probe='from pathlib import Path; p=Path(__import__("sys").argv[1]); f=p.open("r+b"); f.close()'
        path=TOOLS/'run_task.py'; before=sha(path)
        with readonly_tools():
            blocked=subprocess.run([sys.executable,'-c',probe,str(path)],capture_output=True)
            self.assertNotEqual(blocked.returncode,0)
        available=subprocess.run([sys.executable,'-c',probe,str(path)],capture_output=True)
        self.assertEqual(available.returncode,0,available.stderr)
        self.assertEqual(sha(path),before)


if __name__=='__main__':
    unittest.main()
