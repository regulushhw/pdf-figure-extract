# -*- coding: utf-8 -*-
"""一键盘点 PDF：每页的面板框、矢量路径、柱形填充色、位图、数字文本。

用法：
    python scan_pages.py paper.pdf --json scan.json

输出解读：
- images 非空 → 该页含位图对象；仍须按面板检查是否也有矢量对象
- fills 非空 → 候选窄填充图元（可能含柱、图例或装饰，须回图）
- long_paths 非空 → 候选长路径（不保证涵盖短路径或散点）
- frames → 候选面板框坐标，回图确认后可作 job 的 frames
- drawing_count 是全部绘图对象数；long_paths 只是其中的筛选结果
- 位图原始尺寸、显示分辨率估计及覆盖率用于选择检查位置，不证明可提取性
"""
import argparse
import json
from collections import Counter

import fitz

from calib import collect_spans, numeric_spans, color_key


def scan_page(page):
    rep = {'images': [], 'frames': [], 'long_paths': [], 'fills': {}, 'numeric_text': 0}
    image_rects=[]
    for im in page.get_images(full=True):
        info = page.parent.extract_image(im[0])
        try:
            bb = page.get_image_bbox(im)
            shown = [round(bb.width), round(bb.height)]
        except Exception:
            shown = None
        placements=page.get_image_rects(im[0])
        boxes=[];dpis=[]
        for rect in placements:
            boxes.append([round(float(v),3) for v in rect])
            if rect.width>0 and rect.height>0:
                dpis.append([round(info['width']*72/rect.width,2),
                             round(info['height']*72/rect.height,2)])
            clipped=rect & page.rect
            if not clipped.is_empty:image_rects.append(tuple(clipped))
        rep['images'].append({'xref':im[0], 'px': [info['width'], info['height']],
                              'ext': info['ext'], 'shown_pt': shown,
                              'placements_pt':boxes,'display_dpi_estimate':dpis})
    fills = Counter()
    drawings=page.get_drawings()
    rep['drawing_count']=len(drawings)
    for g in drawings:
        r = g['rect']
        if g['type'] == 's' and len(g['items']) <= 2 and r.width > 120 and r.height > 35:
            rep['frames'].append([round(r.x0, 1), round(r.y0, 1),
                                  round(r.x1, 1), round(r.y1, 1)])
        if g['type'] == 's' and len(g['items']) > 20:
            c = g.get('color')
            rep['long_paths'].append({'items': len(g['items']),
                                      'rect': [round(v, 1) for v in r],
                                      'color': color_key(c) if c else None})
        f = g.get('fill')
        if f is not None and 1.5 < r.width < 8 and r.height > 0.03:
            fills[color_key(f)] += 1
    rep['fills'] = dict(fills)
    spans=collect_spans(page)
    rep['numeric_text'] = len(numeric_spans(spans))
    rep['numeric_control_characters']=[]
    for bbox,text in spans:
        controls=sorted({f'U+{ord(c):04X}' for c in text if ord(c)<32 and c not in '\t\n\r'})
        adjacent=[]
        if controls:
            for nb,nt in spans:
                if (any(c.isdigit() for c in nt) and -1 <= nb[0]-bbox[2] <= 8
                        and abs((nb[1]+nb[3]-bbox[1]-bbox[3])/2) <= max(nb[3]-nb[1],bbox[3]-bbox[1])*.5):
                    adjacent.append({'bbox':list(nb),'text':nt})
        if controls and (any(c.isdigit() for c in text) or adjacent):
            rep['numeric_control_characters'].append({'bbox':list(bbox),
                'text_repr':repr(text),'codepoints':controls,'adjacent_numeric':adjacent})
    area=page.rect.width*page.rect.height
    rep['image_area_fraction']=round(union_area(image_rects)/area,6) if area else 0
    rep['routing_hint']=('bitmap_present_review_each_panel' if rep['images'] else
                         'drawing_objects_present_review_each_panel' if drawings else
                         'no_graphic_objects_detected')
    rep['warnings']=[]
    if drawings and not rep['long_paths']:
        rep['warnings'].append('No long-path candidates does not mean no vector objects.')
    if rep['numeric_control_characters']:
        rep['warnings'].append('Numeric text contains control characters; inspect printed glyphs, do not automatically replace their signs.')
    return rep


def union_area(boxes):
    """Union of axis-aligned image boxes; overlaps/repeated XRefs count once."""
    xs=sorted({x for b in boxes for x in (b[0],b[2])})
    area=0.0
    for x0,x1 in zip(xs,xs[1:]):
        intervals=sorted((b[1],b[3]) for b in boxes if b[0]<x1 and b[2]>x0)
        height=0.0;end=None
        for y0,y1 in intervals:
            if end is None or y0>end:
                height+=y1-y0;end=y1
            elif y1>end:
                height+=y1-end;end=y1
        area+=(x1-x0)*height
    return area


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--json', default=None, help='输出盘点 JSON 路径')
    a = ap.parse_args()
    d = fitz.open(a.pdf)
    print(f'共 {d.page_count} 页')
    report = {}
    for i, pg in enumerate(d):
        r = scan_page(pg)
        report[f'page{i + 1}'] = r
        print(f"p{i + 1}: 位图{len(r['images'])} 绘图对象{r['drawing_count']} 框{len(r['frames'])} "
              f"长路径{len(r['long_paths'])} 柱色{len(r['fills'])} "
              f"数字文本{r['numeric_text']}")
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print('已保存', a.json)


if __name__ == '__main__':
    main()
