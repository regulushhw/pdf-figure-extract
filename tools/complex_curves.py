"""Bounded multi-hypothesis bitmap tracking with pen width and slope continuity.

Explicit per-series anchors/selectors; dashed gaps are missing, never invented
measurements. Joint shared-ink checks preserve crossing/overlap uncertainty.
python tools/complex_curves.py job.json new_output_directory
"""
import math
import sys
from bisect import bisect_left
import numpy as np
from PIL import ImageDraw
from propose_bitmap_points import select_pixels,runs
from complex_common import inputs,roi,item,finish
from review_points import axis_fit, value_at_position


def reconstruct(node):
    points={}
    while node is not None:
        x,y,ink,node=node
        points[x]=(y,ink)
    return points


def automatic_anchors(columns, width_range, max_step):
    """Seed only an isolated, locally supported ink run in the series mask."""
    lo, hi = width_range
    isolated = {}
    for x, spans in columns.items():
        compatible = [(a,z) for a,z in spans if lo <= z-a+1 <= hi]
        if len(compatible) == 1:
            a, z = compatible[0]
            isolated[x] = (a+z)/2
    supported = [(x, y) for x, y in isolated.items()
                 if any(k in isolated and abs(isolated[k]-y) <= max_step
                        for k in (x-1, x+1))]
    return [list(supported[0]), list(supported[-1])] if len(supported) >= 2 else []


def find_horizontal_strokes(mask, bounds, rows):
    """Report near-horizontal ink, without deciding its source identity."""
    l,t,r,b = bounds
    if not isinstance(rows, list) or any(type(y) is not int for y in rows):
        raise ValueError('axis_rule_rows must be a list of integer source rows')
    candidates = set()
    for center in rows:
        near = range(max(t,center-2),min(b,center+2)+1)
        if not near:
            continue
        peak = max(near, key=lambda y: int(mask[y,l:r+1].sum()))
        stroke = mask[peak,l:r+1]
        if stroke.mean() < .72 or any(piece.mean() < .6 for piece in np.array_split(stroke,3)):
            continue
        candidates.add(peak)
    return sorted(candidates)


def horizontal_review_candidates(mask, bounds, axis):
    left, top, right, bottom = bounds
    ink = mask[top:bottom+1, left:right+1]
    coverage = ink.mean(axis=1)
    bands = np.flatnonzero((coverage >= .8) &
                           np.logical_and.reduce([part.mean(axis=1) >= .6
                                                  for part in np.array_split(ink, 3, axis=1)]))
    if not len(bands):
        return []
    calibration = axis_fit(axis)
    ticks = axis['ticks'] + axis.get('check_ticks', [])
    candidates = []
    for band in np.split(bands, np.flatnonzero(np.diff(bands) > 1) + 1):
        row = top + int(band[np.argmax(coverage[band])])
        nearest = min(ticks, key=lambda tick: abs(tick[0]-row))
        candidates.append({'row': row, 'row_band': [top+int(band[0]), top+int(band[-1])],
                           'span_px': [left, right], 'ink_fraction': float(coverage[row-top]),
                           'axis_value': value_at_position(row, calibration),
                           'nearby_tick': nearest if abs(nearest[0]-row) <= 2 else None})
    return candidates


