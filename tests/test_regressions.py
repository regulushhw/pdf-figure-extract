"""合成行为回归；不包含论文，不证明任意PDF都可读。
运行：python tests/test_regressions.py [--output-dir PATH]
"""
import sys,json,tempfile,unittest,subprocess,argparse
import zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import fitz,numpy as np,openpyxl
import calib,extract_bars as bars,extract_lines as lines,extract_points as points
from verify import verify

class FakePage:
    def __init__(self,items): self.items=items
    def get_drawings(self): return self.items

class Regression(unittest.TestCase):
    def test_dashes_gap_limits_and_ambiguity(self):
        from extract_dashes import sample
        ss=[((0,10),(2,10)),((4,12),(6,12))]
        out=sample(ss,[1,3,9],max_gap=3,endpoint_tolerance=0)
        self.assertEqual([p[1] for p in out],[10,11,None])
        self.assertIsNone(sample(ss,[3],max_gap=1,endpoint_tolerance=0)[0][1])
        with self.assertRaises(ValueError):sample(ss+[ ((0,20),(2,20)) ],[1])
        with self.assertRaises(ValueError):sample(ss,[1],max_gap=float('inf'))

    def test_filled_quad_dashes_real_pdf_cli(self):
        from extract_dashes import dash_segments
        doc=fitz.open();pg=doc.new_page(width=160,height=160)
        for x,y in [(20,80),(26,78),(32,76)]:
            pg.draw_polyline([(x,y-.2),(x+4,y-.2),(x+4,y+.2),(x,y+.2)],closePath=True,fill=(0,0,0),color=None)
        pg.draw_line((40,70),(40,70.3),width=2.5,color=(0,0,0))
        pg.draw_line((10,90),(150,90),width=.2,color=(0,0,0),dashes='[1 1] 0')
        ss,ids=dash_segments(pg,[10,10,150,150],5,.8,[2,3])
        self.assertEqual(len(ss),4)
        pdf=OUT/'填充虚线.pdf';doc.save(pdf);doc.close()
        job={'pdf':str(pdf),'page':1,'figure':'synthetic quad dashes','conditions':{'series':'explicit single trace'},
             'frame':[10,10,150,150],'fill_color':[0,0,0],'max_length':5,'max_thickness':.8,
             'cap_width':[2,3],'max_gap':3,'endpoint_tolerance':0,
             'x':{'ticks':[[0,0],[50,50],[100,100]]},'y':{'ticks':[[0,0],[50,50],[100,100]]},
             'sample_at':[25,40,70],'prefix':str(OUT/'dashes')}
        jp=OUT/'dashes_job.json';jp.write_text(json.dumps(job))
        r=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools/extract_dashes.py'),str(jp)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(r.returncode,0,r.stderr)
        d=json.loads((OUT/'dashes.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(d['records'][0]['value'],79,places=4)
        self.assertAlmostEqual(d['records'][1]['value'],70.15,places=3)
        self.assertIsNone(d['records'][2]['value'])
        self.assertEqual(d['_evidence']['records'][2]['status'],'missing')
        self.assertTrue((OUT/'dashes_check.png').exists())

    def test_inventory_missing_duplicate_identity_and_points(self):
        from audit_workbook import audit
        wb=openpyxl.Workbook();ws=wb.active;ws.title='Data'
        ws.append(['observed',1,2]);wb.save(OUT/'inventory.xlsx')
        rule={'kind':'inventory','sheet':'Data','row_range':[1,5],
              'key_columns':['A'],'value_columns':['B','C'],
              'expected':[{'key':['observed'],'count':2},{'key':['future'],'count':2}],
              'source':'synthetic original: two labelled series, two points each'}
        path=OUT/'inventory.json';path.write_text(json.dumps({'rules':[rule]}))
        def run():
            wb.save(OUT/'inventory.xlsx');return audit(OUT/'inventory.xlsx',path)
        self.assertIn('漏掉整条系列',run()['failures'][0]['error'])
        ws.append(['future',3,4]);self.assertFalse(run()['failures'])
        ws.append(['future',3,4]);self.assertTrue(run()['failures'])
        ws.delete_rows(3);ws['C2']=None;self.assertTrue(run()['failures'])
        ws['C2']=4;ws['A2']='wrong identity';self.assertTrue(run()['failures'])

    def test_real_pdf_style_binding_and_grid_rejection(self):
        doc=fitz.open();pg=doc.new_page(width=200,height=200)
        pg.draw_line((20,40),(180,80),color=(0,0,0),width=.4)
        pg.draw_line((20,80),(180,120),color=(0,0,0),width=.8)
        pg.draw_rect(fitz.Rect(10,10,190,190),color=(0,0,0),width=.4)
        axis={'f':lambda v:v,'inv':lambda v:v}
        styles=[{'name':name,'color':'0,0,0','width_range':bounds,'line_style':'solid'}
                for name,bounds in [('thin',[.39,.41]),('bold',[.79,.81])]]
        res,_=lines.extract_lines(pg,[10,10,190,190],None,axis,axis,[100],styles)
        self.assertAlmostEqual(res['thin'][0][1],60);self.assertAlmostEqual(res['bold'][0][1],100)
        with self.assertRaises(ValueError):lines.extract_lines(pg,[10,10,190,190],None,axis,axis,[100],styles+[dict(styles[0],name='ambiguous')])
        shape=pg.new_shape()
        for y in (140,160,180):shape.draw_line((20,y),(180,y))
        shape.finish(color=(0,0,0),width=.2,dashes='[1 1] 0');shape.commit()
        with self.assertRaisesRegex(ValueError,'网格'):
            lines.extract_lines(pg,[10,10,190,190],None,axis,axis,[100],
                [{'name':'future','color':'0,0,0','width_range':[.19,.21],'line_style':'dashed'}])
        pdf=OUT/'styles.pdf';doc.save(pdf);doc.close()
        job={'pdf':str(pdf),'page':1,'figure':'synthetic_styles','conditions':{'unit':'test'},
             'frame':[10,10,190,190],'series_styles':styles,
             'x':{'ticks':[[20,20],[100,100],[180,180]]},
             'y':{'ticks':[[20,20],[100,100],[180,180]]},
             'sample_at':[100],'prefix':str(OUT/'styles')}
        jp=OUT/'styles_job.json';jp.write_text(json.dumps(job))
        result=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools/extract_lines.py'),str(jp)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads((OUT/'styles.json').read_text(encoding='utf-8'))
        self.assertEqual(data['_evidence']['series_binding'],styles)
        self.assertTrue((OUT/'styles_check.png').exists())

    def test_empty_checks_rejected(self):
        (OUT/'empty.json').write_text('[]')
        with self.assertRaises(ValueError):verify('unused',OUT/'empty.json')

    def make_checks(self):
        wb=openpyxl.Workbook();ws=wb.active;ws.title='Data';ws['F6']=1;ws['G6']=None
        wb.save(OUT/'book.xlsx')
        (OUT/'source.json').write_text(json.dumps({'values':[1,None]}))
        return {'ranges':[{'sheet':'Data','range':'F6:G6'}],
                'checks':[{'sheet':'Data','cell':cell,'json':'source.json',
                           'path':['values',i]} for i,cell in enumerate(('F6','G6'))]}

    def check(self,c):
        p=OUT/'checks.json';p.write_text(json.dumps(c));return verify(OUT/'book.xlsx',p)

    def test_verify_full_and_missing(self):
        c=self.make_checks();self.assertFalse(self.check(c)['failures'])
        c['checks'].pop();self.assertTrue(self.check(c)['failures'])

    def test_verify_mismatch_duplicate_nonfinite(self):
        c=self.make_checks();c['checks'][0]['path']=['values',1]
        self.assertTrue(self.check(c)['failures'])
        c=self.make_checks();c['checks'].append(c['checks'][0])
        with self.assertRaises(ValueError):self.check(c)
        c=self.make_checks();c['checks'][0]['tol']=float('inf')
        self.assertTrue(self.check(c)['failures'])

    def formula_fixture(self):
        c=self.make_checks()
        wb=openpyxl.load_workbook(OUT/'book.xlsx')
        wb['Data']['Z99']='=2+2';wb.save(OUT/'book.xlsx')
        return c

    def set_fixture_cache(self,value,error=False):
        # Synthetic cache fixture only; no calculation engine is simulated.
        path=OUT/'book.xlsx'
        with zipfile.ZipFile(path) as z:data={n:z.read(n) for n in z.namelist()}
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        tree=ET.fromstring(data['xl/worksheets/sheet1.xml'])
        cell=tree.find('.//m:c[@r="Z99"]',ns)
        if error:cell.set('t','e')
        else:cell.attrib.pop('t',None)
        val=cell.find('m:v',ns)
        if val is None:val=ET.SubElement(cell,'{'+ns['m']+'}v')
        val.text=str(value)
        data['xl/worksheets/sheet1.xml']=ET.tostring(tree)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in data.items():z.writestr(n,b)

    def test_verify_formula_cache_outside_ranges(self):
        c=self.formula_fixture()
        self.assertTrue(self.check(c)['failures'])
        self.set_fixture_cache(4)
        self.assertFalse(self.check(c)['failures'])

    def test_audit_formula_cache_outside_contract(self):
        from audit_workbook import audit
        self.formula_fixture();path=OUT/'formula_audit.json'
        path.write_text(json.dumps({'rules':[{'kind':'count','sheet':'Data','range':'F6:F6','expected':1,'source':'synthetic original single point'}]}))
        self.assertTrue(audit(OUT/'book.xlsx',path)['failures'])
        self.set_fixture_cache(4)
        self.assertFalse(audit(OUT/'book.xlsx',path)['failures'])

    def test_formula_error_cache_rejected(self):
        from audit_workbook import audit
        c=self.formula_fixture();self.set_fixture_cache('#DIV/0!',error=True)
        self.assertTrue(self.check(c)['failures'])
        path=OUT/'formula_error_audit.json'
        path.write_text(json.dumps({'rules':[{'kind':'count','sheet':'Data','range':'F6:F6','expected':1,'source':'synthetic original point'}]}))
        self.assertTrue(audit(OUT/'book.xlsx',path)['failures'])

    def test_calibration_and_color(self):
        f,inv,ab,resid,chain=calib.fit_axis([(0,0),(3,999),(10,20),(20,40),(30,60)])
        self.assertAlmostEqual(f(15),30);self.assertAlmostEqual(inv(30),15)
        with self.assertRaises(ValueError):calib.match_color((.01,.01,.01),{(0,0,0):'A',(.02,.02,.02):'B'})

    def extract(self,drawings,**kwargs):
        fit=(lambda p:100-p,lambda v:100-v,(-1,100),0,[(0,100),(50,50),(100,0)])
        with patch.object(bars,'collect_spans',return_value=[]),patch.object(bars,'numeric_spans',return_value=[]),patch.object(bars,'fit_axis',return_value=fit):
            return bars.extract_bars(FakePage(drawings),{'1,0,0':'A'},frames=[(0,0,100,100),(200,0,300,100)],names=['P1','P2'],slot_labels=['Jan','Feb'],**kwargs)

    def bar(self,x):return {'fill':(1,0,0),'rect':fitz.Rect(x,30,x+4,100)}

    def test_bars_all_panel_marks_and_missing(self):
        out,rep,marks,frames=self.extract([self.bar(20),self.bar(220)],equal_spacing=True)
        self.assertEqual(len(marks),2);self.assertEqual(sum(v['n_bars'] for v in out.values()),2)
        self.assertIsNone(out['P1']['values']['A']['Feb'])
        self.assertEqual(len(out['P1']['records']),2)

    def test_bars_explicit_edges_and_duplicate(self):
        out,_,_,_=self.extract([self.bar(20)],slot_edges={'P1':[0,10,100],'P2':[200,210,300]})
        self.assertEqual(out['P1']['values']['A']['Feb'],70)
        with self.assertRaises(ValueError):self.extract([self.bar(20),self.bar(25)],equal_spacing=True)
        with self.assertRaises(ValueError):self.extract([self.bar(20)])
        out,_,marks,_=self.extract([],equal_spacing=True)
        self.assertEqual(marks,[]);self.assertIsNone(out['P2']['values']['A']['Jan'])

    def test_lines_short_segments_and_ambiguity(self):
        def line(ax,ay,bx,by):
            return {'type':'s','color':(1,0,0),'rect':fitz.Rect(min(ax,bx),min(ay,by),max(ax,bx),max(ay,by)),
                    'items':[('l',fitz.Point(ax,ay),fitz.Point(bx,by))]}
        axis={'f':lambda v:v,'inv':lambda v:v}
        ds=[line(0,0,10,10),line(10,10,20,20)]
        res,marks=lines.extract_lines(FakePage(ds),[0,0,30,30],{'1,0,0':'A'},axis,axis,[5,15,25])
        self.assertEqual(res['A'],[(5,5),(15,15),(25,None)])
        with self.assertRaises(ValueError):lines.extract_lines(FakePage(ds+[line(0,20,20,20)]),[0,0,30,30],{'1,0,0':'A'},axis,axis,[5])

    def test_rectangle_marker(self):
        r=fitz.Rect(10,10,14,14)
        g={'type':'f','fill':(1,0,0),'rect':r,'items':[('re',r,1)]}
        axis={'f':lambda v:v}
        res=points.extract_points(FakePage([g]),[0,0,100,100],{'1,0,0':'A'},axis,axis)
        self.assertEqual(res,{'A':[[12,12]]})

    def test_tick_search_window(self):
        doc=fitz.open();pg=doc.new_page(width=250,height=200)
        for y,value in ((50,100),(100,50),(150,0)):
            pg.insert_text((25,y+3),str(value),fontsize=7)
        args=dict(page=pg,color_map={'1,0,0':'A'},frames=[(80,50,180,150)],
                  names=['P'],slot_labels=['Jan'],equal_spacing=True)
        with self.assertRaises(ValueError):bars.extract_bars(**args)
        result=bars.extract_bars(**args,tick_window=60)
        self.assertIsNone(result[0]['P']['values']['A']['Jan'])
        with self.assertRaises(ValueError):bars.extract_bars(**args,tick_window=0)
        doc.close()

    def test_stroke_bars_and_compound_paths(self):
        ds=[{'type':'s','color':(1,0,0),'fill':None,'width':3,'lineCap':(0,0,0),
             'rect':fitz.Rect(20,30,70,100),
             'items':[('l',fitz.Point(20,100),fitz.Point(20,30)),
                      ('l',fitz.Point(70,100),fitz.Point(70,60))]}]
        out,_,marks,_=self.extract(ds,equal_spacing=True,stroke_bars=True)
        self.assertEqual(out['P1']['values']['A'],{'Jan':70,'Feb':40})
        self.assertEqual(len(marks),2)
        ds[0].update(width=40,items=[('l',fitz.Point(20,80),fitz.Point(24,80))])
        out,_,_,_=self.extract(ds,equal_spacing=True,stroke_bars=True)
        self.assertEqual(out['P1']['values']['A']['Jan'],40)
        out,_,_,_=self.extract(ds,equal_spacing=True)
        self.assertIsNone(out['P1']['values']['A']['Jan'])

    def test_audit_blocks_coverage_and_identity_errors(self):
        from audit_workbook import audit
        self.make_checks()
        path=OUT/'audit.json'
        rules=[{'kind':'count','sheet':'Data','range':'F6:AJ6','expected':31,'source':'synthetic original 31-month axis'},
               {'kind':'cell','sheet':'Data','cell':'F6','expected':-0.21,'source':'synthetic original sign'},
               {'kind':'cell','sheet':'Data','cell':'C6','expected':'Variance','source':'synthetic original panel label'},
               {'kind':'sequence','sheet':'Data','range':'F5:G5','expected':[.05,.15],'source':'synthetic CDF axis'}]
        path.write_text(json.dumps({'rules':rules}))
        result=audit(OUT/'book.xlsx',path)
        self.assertEqual(len(result['failures']),4)
        path.write_text('{"rules":[]}')
        with self.assertRaises(ValueError):audit(OUT/'book.xlsx',path)

    def test_audit_rejects_missing_as_zero(self):
        from audit_workbook import audit
        self.make_checks()
        records=OUT/'evidence.json';contract=OUT/'audit.json'
        records.write_text(json.dumps([{'sheet':'Data','cell':'G6','value':0,'status':'missing'}]))
        contract.write_text(json.dumps({'rules':[{'kind':'source_records','json':'evidence.json','source':'synthetic source'}]}))
        result=audit(OUT/'book.xlsx',contract)
        self.assertTrue(result['failures'])
        wb=openpyxl.load_workbook(OUT/'book.xlsx');wb['Data']['G6']=0;wb.save(OUT/'book.xlsx')
        self.assertTrue(audit(OUT/'book.xlsx',contract)['failures'])
        records.write_text(json.dumps([{'sheet':'Data','cell':'G6','value':0,'status':'near_zero_visual_review','method':'baseline_visually_near_zero'}]))
        self.assertFalse(audit(OUT/'book.xlsx',contract)['failures'])

    def test_real_pdf_cli_evidence_and_overlay(self):
        doc=fitz.open();page=doc.new_page(width=400,height=220)
        page.draw_line((60,120),(140,80),color=(1,0,0))
        page.draw_rect(fitz.Rect(178,98,182,102),color=(0,0,1),fill=(0,0,1))
        for x in (50,230):
            page.draw_rect(fitz.Rect(x,50,x+100,150),color=(0,0,0))
            for y,value in ((50,100),(100,50),(150,0)):
                page.insert_text((x-17,y+3),str(value),fontsize=7)
            page.draw_rect(fitz.Rect(x+20,80,x+24,150),color=None,fill=(1,0,0))
        pdf=OUT/'synthetic.pdf';doc.save(pdf);doc.close()
        base={'pdf':str(pdf),'page':1,'figure':'synthetic','conditions':{'unit':'test'}}
        ticks={'ticks':[[50,0],[150,100],[250,200]]}
        jobs=[('extract_lines.py',dict(base,frame=[50,50,200,150],color_map={'1,0,0':'A'},x=ticks,y={'ticks':[[50,100],[100,50],[150,0]]},sample_at=[20,60],prefix=str(OUT/'line'))),
              ('extract_points.py',dict(base,frame=[150,50,210,150],color_map={'0,0,1':'B'},x=ticks,y={'ticks':[[50,100],[100,50],[150,0]]},prefix=str(OUT/'point'))),
              ('extract_bars.py',dict(base,frames=[[50,50,150,150],[230,50,330,150]],names=['P1','P2'],color_map={'1,0,0':'A'},slot_labels=['one'],equal_spacing=True,prefix=str(OUT/'bar')))]
        for name,job in jobs:
            jp=OUT/(name+'.json');jp.write_text(json.dumps(job))
            r=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools'/name),str(jp)],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(r.returncode,0,r.stderr)
            data=json.loads(Path(job['prefix']+'.json').read_text(encoding='utf-8'))
            self.assertTrue(data['_evidence']['records'])
            self.assertEqual(data['_evidence']['source']['figure'],'synthetic')
            self.assertTrue(Path(job['prefix']+'_check.png').is_file())
        line=json.loads((OUT/'line.json').read_text(encoding='utf-8'));self.assertAlmostEqual(line['series']['A'][0][1],35)
        point=json.loads((OUT/'point.json').read_text(encoding='utf-8'));self.assertAlmostEqual(point['points']['B'][0][0],130);self.assertAlmostEqual(point['points']['B'][0][1],50)
        self.assertEqual(len(json.loads((OUT/'bar.json').read_text(encoding='utf-8'))['_evidence']['records']),2)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir');args=parser.parse_args()
    temporary=None
    if args.output_dir:OUT=Path(args.output_dir).resolve();OUT.mkdir(parents=True,exist_ok=True)
    else:temporary=tempfile.TemporaryDirectory(prefix='pdf_extract_test_');OUT=Path(temporary.name)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Regression))
    (OUT/'test_results.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'scope':'synthetic_regression_only'},indent=2))
    if temporary:temporary.cleanup()
    sys.exit(0 if result.wasSuccessful() else 1)

