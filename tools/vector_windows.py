"""Statistics of displayed linear vector curves within explicit time windows.

No daily-data reconstruction, gap filling, Bezier approximation or inference of
series identity. Degenerate collinear PDF quads encode straight ink only.
Window means integrate the displayed piecewise-linear curve, not author data.
"""
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

from calib import xy, record_envelope
from extract_lines import style_name, reject_grid_bundle
from review_points import axis_fit, value_at_position


def straight_segments(graphic):
    out=[]
    for item in graphic['items']:
        if item[0]=='l':
            out.append((xy(item[1]),xy(item[2])))
        elif item[0]=='qu':
            q=item[1];points=[xy(q.ul),xy(q.ur),xy(q.lr),xy(q.ll)]
            a,b=max(((a,b) for a in points for b in points),
                    key=lambda pair:math.dist(*pair))
            distance=math.dist(a,b)
            if distance==0:continue
            if any(abs((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]))/distance>1e-8 for p in points):
                raise ValueError('non-collinear quad is not a supported single-valued curve')
            out.append((a,b))
        else:
            raise ValueError('unsupported vector item; no control-point substitution')
    return out


def window_stats(segments,start,stop):
    if not all(math.isfinite(v) for v in [start,stop]) or stop<=start:
        raise ValueError('window requires finite start < stop')
    lines=[];points=[];seen=set()
    for a,b in segments:
        if not all(math.isfinite(v) for v in (*a,*b)):raise ValueError('nonfinite segment')
        if a[0]>b[0]:a,b=b,a
        key=tuple(round(v,12) for v in (*a,*b))
        if key in seen:continue
        seen.add(key)
        if b[0]<start or a[0]>stop:continue
        if a[0]==b[0]:
            points.extend([a,b]);continue
        left,right=max(start,a[0]),min(stop,b[0])
        if left>=right:continue
        slope=(b[1]-a[1])/(b[0]-a[0]);intercept=a[1]-slope*a[0]
        points.extend([(left,slope*left+intercept),(right,slope*right+intercept)])
        lines.append((left,right,slope,intercept))
    events={start:[[],[]],stop:[[],[]]}
    for i,(left,right,*_) in enumerate(lines):
        events.setdefault(left,[[],[]])[0].append(i)
        events.setdefault(right,[[],[]])[1].append(i)
    active=set();integral=covered=0.;ambiguous=False;breaks=sorted(events)
    for index,left in enumerate(breaks[:-1]):
        active.difference_update(events[left][1]);active.update(events[left][0])
        right=breaks[index+1]
        if right<=left or not active:continue
        functions=[lines[i][2:] for i in active]
        for x in [left,right]:
            ys=[s*x+c for s,c in functions]
            if max(ys)-min(ys)>1e-7:ambiguous=True
        slope,intercept=functions[0]
        integral+=(right-left)*(slope*(left+right)/2+intercept)
        covered+=right-left
    complete=abs(covered-(stop-start))<=1e-8*max(1,stop-start)
    status='ambiguous_curve' if ambiguous else 'complete' if complete else 'source_window_uncovered'
    result={'status':status,'covered_fraction':covered/(stop-start),
            'min':None,'max':None,'mean':None,'extrema_points':{}}
    if status=='complete' and points:
        low=min(points,key=lambda p:(p[1],p[0]));high=max(points,key=lambda p:(p[1],-p[0]))
        result.update(min=low[1],max=high[1],mean=integral/(stop-start),
                      extrema_points={'min':list(low),'max':list(high)})
    return result


