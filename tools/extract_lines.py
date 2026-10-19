# -*- coding: utf-8 -*-
"""矢量折线读取。输入 job JSON，输出读数 JSON 与校核叠加图。

job 结构：
{
  "pdf": "paper.pdf",
  "page": 14,
  "color_map": {"1,0,0": "CGCM2", "0,0,1": "HadCM3"},   # 线色 -> 系列名
  "frame": [x0,y0,x1,y1],                                # 单面板
  "x": {"ticks": [[pos, value], ...]},                   # 或 {"from": "spans", "side":"bottom"}
  "y": {"ticks": [[pos, value], ...]},
  "sample_at": [值1, 值2, ...]                            # 按数值采样（如年份/月中）
}
输出：前缀.json（每系列 [(采样值, 读数)]）与前缀_check.png。
同色多系列可改用series_styles（与color_map互斥），每项含name、color、
width_range=[最小线宽,最大线宽]（PDF pt）、line_style=solid/dashed。
映射须由原图图例确认；网格束会拒绝，面板边框矩形会排除。
仅支持描边直线段；填充小块拼成的虚线与Bezier路径尚不支持。
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402

from calib import (color_key, fit_axis, path_points, page_pixmap_bgr,
                   mark_points, xy, parse_colormap, match_color)  # noqa: E402


def style_name(g, styles):
    """Explicit legend bindings; never infer identity from ordering or height."""
    matches=[]
    for spec in styles:
        if match_color(g.get('color'),parse_colormap({spec['color']:spec['name']})) is None:
            continue
        lo,hi=spec['width_range']
        if not 0<lo<=hi:raise ValueError('width_range须为正的闭区间')
        if not lo<=g.get('width',0)<=hi:continue
        dash=g.get('dashes','[] 0').replace(' ','')
        solid=dash.split(']')[0]=='['
        if spec['line_style'] not in ('solid','dashed'):raise ValueError('未知线型')
        if solid!=(spec['line_style']=='solid'):continue
        matches.append(spec['name'])
    if len(matches)>1:raise ValueError('线型选择器重叠，系列身份有歧义')
    return matches[0] if matches else None

def reject_grid_bundle(segments):
    # A PDF path may bundle independent horizontal gridlines into one drawing.
    horizontal=[(a,b) for a,b in segments if abs(a[1]-b[1])<0.05 and abs(a[0]-b[0])>1]
    if len(horizontal)>=2:
        spans=[(round(min(a[0],b[0]),1),round(max(a[0],b[0]),1)) for a,b in horizontal]
        if len(set(spans))<len(spans) and max(a[1] for a,b in horizontal)-min(a[1] for a,b in horizontal)>0.05:
            raise ValueError('所选路径含多条平行全宽水平线，疑似网格；不能作为数据曲线')

def extract_lines(page, frame, color_map, xcal, ycal, sample_at, series_styles=None):
    from calib import geometry_segments
    if bool(color_map)==bool(series_styles):raise ValueError('color_map与series_styles须且只能填写一种')
    cmap = parse_colormap(color_map) if color_map else {}
    x0,y0,x1,y1 = frame
    names=[s['name'] for s in series_styles] if series_styles else list(cmap.values())
    if series_styles and len(names)!=len(set(names)):raise ValueError('系列名称重复')
    grouped = {name: [] for name in names}
    all_marks = []
    for g in page.get_drawings():
        if g['type'] not in ('s','fs'):
            continue
        r = g['rect']
        if r.x1 < x0 or r.x0 > x1 or r.y1 < y0 or r.y0 > y1:
            continue
        if len(g['items'])==1 and g['items'][0][0]=='re' and all(abs(a-b)<=0.5 for a,b in zip(r,frame)):
            continue  # Explicit panel frame, not a data curve with the same pen.
        name = style_name(g,series_styles) if series_styles else match_color(g.get('color'), cmap)
        if name:
            segments=geometry_segments(g)
            reject_grid_bundle(segments)
            grouped[name].extend(segments)
    series = {}
    for name, segments in grouped.items():
        res = []
        for sv in sample_at:
            xp = xcal['inv'](sv)
            hits = []
            for (ax,ay),(bx,by) in segments:
                if abs(bx-ax) < 1e-12:
                    continue
                if min(ax,bx)-1e-8 <= xp <= max(ax,bx)+1e-8:
                    yp = ay+(xp-ax)*(by-ay)/(bx-ax)
                    if x0 <= xp <= x1 and y0 <= yp <= y1:
                        hits.append(yp)
            if hits and max(hits)-min(hits) > 0.05:
                raise ValueError(f'同色曲线身份有歧义：{name}, x={sv}；请缩小面板或分配路径')
            if hits:
                yp = float(np.mean(hits))
                res.append((sv, ycal['f'](yp)))
                all_marks.append((xp,yp))
            else:
                res.append((sv,None))
        series[name] = res
    return series, all_marks


def main():
    job = json.load(open(sys.argv[1], encoding='utf-8'))
    d = fitz.open(job['pdf'])
    page = d[job['page'] - 1]

    def mk_axis(spec):
        if spec.get('scale','linear') != 'linear':
            raise ValueError('当前自动标定仅支持linear轴')
        ticks = spec['ticks']
        f, inv, ab, resid, chain = fit_axis([tuple(t) for t in ticks])
        return {'f': f, 'inv': inv, 'fit': ab, 'resid': resid, 'n': len(chain), 'ticks': chain}

    xcal, ycal = mk_axis(job['x']), mk_axis(job['y'])
    series, marks = extract_lines(page, job['frame'], job.get('color_map'),
                                  xcal, ycal, job['sample_at'],job.get('series_styles'))
    out = {'series': series,
           'calibration': {'x': {k: v for k, v in xcal.items() if k != 'f' and k != 'inv'},
                           'y': {k: v for k, v in ycal.items() if k != 'f' and k != 'inv'}}}
    from calib import record_envelope
    records = []
    for name, values in series.items():
        for x,value in values:
            records.append({'series': name, 'x': x, 'value': value,
                            'pdf_xy': [xcal['inv'](x), ycal['inv'](value)] if value is not None else None,
                            'status': 'extracted_unreviewed' if value is not None else 'missing'})
    out['_evidence'] = record_envelope(job, records, out['calibration'], 'vector_lines')
    out['_evidence']['series_binding'] = job.get('series_styles') or job.get('color_map')
    prefix = job.get('prefix' , f'p{job["page"]}_lines')
    with open(prefix + '.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    img = page_pixmap_bgr(page, dpi=int(job.get('dpi', 200)))
    mark_points(img, marks, (255, 0, 0), scale=img.width / page.rect.width)
    img.save(prefix + '_check.png')
    print('标定残差 x=%.4f y=%.4f' % (xcal['resid'], ycal['resid']))
    for s, res in series.items():
        print(s, '采样', len(res), '点')
    print('已保存', prefix + '.json', '与', prefix + '_check.png')


if __name__ == '__main__':
    main()
