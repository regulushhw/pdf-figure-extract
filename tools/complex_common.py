"""Shared source, crop and review-output contracts for complex bitmap modules."""
import copy
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from review_points import read_json, write_json, digest, load_job


def inputs(path, out):
    path, out = Path(path).resolve(), Path(out).resolve()
    if out.exists(): raise ValueError('output exists; preserve prior evidence')
    job = read_json(path)
    review = {'schema_version':1,'sources':copy.deepcopy(job['sources']),
              'panels':copy.deepcopy(job['panels']),'items':[],
              '_source_job':copy.deepcopy(job),'source_job_sha256':digest(path)}
    images, overlays = {}, {}
    for name, source in review['sources'].items():
        p = (path.parent/source['image']).resolve()
        source.update(image=str(p),sha256=digest(p))
        image = Image.open(p).convert('RGB')
        images[name] = np.array(image); overlays[name] = image.copy()
    return job, review, images, overlays, out


def roi(box, shape):
    if len(box)!=4 or any(type(v) is not int for v in box):
        raise ValueError('integer source-pixel ROI required')
    l,t,r,b = box
    if not 0<=l<r<shape[1] or not 0<=t<b<shape[0]: raise ValueError('ROI outside source')
    return l,t,r,b


def item(ident,panel,identity,box,roles,flags,evidence):
    if not identity: raise ValueError('explicit identity required')
    l,t,r,b=box
    return {'id':ident,'panel':panel,'identity':identity,'roi':box,'anchor':[(l+r)/2,(t+b)/2],
            'roles':roles,'flags':sorted(set(flags)), 'complex_evidence':evidence,
            'limitations':['Geometry proposals; series/statistic identity requires original-figure review.']}


def finish(review,overlays,out,records,kind):
    out.mkdir(parents=True)
    write_json(out/'source_job.json',review.pop('_source_job'))
    for record in review['items']:
        source=review['panels'][record['panel']]['source']
        draw=ImageDraw.Draw(overlays[source])
        for role,candidates in record['roles'].items():
            color='red' if len(candidates)!=1 or record['flags'] else 'blue'
            for x,y in candidates: draw.ellipse((x-2,y-2,x+2,y+2),outline=color,width=1)
    write_json(out/'review_job.json',review); load_job(out/'review_job.json')
    write_json(out/'geometry.json',{'kind':kind,'records':records,'scope':'unreviewed candidates'})
    for i,image in enumerate(overlays.values()): image.save(out/f'complex_overlay_{i}.png')
    summary={'items':len(review['items']),'roles':sum(len(i['roles']) for i in review['items']),
        'missing_roles':sum(not v for i in review['items'] for v in i['roles'].values()),
        'multiple_roles':sum(len(v)>1 for i in review['items'] for v in i['roles'].values()),
        'flagged_items':sum(bool(i['flags']) for i in review['items'])}
    write_json(out/'summary.json',summary)
    return summary