def track(mask,spec):
    result = track_mask(mask, spec)
    span = spec.get('dash_fragment_span_px')
    reference = spec.get('dash_fragment_reference_px')
    if reference is not None:
        if span is not None:
            raise ValueError('choose a measured dash reference or an explicit fragment span')
        if isinstance(reference, list) and reference and isinstance(reference[0], list):
            widths = [measure_dash_span(mask, spec, point) for point in reference]
            span = max(widths)
            if span > 2 * min(widths):
                raise ValueError('dash reference spans disagree; check isolated ink and series identity')
            result['dash_fragment_reference'] = {
                'points_px': reference, 'measured_spans_px': widths, 'span_px': span}
        else:
            span = measure_dash_span(mask, spec, reference)
            result['dash_fragment_reference'] = {'point_px': reference, 'span_px': span}
    if span is None:
        return result
    if (type(span) is not int or span < 2
            or spec.get('style', {}).get('pattern') not in ('dashed', 'dotted')
            or spec.get('max_component_span_px') is not None):
        raise ValueError('dash_fragment_span_px requires a dashed or dotted trace and an unfiltered base pass')
    sample_x = sorted(set(spec['sample_x']) | {x for x,_ in spec.get('anchors', [])})
    fragment_spec = {**spec, 'auto_anchors': True, 'sample_x': sample_x,
                     'max_component_span_px': span}
    fragment_spec.pop('anchors', None)
    fragment_mask = filter_component_mask(mask, fragment_spec)
    fragments = track_mask(mask, fragment_spec, component_mask=fragment_mask)
    anchors = result.get('anchors') or spec.get('anchors', [])
    fragment_path = dict(fragments.get('selected_path', []))
    if anchors:
        x, y = anchors[0]
        tol = spec.get('anchor_tolerance_px', 2)
        if not any(abs(fragment_path[k]-y)<=tol for k in (x-1,x,x+1) if k in fragment_path):
            fragment_path = {}
    by_x = {s['x']:s for s in fragments['samples']}
    _, top, _, bottom = spec['roi']
    lo, hi = spec.get('style', {}).get('width_range', [1, 8])
    for sample in result['samples']:
        support = by_x[sample['x']]
        if not fragment_path or support['selected'] is None:
            if sample['selected'] is not None:
                sample['flags'].append('dash_identity_not_supported_by_fragments')
            continue
        center = support['selected'][1]
        base = sample['selected']
        if base is not None and abs(base[1]-center)<=2:
            continue
        sample['base_selected'] = base
        sample['base_flags'] = sample['flags']
        sample['selected'] = support['selected']
        sample['candidates'] = support['candidates']
        sample['flags'] = [flag for flag in support['flags'] if flag != 'dash_gap_unmeasured']
        sample['flags'].append('dash_fragment_route_review')
        for key in ('interpolation_bracket', 'observed_ink_y_bounds'):
            sample.pop(key, None)
            if key in support:
                sample[key] = support[key]
    for sample in result['samples']:
        x = sample['x']
        # A plausible global route can still miss a sharp dotted peak.
        # Isolated marks there are alternatives, not automatically assigned.
        local = [[x,(a+z)/2] for a,z in runs(fragment_mask,x,top,bottom)
                 if lo<=z-a+1<=hi and all(abs((a+z)/2-y)>1 for _,y in sample['candidates'])]
        if local:
            sample['fragment_candidates'] = local
            sample['flags'].append('unlinked_dash_marks_review')
            sample['candidates'].extend(local)
    if fragment_path:
        result['dash_fragment_path'] = fragments['selected_path']
    return result


def measure_dash_span(mask, spec, reference):
    import cv2

    left, top, right, bottom = roi(spec['roi'], mask.shape)
    if (not isinstance(reference, list) or len(reference) != 2
            or any(type(coordinate) is not int for coordinate in reference)):
        raise ValueError('dash fragment reference requires integer source pixel coordinates')
    x, y = reference
    if not (left < x < right and top < y < bottom and mask[y, x]):
        raise ValueError('dash fragment reference must be on ink inside the ROI')
    region = mask[top:bottom+1, left:right+1]
    _, labels, stats, _ = cv2.connectedComponentsWithStats(region.astype('uint8'), connectivity=8)
    label = labels[y-top, x-left]
    offset_x, offset_y, width, height, _ = stats[label]
    if (offset_x == 0 or offset_y == 0 or offset_x+width == region.shape[1]
            or offset_y+height == region.shape[0]):
        raise ValueError('dash fragment reference component is clipped by the ROI')
    return int(width)


