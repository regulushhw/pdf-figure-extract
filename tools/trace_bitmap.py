"""Anchor-constrained ink paths. Original DP implementation, no model calls.

python tools/trace_bitmap.py job.json new_output_dir
Every point stays on a retained ink run; absent columns stay empty.
Path selection is a proposal, not an independent curve-identity verification.
"""
import copy
import json
import math
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw
from propose_bitmap_points import select_pixels,runs
from review_points import read_json,write_json,digest,load_job


def anchored_path(columns, anchors, max_step=20, anchor_tolerance=2, ambiguity_cost=0.5):
    """Forward/backward costs expose locally competing routes, not probabilities."""
    if not columns:raise ValueError('empty tracing interval')
    if not all(math.isfinite(float(v)) and v>=0 for v in [max_step,anchor_tolerance,ambiguity_cost]):
        raise ValueError('invalid tracing tolerances')
    if max_step==0:raise ValueError('max_step must be positive')
    xs=sorted(columns)
    if xs!=list(range(xs[0],xs[-1]+1)):raise ValueError('columns must include gaps explicitly')
    if len(anchors)<2 or anchors[0][0]!=xs[0] or anchors[-1][0]!=xs[-1]:
        raise ValueError('first and last columns require anchors')
    if any(type(x) is not int or not math.isfinite(y) for x,y in anchors):raise ValueError('invalid anchor')
    if [x for x,_ in anchors]!=sorted(set(x for x,_ in anchors)):raise ValueError('anchors must have unique increasing x')
    if any(x not in columns for x,_ in anchors):raise ValueError('anchor outside trace')
    constraints=dict(anchors);centers={x:np.array([(a+b)/2 for a,b in columns[x]]) for x in xs}
    allowed={x:np.ones(len(centers[x]),dtype=bool) for x in xs}
    for x,y in anchors:
        allowed[x]=np.array([max(a-y,y-b,0)<=anchor_tolerance for a,b in columns[x]])
        if not allowed[x].any():raise ValueError(f'anchor has no ink support at x={x}')
    # An absent column or disconnected jump ends a segment. Do not invent pixels.
    segments=[];current=[]
    for x in xs:
        if not len(centers[x]):
            if current:segments.append(current);current=[]
            continue
        current.append(x)
    if current:segments.append(current)
    result={x:{'pixel':None,'candidates':[],'flags':['no_ink']} for x in xs}
    for segment in segments:
        # A segment separated by missing ink needs two local anchors to establish identity.
        if segment[0] not in constraints or segment[-1] not in constraints:
            for x in segment:result[x]={'pixel':None,'candidates':[[x,float(v)] for v in centers[x]],'flags':['unanchored_segment']}
            continue
        forward=[];backlinks=[];edges=[]
        f=np.where(allowed[segment[0]],0.0,np.inf);forward.append(f)
        for left,right in zip(segment,segment[1:]):
            costs=np.abs(centers[left][:,None]-centers[right][None,:])
            costs[costs>max_step]=np.inf
            costs[:,~allowed[right]]=np.inf;edges.append(costs)
            total=f[:,None]+costs
            backlinks.append(np.argmin(total,axis=0));f=np.min(total,axis=0);forward.append(f)
        best=float(np.min(f))
        if not math.isfinite(best):
            for x in segment:result[x]={'pixel':None,'candidates':[[x,float(v)] for v in centers[x]],'flags':['disconnected_path']}
            continue
        backward=[None]*len(segment);backward[-1]=np.where(allowed[segment[-1]],0.0,np.inf)
        for i in range(len(segment)-2,-1,-1):
            backward[i]=np.min(edges[i]+backward[i+1][None,:],axis=1)
        indices=[int(np.argmin(f))]
        for links in reversed(backlinks):indices.append(int(links[indices[-1]]))
        indices.reverse()
        for i,x in enumerate(segment):
            marg=forward[i]+backward[i];plausible=np.flatnonzero(marg<=best+ambiguity_cost)
            chosen=indices[i];a,b=columns[x][chosen];flags=[]
            if len(plausible)>1:flags.append('competing_paths')
            if len(columns[x])>1:flags.append('multiple_ink_runs')
            if b-a+1>8:flags.append('thick_or_merged_ink')
            if i and len(columns[segment[i-1]])!=len(columns[x]):flags.append('branch_count_changed')
            result[x]={'pixel':[x,float(centers[x][chosen])],
                       'candidates':[[x,float(centers[x][k])] for k in plausible],
                       'ink_span':[a,b],'flags':flags,'path_cost':best}
    return result


