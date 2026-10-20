"""Bounded routing: column path -> strict ink graph -> penalized dark occlusion.

python tools/auto_trace_bitmap.py trace_job.json new_output_dir
Original implementation. No model calls, white-space interpolation or automatic
identity approval. Reuses trace_bitmap input and review_points output contracts.
"""
import copy
import heapq
import math
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw
from propose_bitmap_points import select_pixels,runs
from trace_bitmap import anchored_path
from review_points import read_json,write_json,digest,load_job


def graph_path(rgb,roi,selector,anchors,occlusion=False,max_occlusion_fraction=.15):
    left,top,right,bottom=roi
    if any(type(v) is not int for v in roi) or not (0<=left<right<rgb.shape[1] and 0<=top<bottom<rgb.shape[0]):
        raise ValueError('invalid graph ROI')
    if (right-left+1)*(bottom-top+1)>300000:raise ValueError('graph ROI exceeds 300000 pixels; split region')
    if not 0<=max_occlusion_fraction<=1:raise ValueError('invalid occlusion fraction')
    if len(anchors)<2 or any(len(p)!=2 or any(type(v) is not int for v in p) for p in anchors):
        raise ValueError('graph anchors must use integer source pixels')
    if any(not(left<=x<=right and top<=y<=bottom) for x,y in anchors):raise ValueError('anchor outside graph ROI')
    if any(a[0]>=b[0] for a,b in zip(anchors,anchors[1:])):raise ValueError('anchors must have increasing x')
    preferred=select_pixels(rgb,selector)
    gray=np.asarray(Image.fromarray(rgb).convert('L'))
    if occlusion and selector['mode']!='gray':raise ValueError('dark-occlusion route requires a gray selector')
    ceiling=min(245,selector['range'][1]+30) if occlusion else None
    mask=(gray<=ceiling) if occlusion else preferred
    # Stay on visible ink. Crossing dark pixels is more expensive than gray ink.
    costs=np.where(preferred,1.,6.)
    route=[];total_cost=0.
    for start,end in zip(anchors,anchors[1:]):
        start,end=tuple(start),tuple(end)
        if not preferred[start[1],start[0]] or not preferred[end[1],end[0]]:
            return {'status':'anchor_without_preferred_ink','path':[]}
        distance={start:0.};previous={};queue=[(0.,start)]
        while queue:
            cost,p=heapq.heappop(queue)
            if cost!=distance[p]:continue
            if p==end:break
            x,y=p
            for dx,dy in ((1,0),(1,1),(1,-1),(0,1),(0,-1),(-1,0),(-1,1),(-1,-1)):
                xx,yy=x+dx,y+dy
                if not(left<=xx<=right and top<=yy<=bottom) or not mask[yy,xx]:continue
                q=(xx,yy);new=cost+math.hypot(dx,dy)*(costs[y,x]+costs[yy,xx])/2
                if new<distance.get(q,math.inf):
                    distance[q]=new;previous[q]=p;heapq.heappush(queue,(new,q))
        if end not in distance:return {'status':'disconnected','path':[]}
        path=[end]
        while path[-1]!=start:path.append(previous[path[-1]])
        path.reverse();route.extend(path if not route else path[1:]);total_cost+=distance[end]
    nonpreferred=sum(not preferred[y,x] for x,y in route)
    fraction=nonpreferred/len(route)
    if fraction>max_occlusion_fraction:return {'status':'excess_occlusion','path':[],
        'nonpreferred_pixels':int(nonpreferred),'nonpreferred_fraction':fraction}
    return {'status':'proposed','path':[list(p) for p in route],'cost':float(total_cost),
            'nonpreferred_pixels':int(nonpreferred),'nonpreferred_fraction':fraction,
            'nonpreferred_path':[list(p) for p in route if not preferred[p[1],p[0]]],
            'dark_ceiling':ceiling}


def points_at_x(path,x,preferred=None,top=0,bottom=None):
    ys=sorted(set(y for xx,y in path if xx==x))
    if not ys:return []
    # Graph paths can follow a stroke edge. Center only the particular original
    # ink run touched by the route, never the mean of unrelated tracks.
    if preferred is not None:
        spans=runs(preferred,x,top,bottom)
        touched=[(a,b) for a,b in spans if any(a<=y<=b for y in ys)]
        if touched:return [[x,(a+b)/2] for a,b in touched]
    groups=np.split(np.array(ys),np.flatnonzero(np.diff(ys)>1)+1)
    return [[x,float((g[0]+g[-1])/2)] for g in groups]


