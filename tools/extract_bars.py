# -*- coding: utf-8 -*-
"""矢量柱形图读取。输入 job JSON，输出读数 JSON 与校核叠加图。

job 结构：
{
  "pdf": "paper.pdf",
  "page": 12,                                   # 1-based
  "color_map": {"0,0,0": "HadCM3",              # 填充色(见scan的fills键) -> 系列名
                "0.588,0.588,0.588": "CGCM2"},
  "frames": [[x0,y0,x1,y1], ...],               # 可省略：自动找框
  "names": ["2020s", "2050s", "2080s"],         # 面板名，与框顺序一致；可省略
  "slot_labels": ["1月","2月", ... ],           # 组内槽位标签（如12个月）
  "equal_spacing": true,                       # 仅确认等距后使用；否则给slot_edges
  "stroke_bars": false,                        # 仅原图确认加粗竖线为柱形时开启
  "tick_window": 18,                            # 刻度搜索外侧宽度，单位PDF pt
  "per_group": 3,                               # 旧调用兼容参数；现有槽位定位不使用
  "y_side": "left"                              # 刻度在哪侧：left(默认)/right
}

输出：调用方prefix指定位置的 <前缀>.json 与 <前缀>_check.png。
缺柱记 null 并写入 missing 列表（坑库 #9：零高柱才记 0）。
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402

from calib import (collect_spans, numeric_spans, color_key, fit_axis,
                   find_frames, far_from_base, page_pixmap_bgr, mark_points,
                   parse_colormap, match_color)  # noqa: E402


def extract_bars(page, color_map, frames=None, names=None, slot_labels=None,
                 per_group=3, y_side='left', slot_edges=None, equal_spacing=False, tick_window=18, stroke_bars=False):
    if not np.isfinite(tick_window) or tick_window <= 0:
        raise ValueError('tick_window须为正且有限的PDF pt宽度')
    if y_side not in ('left','right'):
        raise ValueError('y_side必须为left或right')
    cmap = parse_colormap(color_map)
    spans = collect_spans(page)
    nums = numeric_spans(spans)
    if frames is None:
        frames = find_frames(page)
    if names is None:
        names = [f'panel{i + 1}' for i in range(len(frames))]
    n_slots = len(slot_labels) if slot_labels else 0

    if not frames or len(names) != len(frames) or len(set(names)) != len(names):
        raise ValueError('面板为空或名称数量/唯一性不符')
    if not slot_labels or len(set(slot_labels)) != len(slot_labels):
        raise ValueError('必须提供不重复的slot_labels')
    if slot_edges is None and not equal_spacing:
        raise ValueError('提供实际slot_edges，或经图轴确认后显式设置equal_spacing=true')
    out = {}
    report = []
    all_marks = []
    for fi, (x0, y0, x1, y1) in enumerate(frames):
        # 刻度候选：外侧搜索宽度由tick_window指定；轴内保留2pt容差
        cands = []
        for bb, v in nums:
            cy = (bb[1] + bb[3]) / 2
            if not (y0 - 5 <= cy <= y1 + 5):
                continue
            if y_side == 'left' and x0 - tick_window <= bb[2] <= x0 + 2:
                cands.append((cy, v))
            if y_side == 'right' and x1 - 2 <= bb[0] <= x1 + tick_window:
                cands.append((cy, v))
        f, inv, ab, resid, chain = fit_axis(cands)
        base_pos = inv(0)  # 基线位置

        bars = []
        for g in page.get_drawings():
            fill = g.get('fill')
            r = g['rect']
            if fill is not None and 1.5 < r.width < 8 and r.height >= 0.03:
                series = match_color(fill,cmap)
                if series and x0 < r.x0 < x1 and y0-1 < r.y0 < y1+1:
                    bars.append(((r.x0+r.x1)/2,r.y0,r.y1,series))
            elif stroke_bars and g['type']=='s':
                width=g.get('width',0) or 0
                series=match_color(g.get('color'),cmap)
                if not series or max(g.get('lineCap',(0,0,0)))!=0:
                    continue
                for item in g['items']:
                    if item[0]!='l':continue
                    a,b=item[1],item[2]
                    if abs(a.x-b.x)<0.01 and 1.5<width<8:
                        cx=float(a.x);lo=float(min(a.y,b.y));hi=float(max(a.y,b.y))
                    elif abs(a.y-b.y)<0.01 and 1.5<abs(a.x-b.x)<8 and width>=0.03:
                        cx=float((a.x+b.x)/2);lo=float(a.y-width/2);hi=float(a.y+width/2)
                    else:continue
                    if (x0<cx<x1 and y0-1<=lo<=y1+1 and hi<=y1+1
                        and min(abs(lo-base_pos),abs(hi-base_pos))<=1):
                        bars.append((cx,lo,hi,series))
        bars.sort(key=lambda b: b[0])

        edges = (slot_edges.get(names[fi]) if isinstance(slot_edges, dict)
                 else slot_edges)
        if edges is None:
            edges = np.linspace(x0, x1, n_slots+1).tolist()
        if len(edges) != n_slots+1 or any(b <= a for a,b in zip(edges, edges[1:])):
            raise ValueError('槽位边界必须递增且数量为标签数+1')
        vals = {s: {} for s in color_map.values()}
        evidence = []
        missing = []
        for bx, bt, bbm, series in bars:
            gi = int(np.searchsorted(edges, bx, side='right'))-1
            if not 0 <= gi < n_slots:
                raise ValueError('柱中心超出槽位边界')
            key = f'g{gi}'
            if key in vals[series]:
                raise ValueError(f'同一面板/系列/槽位有多个柱：{names[fi]}/{series}/{gi}')
            pos = far_from_base(bt, bbm, base_pos)
            value = f(pos)
            vals[series][key] = value
            all_marks.append((bx,pos))
            evidence.append({'panel': names[fi], 'series': series,
                             'x': slot_labels[gi], 'value': value,
                             'pdf_xy': [bx,pos], 'status': 'extracted_unreviewed'})
        kmax = n_slots-1
        if n_slots:
            for gi in range(min(kmax + 1, n_slots)):
                for series in vals:
                    if f'g{gi}' not in vals[series]:
                        vals[series][f'g{gi}'] = None   # 坑库 #9
                        missing.append({'group': gi, 'series': series})
                        evidence.append({'panel': names[fi], 'series': series,
                                         'x': slot_labels[gi], 'value': None, 'pdf_xy': None, 'status': 'missing'})
        panel = {'fit': ab, 'resid': round(resid, 4), 'ticks': len(chain),
                 'n_bars': len(bars), 'values': vals, 'records': evidence, 'tick_chain': chain, 'slot_edges': edges}
        if slot_labels:
            panel['values'] = {s: {slot_labels[int(g[1:])] if g[1:].isdigit() and
                                   int(g[1:]) < n_slots else g: v
                                   for g, v in dv.items()}
                               for s, dv in vals.items()}
        if missing:
            panel['missing'] = missing
        out[names[fi]] = panel
        report.append({'panel': names[fi], 'resid': round(resid, 4),
                       'bars': len(bars), 'missing': len(missing)})
    return out, report, all_marks, frames


def main():
    job = json.load(open(sys.argv[1], encoding='utf-8'))
    d = fitz.open(job['pdf'])
    page = d[job['page'] - 1]
    cmap = job['color_map']
    out, report, marks, frames = extract_bars(
        page, cmap, frames=job.get('frames'), names=job.get('names'),
        slot_labels=job.get('slot_labels'), per_group=job.get('per_group', 3),
        y_side=job.get('y_side', 'left'), slot_edges=job.get('slot_edges'),
        equal_spacing=job.get('equal_spacing', False), tick_window=job.get('tick_window',18), stroke_bars=job.get('stroke_bars',False))
    prefix = job.get('prefix', f'p{job["page"]}_bars')
    from calib import record_envelope
    out['_evidence'] = record_envelope(job, [r for panel in out.values() for r in panel['records']],
                                      {k: {'fit': v['fit'], 'ticks': v['tick_chain'], 'resid': v['resid']} for k,v in out.items()}, 'vector_bars')
    with open(prefix + '.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    # 校核叠加图（默认输出，不看不许交付）
    img = page_pixmap_bgr(page, dpi=int(job.get('dpi', 200)))
    scale = img.width / page.rect.width
    mark_points(img, marks, (255, 0, 0), scale=scale)
    img.save(prefix + '_check.png')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print('已保存', prefix + '.json', '与', prefix + '_check.png')


if __name__ == '__main__':
    main()