def trace(job_path,out_dir):
    jp=Path(job_path).resolve();job=read_json(jp);out=Path(out_dir).resolve()
    if out.exists():raise ValueError('output directory exists')
    review={'schema_version':1,'sources':copy.deepcopy(job['sources']),
            'panels':copy.deepcopy(job['panels']),'items':[]}
    sources={};overlays={};evidence=[]
    for name,src in review['sources'].items():
        path=(jp.parent/src['image']).resolve();src['image']=str(path);src['sha256']=digest(path)
        image=Image.open(path).convert('RGB');sources[name]=np.array(image);overlays[name]=image.copy()
    for t in job['traces']:
        panel=review['panels'][t['panel']];rgb=sources[panel['source']]
        left,top,right,bottom=t['roi']
        if any(type(v) is not int for v in t['roi']) or not (0<=left<right<rgb.shape[1] and 0<=top<bottom<rgb.shape[0]):
            raise ValueError('invalid trace ROI')
        if not t.get('identity'):raise ValueError('trace identity required')
        mask=select_pixels(rgb,t['selector'])
        for a,b,c,d in t.get('exclude_boxes',[]):
            if not (left<=a<=c<=right and top<=b<=d<=bottom):raise ValueError('exclusion outside ROI')
            mask[b:d+1,a:c+1]=False
        start,end=t['anchors'][0][0],t['anchors'][-1][0]
        if not left<=start<end<=right:raise ValueError('anchor interval outside ROI')
        if any(not top<=y<=bottom for _,y in t['anchors']):raise ValueError('anchor outside ROI')
        columns={x:runs(mask,x,top,bottom) for x in range(start,end+1)}
        points=anchored_path(columns,t['anchors'],t.get('max_step_px',20),
                             t.get('anchor_tolerance_px',2),t.get('ambiguity_cost',0.5))
        draw=ImageDraw.Draw(overlays[panel['source']])
        for x,r in points.items():
            if r['pixel']:
                y=r['pixel'][1];draw.point((x,int(round(y))),fill='red')
        for x in t['sample_x']:
            if type(x) is not int or x not in points:raise ValueError('sample_x outside traced interval')
            r=points[x];pixel=r['pixel']
            candidates=r['candidates'] if len(r['candidates'])>1 or pixel is None else [pixel]
            review['items'].append({'id':f"{t['id']}-{x}",'panel':t['panel'],
                'identity':{**t['identity'],'source_pixel_x':x},'roi':t['roi'],
                'anchor':pixel or [x,(top+bottom)/2], 'roles':{'value':candidates},
                'flags':r['flags'], 'limitations':['anchored path proposal; identity not automatically verified'],
                'trace_evidence':{'anchors':t['anchors'],'selector':t['selector'],
                                  'max_step_px':t.get('max_step_px',20),'record':r}})
        evidence.append({'id':t['id'],'identity':t['identity'],'points':points,
                         'parameters':t,'method':'anchor_constrained_ink_path'})
    out.mkdir(parents=True);write_json(out/'review_job.json',review);load_job(out/'review_job.json')
    write_json(out/'paths.json',{'sources':review['sources'],'traces':evidence,'scope':'unreviewed path proposals'})
    for i,im in enumerate(overlays.values()):im.save(out/f'path_overlay_{i}.png')
    return {'job':str(out/'review_job.json'),'traces':len(evidence),'samples':len(review['items'])}


if __name__=='__main__':
    try:print(json.dumps(trace(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
