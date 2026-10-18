# -*- coding: utf-8 -*-
"""PDF 图表数值化：通用标定与几何工具。

约定：
- 所有坐标为 PDF 点（pt），PyMuPDF 页面坐标系（原点左上，y 向下增大）。
- 标定方向统一为 f(pos)=value；反查采样位置用 inv(value)。
- 所有文本先过 fix_text()（负号还原，见 pitfalls #1）。
"""
import re
import numpy as np

_CTRL_MINUS = {'\x04': '-', '−': '-'}  # 坑库 #1


def fix_text(t: str) -> str:
    for k, v in _CTRL_MINUS.items():
        t = t.replace(k, v)
    return t.strip()


def collect_spans(page):
    """页面全部文本片段 [(bbox(x0,y0,x1,y1), text)]。"""
    out = []
    for b in page.get_text('dict')['blocks']:
        if b['type'] != 0:
            continue
        for l in b['lines']:
            for s in l['spans']:
                t = fix_text(s['text'])
                if t:
                    out.append((s['bbox'], t))
    return out


def numeric_spans(spans, pattern=r'-?\d+(\.\d+)?'):
    """[(bbox, float)] 数值文本。"""
    return [(bb, float(t)) for bb, t in spans if re.fullmatch(pattern, t)]


def xy(o):
    """兼容 fitz 各类图元（Point/Rect/quad），返回 (x, y)。"""
    for a, b in (('x', 'y'), ('x0', 'y0')):
        if hasattr(o, a):
            return (getattr(o, a), getattr(o, b))
    if hasattr(o, 'ul'):
        return (o.ul.x, o.ul.y)
    return (o.x, o.y)


def path_points(graphic):
    """矢量路径顶点序列 [(x, y)]。"""
    pts = []
    for it in graphic['items']:
        pts.append(xy(it[1]))
        if len(it) > 2:
            pts.append(xy(it[2]))
    return pts


def tick_chains(cands, tol_pos=0.6, tol_val=1e-6):
    """等距链筛选（坑库 #6）：从刻度候选 [(pos, value)] 找最长等距等差链。

    依据：真刻度沿轴等距排列且数值等差；标题数字、图例数字会破坏等距。
    """
    cands = sorted(set((float(p),float(v)) for p,v in cands))
    best = []
    for i,(p0,v0) in enumerate(cands):
        for p1,v1 in cands[i+1:]:
            dp,dv = p1-p0,v1-v0
            if dp <= 0 or dv == 0:
                continue
            chain = []
            used = set()
            for p,v in cands:
                k = round((p-p0)/dp)
                if k < 0 or k in used:
                    continue
                if abs(p-(p0+k*dp))<=tol_pos and abs(v-(v0+k*dv))<=tol_val:
                    chain.append((p,v)); used.add(k)
            if len(chain)>len(best):best=chain
    return best


def fit_axis(cands, min_ticks=3):
    """标定：[(pos, value)] -> (f, inv, (斜率, 截距), 残差, 刻度链)。

    f(pos)=value 用于读数；inv(value)=pos 用于反查采样位置（坑库 #5）。
    """
    if any(not np.isfinite(v) for pair in cands for v in pair):
        raise ValueError("刻度包含非有限数")
    chain = tick_chains(cands)
    if len(chain) < min_ticks:
        raise ValueError(f'刻度不足：候选 {len(cands)} 个，等距链仅 {len(chain)} 个')
    A = np.polyfit([c[0] for c in chain], [c[1] for c in chain], 1)
    resid = max(abs(np.polyval(A, c[0]) - c[1]) for c in chain)
    f = lambda pos: float(np.polyval(A, pos))
    inv = lambda val: float((val - A[1]) / A[0])
    return f, inv, (float(A[0]), float(A[1])), float(resid), chain


def far_from_base(p0, p1, base):
    """柱两端中离基线更远的一端的位置（坑库 #2：禁用容差判方向）。"""
    return max((p0, p1), key=lambda p: abs(p - base))


def find_frames(page, min_w=120, min_h=35):
    """自动找面板框：细长边框矩形（items<=2 的描边）。"""
    frames = []
    for g in page.get_drawings():
        r = g['rect']
        if g['type'] == 's' and len(g['items']) <= 2 and r.width > min_w and r.height > min_h:
            frames.append((round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)))
    # 行聚类后行内按 x 排序（坑库 #3）
    rows = {}
    for f in frames:
        rows.setdefault(round(f[1] / 8.0), []).append(f)
    out = []
    for k in sorted(rows):
        out.extend(sorted(rows[k], key=lambda f: f[0]))
    return out


def color_key(rgb, nd=3):
    """fitz 颜色元组 -> 稳定字符串键。"""
    return ','.join(str(round(float(v), nd)) for v in rgb)


def parse_colormap(cm):
    """job 里的颜色映射 "0.588,0.588,0.588": "系列名" -> {tuple: 名称}。"""
    return {tuple(float(x) for x in k.split(',')): v for k, v in cm.items()}


def match_color(rgb, cmap, tol=0.02):
    """容差匹配填充/线色（坑库 #13：PDF 填充色常带量化误差，精确匹配会整列漏读）。"""
    if rgb is None:
        return None
    hits = {v for k, v in cmap.items()
            if len(k) == len(rgb) and all(abs(float(a)-float(b)) <= tol
                                         for a, b in zip(k, rgb))}
    if len(hits) > 1:
        raise ValueError('颜色映射有歧义，请调整映射或容差')
    return next(iter(hits)) if hits else None


def page_pixmap_bgr(page, dpi=150):
    """页面渲染为 PIL RGB 图（供校核叠加）。"""
    import io
    from PIL import Image
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')


def pt2px(scale, x, y):
    return x * scale, y * scale


def mark_points(img, pts, color, r=2, scale=1.0):
    """在渲染图上标记点（PDF pt 坐标经 scale 转像素）。"""
    from PIL import ImageDraw
    dr = ImageDraw.Draw(img)
    for x, y in pts:
        px, py = pt2px(scale, x, y)
        dr.ellipse([px - r, py - r, px + r, py + r], outline=color, width=2)
    return img


def geometry_segments(graphic):
    """只支持真实直线段；不把Bezier控制点当成曲线上的点。"""
    segments = []
    for item in graphic['items']:
        if item[0] != 'l':
            raise ValueError('折线路径含非直线图元；需专门处理，不能用控制点代替曲线')
        segments.append((xy(item[1]), xy(item[2])))
    return segments


def marker_center(graphic):
    """标记包围框中心（不是多边形质心）；拒绝非有限坐标。"""
    r = graphic['rect']
    return (float((r.x0+r.x1)/2), float((r.y0+r.y1)/2))


def record_envelope(job, records, calibration, kind):
    """各读取器共用逐点证据结构；旧数值字段仍保留。"""
    from pathlib import Path
    import hashlib
    pdf = Path(job['pdf']).resolve()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return {'schema_version': 1, 'source': {'pdf': str(pdf), 'sha256': digest,
            'page': job['page'], 'figure': job.get('figure'),
            'conditions': job.get('conditions', {})},
            'method': kind, 'coordinate_system': 'PDF pt, origin top-left',
            'calibration': calibration, 'records': records}
