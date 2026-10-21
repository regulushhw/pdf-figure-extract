"""Bounded multi-hypothesis bitmap tracking with pen width and slope continuity.

Explicit per-series anchors/selectors; dashed gaps are missing, never invented
measurements. Joint shared-ink checks preserve crossing/overlap uncertainty.
python tools/complex_curves.py job.json new_output_directory
"""
import math
import sys
from PIL import ImageDraw
from propose_bitmap_points import select_pixels,runs
from complex_common import inputs,roi,item,finish


def reconstruct(node):
    points={}
    while node is not None:
        x,y,width,node=node;points[x]=(y,width)
    return points


def track(mask,spec):
    l,t,r,b=roi(spec['roi'],mask.shape)
    if (r-l+1)*(b-t+1)>300000:raise ValueError('curve ROI exceeds 300000 pixels; split panel')
    anchors=spec['anchors']
    if len(anchors)<2 or any(len(p)!=2 or type(p[0]) is not int or not math.isfinite(p[1]) for p in anchors):
        raise ValueError('at least two source-pixel anchors required')
    if [p[0] for p in anchors]!=sorted(set(p[0] for p in anchors)):
        raise ValueError('anchors require increasing unique x')
    if any(not(l<=x<=r and t<=y<=b) for x,y in anchors):raise ValueError('anchor outside ROI')
    start,end=anchors[0][0],anchors[-1][0]
    samples=spec['sample_x']
    if not samples or len(samples)!=len(set(samples)) or any(type(x) is not int or not start<=x<=end for x in samples):
        raise ValueError('unique sample_x inside anchor interval required')
    style=spec.get('style',{})
    pattern=style.get('pattern','solid')
    if pattern not in ('solid','dashed'):raise ValueError('pattern must be solid/dashed')
    max_gap=style.get('max_gap_px',0 if pattern=='solid' else 8)
    beam=spec.get('beam_width',32);tol=spec.get('anchor_tolerance_px',2)
    smooth=spec.get('slope_weight',1.5);max_step=spec.get('max_step_px',10)
    margin=spec.get('ambiguity_cost',1.0)
    lo,hi=style.get('width_range',[1,8])
    if not(0<=max_gap<=30 and type(max_gap) is int and 2<=beam<=128 and
           0<=tol<=10 and 0<=smooth<=20 and 0<max_step<=100 and 0<lo<=hi and margin>=0):
        raise ValueError('invalid bounded tracking parameters')
    columns={x:runs(mask,x,t,b) for x in range(start,end+1)}
    constraints=dict(anchors)
    initial=[(a,z) for a,z in columns[start] if max(a-anchors[0][1],anchors[0][1]-z,0)<=tol]
    if not initial:raise ValueError('first anchor lacks selected ink')
    # State: total cost, last observed x/y, slope, current gap, linked ink path.
    states=[(0.,start,(a+z)/2,None,0,(start,(a+z)/2,z-a+1,None)) for a,z in initial]
    failed_at=None
    for x in range(start+1,end+1):
        proposals=[]
        for cost,px,py,slope,gap,node in states:
            dx=x-px
            for a,z in columns[x]:
                y=(a+z)/2;width=z-a+1
                if abs(y-py)>max_step*dx:continue
                if x in constraints and max(a-constraints[x],constraints[x]-z,0)>tol:continue
                dy=(y-py)/dx
                bend=0 if slope is None else (dy-slope)**2
                width_penalty=max(lo-width,0,width-hi)*.4
                score=cost+smooth*bend+.015*abs(y-py)+width_penalty
                filtered_slope=dy if slope is None else .7*slope+.3*dy
                proposals.append((score,x,y,filtered_slope,0,(x,y,width,node)))
            # This is a missing-ink hypothesis, not a measurement or interpolation.
            prediction=py+(slope or 0)*dx
            nearby_ink=any(abs((a+z)/2-prediction)<=max(3,hi) for a,z in columns[x])
            if x not in constraints and gap<max_gap and (pattern=='dashed' or not nearby_ink):
                proposals.append((cost+1.5,px,py,slope,gap+1,node))
        if not proposals:
            failed_at=x;break
        # Keep at most two histories per local state so alternatives can survive
        # a crossing without one state flooding the bounded beam.
        counts={};states=[]
        for s in sorted(proposals,key=lambda s:s[0]):
            key=(s[1],round(s[2],1),None if s[3] is None else round(s[3],1),s[4])
            if counts.get(key,0)>=2:continue
            counts[key]=counts.get(key,0)+1;states.append(s)
            if len(states)>=beam:break
    if failed_at is not None:
        return {'status':'disconnected','failed_at':failed_at,'samples':[
            {'x':x,'selected':None,'candidates':[],'flags':['no_end_anchor_connected_path']} for x in samples]}
    best=states[0][0]
    finalists=[s for s in states if s[0]<=best+margin]
    paths=[reconstruct(s[5]) for s in finalists]
    selected=paths[0]
    records=[]
    for x in samples:
        options=[]
        for path in paths:
            if x in path and all(abs(path[x][0]-y)>1 for _,y in options):options.append([x,path[x][0]])
        flags=[];chosen=selected.get(x)
        if chosen is None:flags.append('dash_gap_unmeasured' if pattern=='dashed' else 'gap_unmeasured')
        if len(options)>1:flags.append('competing_identity_paths')
        if chosen and not lo<=chosen[1]<=hi:flags.append('merged_ink_or_pen_width_conflict')
        # Only changes near the chosen trajectory are relevant. Other curves in
        # the same vertical column do not automatically enter the review queue.
        if chosen:
            nearby=[(a,z) for a,z in columns[x] if abs((a+z)/2-chosen[0])<=max(4,hi)]
            if len(nearby)>1:flags.append('nearby_track_requires_identity_review')
        records.append({'x':x,'selected':[x,chosen[0]] if chosen else None,
                        'candidates':options,'flags':flags})
    # Dash identity is checked, not manufactured by drawing across white gaps.
    gaps=[x for x in range(start,end+1) if x not in selected]
    observed_gaps=[]
    for x in gaps:
        previous=next((k for k in range(x-1,start-1,-1) if k in selected),None)
        following=next((k for k in range(x+1,end+1) if k in selected),None)
        if previous is None or following is None:continue
        expected=selected[previous][0]+(selected[following][0]-selected[previous][0])*(x-previous)/(following-previous)
        if not any(abs((a+z)/2-expected)<=max(2,hi) for a,z in columns[x]):observed_gaps.append(x)
    if pattern=='dashed' and not observed_gaps:
        for rec in records:rec['flags'].append('dash_pattern_not_observed')
    return {'status':'proposed_unreviewed','cost':best,'hypotheses':len(paths),
        'selected_path':[[x,yw[0]] for x,yw in sorted(selected.items())],
        'missing_x':gaps,'source_supported_dash_gaps':observed_gaps,'samples':records}


