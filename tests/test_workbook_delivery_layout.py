"""Narrow native-package test for batched cell edits and delivery layout."""
import hashlib,json,os,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from xml.etree import ElementTree as E

N='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
Q=lambda t:'{'+N+'}'+t
TOOL=Path(__file__).resolve().parents[1]/'tools/workbook_save/merge_cells.py'

class DeliveryLayout(unittest.TestCase):
 def test_values_grouped_columns_merges_and_unrelated_parts(self):
  with tempfile.TemporaryDirectory(dir=os.environ.get('FIGURE_TEST_TMPDIR'))as tmp:
   p=Path(tmp)
   common={'xl/workbook.xml':f'<workbook xmlns="{N}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
    'xl/media/source.bin':b'unchanged-media'}
   def pack(path,sheet):
    if path.name=='source.xlsx':sheet=sheet.replace('</worksheet>','<mergeCells count="1"><mergeCell ref="A1:C1"/></mergeCells></worksheet>')
    with zipfile.ZipFile(path,'w')as z:
     for name,value in {**common,'xl/worksheets/sheet1.xml':sheet}.items():z.writestr(name,value)
   source=p/'source.xlsx'
   pack(source,f'<worksheet xmlns="{N}"><dimension ref="A1:C4"/><cols><col min="1" max="3" width="12" style="2"/></cols><sheetData><row r="1"><c r="A1"><v>4</v></c></row><row r="2"><c r="B2"><v>7</v></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>section</t></is></c></row></sheetData></worksheet>')
   pack(p/'A17_authored_cells.xlsx',f'<worksheet xmlns="{N}"><sheetData><row r="2"><c r="B2"><v>8</v></c></row><row r="3"><c r="C3"><v>9</v></c></row></sheetData></worksheet>')
   plan={'target':str(source),'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'changes':[{'sheet':'S','range':'B2','values':[[8]]},{'sheet':'S','range':'C3','values':[[9]]}],'column_widths':[{'sheet':'S','min':2,'max':2,'width':24}],'merges':[{'sheet':'S','range':'A4:C4'}],'row_heights':[{'sheet':'S','row':8,'height':32}]}
   (p/'A17_修改计划.json').write_text(json.dumps(plan),encoding='utf-8')
   subprocess.run([sys.executable,str(TOOL),'17'],env={**os.environ,'SRDP_TASK_DIR':str(p)},check=True,capture_output=True)
   with zipfile.ZipFile(p/'A17_保存前.xlsx')as z:
    self.assertEqual(z.read('xl/media/source.bin'),b'unchanged-media')
    root=E.fromstring(z.read('xl/worksheets/sheet1.xml'))
   cells={x.get('r'):x for x in root.iter(Q('c'))}
   self.assertEqual(cells['A1'].find(Q('v')).text,'4')
   self.assertEqual(cells['B2'].find(Q('v')).text,'8')
   self.assertEqual(cells['C3'].find(Q('v')).text,'9')
   cols=list(root.find(Q('cols')))
   self.assertEqual([(x.get('min'),x.get('max'),float(x.get('width')),x.get('style'))for x in cols],[('1','1',12.,'2'),('2','2',24.,'2'),('3','3',12.,'2')])
   self.assertEqual([x.get('ref')for x in root.find(Q('mergeCells'))],['A1:C1','A4:C4'])
   self.assertEqual(next(r for r in root.find(Q('sheetData'))if r.get('r')=='8').get('ht'),'32')

if __name__=='__main__':unittest.main()
