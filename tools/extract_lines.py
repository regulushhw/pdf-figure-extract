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
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402

from calib import (color_key, fit_axis, path_points, page_pixmap_bgr,
                   mark_points, xy, parse_colormap, match_color)  # noqa: E402


def extract_lines(page, frame, color_map, xcal, ycal, sample_at):
    from calib import geometry_segments
    cmap = parse_colormap(color_map)
    x0,y0,x1,y1 = frame
    grouped = {name: [] for name in cmap.values()}
    all_marks = []
    for g in page.get_drawings():
        if g['type'] not in ('s','fs'):
            continue
        r = g['rect']
        if r.x1 < x0 or r.x0 > x1 or r.y1 < y0 or r.y0 > y1:
            continue
        name = match_color(g.get('color'), cmap)
        if name:
            grouped[name].extend(geometry_segments(g))
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
    series, marks = extract_lines(page, job['frame'], job['color_map'],
                                  xcal, ycal, job['sample_at'])
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
