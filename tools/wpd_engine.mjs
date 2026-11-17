// Isolated WPD core adapter. No network, model calls, or alternative tracer.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const [inputFile,outputFile]=process.argv.slice(2);
if(!inputFile||!outputFile)throw new Error('Usage: node wpd-engine.mjs engine-input.json engine-result.json');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const config=read(inputFile), lock=read(path.join(here,'wpd_sources.json'));
if(config.schema_version!==1)throw new Error('schema_version=1 required');
const context=vm.createContext({console, Set, Map});
for(const f of lock.files){
  const bytes=fs.readFileSync(path.join(config.engine_sources_dir,f.file));
  if(sha(bytes)!==f.sha256)throw new Error(`WPD source changed: ${f.file}`);
  vm.runInContext(bytes.toString('utf8'),context,{filename:f.file});
}
const {wpd}=context;
const {width,height,rgba_file}=config.image;
if(!Number.isInteger(width)||!Number.isInteger(height)||width<2||height<2||width*height>3000000)
  throw new Error('Image dimensions outside prototype limit');
const pixels=fs.readFileSync(rgba_file);
if(pixels.length!==width*height*4)throw new Error('RGBA size mismatch');
const sample=config.sample_at;
if(!Array.isArray(sample)||sample.length<2||sample.length>2000||sample.some(x=>!Number.isFinite(x))||
   new Set(sample).size!==sample.length)throw new Error('Distinct finite fixed sample_at values required');
function endpoints(axis){
  if(!['linear','log10'].includes(axis.kind))throw new Error('Unsupported WPD axis type; no linear fallback');
  if(!Array.isArray(axis.ticks)||axis.ticks.length<2)throw new Error('At least two tick positions required');
  return [axis.ticks[0],axis.ticks[axis.ticks.length-1]];
}
const [xt0,xt1]=endpoints(config.axes.x),[yt0,yt1]=endpoints(config.axes.y);
const cal=new wpd.Calibration(2);
cal.addPoint(xt0[0],yt0[0],String(xt0[1]),String(yt0[1]));
cal.addPoint(xt1[0],yt0[0],String(xt1[1]),String(yt0[1]));
cal.addPoint(xt0[0],yt0[0],String(xt0[1]),String(yt0[1]));
cal.addPoint(xt0[0],yt1[0],String(xt0[1]),String(yt1[1]));
const axes=new wpd.XYAxes();
if(!axes.calibrate(cal,config.axes.x.kind==='log10',config.axes.y.kind==='log10',false))
  throw new Error('WPD calibration rejected');
// Check unused ticks independently of the two endpoints used for calibration.
const checks=[];
for(const [name,axis] of Object.entries(config.axes)){
  const tol=axis.max_residual_px??1;
  for(const [pixel,value] of [...axis.ticks.slice(1,-1),...(axis.check_ticks??[])]){
    const p=name==='x'?axes.dataToPixel(value,yt0[1]):axes.dataToPixel(xt0[1],value);
    const err=Math.abs((name==='x'?p.x:p.y)-pixel);
    checks.push({axis:name,pixel,value,residual_px:err});
    if(err>tol)throw new Error(`Independent ${name} tick residual ${err}px > ${tol}px`);
  }
}
function contains(x,y,polygon){
  let inside=false;
  for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
    const [xi,yi]=polygon[i],[xj,yj]=polygon[j];
    if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)inside=!inside;
  }
  return inside;
}
const bounds=config.plot_bounds;
if(!Array.isArray(bounds)||bounds.length!==4||bounds.some(x=>!Number.isFinite(x))||
   bounds[0]<0||bounds[1]<0||bounds[2]>=width||bounds[3]>=height)throw new Error('Invalid plot bounds');
