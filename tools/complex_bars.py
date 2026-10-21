"""Bitmap bar bodies, stem endpoints and T-caps; original geometry rules.

Uses OpenCV morphology, explicit slots/zero line and reviewed semantic labels.
No inferred series ordering, missing-to-zero fill or endpoint averaging.
python tools/complex_bars.py job.json new_output_directory
"""
import sys
import json
import math
import numpy as np
import cv2
from propose_bitmap_points import select_pixels, runs, locate_bar_groups
from review_points import axis_fit, value_at
from complex_common import inputs, roi, item, finish


def blocks(values):
    values=np.asarray(values)
    if not len(values):return []
    return [list(map(int,(g[0],g[-1]))) for g in np.split(values,np.flatnonzero(np.diff(values)>1)+1)]


def bar_geometry(mask,box,x,zero,min_body_width=4,max_body_gap=2,symmetric=True):
    l,t,r,b=roi(box,mask.shape)
    if not l<x<r or not t<zero<b:raise ValueError('bar x/zero must be inside slot ROI')
    # A thin central stem is not a bar. Require ink on both sides of it.
    local=(mask[t:b+1,l:r+1].astype(np.uint8)*255)
    # Closing is used only to assess a hatched bar body. Range endpoints and
    # caps always use the untouched source mask.
    body=cv2.morphologyEx(local,cv2.MORPH_CLOSE,np.ones((5,3),np.uint8))>0
    left=body[:,0:max(0,int(x)-1-l)].sum(axis=1)
    right=body[:,min(r-l+1,int(x)+2-l):].sum(axis=1)
    supported=(left+right>=min_body_width)&(left>0)&(right>0)
    if not symmetric:
        # A declared bar-only chart has no central range stem to exclude.
        # Near-edge target columns still belong to a real finite-width body;
        # forcing support beyond x+2 would truncate it to a shorter neighbour.
        local_x=int(round(x))-l;supported=np.zeros(len(body),dtype=bool)
        for y,row in enumerate(body):
            if not row[local_x]:continue
            span_l=span_r=local_x
            while span_l>0 and row[span_l-1]:span_l-=1
            while span_r+1<len(row) and row[span_r+1]:span_r+=1
            supported[y]=span_r-span_l+1>=min_body_width
    tips=[]
    for direction in (-1,1):
        hit=[];gap=0
        for y in range(int(round(zero))+direction*3, t-1 if direction<0 else b+1,direction):
            if supported[y-t]:hit.append(y);gap=0
            else:
                gap+=1
                if gap>max_body_gap:break
        if len(hit)>=3:tips.append(min(hit) if direction<0 else max(hit))
    # Explicitly preserve two-sided/near-zero ambiguity.
    return {'bar_tips':[[x,float(y)] for y in tips], 'body_support_rows':np.flatnonzero(supported).tolist()}


def stems(mask,box,min_length=6,cap_min_width=5,cap_max_width=24,zero=None):
    l,t,r,b=roi(box,mask.shape)
    crop=(mask[t:b+1,l:r+1].astype(np.uint8)*255)
    horizontal=cv2.morphologyEx(crop,cv2.MORPH_OPEN,np.ones((1,cap_min_width),np.uint8))>0
    records=[]
    for x in range(l+1,r):
        for a,z in runs(mask,x,t,b):
            if z-a+1<min_length:continue
            caps=[]
            for end in (a,z):
                found=[]
                for y in range(max(t,end-2),min(b,end+2)+1):
                    for lo,hi in blocks(np.flatnonzero(horizontal[y-t])):
                        lo+=l;hi+=l
                        if lo<=x-2 and hi>=x+2 and cap_min_width<=hi-lo+1<=cap_max_width:
                            found.append({'x0':lo,'x1':hi,'y':y})
                caps.append(found)
            records.append({'x':x,'top':a,'bottom':z,'caps':caps})
    # Adjacent ink columns of the same stroke produce one geometric record.
    unique=[]
    for record in records:
        same=next((u for u in unique if abs(u['x']-record['x'])<=3 and
                   abs(u['top']-record['top'])<=2 and abs(u['bottom']-record['bottom'])<=2),None)
        if same:
            for k in (0,1):same['caps'][k].extend(record['caps'][k])
        else:unique.append(record)
    return unique


