"""Original bitmap candidate detector; no third-party digitizer code.

python tools/propose_bitmap_points.py job.json output_directory
Input: review_points job with each item's roles replaced by a selector.
Preserves separate vertical ink runs instead of averaging different tracks.
A thin stem with a thicker marker uses the marker center when that differs
from the full-run midpoint (asymmetric error bars). Uniform strokes are unchanged.
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


def marker_center_y(mask, x, y0, y1):
    """Vertical center of a compact marker sitting on a thinner stem.

    The midpoint of a tall ink run is the error-bar center. An asymmetric bar
    pulls that midpoint off the marker, so the reading is systematically low or
    high. The wider band that is not a short cap at either end is the marker.
    A uniform stroke, including a plain thick line or a symmetric bar whose
    center already matches the marker, returns None and the run center stays.
    """
    if y1 - y0 < 8 or not (0 <= x < mask.shape[1]):
        return None
    widths = []
    for y in range(y0, y1 + 1):
        if y < 0 or y >= mask.shape[0] or not mask[y, x]:
            return None
        left = x
        while left > 0 and mask[y, left - 1]:
            left -= 1
        right = x
        limit = mask.shape[1] - 1
        while right < limit and mask[y, right + 1]:
            right += 1
        widths.append(right - left + 1)
    stem = min(widths)
    wide = [w >= stem + 2 for w in widths]
    groups = []
    start = None
    for index, flag in enumerate(wide + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            groups.append((start, index - 1))
            start = None
    last = len(widths) - 1
    bodies = []
    for a, b in groups:
        height = b - a + 1
        cap = height <= 3 and (a <= 2 or b >= last - 2)
        if not cap and height >= 4:
            bodies.append((a, b))
    if not bodies:
        return None
    mid = last / 2
    a, b = max(bodies, key=lambda span: (span[1] - span[0] + 1, -abs((span[0] + span[1]) / 2 - mid)))
    return y0 + (a + b) / 2


def locate_bar_groups(mask, *, row, left, right, expected_count, min_width=8):
    """Find actual horizontal ink spans at an explicitly checked bar-foot row.

    For grouped solid bars with a visible common foot only. Inclusive source
    pixels; no equal-spacing assumption, identity assignment or missing fill.
    The caller excludes axes/grid/legend and verifies splitting within groups.
    A count mismatch fails before generating a full set of point candidates.
    """
    mask=np.asarray(mask)
    if mask.ndim!=2 or mask.dtype!=np.bool_:
        raise ValueError('bar-group mask must be a 2D boolean array')
    values=(row,left,right,expected_count,min_width)
    if any(type(v) is not int for v in values):
        raise ValueError('bar-group bounds and counts must be integers')
    if not (0<=row<mask.shape[0] and 0<=left<=right<mask.shape[1]
            and expected_count>=1 and min_width>=1):
        raise ValueError('invalid bar-group bounds/count/width')
    xs=np.flatnonzero(mask[row,left:right+1])+left
    spans=[]
    if len(xs):
        for group in np.split(xs,np.flatnonzero(np.diff(xs)>1)+1):
            if len(group)>=min_width:spans.append([int(group[0]),int(group[-1])])
    if len(spans)!=expected_count:
        raise ValueError(f'bar-group count {len(spans)} != {expected_count}; '
                         'check foot row, ROI and group boundaries before selector thresholds')
    return spans


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
                center=(a+b)/2
                marker=marker_center_y(cropped,used,a,b)
                y=center
                span={'x':used,'y_range':[a,b]}
                if marker is not None and abs(marker-center)>1:
                    y=float(marker)
                    span['marker_y']=y
                    span['run_center_y']=float(center)
                points.append([used,y]);spans.append(span)
        if choices and any(xx!=x for xx,_ in choices):flags.append('neighbor_column_not_target')
        if not points:flags.append('no_ink_candidate')
        if len(points)>1:flags.append('multiple_ink_runs')
        max_run=item.get('max_run_px',8)
        if type(max_run) is not int or max_run<1:raise ValueError('invalid max_run_px')
        if any(s['y_range'][1]-s['y_range'][0]+1>max_run for s in spans):flags.append('thick_or_merged_ink')
        if any('marker_y' in s for s in spans):flags.append('marker_center_not_error_bar_span')
        role=item.pop('role','value');item['roles']={role:points};item['flags']=flags
        evidence={'selector':selector,'requested_x':raw_x,'sampled_x':x,
                  'neighbor_px':half,'runs':spans,'method':'vertical_ink_runs',
                  'identity_verified':False}
        if any('marker_y' in s for s in spans):
            evidence['marker_rule']='wider marker on a thinner stem; end caps ignored; symmetric stems unchanged'
        item['proposal_evidence']=evidence
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
