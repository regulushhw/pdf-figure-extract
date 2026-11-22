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
            for end, direction in ((a, 1), (z, -1)):
                found=[]
                # A thick vertical stroke can meet the absolute cap width.
                # Compare against the contiguous shaft on the inward side.
                shaft = []
                for offset in range(3, min(11, (z-a)//2+1)):
                    row = end + direction*offset
                    lo = hi = x-l
                    while lo > 0 and crop[row-t, lo-1]:
                        lo -= 1
                    while hi < r-l and crop[row-t, hi+1]:
                        hi += 1
                    shaft.append((lo+l, hi+l))
                if len(shaft) >= 2:
                    shaft_left, shaft_right = np.median(shaft, axis=0)
                else:
                    shaft_left = shaft_right = x
                for y in range(max(t,end-2),min(b,end+2)+1):
                    for lo,hi in blocks(np.flatnonzero(horizontal[y-t])):
                        lo+=l;hi+=l
                        if (lo <= x-2 and hi >= x+2 and cap_min_width <= hi-lo+1 <= cap_max_width
                                and lo <= shaft_left-1 and hi >= shaft_right+1):
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


def guided_stems(mask, spec):
    """Search along two observed shaft positions while retaining source pixels."""
    l,t,r,b = roi(spec['roi'],mask.shape)
    (x0,y0),(x1,y1) = spec['stem_guide']
    if not (l <= x0 <= r and l <= x1 <= r and t <= y0 < y1 <= b):
        raise ValueError('stem guide anchors must be ordered within the source ROI')
    x = round(spec['x'])
    slope = (x1-x0)/(y1-y0)
    if abs(slope) > .1:
        raise ValueError('stem guide exceeds the supported near-vertical range')
    dy = np.arange(t,b+1)
    shifts = np.rint(x0+(dy-y0)*slope-x).astype(int)
    source_x = np.arange(l,r+1)[None,:] + shifts[:,None]
    valid = (source_x>=l)&(source_x<=r)
    aligned = valid & mask[dy[:,None],np.clip(source_x,l,r)]
    candidates = stems(aligned,[0,0,r-l,b-t],spec.get('min_stem_length',6),
                       spec.get('cap_min_width',5),spec.get('cap_max_width',24))
    for stem in candidates:
        stem['x']+=l
        stem['top']+=t
        stem['bottom']+=t
        stem['source_x']=[int(stem['x']+shifts[y-t]) for y in (stem['top'],stem['bottom'])]
        for caps in stem['caps']:
            for cap in caps:
                shift=int(shifts[cap['y']])
                cap['y']+=t
                cap['x0']+=l+shift
                cap['x1']+=l+shift
    return candidates


def analyze_bar(mask,spec,zero,body_mask=None):
    box=spec['roi'];x=spec['x'];geo=bar_geometry(mask if body_mask is None else body_mask,box,x,zero,
        spec.get('min_body_width',4),spec.get('max_body_gap',2),spec.get('range_expected',True))
    tips=geo['bar_tips'];flags=[]
    bounds = geo['role_y_bounds_px'] = {}
    if len(tips) == 1:
        left, _, right, _ = box
        row = mask[int(tips[0][1]), left:right+1]
        # The selected body edge must also have support in untouched ink.
        center = int(round(x)) - left
        min_width = spec.get('min_body_width', 4)
        if spec.get('range_expected', True):
            left_ink = row[:max(0, center-1)].sum()
            right_ink = row[center+2:].sum()
            raw_support = left_ink > 0 and right_ink > 0 and left_ink + right_ink >= min_width
        else:
            raw_support = row[center] and row.sum() >= min_width
        if raw_support:
            bounds['bar_tip'] = [tips[0][1], tips[0][1]]
    if len(tips)!=1:flags.append('bar_body_missing_or_two_sided')
    if not spec.get('range_expected',True):
        return {'bar_tip':tips},flags,geo
    candidates=guided_stems(mask,spec) if 'stem_guide' in spec else stems(
        mask,box,spec.get('min_stem_length',6),spec.get('cap_min_width',5),spec.get('cap_max_width',24),zero)
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
            inside_body=any(min(zero,p[1])<y<max(zero,p[1]) for p in tips)
            if caps and not coincident:flags.append(f'{key}_has_cap_requires_review')
            elif coincident:flags.append(f'{key}_merged_with_body_or_zero')
            elif inside_body:flags.append(f'{key}_inside_bar_body')
            else:
                segment=min(relevant,key=lambda s:(abs(s['top' if key.endswith('top') else 'bottom']-y),abs(s['x']-x)))
                source_x=segment.get('source_x',[segment['x']]*2)[0 if key.endswith('top') else 1]
                roles[key]=[[source_x,float(y)]]
                rows = [s['top' if key.endswith('top') else 'bottom'] for s in relevant]
                bounds[key] = [min(rows), max(rows)]
    else:flags.append('range_stem_not_identified')
    geo.update(stems=candidates,paired_segments=segments)
    if 'stem_guide' in spec:
        geo['stem_guide']=spec['stem_guide']
    return roles,flags,geo


def same_capped_stroke(mask, left, right):
    """Collapse thick ink only when the stem and both caps are shared."""
    if abs(left['top']-right['top'])>2 or abs(left['bottom']-right['bottom'])>2:
        return False
    lo, hi = sorted((left['x'], right['x']))
    for a, b in zip(left['caps'], right['caps']):
        if not any(abs(c['y']-d['y'])<=1 and max(c['x0'],d['x0'])<=lo
                   and min(c['x1'],d['x1'])>=hi for c in a for d in b):
            return False
    top=max(left['top'],right['top'])
    bottom=min(left['bottom'],right['bottom'])
    for fraction in (.25, .5, .75):
        row=round(top+(bottom-top)*fraction)
        if not mask[row-1:row+2,lo:hi+1].any(axis=0).all():
            return False
    return True


def analyze_capped(mask,spec,zero):
    window = spec.get('stem_x_window')
    if window is not None:
        left, _, right, _ = spec['roi']
        if (not isinstance(window, list) or len(window) != 2
                or any(type(x) is not int for x in window)
                or not left < window[0] <= window[1] < right):
            raise ValueError('stem_x_window must be an inclusive source-pixel interval inside the ROI')
    pairs=[]
    for s in stems(mask,spec['roi'],spec.get('min_stem_length',6),spec.get('cap_min_width',5),spec.get('cap_max_width',24)):
        if s['caps'][0] and s['caps'][1] and (not spec.get('crosses_zero',False) or s['top']<zero<s['bottom']):
            pairs.append(s)
    groups=[]
    for pair in sorted(pairs,key=lambda s:s['x']):
        same=next((group for group in groups if same_capped_stroke(mask,group[-1],pair)
                   and abs(group[0]['top']-pair['top'])<=2
                   and abs(group[0]['bottom']-pair['bottom'])<=2),None)
        if same is None:
            groups.append([pair])
        else:
            same.append(pair)
    distinct=[]
    outside=[]
    for group in groups:
        columns = [s['x'] for s in group]
        within = group if window is None else [s for s in group if window[0] <= s['x'] <= window[1]]
        middle=(columns[0]+columns[-1])/2
        representative=min(within or group,key=lambda s:abs(s['x']-middle))
        evidence = {**representative,'contributing_stem_columns':columns}
        if not within:
            outside.append(evidence)
        else:
            distinct.append(evidence)
    roles={'capped_top':[[float(s['x']),float(s['top'])] for s in distinct],
           'capped_bottom':[[float(s['x']),float(s['bottom'])] for s in distinct]}
    bounds = {}
    if len(distinct) == 1:
        stroke = distinct[0]
        for index, end in enumerate(('top', 'bottom')):
            rows = [stroke[end], *(cap['y'] for cap in stroke['caps'][index])]
            bounds[f'capped_{end}'] = [min(rows), max(rows)]
    evidence = {'pairs':distinct, 'role_y_bounds_px':bounds}
    if window is not None:
        evidence.update(stem_x_window=window, outside_stem_window=outside)
    return roles,([] if len(distinct)==1 else ['capped_pair_missing_or_ambiguous']),evidence


def body_baseline_at(panel, x):
    """Locate bar-body connectivity on a skewed scan; the axis stays fixed."""
    (left, y0), (right, y1) = panel['body_baseline']
    pl, pt, pr, pb = panel['bounds']
    if not (pl <= left < right <= pr and pt < y0 < pb and pt < y1 < pb and left <= x <= right):
        raise ValueError('body_baseline must bracket the bar inside the source panel')
    return y0 + (y1-y0)*(x-left)/(right-left)


def analyze_boxplot(mask, spec):
    left, top, right, bottom = roi(spec['roi'], mask.shape)
    x = spec['x']
    half = spec['box_half_width']
    if not left <= x-half < x < x+half <= right or half < 5:
        raise ValueError('box center and half-width must fit inside the slot')
    band = mask[top:bottom+1, x-half:x+half+1]
    width = band.shape[1]
    horizontal = band.sum(axis=1)
    edges = blocks(np.flatnonzero(horizontal >= .85 * width))
    roles = {role: [] for role in ('q3', 'median', 'q1', 'upper_whisker', 'lower_whisker')}
    evidence = {'horizontal_box_bands': [[top+a, top+b] for a,b in edges],
                'role_y_bounds_px': {}}
    if len(edges) != 3:
        return roles, ['box_horizontal_bands_ambiguous'], evidence
    first, last = edges[0][0], edges[-1][1]
    if min(band[first:last+1, :2].any(axis=1).mean(),
           band[first:last+1, -2:].any(axis=1).mean()) < .8:
        return roles, ['box_side_connectivity_insufficient'], evidence
    for role, (a,b) in zip(('q3', 'median', 'q1'), edges):
        roles[role].append([x, top+(a+b)/2])
        evidence['role_y_bounds_px'][role] = [top+a, top+b]
    center = band[:, half-1:half+2].any(axis=1)
    flags = []
    for role, rows, anchor in (('upper_whisker', range(first), first),
                               ('lower_whisker', range(last+1, len(band)), last)):
        caps = blocks([y for y in rows if 5 <= horizontal[y] <= .75*width and center[y]])
        connected = [(a,b) for a,b in caps if center[min(a,anchor):max(b,anchor)+1].mean() >= .9]
        roles[role] = [[x, top+(a+b)/2] for a,b in connected]
        if len(connected) == 1:
            a,b = connected[0]
            evidence['role_y_bounds_px'][role] = [top+a, top+b]
        else:
            flags.append(f'{role}_ambiguous')
    return roles, flags, evidence


def run(path,out):
    job,review,images,overlays,out=inputs(path,out)
    if not any(job.get(key) for key in ('bars', 'capped_ranges', 'boxplots')):
        raise ValueError('explicit bars, capped_ranges or boxplots required')
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
    for kind,specs in (('bar',job.get('bars',[])),('capped',job.get('capped_ranges',[])),
                       ('boxplot',job.get('boxplots',[]))):
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
            if zero is None and kind!='boxplot':raise ValueError('explicit confirmed zero_y required')
            slot=None;original_spec=spec
            if kind=='bar' and spec['id'] in layouts:
                layout=layouts[spec['id']];layout_key=(key,json.dumps(layout,sort_keys=True),width)
                if layout_key not in layout_cache:
                    layout_cache[layout_key]=locate_group_slots(mask if body_mask is None else body_mask,panel,layout)
                slot=layout_cache[layout_key][spec['id']]
                spec={**spec,'x':slot['x'],'roi':[slot['left'],spec['roi'][1],slot['right'],spec['roi'][3]]}
            if kind=='bar' and 'body_baseline' in panel:
                if spec.get('range_expected',True):
                    raise ValueError('body_baseline is only for bar positions without range stems')
                zero=body_baseline_at(panel,spec['x'])
            if kind=='boxplot':
                roles,flags,geo=analyze_boxplot(mask,spec)
            elif kind=='bar':
                roles,flags,geo=analyze_bar(mask,spec,zero,body_mask)
            else:
                roles,flags,geo=analyze_capped(mask,spec,zero)
            if kind=='bar' and 'body_baseline' in panel:
                geo['body_baseline_evidence']={'source_anchors':panel['body_baseline'],'contact_y':zero}
            if body_mask is not None:geo['body_support_preprocessing']={'fill_outline_max_width':width,'range_ink_unchanged':True}
            if slot:geo['slot_location']={**slot,'previous_x':original_spec['x'],'previous_roi':original_spec['roi']}
            cal=axis_fit(panel['axis'])
            if cal['coordinate']!='y':raise ValueError('bar/range values require a y axis')
            geo['candidate_values']={role:[value_at(p,cal) for p in ps] for role,ps in roles.items()}
            if 'review_roles' in spec:
                selected = spec['review_roles']
                if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
                        or any(role not in roles for role in selected)):
                    raise ValueError('review_roles must be distinct supported object roles')
                roles = {role: roles[role] for role in selected}
            review['items'].append(item(spec['id'],spec['panel'],spec['identity'],spec['roi'],roles,flags,geo))
            records.append({'id':spec['id'],'kind':kind,**geo})
    return finish(review,overlays,out,records,'bar_stem_cap_geometry')


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2])))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
