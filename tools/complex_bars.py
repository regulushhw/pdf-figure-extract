"""Bitmap bar bodies, stem endpoints and T-caps; original geometry rules.

Uses OpenCV morphology, explicit slots/zero line and reviewed semantic labels.
No inferred series ordering, missing-to-zero fill or endpoint averaging.
python tools/complex_bars.py job.json new_output_directory
"""
import sys
import numpy as np
import cv2
from propose_bitmap_points import select_pixels, runs
from review_points import axis_fit, value_at
from complex_common import inputs, roi, item, finish


def blocks(values):
    values=np.asarray(values)
    if not len(values):return []
    return [list(map(int,(g[0],g[-1]))) for g in np.split(values,np.flatnonzero(np.diff(values)>1)+1)]


def bar_geometry(mask,box,x,zero,min_body_width=4,max_body_gap=2):
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


def analyze_bar(mask,spec,zero):
    box=spec['roi'];x=spec['x'];geo=bar_geometry(mask,box,x,zero,
        spec.get('min_body_width',4),spec.get('max_body_gap',2))
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
    records=[]
    for kind,specs in (('bar',job.get('bars',[])),('capped',job.get('capped_ranges',[]))):
        for spec in specs:
            panel=review['panels'][spec['panel']];rgb=images[panel['source']]
            mask=select_pixels(rgb,spec.get('selector',job.get('selector',{'mode':'gray','range':[0,180]})))
            for a,b,c,d in spec.get('exclude_boxes',[]):
                roi([a,b,c,d],mask.shape);mask[b:d+1,a:c+1]=False
            zero=spec.get('zero_y',panel.get('zero_y'))
            if zero is None:raise ValueError('explicit confirmed zero_y required')
            roles,flags,geo=analyze_bar(mask,spec,zero) if kind=='bar' else analyze_capped(mask,spec,zero)
            cal=axis_fit(panel['axis'])
            if cal['coordinate']!='y':raise ValueError('bar/range values require a y axis')
            geo['candidate_values']={role:[value_at(p,cal) for p in ps] for role,ps in roles.items()}
            review['items'].append(item(spec['id'],spec['panel'],spec['identity'],spec['roi'],roles,flags,geo))
            records.append({'id':spec['id'],'kind':kind,**geo})
    return finish(review,overlays,out,records,'bar_stem_cap_geometry')


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2])))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