def fill_bar_interiors(mask,max_width):
    """Fill only enclosed, narrow background components for body support.

    The large plot background and open outlines remain untouched. Range stems
    and caps must still use the original mask. Width is an explicit parameter.
    """
    if type(max_width) is not int or not 2<=max_width<=40:
        raise ValueError('fill_outline_max_width must be an integer2..40 source pixels')
    n,labels,stats,_=cv2.connectedComponentsWithStats((~mask).astype(np.uint8),8)
    h,w=mask.shape;accept=np.zeros(n,dtype=bool)
    for k in range(1,n):
        x,y,ww,hh,area=map(int,stats[k])
        accept[k]=(x>0 and y>0 and x+ww<w and y+hh<h and ww<=max_width and area>=2)
    return mask|accept[labels]


def locate_group_slots(mask,panel,spec):
    """Use explicit equal-width group membership, not guessed series order."""
    if spec.get('within_group')!='equal_width':raise ValueError('explicit equal_width layout required')
    groups=spec['groups'];ids=[ident for group in groups for ident in group]
    if not groups or any(len(g)<2 for g in groups) or len(set(ids))!=len(ids):
        raise ValueError('distinct explicit grouped bar IDs required')
    near,far=spec.get('probe_offsets',[4,7])
    if any(type(v) is not int for v in (near,far)) or not 2<=near<far<=20:
        raise ValueError('probe_offsets need2..20 source pixels, outside zero-line ink')
    y=int(round(panel['zero_y']));l,r=spec['left'],spec['right']
    if any(type(v) is not int for v in (l,r)) or not (panel['bounds'][0]<=l<r<=panel['bounds'][2]):
        raise ValueError('group bounds outside panel')
    if y-far<panel['bounds'][1] or y+far>panel['bounds'][3]:raise ValueError('group probe outside panel')
    active=mask[y-far:y-near+1].any(axis=0)|mask[y+near:y+far+1].any(axis=0)
    # Join outline interiors, never bridge the known inter-month whitespace.
    kernel=spec.get('join_px',5)
    if type(kernel) is not int or not 1<=kernel<=9:raise ValueError('join_px must be1..9')
    profile=cv2.morphologyEx(active[None,:].astype(np.uint8),cv2.MORPH_CLOSE,
                              np.ones((1,kernel),np.uint8))>0
    spans=locate_bar_groups(profile,row=0,left=l,right=r,expected_count=len(groups),
                            min_width=spec.get('min_group_width',24))
    slots={}
    for group,(lo,hi) in zip(groups,spans):
        width=(hi-lo+1)/len(group)
        if not spec.get('min_bar_width',4)<=width<=spec.get('max_bar_width',10):
            raise ValueError('detected group incompatible with declared equal-width bars')
        for n,ident in enumerate(group):
            a=lo+n*width;b=lo+(n+1)*width-1
            slots[ident]={'x':round((a+b)/2,3),'left':math.floor(a),'right':math.ceil(b),
                          'group_span':[lo,hi],'within_group':'equal_width'}
    return slots


def analyze_bar(mask,spec,zero,body_mask=None):
    box=spec['roi'];x=spec['x'];geo=bar_geometry(mask if body_mask is None else body_mask,box,x,zero,
        spec.get('min_body_width',4),spec.get('max_body_gap',2),spec.get('range_expected',True))
    tips=geo['bar_tips'];flags=[]
    if len(tips)!=1:flags.append('bar_body_missing_or_two_sided')
    if not spec.get('range_expected',True):
        return {'bar_tip':tips},flags,geo
    candidates=stems(mask,box,spec.get('min_stem_length',6),spec.get('cap_min_width',5),
                     spec.get('cap_max_width',24),zero)
    # The actual stem must be near the assigned bar center. Edges are not stems.
    center=[s for s in candidates if abs(s['x']-x)<=spec.get('stem_window_px',2)]
    segments=[]
    for s in center:
        # Zero-line gaps may split a single stroke; endpoints remain explicit.
        if s['top']<=zero+4 and s['bottom']>=zero-4:segments.append(s)
        elif tips and max(s['top'],min(zero,tips[0][1]))<=min(s['bottom'],max(zero,tips[0][1])):
            segments.append(s)
    roles={'bar_tip':tips,'uncapped_top':[],'uncapped_bottom':[]}
    if segments:
        top=min(s['top'] for s in segments);bottom=max(s['bottom'] for s in segments)
        # A range endpoint coincident with body/baseline cannot be distinguished.
        for key,y in (('uncapped_top',top),('uncapped_bottom',bottom)):
            relevant=[s for s in segments if abs(s['top' if key.endswith('top') else 'bottom']-y)<=1]
            caps=[cap for s in relevant for cap in s['caps'][0 if key.endswith('top') else 1]]
            coincident=abs(y-zero)<=3 or any(abs(y-p[1])<=2 for p in tips)
            if caps and not coincident:flags.append(f'{key}_has_cap_requires_review')
            elif coincident:flags.append(f'{key}_merged_with_body_or_zero')
            else:roles[key]=[[x,float(y)]]
    else:flags.append('range_stem_not_identified')
    geo.update(stems=candidates,paired_segments=segments)
    return roles,flags,geo