def filter_component_mask(mask, spec):
    min_span = spec.get('min_component_span_px', 1)
    max_span = spec.get('max_component_span_px')
    if type(min_span) is not int or min_span < 1:
        raise ValueError('min_component_span_px must be a positive integer')
    if max_span is not None and (type(max_span) is not int or max_span < min_span):
        raise ValueError('max_component_span_px must be an integer at least min_component_span_px')
    if min_span > 1 or max_span is not None:
        import cv2
        l,t,r,b = roi(spec['roi'],mask.shape)
        if (r-l+1)*(b-t+1)>(20_000_000 if spec.get('auto_anchors',False) else 300000):
            raise ValueError('curve ROI exceeds pixel limit; split panel')
        region = mask[t:b+1,l:r+1]
        _, labels, stats, _ = cv2.connectedComponentsWithStats(region.astype('uint8'),connectivity=8)
        mask = mask.copy()
        widths = stats[labels,cv2.CC_STAT_WIDTH]
        kept = (labels != 0) & (widths >= min_span)
        if max_span is not None:
            kept &= widths <= max_span
        mask[t:b+1,l:r+1] = kept
    return mask


def track_mask(mask,spec,component_mask=None):
    horizontal_strokes = []
    if spec.get('axis_rule_rows') is not None:
        horizontal_strokes = find_horizontal_strokes(mask,roi(spec['roi'],mask.shape),spec['axis_rule_rows'])
    mask = filter_component_mask(mask,spec) if component_mask is None else component_mask
    result = _track(mask, spec)
    style = spec.get('style', {})
    if (spec.get('retry_measured_dash_gap') is True and result['status'] == 'disconnected'
            and style.get('pattern') in ('dashed', 'dotted')
            and spec.get('max_component_span_px') is not None and spec.get('anchors')):
        top, bottom = spec['roi'][1::2]
        anchors = np.asarray(spec['anchors'])
        left, right = int(anchors[0, 0]), int(anchors[-1, 0])
        columns = np.arange(left, right+1)
        center = np.interp(columns, anchors[:, 0], anchors[:, 1])
        width_hi = style.get('width_range', [1, 8])[1]
        # Estimate gaps near the anchored series so distant ink cannot fill them.
        distance = np.abs(np.arange(top, bottom+1)[:, None] - center)
        corridor = distance <= 2 * width_hi
        ink = columns[(mask[top:bottom+1, left:right+1] & corridor).any(axis=0)]
        gaps = np.diff(ink) - 1
        original_limit = style.get('max_gap_px', 8)
        measured = int(gaps.max()) if gaps.size else 0
        cap = min(120, max(30, 3*width_hi))
        if original_limit < measured <= cap:
            retry_spec = {**spec, 'style': {**style, 'max_gap_px': measured}}
            retry = _track(mask, retry_spec)
            if retry['status'] == 'proposed_unreviewed':
                spec, result = retry_spec, retry
                result['dash_gap_retry'] = {'original_limit_px': original_limit,
                                            'measured_gap_px': measured}
                for sample in result['samples']:
                    sample['flags'].append('measured_dash_gap_retry_review')
    result['horizontal_stroke_rows'] = horizontal_strokes
    if result['status'] == 'disconnected':
        flag_horizontal_strokes(result['samples'], horizontal_strokes)
        return result
    last = mask.shape[1] - 1
    left, top, right, bottom = spec['roi']
    reverse_spec = {**spec, 'roi': [last-right, top, last-left, bottom],
                    'sample_x': [last-x for x in spec['sample_x']]}
    if not spec.get('auto_anchors'):
        reverse_spec['anchors'] = [[last-x,y] for x,y in reversed(spec['anchors'])]
    reverse = _track(mask[:, ::-1], reverse_spec)
    by_x = {last-s['x']:s for s in reverse['samples'] if s['selected'] is not None}
    path = dict(result['selected_path'])
    for sample in result['samples']:
        x = sample['x']
        back = by_x.get(x)
        if back is None:
            continue
        if sample['selected'] is None:
            if result['status'] != 'partial_unreviewed':
                continue
            sample.update(selected=[x,back['selected'][1]],
                          candidates=[[x,y] for _,y in back['candidates']],
                          flags=[*back['flags'],'recovered_from_right_anchor_review'])
            if 'observed_ink_y_bounds' in back:
                sample['observed_ink_y_bounds'] = back['observed_ink_y_bounds']
            if 'interpolation_bracket' in back:
                sample['interpolation_bracket'] = [[last-k,y] for k,y in reversed(back['interpolation_bracket'])]
        elif abs(sample['selected'][1]-back['selected'][1])>2:
            sample['flags'].append('opposite_directions_disagree')
            sample['reverse_candidate'] = [x,back['selected'][1]]
            if all(abs(y-back['selected'][1])>1 for _,y in sample['candidates']):
                sample['candidates'].append(sample['reverse_candidate'])
    for x,y in reverse.get('selected_path',[]):
        if result['status'] == 'partial_unreviewed':
            path.setdefault(last-x,y)
    result['selected_path'] = [[x,path[x]] for x in sorted(path)]
    result['missing_x'] = sorted(x for x in range(result['anchors'][0][0],result['anchors'][-1][0]+1)
                                 if x not in path)
    flag_horizontal_strokes(result['samples'], horizontal_strokes)
    return result


