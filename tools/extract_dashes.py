"""Single preselected filled-quadrilateral dash trace, with bounded local gap interpolation.
job fields: pdf/page/figure/conditions/frame; x/y.ticks; sample_at; prefix;
max_length/max_thickness/max_gap/endpoint_tolerance (PDF pt); fill_color=[r,g,b].
Optional cap_width=[lo,hi] admits short vertical strokes whose pen width forms a dash.
Frames must exclude legends/text and other same-fill curves. This is not automatic
series recognition, not a bitmap reader, and does not reconstruct arbitrary paths.
No extrapolation beyond endpoint_tolerance; no automatic clipping to zero.
"""
import sys,json,math,os
from pathlib import Path
import numpy as np
import fitz
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from calib import page_pixmap_bgr,mark_points,record_envelope,fit_axis

def dash_segments(page,frame,max_length=5,max_thickness=.8,cap_width=None,fill_color=(0,0,0)):
    if any(not math.isfinite(float(v)) or float(v)<=0 for v in (max_length,max_thickness)):
        raise ValueError('glyph dimensions must be finite and positive')
    if len(fill_color)!=3 or any(not 0<=c<=1 for c in fill_color):raise ValueError('invalid RGB')
    if cap_width and not 0<cap_width[0]<=cap_width[1]:raise ValueError('invalid cap_width')
    segments=[];objects=[];fr=fitz.Rect(frame)
    if fr.is_empty or fr.is_infinite:raise ValueError('invalid frame')
    for index,g in enumerate(page.get_drawings()):
        r=g['rect']
        if not fr.contains(r):continue
        color=g.get('fill') if g['type']=='f' else g.get('color')
        if color is None or max(abs(c-t) for c,t in zip(color,fill_color))>.02:continue
        if g['type']=='f':
            its=g['items']
            if len(its)==1 and its[0][0]=='re':
                box=its[0][1];vs=np.array([[box.x0,box.y0],[box.x1,box.y0],[box.x1,box.y1],[box.x0,box.y1]])
            elif len(its)==3 and all(t[0]=='l' for t in its):
                if any(math.dist(its[i][2],its[i+1][1])>.01 for i in range(2)):continue
                vs=np.array([list(its[0][1])]+[list(t[2]) for t in its])
            else:continue
            lengths=[np.linalg.norm(vs[(i+1)%4]-vs[i]) for i in range(4)]
            if max(lengths)>max_length or sorted(lengths)[1]>max_thickness:continue
            if lengths[0]+lengths[2]>=lengths[1]+lengths[3]:a,b=(vs[0]+vs[3])/2,(vs[1]+vs[2])/2
            else:a,b=(vs[0]+vs[1])/2,(vs[2]+vs[3])/2
            if a[0]>b[0]:a,b=b,a
            if b[0]-a[0]<.02:continue
            segments.append((tuple(a),tuple(b)));objects.append(index)
        elif cap_width and g['type']=='s' and cap_width[0]<=g.get('width',0)<=cap_width[1]:
            for t in g['items']:
                if t[0]=='l' and abs(t[1].x-t[2].x)<.01 and abs(t[1].y-t[2].y)<=max_thickness:
                    x=t[1].x;y=(t[1].y+t[2].y)/2
                    segments.append(((x-g['width']/2,y),(x+g['width']/2,y)));objects.append(index)
    return sorted(segments),objects

def sample(segments,xs,max_gap=3,endpoint_tolerance=.5):
    if not math.isfinite(max_gap) or max_gap<=0 or not math.isfinite(endpoint_tolerance) or endpoint_tolerance<0:
        raise ValueError('invalid gap or endpoint tolerance')
    bridges=[]
    for left,right in zip(segments,segments[1:]):
        a,b=left[1],right[0]
        if 0<b[0]-a[0]<=max_gap and math.dist(a,b)<=max_gap:bridges.append((a,b))
    result=[]
    for x in xs:
        hits=[];method='fragment'
        for a,b in segments:
            if a[0]-1e-4<=x<=b[0]+1e-4:hits.append(a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0]))
        if not hits:
            method='local_gap_interpolation'
            for a,b in bridges:
                if a[0]<=x<=b[0]:hits.append(a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0]))
        if not hits:
            nearest=min((p for s in segments for p in s),key=lambda p:abs(p[0]-x),default=None)
            if nearest and abs(nearest[0]-x)<=endpoint_tolerance:hits=[nearest[1]];method='near_endpoint_approximation'
        if hits and max(hits)-min(hits)>.3:raise ValueError('ambiguous overlapping dash fragments')
        result.append((x,float(np.mean(hits)) if hits else None,method if hits else 'missing'))
    return result

def main():
    job=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    doc=fitz.open(job['pdf']);pg=doc[job['page']-1]
    def axis(spec):
        if spec.get('scale','linear')!='linear':raise ValueError('linear axes only')
        f,inv,ab,resid,ticks=fit_axis(spec['ticks'])
        return f,inv,{'fit':ab,'residual':resid,'ticks':ticks}
    xf,xi,xc=axis(job['x']);yf,yi,yc=axis(job['y'])
    segments,ids=dash_segments(pg,job['frame'],job['max_length'],job['max_thickness'],job.get('cap_width'),job['fill_color'])
    if not segments:raise ValueError('no selected dash fragments; do not fill with zero')
    pts=sample(segments,[xi(v) for v in job['sample_at']],job['max_gap'],job['endpoint_tolerance'])
    records=[{'x':label,'value':yf(y) if y is not None else None,'pdf_xy':[x,y] if y is not None else None,
              'method':method,'status':'extracted_unreviewed' if y is not None else 'missing'}
             for label,(x,y,method) in zip(job['sample_at'],pts)]
    out={'records':records,'segments':segments,'object_indices':ids,
         '_evidence':record_envelope(job,records,{'x':xc,'y':yc},'filled_quadrilateral_dashes')}
    out['_evidence']['selection']={k:job[k] for k in ('frame','fill_color','max_length','max_thickness','max_gap','endpoint_tolerance')}
    if 'cap_width' in job:out['_evidence']['selection']['cap_width']=job['cap_width']
    prefix=job['prefix'];Path(prefix).parent.mkdir(parents=True,exist_ok=True)
    Path(prefix+'.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    img=page_pixmap_bgr(pg,dpi=int(job.get('dpi',200)))
    mark_points(img,[(x,y) for x,y,_ in pts if y is not None],(255,0,0),scale=img.width/pg.rect.width)
    img.save(prefix+'_check.png')
    print(json.dumps({'selected_fragments':len(segments),'sample_count':len(records),'missing':sum(r['value'] is None for r in records)}))
if __name__=='__main__':main()