def analyze_capped(mask,spec,zero):
    pairs=[]
    for s in stems(mask,spec['roi'],spec.get('min_stem_length',6),spec.get('cap_min_width',5),spec.get('cap_max_width',24)):
        if s['caps'][0] and s['caps'][1] and (not spec.get('crosses_zero',False) or s['top']<zero<s['bottom']):
            pairs.append(s)
    roles={'capped_top':[[float(s['x']),float(s['top'])] for s in pairs],
           'capped_bottom':[[float(s['x']),float(s['bottom'])] for s in pairs]}
    return roles,([] if len(pairs)==1 else ['capped_pair_missing_or_ambiguous']),{'pairs':pairs}


def run(path,out):
    job,review,images,overlays,out=inputs(path,out)
    if not job.get('bars') and not job.get('capped_ranges'):raise ValueError('explicit bars or capped_ranges required')
    records=[];mask_cache={};body_cache={};layout_cache={};layouts={}
    bar_lookup={b['id']:b for b in job.get('bars',[])}
    for layout in job.get('bar_groups',[]):
        members=[ident for group in layout['groups'] for ident in group]
        selectors=set()
        for ident in members:
            if ident not in bar_lookup or ident in layouts:raise ValueError('unknown/duplicate grouped bar ID')
            member=bar_lookup[ident]
            if member['panel']!=layout['panel'] or member.get('range_expected',True):
                raise ValueError('group layout requires same-panel bars explicitly without ranges')
            selectors.add(json.dumps([member.get('selector',job.get('selector',{'mode':'gray','range':[0,180]})),member.get('exclude_boxes',[])],sort_keys=True))
            layouts[ident]=layout
        if len(selectors)!=1:raise ValueError('grouped bars must use the same selector/exclusions')
    for kind,specs in (('bar',job.get('bars',[])),('capped',job.get('capped_ranges',[]))):
        for spec in specs:
            panel=review['panels'][spec['panel']];rgb=images[panel['source']]
            selector=spec.get('selector',job.get('selector',{'mode':'gray','range':[0,180]}))
            key=(panel['source'],json.dumps(selector,sort_keys=True),json.dumps(spec.get('exclude_boxes',[])))
            if key not in mask_cache:
                mask=select_pixels(rgb,selector)
                for a,b,c,d in spec.get('exclude_boxes',[]):
                    roi([a,b,c,d],mask.shape);mask[b:d+1,a:c+1]=False
                mask_cache[key]=mask
            mask=mask_cache[key]
            body_mask=None
            width=spec.get('fill_outline_max_width',job.get('fill_outline_max_width',0))
            if kind=='bar' and width:
                body_key=(key,width)
                if body_key not in body_cache:body_cache[body_key]=fill_bar_interiors(mask,width)
                body_mask=body_cache[body_key]
            zero=spec.get('zero_y',panel.get('zero_y'))
            if zero is None:raise ValueError('explicit confirmed zero_y required')
            slot=None;original_spec=spec
            if kind=='bar' and spec['id'] in layouts:
                layout=layouts[spec['id']];layout_key=(key,json.dumps(layout,sort_keys=True),width)
                if layout_key not in layout_cache:
                    layout_cache[layout_key]=locate_group_slots(mask if body_mask is None else body_mask,panel,layout)
                slot=layout_cache[layout_key][spec['id']]
                spec={**spec,'x':slot['x'],'roi':[slot['left'],spec['roi'][1],slot['right'],spec['roi'][3]]}
            roles,flags,geo=analyze_bar(mask,spec,zero,body_mask) if kind=='bar' else analyze_capped(mask,spec,zero)
            if body_mask is not None:geo['body_support_preprocessing']={'fill_outline_max_width':width,'range_ink_unchanged':True}
            if slot:geo['slot_location']={**slot,'previous_x':original_spec['x'],'previous_roi':original_spec['roi']}
            cal=axis_fit(panel['axis'])
            if cal['coordinate']!='y':raise ValueError('bar/range values require a y axis')
            geo['candidate_values']={role:[value_at(p,cal) for p in ps] for role,ps in roles.items()}
            review['items'].append(item(spec['id'],spec['panel'],spec['identity'],spec['roi'],roles,flags,geo))
            records.append({'id':spec['id'],'kind':kind,**geo})
    return finish(review,overlays,out,records,'bar_stem_cap_geometry')


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2])))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
