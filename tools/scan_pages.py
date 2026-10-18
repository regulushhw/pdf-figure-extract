# -*- coding: utf-8 -*-
"""一键盘点 PDF：每页的面板框、矢量路径、柱形填充色、位图、数字文本。

用法：
    python scan_pages.py paper.pdf --json scan.json

输出解读：
- images 非空 → 该页含位图对象；仍须按面板检查是否也有矢量对象
- fills 非空 → 候选窄填充图元（可能含柱、图例或装饰，须回图）
- long_paths 非空 → 候选长路径（不保证涵盖短路径或散点）
- frames → 面板框坐标，可直接用作提取 job 的 frames
"""
import argparse
import json
from collections import Counter

import fitz

from calib import collect_spans, numeric_spans, color_key


def scan_page(page):
    rep = {'images': [], 'frames': [], 'long_paths': [], 'fills': {}, 'numeric_text': 0}
    for im in page.get_images(full=True):
        info = page.parent.extract_image(im[0])
        try:
            bb = page.get_image_bbox(im)
            shown = [round(bb.width), round(bb.height)]
        except Exception:
            shown = None
        rep['images'].append({'px': [info['width'], info['height']],
                              'ext': info['ext'], 'shown_pt': shown})
    fills = Counter()
    for g in page.get_drawings():
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
    rep['numeric_text'] = len(numeric_spans(collect_spans(page)))
    return rep


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
        print(f"p{i + 1}: 位图{len(r['images'])} 框{len(r['frames'])} "
              f"长路径{len(r['long_paths'])} 柱色{len(r['fills'])} "
              f"数字文本{r['numeric_text']}")
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print('已保存', a.json)


if __name__ == '__main__':
    main()
