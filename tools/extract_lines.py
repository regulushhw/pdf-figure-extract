# -*- coding: utf-8 -*-
"""矢量折线读取。输入 job JSON，输出读数 JSON 与校核叠加图。

job 结构：
{
  "pdf": "paper.pdf",
  "page": 14,
  "color_map": {"1,0,0": "CGCM2", "0,0,1": "HadCM3"},   # 线色 -> 系列名
  "frame": [x0,y0,x1,y1],                                # 单面板
  "x": {"ticks": [[pos, value], ...]},
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
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402

from calib import page_pixmap_bgr, mark_points, parse_colormap, match_color  # noqa: E402
from review_points import calibrated_axis, measured_precision  # noqa: E402


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

def clipped_endpoints(a, b, frame):
    start, stop = 0.0, 1.0
    for p, q, lo, hi in zip(a, b, frame[:2], frame[2:]):
        delta = q-p
        if delta == 0:
            if not lo <= p <= hi:
                return ()
        else:
            before, after = sorted(((lo-p)/delta, (hi-p)/delta))
            start, stop = max(start, before), min(stop, after)
            if start > stop:
                return ()
    return tuple(tuple(p+t*(q-p) for p,q in zip(a,b)) for t in (start, stop))


def missing_intersection(x, frame, endpoints, calibration_error):
    result = {'sample_pdf_x_pt': x, 'frame_pdf_x_pt': [frame[0], frame[2]]}
    if not frame[0]-1e-8 <= x <= frame[2]+1e-8:
        result['reason'] = 'sample_outside_panel'
    elif not endpoints:
        result['reason'] = 'no_selected_geometry_in_panel'
    else:
        low = min(p[0] for p, _ in endpoints)
        high = max(p[0] for p, _ in endpoints)
        point, width = min(endpoints, key=lambda item: abs(x-item[0][0]))
        result.update(path_pdf_x_pt=[low, high], nearest_pdf_xy_pt=list(point),
                      nearest_stroke_width_pt=width)
        if low <= x <= high:
            result['reason'] = 'no_intersection_within_path_extent'
        elif abs(x-point[0]) <= width/2 + calibration_error:
            result['reason'] = 'close_to_path_endpoint'
        else:
            result['reason'] = 'outside_selected_path_extent'
    return result


def extract_lines(page, frame, color_map, xcal, ycal, sample_at, series_styles=None, insets=(),
                  measurements=None, missing=None):
    from calib import geometry_segments
    if bool(color_map)==bool(series_styles):raise ValueError('color_map与series_styles须且只能填写一种')
    cmap = parse_colormap(color_map) if color_map else {}
    x0,y0,x1,y1 = frame
    frame_box = fitz.Rect(frame)
    inset_boxes = [fitz.Rect(box) for box in insets]
    if any(box.is_empty or not frame_box.contains(box) or box == frame_box for box in inset_boxes):
        raise ValueError('inset must be a proper rectangle within the main frame')
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
        if any(box.contains(r) for box in inset_boxes):
            continue
        if len(g['items'])==1 and g['items'][0][0]=='re' and all(abs(a-b)<=0.5 for a,b in zip(r,frame)):
            continue  # Explicit panel frame, not a data curve with the same pen.
        name = style_name(g,series_styles) if series_styles else match_color(g.get('color'), cmap)
        if name:
            segments=geometry_segments(g)
            reject_grid_bundle(segments)
            grouped[name].extend((a, b, g.get('width') or 0.0) for a, b in segments)
    series = {}
    for name, segments in grouped.items():
        res = []
        endpoints = None
        for sv in sample_at:
            xp = xcal['inv'](sv)
            hits = []
            half_spans = []
            for (ax,ay),(bx,by),width in segments:
                if abs(bx-ax) < 1e-12:
                    continue
                if min(ax,bx)-1e-8 <= xp <= max(ax,bx)+1e-8:
                    slope = (by-ay)/(bx-ax)
                    yp = ay+(xp-ax)*slope
                    if x0 <= xp <= x1 and y0 <= yp <= y1:
                        hits.append(yp)
                        if measurements is not None:
                            half_spans.append(width/2 * math.hypot(1, slope)
                                              + abs(slope) * xcal['measurement_resid']
                                              + ycal['measurement_resid'])
            if hits and max(hits)-min(hits) > 0.05:
                raise ValueError(f'同色曲线身份有歧义：{name}, x={sv}；请缩小面板或分配路径')
            if hits:
                yp = float(np.mean(hits))
                res.append((sv, ycal['f'](yp)))
                all_marks.append((xp,yp))
                if measurements is not None:
                    lo = min(y-span for y, span in zip(hits, half_spans))
                    hi = max(y+span for y, span in zip(hits, half_spans))
                    if hi > lo:
                        bounds = sorted((float(ycal['f'](lo)), float(ycal['f'](hi))))
                        decimals = measured_precision(ycal['f'](yp), bounds)
                        if decimals is not None:
                            measurements[name, sv] = {
                                'pdf_y_bounds_pt': [lo, hi], 'value_bounds': bounds,
                                'display_decimals': decimals}
            else:
                res.append((sv,None))
                if missing is not None:
                    if endpoints is None:
                        endpoints = [(point, width) for a,b,width in segments
                                     for point in clipped_endpoints(a,b,frame)]
                    missing[name, sv] = missing_intersection(xp, frame, endpoints, xcal['measurement_resid'])
        series[name] = res
    return series, all_marks


def main():
    job = json.load(open(sys.argv[1], encoding='utf-8'))
    d = fitz.open(job['pdf'])
    page = d[job['page'] - 1]

    xcal, ycal = calibrated_axis(job['x'], 'x'), calibrated_axis(job['y'], 'y')
    measurements, missing = {}, {}
    series, marks = extract_lines(page, job['frame'], job.get('color_map'),
                                  xcal, ycal, job['sample_at'],job.get('series_styles'),
                                  job.get('insets', ()), measurements, missing)
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
            if (name, x) in measurements:
                records[-1]['measurement'] = measurements[name, x]
            if (name, x) in missing:
                records[-1]['gap_candidate'] = missing[name, x]
    out['_evidence'] = record_envelope(job, records, out['calibration'], 'vector_lines')
    out['_evidence']['series_binding'] = job.get('series_styles') or job.get('color_map')
    if job.get('insets'):
        out['_evidence']['excluded_insets_pt'] = job['insets']
    prefix = job.get('prefix' , f'p{job["page"]}_lines')
    img = page_pixmap_bgr(page, dpi=int(job.get('dpi', 200)))
    scale = img.width / page.rect.width
    if missing:
        img.save(prefix + '_source_check.png')
        for gap in missing.values():
            if nearest := gap.get('nearest_pdf_xy_pt'):
                x, y = gap['sample_pdf_x_pt'], nearest[1]
                gap['source_bbox_px'] = [
                    max(0, math.floor((min(x, nearest[0])-10)*scale)),
                    max(0, math.floor((y-10)*scale)),
                    min(img.width, math.ceil((max(x, nearest[0])+10)*scale)),
                    min(img.height, math.ceil((y+10)*scale))]
    with open(prefix + '.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    mark_points(img, marks, (255, 0, 0), scale=scale)
    gap_marks = [(gap['sample_pdf_x_pt'], gap['nearest_pdf_xy_pt'][1])
                 for gap in missing.values() if 'nearest_pdf_xy_pt' in gap]
    mark_points(img, gap_marks, (235, 145, 0), scale=scale)
    img.save(prefix + '_check.png')
    print('标定残差 x=%.4f y=%.4f' % (xcal['resid'], ycal['resid']))
    for s, res in series.items():
        print(s, '采样', len(res), '点')
    print('已保存', prefix + '.json', '与', prefix + '_check.png')


if __name__ == '__main__':
    main()