def shared_ink(traces,tolerance=2):
    """Coincident candidates do not establish equality or interchangeable IDs."""
    for i,a in enumerate(traces):
        if a['result']['status']=='disconnected':continue
        pa=dict(a['result']['selected_path'])
        for b in traces[i+1:]:
            if a['panel']!=b['panel'] or b['result']['status']=='disconnected':continue
            pb=dict(b['result']['selected_path'])
            common={x for x in pa.keys()&pb.keys() if abs(pa[x]-pb[x])<=tolerance}
            # A small neighborhood retains identity uncertainty at crossings.
            for trace in (a,b):
                for sample in trace['result']['samples']:
                    if any(sample['x']+dx in common for dx in range(-2,3)):
                        sample['flags'].append('shared_ink_identity_review')


def run(path,out):
    job,review,images,overlays,out=inputs(path,out)
    if not job.get('traces'):raise ValueError('explicit per-series traces required')
    records=[]
    for spec in job['traces']:
        panel=review['panels'][spec['panel']];rgb=images[panel['source']]
        mask=select_pixels(rgb,spec['selector'])
        for a,b,c,d in spec.get('exclude_boxes',[]):
            roi([a,b,c,d],mask.shape);mask[b:d+1,a:c+1]=False
        records.append({'id':spec['id'],'panel':spec['panel'],'parameters':spec,'result':track(mask,spec)})
    shared_ink(records)
    for record in records:
        spec,result=record['parameters'],record['result']
        draw=ImageDraw.Draw(overlays[review['panels'][spec['panel']]['source']])
        for x,y in result.get('selected_path',[]):draw.point((x,round(y)),fill='orange')
        for sample in result['samples']:
            flags=sample['flags']
            # A measured-looking alternative must not fill the selected gap.
            options=sample['candidates'] if sample['selected'] else []
            review['items'].append(item(f'{spec["id"]}-{sample["x"]}',spec['panel'],
                {**spec['identity'],'source_pixel_x':sample['x']},spec['roi'],{'value':options},flags,sample))
    return finish(review,overlays,out,records,'width_slope_multihypothesis_curves')


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2])))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