const ids=new Set(),series=[];
for(const s of config.series){
  if(!s.id||!s.name||ids.has(s.id))throw new Error('Unique series id and name required');
  ids.add(s.id);
  const polygons=s.mask?.polygons;
  if(!Array.isArray(polygons)||!polygons.length||polygons.some(p=>p.length<3))
    throw new Error('An explicit series ROI polygon is required');
  const excluded=s.mask?.exclude_boxes??[];
  if(!Array.isArray(excluded)||excluded.some(b=>!Array.isArray(b)||b.length!==4||
      b.some(v=>!Number.isFinite(v))||b[0]>b[2]||b[1]>b[3]||
      b[0]<bounds[0]||b[1]<bounds[1]||b[2]>bounds[2]||b[3]>bounds[3]))
    throw new Error('Excluded mask boxes must stay inside plot bounds');
  const detector=new wpd.AutoDetectionData();
  detector.imageWidth=width;detector.imageHeight=height;
  detector.fgColor=s.selector.color;detector.colorDistance=s.selector.distance;
  for(let y=Math.ceil(bounds[1]);y<=Math.floor(bounds[3]);y++)
    for(let x=Math.ceil(bounds[0]);x<=Math.floor(bounds[2]);x++)
      if(polygons.some(poly=>contains(x+0.5,y+0.5,poly))&&
         !excluded.some(b=>x>=b[0]&&x<=b[2]&&y>=b[1]&&y<=b[3]))detector.mask.add(y*width+x);
  if(detector.mask.size===0)throw new Error(`Empty series ROI: ${s.id}`);
  detector.generateBinaryData({data:pixels,width,height});
  const eligible=sample.filter(x=>!s.domain||(x>=s.domain[0]&&x<=s.domain[1]));
  let extracted=[];
  const start=performance.now();
  if(eligible.length>=2){
    const algo=new wpd.CustomIndependents(),dataset=new wpd.Dataset();
    // Labeled ticks need not reach the frame edges. Scan the declared plot,
    // rather than silently dropping ink above the highest printed tick.
    const yvalues=[axes.pixelToData(bounds[0],bounds[1])[1],
                   axes.pixelToData(bounds[0],bounds[3])[1]];
    algo.setParams({ymin:Math.min(...yvalues),ymax:Math.max(...yvalues),curveWidth:s.curve_width_px??1});
    algo.setXVals(JSON.stringify(eligible));algo.run(detector,dataset,axes,{data:pixels,width,height});
    extracted=Array.from({length:dataset.getCount()},(_,i)=>{
      const p=dataset.getPixel(i);return {pixel:[p.x,p.y],wpd_data:Array.from(axes.pixelToData(p.x,p.y))};
    });
  }
  const points=sample.map(x=>{
    const outside=s.domain&&(x<s.domain[0]||x>s.domain[1]);
    const matches=extracted.filter(p=>Math.abs(p.wpd_data[0]-x)<1e-7*Math.max(1,Math.abs(x)));
    if(matches.length>1)throw new Error(`Duplicate WPD output at ${s.id}:${x}`);
    const p=matches[0];
    return {sample_x:x,pixel:p?.pixel??null,wpd_data:p?.wpd_data??null,
      status:outside?'outside_declared_domain':p?'candidate':'no_output'};
  });
  series.push({id:s.id,name:s.name,points,ink_pixels:detector.binaryData.size,
    algorithm_ms:performance.now()-start,
    limitations:['ROI and series identity supplied externally; WPD interpolation is not original measured points.']});
}
const result={schema_version:1,engine:{name:'WebPlotDigitizer public core',sources:lock.files},
  source:{image_sha256:config.source.image_sha256,width,height},config_sha256:config.config_sha256,
  sample_at:sample,axes:config.axes,checks,series,
  status:'unreviewed_candidates',scope:'Headless core extraction; no GUI, AI Assist, paper-level verification or quota claim'};
fs.writeFileSync(outputFile,JSON.stringify(result,null,2));
console.log(JSON.stringify({series:series.length,expected_positions:series.length*sample.length,
  candidates:series.flatMap(s=>s.points).filter(p=>p.pixel).length,output:outputFile}));