def flag_horizontal_strokes(samples, rows):
    if rows:
        for sample in samples:
            if ('horizontal_stroke_identity_review' not in sample['flags']
                    and (not sample['selected'] or min(abs(sample['selected'][1]-y) for y in rows)<=3)):
                sample['flags'].append('horizontal_stroke_identity_review')


def _track(mask,spec):
    l,t,r,b=roi(spec['roi'],mask.shape)
    auto=spec.get('auto_anchors',False)
    if auto and spec.get('anchors'):
        raise ValueError('choose explicit anchors or auto_anchors')
    if (r-l+1)*(b-t+1)>(20_000_000 if auto else 300000):
        raise ValueError('curve ROI exceeds pixel limit; split panel')
    samples=spec['sample_x']
    if not samples or len(samples)!=len(set(samples)) or any(type(x) is not int or not l<=x<=r for x in samples):
        raise ValueError('unique sample_x inside ROI required')
    if auto:
        columns={x:runs(mask,x,t,b) for x in range(min(samples),max(samples)+1)}
        anchors=automatic_anchors(columns,spec.get('style',{}).get('width_range',[1,8]),
                                  spec.get('max_step_px',10))
        if not anchors:
            return {'status':'disconnected','anchors':[],'samples':[
                {'x':x,'selected':None,'candidates':[],'flags':['no_isolated_ink_anchors']} for x in samples]}
    else:
        anchors=spec['anchors']
    if len(anchors)<2 or any(len(p)!=2 or type(p[0]) is not int or not math.isfinite(p[1]) for p in anchors):
        raise ValueError('at least two source-pixel anchors required')
    if [p[0] for p in anchors]!=sorted(set(p[0] for p in anchors)):
        raise ValueError('anchors require increasing unique x')
    if any(not(l<=x<=r and t<=y<=b) for x,y in anchors):raise ValueError('anchor outside ROI')
    start,end=anchors[0][0],anchors[-1][0]
    if not auto and any(not start<=x<=end for x in samples):
        raise ValueError('unique sample_x inside anchor interval required')
    style=spec.get('style',{})
    pattern=style.get('pattern','solid')
    if pattern not in ('solid','dashed','dotted'):raise ValueError('pattern must be solid/dashed/dotted')
    broken = pattern != 'solid'
    max_gap=style.get('max_gap_px',0 if pattern=='solid' else 8)
    beam=spec.get('beam_width',32);tol=spec.get('anchor_tolerance_px',2)
    smooth=spec.get('slope_weight',0 if pattern=='dotted' else 1.5);max_step=spec.get('max_step_px',10)
    margin=spec.get('ambiguity_cost',1.0)
    lo,hi=style.get('width_range',[1,8])
    gap_limit = min(120, max(30, 3*hi)) if broken else 30
    if not(0<=max_gap<=gap_limit and type(max_gap) is int and 2<=beam<=128 and
           0<=tol<=10 and 0<=smooth<=20 and 0<max_step<=100 and 0<lo<=hi and margin>=0):
        raise ValueError('invalid bounded tracking parameters')
    if not auto:
        columns={x:runs(mask,x,t,b) for x in range(start,end+1)}
    constraints=dict(anchors)
    initial=[(a,z) for a,z in columns[start] if max(a-anchors[0][1],anchors[0][1]-z,0)<=tol]
    if not initial:raise ValueError('first anchor lacks selected ink')
    # State: total cost, last observed x/y, slope, current gap, linked ink path.
    states=[]
    for a,z in initial:
        y=max(a,min(z,anchors[0][1])) if z-a+1>hi else (a+z)/2
        states.append((0.,start,y,None,0,(start,y,(a,z),None)))
    failed_at=None
    for x in range(start+1,end+1):
        proposals=[]
        for cost,px,py,slope,gap,node in states:
            dx=x-px
            prediction=py+(slope or 0)*dx
            for a,z in columns[x]:
                center=(a+z)/2
                width=z-a+1
                if x in constraints and max(a-constraints[x],constraints[x]-z,0)>tol:continue
                width_penalty=max(lo-width,0,width-hi)*.4
                positions=[center]
                if width>hi and x in constraints:
                    positions=[max(a,min(z,constraints[x]))]
                elif width>hi and slope is not None:
                    # A wide run can contain multiple identities. Keep motion
                    # through the same observed ink as an alternative to its center.
                    continuation=max(a,min(z,prediction))
                    if abs(continuation-center)>1:
                        positions.append(continuation)
                for y in positions:
                    if abs(y-py)>max_step*dx:continue
                    dy=(y-py)/dx
                    bend=0 if slope is None else (dy-slope)**2
                    score=cost+smooth*bend+.015*abs(y-py)+width_penalty
                    filtered_slope=dy if slope is None else .7*slope+.3*dy
                    proposals.append((score,x,y,filtered_slope,0,(x,y,(a,z),node)))
            # This is a missing-ink hypothesis, not a measurement or interpolation.
            nearby_ink=any(abs((a+z)/2-prediction)<=max(3,hi) for a,z in columns[x])
            if x not in constraints and gap<max_gap and (broken or not nearby_ink):
                proposals.append((cost+1.5,px,py,slope,gap+1,node))
        if not proposals:
            failed_at=x;break
        # Keep at most two histories per local state.
        counts={};states=[]
        for s in sorted(proposals,key=lambda s:s[0]):
            key=(s[1],round(s[2],1),None if s[3] is None else round(s[3],1),s[4])
            if counts.get(key,0)>=2:continue
            counts[key]=counts.get(key,0)+1;states.append(s)
            if len(states)>=beam:break
    if failed_at is not None and not auto:
        return {'status':'disconnected','failed_at':failed_at,'samples':[
            {'x':x,'selected':None,'candidates':[],'flags':['no_end_anchor_connected_path']} for x in samples]}
    best=states[0][0]
    finalists=[s for s in states if s[0]<=best+margin]
    paths=[reconstruct(s[5]) for s in finalists]
    selected=paths[0]
    records=[]
    for x in samples:
        if not start<=x<=end:
            records.append({'x':x,'selected':None,'candidates':[],'flags':['outside_automatic_anchor_interval']})
            continue
        if failed_at is not None and x>=failed_at:
            records.append({'x':x,'selected':None,'candidates':[],
                            'flags':['trace_disconnected_at_or_before_sample']})
            continue
        options=[]
        for path in paths:
            if x in path and all(abs(path[x][0]-y)>1 for _,y in options):options.append([x,path[x][0]])
        flags=[];chosen=selected.get(x)
        if failed_at is not None:flags.append('trace_not_confirmed_by_end_anchor')
        if chosen is None:flags.append('dash_gap_unmeasured' if broken else 'gap_unmeasured')
        if len(options)>1:flags.append('competing_identity_paths')
        if chosen and not lo<=chosen[1][1]-chosen[1][0]+1<=hi:
            flags.append('merged_ink_or_pen_width_conflict')
        # Only changes near the chosen trajectory are relevant. Other curves in
        # the same vertical column do not automatically enter the review queue.
        if chosen:
            nearby=[(a,z) for a,z in columns[x] if abs((a+z)/2-chosen[0])<=max(4,hi)]
            if len(nearby)>1:flags.append('nearby_track_requires_identity_review')
        record={'x':x,'selected':[x,chosen[0]] if chosen else None,
                'candidates':options,'flags':flags}
        if chosen:
            record['observed_ink_y_bounds']=list(chosen[1])
        records.append(record)
    # Dash identity is checked, not manufactured by drawing across white gaps.
    gaps=[x for x in range(start,failed_at if failed_at is not None else end+1) if x not in selected]
    observed_gaps=[]
    for x in gaps:
        previous=next((k for k in range(x-1,start-1,-1) if k in selected),None)
        following=next((k for k in range(x+1,end+1) if k in selected),None)
        if previous is None or following is None:continue
        expected=selected[previous][0]+(selected[following][0]-selected[previous][0])*(x-previous)/(following-previous)
        if not any(abs((a+z)/2-expected)<=max(2,hi) for a,z in columns[x]):observed_gaps.append(x)
    if broken and not observed_gaps:
        for rec in records:rec['flags'].append('dash_pattern_not_observed')
        if auto:
            # A solid neighbour can win on smoothness. Keep it out of the
            # proposed dashed-series points unless the trace supports gaps.
            for rec in records:
                rec.update(selected=None,candidates=[])
            return {'status':'disconnected','anchors':anchors,'samples':records}
    if broken and spec.get('interpolate_dash_gaps') and observed_gaps:
        positions = sorted(selected)
        gap_positions = set(observed_gaps)
        for rec in records:
            x = rec['x']
            if rec['selected'] is not None or x not in gap_positions:
                continue
            index = bisect_left(positions, x)
            if index == 0 or index == len(positions):
                continue
            a,b = positions[index-1:index+1]
            if b-a-1 > max_gap:
                continue
            y = selected[a][0] + (selected[b][0]-selected[a][0]) * (x-a)/(b-a)
            rec.update(selected=[x,y],candidates=[[x,y]],interpolation_bracket=[[a,selected[a][0]],[b,selected[b][0]]])
            rec['flags'].append('local_dash_interpolation_review')
    return {'status':'partial_unreviewed' if failed_at is not None else 'proposed_unreviewed',
        'failed_at':failed_at,'cost':best,'hypotheses':len(paths),'anchors':anchors,
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
            common=sorted(x for x in pa.keys()&pb.keys() if abs(pa[x]-pb[x])<=tolerance)
            intervals=[]
            for x in common:
                if intervals and x<=intervals[-1][1]+1:
                    intervals[-1][1]=x
                else:
                    intervals.append([x,x])
            starts=[low-2 for low,_ in intervals]
            by_x = [{s['x']: s for s in trace['result']['samples']} for trace in (a,b)]
            for trace,peer in ((a,b),(b,a)):
                for sample in trace['result']['samples']:
                    x=sample['x']
                    index=bisect_left(starts,x+1)-1
                    if index<0 or x>intervals[index][1]+2:
                        continue
                    span={'x_bounds':intervals[index]}
                    if 'id' in peer:
                        span['peer_trace_id']=peer['id']
                    first, second = (samples.get(x, {}) for samples in by_x)
                    left = first.get('observed_ink_y_bounds')
                    right = second.get('observed_ink_y_bounds')
                    if (left and right and all(s.get('selected') and not s.get('interpolation_bracket')
                                              for s in (first, second))):
                        bottom, top = min(left[1], right[1]), max(left[0], right[0])
                        if top <= bottom:
                            span['shared_source_y_bounds_px'] = [top, bottom]
                            span['identity_confirmation_required'] = True
                    sample.setdefault('shared_ink_spans',[]).append(span)
                    if 'shared_ink_identity_review' not in sample['flags']:
                        sample['flags'].append('shared_ink_identity_review')


def run(path,out):
    job,review,images,overlays,out=inputs(path,out)
    if not job.get('traces'):raise ValueError('explicit per-series traces required')
    records=[]
    for spec in job['traces']:
        panel=review['panels'][spec['panel']];rgb=images[panel['source']]
        mask=select_pixels(rgb,spec['selector'])
        bounds = roi(spec['roi'], mask.shape)
        rules = horizontal_review_candidates(mask, bounds, panel['axis'])
        confirmed = spec.get('confirmed_grid_rows', [])
        removed_rows = []
        if confirmed:
            left, top, right, bottom = bounds
            for grid in confirmed:
                y = grid['row']
                if (type(y) is not int or not top <= y <= bottom or y in removed_rows
                        or not isinstance(grid.get('reason'), str) or not grid['reason'].strip()
                        or mask[y, left:right+1].mean() < .8):
                    raise ValueError('confirmed grid row requires visual evidence and ink spanning the extraction window')
                removed_rows.append(y)
            mask[removed_rows, left:right+1] = False
        for a,b,c,d in spec.get('exclude_boxes',[]):
            roi([a,b,c,d],mask.shape);mask[b:d+1,a:c+1]=False
        result = track(mask,spec)
        result['horizontal_review_candidates'] = rules
        flag_horizontal_strokes(result['samples'], [rule['row'] for rule in rules
                                                    if rule['row'] not in removed_rows])
        if confirmed:
            result['confirmed_grid_rows'] = confirmed
            for sample in result['samples']:
                point = sample['selected']
                sample['confirmed_grid_rows'] = removed_rows
                if point is None:
                    sample['flags'].append('check_grid_overlap_at_unresolved_sample')
                elif any(abs(point[1]-y) <= 1 for y in removed_rows):
                    sample['flags'].append('candidate_adjacent_to_confirmed_grid')
        records.append({'id':spec['id'],'panel':spec['panel'],'parameters':spec,'result':result})
    shared_ink(records)
    for record in records:
        spec,result=record['parameters'],record['result']
        draw=ImageDraw.Draw(overlays[review['panels'][spec['panel']]['source']])
        for x,y in result.get('selected_path',[]):draw.point((x,round(y)),fill='orange')
        for x,y in result.get('dash_fragment_path',[]):draw.point((x,round(y)),fill='deepskyblue')
        for sample in result['samples']:
            flags=sample['flags']
            # A measured-looking alternative must not fill the selected gap.
            options=sample['candidates'] if sample['selected'] or sample.get('fragment_candidates') else []
            review['items'].append(item(f'{spec["id"]}-{sample["x"]}',spec['panel'],
                {**spec['identity'],'source_pixel_x':sample['x']},spec['roi'],{'value':options},flags,sample))
    return finish(review,overlays,out,records,'width_slope_multihypothesis_curves')


if __name__=='__main__':
    try:print(__import__('json').dumps(run(sys.argv[1],sys.argv[2])))
    except Exception as exc:print('FAIL:',exc);sys.exit(1)
