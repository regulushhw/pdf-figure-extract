import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
const toolDir=path.dirname(fileURLToPath(import.meta.url));
const here=path.resolve(process.env.SRDP_TASK_DIR||toolDir);
const python=process.env.FIGURE_PYTHON||'python';
const localRequire=createRequire(import.meta.url);
let artifactPath;
try { artifactPath=localRequire.resolve('@oai/artifact-tool'); }
catch {
 const modules=process.env.FIGURE_ARTIFACT_NODE_MODULES||path.resolve(path.dirname(process.execPath),'..','node_modules');
 artifactPath=createRequire(path.join(modules,'_figure_resolve.cjs')).resolve('@oai/artifact-tool');
}
const mode=process.argv[2]||'preview';
const papers=process.argv.slice(3).map(Number);
if(!mode.startsWith('preview')){
 execFileSync(python,[path.join(toolDir,'verify_continuation.py'),'--plan-only','--require-current-source','--prepare-source',...(papers.length?papers:[1]).map(String)],
  {stdio:'inherit',windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
}
const {Workbook,SpreadsheetFile,FileBlob}=await import(pathToFileURL(artifactPath));
for(const a of (papers.length?papers:[1])){
 const plan=JSON.parse(await fs.readFile(path.join(here,`A${a}_修改计划.json`),'utf8'));
 if(mode==='engine_save'){
  const draft=path.join(here,`A${a}_保存前.xlsx`),full=path.join(here,`A${a}_引擎完整导出.xlsx`);
  const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(draft));
  wb.recalculate();
  await (await SpreadsheetFile.exportXlsx(wb)).save(full);
  execFileSync(python,[path.join(toolDir,'verify_continuation.py'),'--engine-values','--candidate',full,String(a)],
   {stdio:'inherit',windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  await fs.copyFile(draft,path.join(here,`A${a}_引擎暂存.xlsx`));
  console.log(`A${a}: full-engine static values checked; native package retained without blank normalization`);continue;
 }
 if(mode.startsWith('preview')){
  const input=mode.startsWith('preview_draft')?path.join(here,`A${a}_保存前.xlsx`):plan.target;
  const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
  if(!plan.previews?.length) throw new Error('Declare plan.previews with explicit sheet and range.');
  for(const view of plan.previews){
   const img=await wb.render({sheetName:view.sheet,range:view.range,scale:1.1,format:'png'});
   const label=view.label?`_${view.label.replace(/[^\p{L}\p{N}_-]/gu,'_')}`:'';
   await fs.writeFile(path.join(here,`A${a}_${mode}${label}.png`),new Uint8Array(await img.arrayBuffer()));
  }
  console.log(`A${a}: ${mode.startsWith('preview_draft')?'draft':'saved file'} planned views rendered`);continue;
 }
 const wb=Workbook.create();
 for(const name of new Set(plan.changes.map(c=>c.sheet)))wb.worksheets.add(name);
 for(const c of plan.changes){
  const r=wb.worksheets.getItem(c.sheet).getRange(c.range);
  if(c.formula)r.formulas=c.values;else r.values=c.values;
 }
 wb.recalculate();
 await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(here,`A${a}_authored_cells.xlsx`));
 console.log(`A${a}: ${plan.changes.length} patch ranges authored`);
}