def route_trace(rgb,t):
    selector=t['selector'];mask=select_pixels(rgb,selector)
    # Explicit masks must affect every method equally, while preserving the source.
    working=rgb.copy()
    left,top,right,bottom=t['roi']
    for a,b,c,d in t.get('exclude_boxes',[]):
        if not(left<=a<=c<=right and top<=b<=d<=bottom):raise ValueError('exclusion outside ROI')
        mask[b:d+1,a:c+1]=False;working[b:d+1,a:c+1]=255
    start,end=t['anchors'][0][0],t['anchors'][-1][0]
    if not 0<=left<=start<end<=right<rgb.shape[1] or not 0<=top<bottom<rgb.shape[0]:raise ValueError('invalid trace bounds')
    columns={x:runs(mask,x,top,bottom) for x in range(start,end+1)}
    points=anchored_path(columns,t['anchors'],t.get('max_step_px',20),t.get('anchor_tolerance_px',2),t.get('ambiguity_cost',.5))
    connected=all(r['pixel'] is not None for r in points.values())
    methods={'column_path':{'status':'proposed' if connected else 'disconnected',
                           'path':[r['pixel'] for r in points.values() if r['pixel']]}}
    methods['ink_graph']=graph_path(working,t['roi'],selector,t['anchors'])
    if not connected and methods['ink_graph']['status']!='proposed' and selector['mode']=='gray':
        methods['occlusion_graph']=graph_path(working,t['roi'],selector,t['anchors'],True,t.get('max_occlusion_fraction',.15))
    selected=next((m for m in ('column_path','ink_graph','occlusion_graph') if methods.get(m,{}).get('status')=='proposed'),None)
    samples=[]
    for x in t['sample_x']:
        if type(x) is not int or x not in columns:raise ValueError('sample outside anchor interval')
        by_method={m:points_at_x(r['path'],x,mask,top,bottom) for m,r in methods.items() if r['status']=='proposed'}
        candidates=by_method.get(selected,[]) if selected else [[x,(a+b)/2] for a,b in columns[x]]
        candidates=copy.deepcopy(candidates);flags=[]
        if not selected:flags.append('no_connected_method')
        if len(by_method)==1:flags.append('single_method_proposal')
        if selected=='occlusion_graph':flags.append('dark_occlusion_route_requires_review')
        for m,ps in by_method.items():
            for p in ps:
                if candidates and all(abs(p[1]-q[1])>2 for q in candidates):
                    flags.append('method_disagreement');candidates.append(p)
        if len(columns[x])>1:flags.append('multiple_ink_runs')
        if any(b-a+1>8 for a,b in columns[x]):flags.append('thick_or_merged_ink')
        column_flags=points[x]['flags']
        if selected:column_flags=[f for f in column_flags if f not in ('disconnected_path','unanchored_segment','no_ink')]
        flags.extend(column_flags)
        samples.append({'x':x,'candidates':candidates,'flags':sorted(set(flags)),
                        'method_candidates':by_method,'selected_method':selected})
    return {'selected_method':selected,'methods':methods,'samples':samples}


def run(job_path,out_dir):
    jp=Path(job_path).resolve();job=read_json(jp);out=Path(out_dir).resolve()
    if out.exists():raise ValueError('output directory exists')
    review={'schema_version':1,'sources':copy.deepcopy(job['sources']),
            'panels':copy.deepcopy(job['panels']),'items':[]}
    images={};overlays={};records=[]
    for name,src in review['sources'].items():
        path=(jp.parent/src['image']).resolve();src['image']=str(path);src['sha256']=digest(path)
        im=Image.open(path).convert('RGB');images[name]=np.array(im);overlays[name]=im.copy()
    for t in job['traces']:
        source=review['panels'][t['panel']]['source'];result=route_trace(images[source],t)
        draw=ImageDraw.Draw(overlays[source])
        if result['selected_method']:
            for x,y in result['methods'][result['selected_method']]['path']:draw.point((round(x),round(y)),fill='red')
        for s in result['samples']:
            review['items'].append({'id':f"{t['id']}-{s['x']}",'panel':t['panel'],
                'identity':{**t['identity'],'source_pixel_x':s['x']},'roi':t['roi'],
                'anchor':s['candidates'][0] if s['candidates'] else [s['x'],(t['roi'][1]+t['roi'][3])/2],
                'roles':{'value':s['candidates']},'flags':s['flags'],
                'limitations':['automatic method routing is not visual or independent verification'],
                'routing_evidence':s})
        records.append({'id':t['id'],'parameters':t,**result})
    out.mkdir(parents=True);write_json(out/'review_job.json',review);load_job(out/'review_job.json')
    write_json(out/'routing.json',{'sources':review['sources'],'traces':records,'scope':'unreviewed method proposals'})
    for i,im in enumerate(overlays.values()):im.save(out/f'route_overlay_{i}.png')
    return {'job':str(out/'review_job.json'),'traces':len(records),
            'routes':[{'id':r['id'],'selected':r['selected_method']} for r in records]}


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
