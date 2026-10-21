"""Local OCR contracts and sparse confirmation; optional real Windows engine."""
import copy
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from ocr_assist import (numeric, inverse_box, parse_tsv, normalize, suggestions,
                        recognize, confirmed, read, write, sha)


class OcrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = self.root / 'image.png'
        Image.new('RGB',(200,200),'white').save(self.image)

    def fixture(self):
        words = [{'id': f'w{i}', 'line_id': f'l{i}', 'text': str(v), 'numeric_candidate': v,
                  'bbox': [10,y-5,30,y+5], 'coordinate_reliable': True, 'rotation':0, 'confidence':None}
                 for i,(y,v) in enumerate(((40,100),(100,50),(160,0)))]
        lines = [{'id':'unit','text':'Discharge (Mlld)','bbox':[0,50,9,140], 'coordinate_reliable':True,
                  'rotation':90, 'word_ids':[]},
                 {'id':'legend','text':'Observed','bbox':[90,90,170,110], 'coordinate_reliable':True,
                  'rotation':0,'word_ids':[]}]
        review = self.root/'check.png'; Image.new('RGB',(200,200),'white').save(review)
        a = {'fingerprint':'test', 'image':str(self.image),'source_sha256':sha(self.image),
             'size':[200,200], 'bounds':[40,20,180,180], 'words':words,'lines':lines,
             'review_image':str(review),'files':{str(review):sha(review)}}
        write(self.root/'analysis.json',a)
        d = {'analysis_fingerprint':'test','source_sha256':sha(self.image), 'review_image_sha256':sha(review),
             'viewed':True,'note':'Synthetic attestation, not an actual image review.',
             'x':[],'y':[{'word_id':f'w{i}'} for i in range(3)],'unit':None,'series':[]}
        write(self.root/'confirmation.json',d)
        return a,d

    def confirm(self):
        return confirmed(self.root/'analysis.json',self.root/'confirmation.json')

    def test_numeric_no_guessed_letters_or_thousands(self):
        self.assertEqual(numeric('−2.5'),-2.5)
        self.assertEqual(numeric('20%'),20)
        for s in ('O','1O0','3,300','NaN','inf'): self.assertIsNone(numeric(s))

    def test_rotated_scaled_boxes_round_trip(self):
        self.assertEqual(inverse_box([10,20,30,40],0,(100,80),2),[5,10,15,20])
        self.assertEqual(inverse_box([20,10,40,30],90,(100,80),2),[5,60,15,70])
        self.assertEqual(inverse_box([10,20,30,40],180,(100,80),2),[85,60,95,70])
        self.assertEqual(inverse_box([20,10,40,30],270,(100,80),2),[85,10,95,20])

    def test_tsv_boxes_confidence_and_line_groups(self):
        tsv='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        tsv+='5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t89.5\t−50\n'
        rows=parse_tsv(tsv)
        self.assertEqual(rows[0]['words'][0]['bbox'],[10,20,40,32])
        self.assertEqual(rows[0]['words'][0]['confidence'],89.5)

    def test_windows_null_confidence_and_deskew_boundary(self):
        raw={'results':[{'rotation':0,'text_angle':1.2,'lines':[
            {'text':'100','words':[{'text':'100','bbox':[10,10,30,30]}]}]}]}
        words,lines,warnings=normalize(raw,(100,100),1)
        self.assertIsNone(words[0]['confidence'])
        self.assertFalse(words[0]['coordinate_reliable'])
        self.assertTrue(warnings)

    def test_groups_are_unconfirmed_and_labels_do_not_assign_style(self):
        a,d=self.fixture()
        hints=suggestions(a['words'],a['lines'],a['bounds'])
        self.assertEqual(hints['axis_groups'][0]['fit_status'],'linear_candidate_unconfirmed')
        self.assertEqual(len(hints['axis_groups'][0]['ticks']),3)
        self.assertEqual(hints['label_candidates'][0]['kind'],'unit_or_axis_label')
        self.assertNotIn('selector',hints)

    def test_valid_confirmation_and_unit_correction_reason(self):
        a,d=self.fixture()
        d['unit']={'line_id':'unit','text':'Ml/d','reason':'Slash checked on source image.'}
        d['series']=[{'line_id':'legend','name':'Observed'}]
        write(self.root/'confirmation.json',d)
        result=self.confirm()
        self.assertEqual(result['y_ticks'],[[40,100],[100,50],[160,0]])
        self.assertEqual(result['unit'],'Ml/d')
        self.assertEqual(result['series_labels'][0]['name'],'Observed')
        self.assertNotIn('selector',result)

    def test_unviewed_or_wrong_hash_refused(self):
        a,d=self.fixture(); d['viewed']=False; write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'review'): self.confirm()
        d['viewed']=True;d['review_image_sha256']='wrong';write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'review'): self.confirm()

    def test_changed_source_or_review_refused(self):
        self.fixture();Image.new('RGB',(200,200),'black').save(self.image)
        with self.assertRaisesRegex(ValueError,'source image changed'):self.confirm()

    def test_correction_requires_reason_and_nonlinear_refused(self):
        a,d=self.fixture();d['y'][1]['value']=60;write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'reason'):self.confirm()
        d['y'][1]['reason']='Synthetic mistaken axis';write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'residual'):self.confirm()

    def test_duplicate_tick_and_unreliable_coordinate_refused(self):
        a,d=self.fixture();d['y'].append({'word_id':'w0'});write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.confirm()
        a,d=self.fixture();a['words'][0]['coordinate_reliable']=False;write(self.root/'analysis.json',a)
        with self.assertRaisesRegex(ValueError,'unreliable'):self.confirm()

    def test_generated_config_merge_and_stale_receipt(self):
        from prepare_task import apply_ocr, build
        a,d=self.fixture()
        # The PDF-only package itself is tested in test_prepare_task; use that fixture here.
        import fitz
        pdf=self.root/'source.pdf'
        with fitz.open() as doc:
            p=doc.new_page();p.insert_text((20,30),'Figure 1. Synthetic fixture caption.');doc.save(pdf)
        from prepare_task import prepare,configure
        pkg=self.root/'package';prepare(pdf,pkg)
        pid=read(pkg/'decisions.json')['parts'][0]['id'];configure(pkg,pid,'bitmap_points')
        write(pkg/'ocr_links.json',{pid:{'analysis':str(self.root/'analysis.json'),'image':str(self.image),'sha256':sha(self.image)}})
        reply=apply_ocr(pkg,pid,self.root/'confirmation.json')
        self.assertEqual(reply['y_ticks'],3)
        part=read(pkg/'decisions.json')['parts'][0]
        self.assertEqual(part['parameters']['image'],str(self.image))
        self.assertEqual(part['scope']['series'],[])
        data=read(pkg/'decisions.json');p=data['parts'][0]
        p['scope']={'series':['a'],'sampling':'0,5,10'}
        p['parameters'].update(x_ticks=[[50,0],[170,10]],x_unit='index',sample_at=[0,5,10],
                               series=[{'name':'a','selector':{'mode':'gray','range':[0,80]}}])
        write(pkg/'decisions.json',data)
        self.assertEqual(build(pkg)['selected_parts'],1)
        d['note']='Changed confirmation';write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'evidence changed'):build(pkg)

    def test_mismatched_image_not_merged(self):
        from prepare_task import apply_ocr
        a,d=self.fixture()
        # Reject before any decision file is read or changed.
        with patch('prepare_task.check_package',return_value=(self.root,{})):
            write(self.root/'ocr_links.json',{'p':{'analysis':str(self.root/'analysis.json'),'image':'wrong','sha256':sha(self.image)}})
            with self.assertRaisesRegex(ValueError,'mismatch'):apply_ocr(self.root,'p',self.root/'confirmation.json')

    def test_image_only_scanned_pdf_uses_original_asset(self):
        import fitz
        from prepare_task import prepare,ocr_labels
        pdf=self.root/'scanned.pdf'
        with fitz.open() as doc:
            page=doc.new_page(width=200,height=200)
            page.insert_image(page.rect,filename=str(self.image));doc.save(pdf)
        pkg=self.root/'scan_package';prepare(pdf,pkg)
        inv=read(pkg/'inventory.json')
        self.assertTrue(inv['warnings'])
        self.assertEqual(inv['candidates'][0]['kind'],'unidentified')
        with patch('ocr_assist.recognize',return_value={'analysis':'synthetic_analysis.json'}) as detect:
            ocr_labels(pkg,inv['candidates'][0]['id'])
            source=Path(detect.call_args.args[0])
            with Image.open(source) as im:
                self.assertEqual(im.size,(200,200))

    def test_unit_correction_without_reason_refused(self):
        a,d=self.fixture();d['unit']={'line_id':'unit','text':'Ml/d'}
        write(self.root/'confirmation.json',d)
        with self.assertRaisesRegex(ValueError,'unit text requires reason'):self.confirm()

    @unittest.skipUnless(os.name=='nt' and os.environ.get('FIGURE_OCR_INTEGRATION')=='1','explicit Windows integration')
    def test_real_windows_engine_and_cache(self):
        image=Image.new('RGB',(600,300),'white');d=ImageDraw.Draw(image)
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',30)
        for y,text in ((20,'100'),(110,'50'),(200,'0')):d.text((15,y),text,font=font,fill='black')
        d.text((160,100),'Observed',font=font,fill='black');image.save(self.image)
        result=recognize(self.image,self.root/'ocr',[90,20,550,230],rotations=[0])
        a=read(result['analysis'])
        self.assertTrue(any(w['text']=='Observed' for w in a['words']))
        self.assertTrue(any(w['numeric_candidate']==100 for w in a['words']))
        self.assertTrue(recognize(self.image,self.root/'ocr',[90,20,550,230],rotations=[0])['cached'])


if __name__=='__main__': unittest.main()
