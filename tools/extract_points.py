# -*- coding: utf-8 -*-
"""矢量散点读取（对称标记包围框中心）。输入job，输出JSON与校核PNG。

job 结构：
{
  "pdf": "paper.pdf",
  "page": 6,
  "frame": [x0,y0,x1,y1],
  "color_map": {"0.753,0.753,0.753": "观测"},   # 填充色 -> 系列名
  "x": {"ticks": [[pos, value], ...]},
  "y": {"ticks": [[pos, value], ...]},
  "legend_rect": [x0,y0,x1,y1]                  # 可选：图例外扩剔除（坑库 #4）
}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402

from calib import color_key, fit_axis, xy, parse_colormap, match_color  # noqa: E402


def extract_points(page, frame, color_map, xcal, ycal, legend_rect=None):
    cmap = parse_colormap(color_map)
    x0, y0, x1, y1 = frame
    out = {}
    for g in page.get_drawings():
        if g['type'] not in ('fs', 'f'):
            continue
        fill = g.get('fill')
        if fill is None:
            continue
        name = match_color(fill, cmap)   # 坑库 #13
        if not name:
            continue
        r = g['rect']
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if legend_rect and (legend_rect[0] - 6 <= cx <= legend_rect[2] + 6
                            and legend_rect[1] - 6 <= cy <= legend_rect[3] + 6):
            continue  # 坑库 #4
        from calib import marker_center
        mx,my = marker_center(g)
        out.setdefault(name, []).append([xcal['f'](mx),ycal['f'](my)])
    for k in out:
        out[k].sort()
    return out


def main():
    job = json.load(open(sys.argv[1], encoding='utf-8'))
    d = fitz.open(job['pdf'])
    page = d[job['page'] - 1]

    def mk_axis(spec):
        if spec.get('scale','linear') != 'linear':
            raise ValueError('当前自动标定仅支持linear轴')
        f, inv, ab, resid, chain = fit_axis([tuple(t) for t in spec['ticks']])
        return {'f': f, 'inv': inv, 'fit': ab, 'resid': resid, 'n': len(chain), 'ticks': chain}

    xcal, ycal = mk_axis(job['x']), mk_axis(job['y'])
    pts = extract_points(page, job['frame'], job['color_map'], xcal, ycal,
                         legend_rect=job.get('legend_rect'))
    out = {'points': pts,
           'calibration': {'x': {k: v for k, v in xcal.items() if k not in ('f', 'inv')},
                           'y': {k: v for k, v in ycal.items() if k not in ('f', 'inv')}}}
    from calib import record_envelope, page_pixmap_bgr, mark_points
    records = [{'series': name, 'x': x, 'value': y,
                'pdf_xy': [xcal['inv'](x),ycal['inv'](y)], 'status': 'extracted_unreviewed'}
               for name, values in pts.items() for x,y in values]
    out['_evidence'] = record_envelope(job, records, out['calibration'], 'vector_points_bbox_center')
    prefix = job.get('prefix' , f'p{job["page"]}_points')
    with open(prefix + '.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    img = page_pixmap_bgr(page, dpi=int(job.get('dpi',200)))
    mark_points(img, [r['pdf_xy'] for r in records], (255,0,0), scale=img.width/page.rect.width)
    img.save(prefix+'_check.png')
    print('标定残差 x=%.4f y=%.4f' % (xcal['resid'], ycal['resid']))
    for s, v in pts.items():
        print(s, len(v), '点')
    print('已保存', prefix + '.json')


if __name__ == '__main__':
    main()