def run(job_path):
    import fitz
    from PIL import ImageDraw
    path=Path(job_path).resolve();job=json.loads(path.read_text(encoding='utf-8-sig'))
    pdf=(path.parent/job['pdf']).resolve();job['pdf']=str(pdf)
    if job['axes']['x'].get('kind','linear')!='linear' or job['axes']['y'].get('kind','linear')!='linear':
        raise ValueError('window integration currently requires linear axes')
    xfit=axis_fit({**job['axes']['x'],'coordinate':'x'});yfit=axis_fit({**job['axes']['y'],'coordinate':'y'})
    if xfit['slope']<=0:raise ValueError('window time axis must increase to the right')
    frame=list(map(float,job['frame']))
    if len(frame)!=4 or frame[0]>=frame[2] or frame[1]>=frame[3]:raise ValueError('invalid frame')
    series=job['series'];windows=job['windows']
    if not series or len({s['id'] for s in series})!=len(series) or len({s['name'] for s in series})!=len(series):
        raise ValueError('unique nonempty series IDs and names required')
    if not windows or len({w['id'] for w in windows})!=len(windows):raise ValueError('unique windows required')
    for w in windows:
        if not isinstance(w['id'],str) or not w['id'] or not w.get('identity') or w['stop']<=w['start']:
            raise ValueError('window identity and positive duration required')
        if not w['statistics'] or len(set(w['statistics']))!=len(w['statistics']) or set(w['statistics'])-{'min','max','mean'}:
            raise ValueError('window statistics must be distinct min/max/mean')
    with fitz.open(pdf) as doc:
        page=doc[int(job['page'])-1]
        drawings=page.get_drawings()
        pix=page.get_pixmap(matrix=fitz.Matrix(3,3))
    grouped={s['name']:[] for s in series};source_paths=[]
    for index,g in enumerate(drawings):
        bounds=g['rect']
        # A horizontal/vertical stroke has an empty-area Rect in PyMuPDF,
        # yet is valid curve ink. Compare coordinate bounds inclusively.
        if g['type'] not in ('s','fs') or bounds.x1<frame[0] or bounds.x0>frame[2] or bounds.y1<frame[1] or bounds.y0>frame[3]:continue
        if len(g['items'])==1 and g['items'][0][0]=='re' and all(abs(a-b)<.5 for a,b in zip(g['rect'],frame)):continue
        name=style_name(g,series)
        if name is None:continue
        raw=straight_segments(g);reject_grid_bundle(raw);selected=[]
        for a,b in raw:
            # The declared panel binds identity. Partly clipped paths need an
            # explicit panel-safe source rather than silently importing outside ink.
            if not all(frame[0]-1e-6<=x<=frame[2]+1e-6 and frame[1]-1e-6<=y<=frame[3]+1e-6 for x,y in (a,b)):
                raise ValueError('selected path extends outside frame; narrow the series binding')
            selected.append(((value_at_position(a[0],xfit),value_at_position(a[1],yfit)),
                             (value_at_position(b[0],xfit),value_at_position(b[1],yfit))))
        grouped[name].extend(selected);source_paths.append({'path_index':index,'series':name,'segments':len(selected)})
    if any(not grouped[s['name']] for s in series):
        raise ValueError('series binding selected no source paths; not a source-data gap')
    records=[];marks=[]
    for s in series:
        for w in windows:
            stats=window_stats(grouped[s['name']],w['start'],w['stop'])
            for metric in w['statistics']:
                point=stats['extrema_points'].get(metric)
                pdf_point=None if point is None else [(point[0]-xfit['intercept'])/xfit['slope'],(point[1]-yfit['intercept'])/yfit['slope']]
                if pdf_point:marks.append(pdf_point)
                records.append({'id':f"{s['id']}__{w['id']}__{metric}",'series':s['name'],'series_id':s['id'],
                    'x':w.get('label',w['start']),'window_id':w['id'],'window_start':w['start'],'window_stop':w['stop'],
                    'identity':w['identity'],'statistic':metric,'value':stats[metric],'pdf_xy':pdf_point,
                    'covered_fraction':stats['covered_fraction'],'status':stats['status'],
                    'reading_kind':'calculated_from_displayed_vector_curve'})
                if metric == 'mean' and stats['status'] == 'complete':
                    records[-1]['pdf_window'] = {
                        'x': [(end - xfit['intercept']) / xfit['slope'] for end in (w['start'], w['stop'])],
                        'path_indices': [path['path_index'] for path in source_paths if path['series'] == s['name']],
                    }
    prefix=Path(job.get('prefix','data')).resolve();prefix.parent.mkdir(parents=True,exist_ok=True)
    out={'records':records,'calibration':{'x':xfit,'y':yfit},'source_paths':source_paths,
         '_evidence':record_envelope(job,records,{'x':xfit,'y':yfit},'vector_window_stats'),
         'limitations':['Statistics describe the displayed continuous curve, not author daily observations.',
                        'Window endpoints are boundaries of the displayed curve; missing coverage is not interpolated.',
                        'Series identity and date calibration require original-figure review.']}
    out['_evidence']['job_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    out['_evidence']['tool_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prefix.with_suffix('.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
    with prefix.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
        columns=['id','series','x','window_id','statistic','value','covered_fraction','status','reading_kind']
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(records)
    from PIL import Image
    image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);draw=ImageDraw.Draw(image)
    for x,y in marks:draw.ellipse((x*3-2,y*3-2,x*3+2,y*3+2),outline='red',width=1)
    image.save(str(prefix)+'_check.png')
    return {'records':len(records),'complete':sum(r['status']=='complete' for r in records),
            'uncovered':sum(r['status']=='source_window_uncovered' for r in records),
            'ambiguous':sum(r['status']=='ambiguous_curve' for r in records),'output':str(prefix.with_suffix('.json'))}


if __name__=='__main__':
    try:print(json.dumps(run(sys.argv[1]),ensure_ascii=False))
    except Exception as e:print('FAIL:',str(e));sys.exit(1)
