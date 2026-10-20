"""Original bitmap candidate detector; no third-party digitizer code.

python tools/propose_bitmap_points.py job.json output_directory
Input: review_points job with each item's roles replaced by a selector.
Preserves separate vertical ink runs instead of averaging different tracks.
No automatic curve identity assignment, interpolation or visual approval.
"""
import copy
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
from review_points import read_json, write_json, load_job, digest


def select_pixels(rgb, selector):
    if selector['mode']=='gray':
        lo,hi=selector['range']
        if not 0<=lo<=hi<=255:raise ValueError('invalid gray range')
        gray=np.asarray(Image.fromarray(rgb).convert('L'))
        return (gray>=lo)&(gray<=hi)
    if selector['mode']=='color':
        target=np.asarray(selector['rgb'],dtype=float)
        tol=float(selector['tolerance'])
        if target.shape!=(3,) or not np.isfinite(target).all() or np.any((target<0)|(target>255)):
            raise ValueError('invalid RGB target')
        if not np.isfinite(tol) or tol<0:raise ValueError('invalid color tolerance')
        return np.linalg.norm(rgb.astype(float)-target,axis=2)<=tol
    raise ValueError('mode must be gray or color')


def runs(mask,x,top,bottom):
    ys=np.flatnonzero(mask[top:bottom+1,x])+top
    if not len(ys):return []
    groups=np.split(ys,np.flatnonzero(np.diff(ys)>1)+1)
    return [[int(g[0]),int(g[-1])] for g in groups]


def propose(job_path,out_dir):
    job_path=Path(job_path).resolve();job=read_json(job_path)
    out=Path(out_dir).resolve()
    if out.exists():raise ValueError('output directory already exists')
    result=copy.deepcopy(job);images={};masks={};records=[];selected={}
    for name,src in result['sources'].items():
        path=(job_path.parent/src['image']).resolve()
        src['image']=str(path);src['sha256']=digest(path)
        images[name]=np.array(Image.open(path).convert('RGB'))
        masks[name]=np.zeros(images[name].shape[:2],dtype=bool)
    for item in result['items']:
        panel=result['panels'][item['panel']];src=panel['source'];rgb=images[src]
        left,top,right,bottom=item['roi']
        if any(type(v) is not int for v in item['roi']):raise ValueError('ROI must use integer source pixels')
        if not (0<=left<right<rgb.shape[1] and 0<=top<bottom<rgb.shape[0]):raise ValueError('ROI outside image')
        raw_x=item['anchor'][0];x=int(round(raw_x))
        if not left<=x<=right:raise ValueError('anchor outside ROI')
        selector=item.pop('selector');cache_key=(src,json.dumps(selector,sort_keys=True))
        if cache_key not in selected:selected[cache_key]=select_pixels(rgb,selector)
        mask=selected[cache_key]
        cropped=np.zeros_like(mask);cropped[top:bottom+1,left:right+1]=mask[top:bottom+1,left:right+1]
        for box in item.get('exclude_boxes',[]):
            a,b,c,d=box
            if any(type(v) is not int for v in box) or not (left<=a<=c<=right and top<=b<=d<=bottom):
                raise ValueError('exclusion must be inside ROI')
            cropped[b:d+1,a:c+1]=False
        half=item.get('neighbor_px',0)
        if type(half) is not int or not 0<=half<=20:raise ValueError('neighbor_px must be 0..20')
        candidates=runs(cropped,x,top,bottom);used=x
        flags=list(item.get('flags',[]))
        # Nearest populated column only; keep ties and multiple tracks as choices.
        choices=[(x,candidates)] if candidates else []
        if not choices:
            for distance in range(1,half+1):
                choices=[(xx,runs(cropped,xx,top,bottom)) for xx in (x-distance,x+distance) if left<=xx<=right]
                choices=[(xx,rr) for xx,rr in choices if rr]
                if choices:break
        points=[];spans=[]
        for used,groups in choices:
            for a,b in groups:
                points.append([used,(a+b)/2]);spans.append({'x':used,'y_range':[a,b]})
        if choices and any(xx!=x for xx,_ in choices):flags.append('neighbor_column_not_target')
        if not points:flags.append('no_ink_candidate')
        if len(points)>1:flags.append('multiple_ink_runs')
        max_run=item.get('max_run_px',8)
        if type(max_run) is not int or max_run<1:raise ValueError('invalid max_run_px')
        if any(s['y_range'][1]-s['y_range'][0]+1>max_run for s in spans):flags.append('thick_or_merged_ink')
        role=item.pop('role','value');item['roles']={role:points};item['flags']=flags
        item['proposal_evidence']={'selector':selector,'requested_x':raw_x,'sampled_x':x,
                                   'neighbor_px':half,'runs':spans,'method':'vertical_ink_runs',
                                   'identity_verified':False}
        masks[src]|=cropped
        records.append({'id':item['id'],'candidates':len(points),'flags':flags})
    # Reuse the existing interface's bounds, calibration and inventory validation.
    out.mkdir(parents=True)
    output=out/'review_job.json';write_json(output,result)
    load_job(output)
    for index,(src,mask) in enumerate(masks.items()):
        Image.fromarray((mask*255).astype('uint8')).save(out/f'mask_{index}.png')
        overlay=images[src].copy();overlay[mask]=[255,0,0]
        Image.fromarray(overlay).save(out/f'mask_overlay_{index}.png')
    summary={'job':str(output),'items':len(records),'records':records,
             'scope':'unreviewed pixel candidates only; no curve identity guarantee'}
    write_json(out/'summary.json',summary)
    return {'job':str(output),'items':len(records),'ambiguous':sum(r['candidates']>1 for r in records),
            'missing':sum(r['candidates']==0 for r in records)}


if __name__=='__main__':
    try:print(json.dumps(propose(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
