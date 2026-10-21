"""Batch pixel proposals -> small visual-review sheets -> sparse corrections.

CLI: prepare job.json out_dir [--limit 6 --offset 0 --resume result.json]
     apply manifest.json decisions.json result.json [--resume result.json]

Original implementation; no third-party digitizer code. Image coordinates use
the original image's top-left origin. Only explicit linear tick calibration is
supported. Proposed coordinates are never promoted to visually reviewed ones
without an individual decision. See ../references/agent_review.md.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("pixel must have exactly two coordinates")
    point = [float(x) for x in value]
    if not all(math.isfinite(x) for x in point):
        raise ValueError("non-finite pixel")
    return point


def inside(point, bounds):
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def axis_fit(axis):
    if axis.get("kind", "linear") != "linear" or axis.get("coordinate") not in ("x", "y"):
        raise ValueError("only explicitly identified linear x/y axes are supported")
    ticks = np.array([pair(t) for t in axis["ticks"]], dtype=float)
    if len(ticks) < 2 or len(set(ticks[:, 0])) != len(ticks):
        raise ValueError("provide at least two distinct tick positions")
    slope, intercept = np.polyfit(ticks[:, 0], ticks[:, 1], 1)
    if abs(slope) < 1e-12:
        raise ValueError("constant calibration")
    residual = float(np.max(np.abs((ticks[:, 1] - intercept) / slope - ticks[:, 0])))
    tolerance = float(axis.get("max_residual_px", 1.0))
    if not math.isfinite(tolerance) or tolerance < 0 or residual > tolerance:
        raise ValueError(f"tick calibration residual {residual:.4g}px exceeds {tolerance}px")
    return {"slope": float(slope), "intercept": float(intercept),
            "max_residual_px": residual, "coordinate": axis["coordinate"],
            "ticks": ticks.tolist(), "unit": axis["unit"], "kind": "linear"}


def value_at(pixel, calibration):
    pos = pixel[0 if calibration["coordinate"] == "x" else 1]
    return calibration["slope"] * pos + calibration["intercept"]


def load_job(path):
    path = Path(path).resolve()
    job = read_json(path)
    if job.get("schema_version") != 1 or not job.get("items"):
        raise ValueError("expected schema_version=1 and nonempty items")
    sources = {}
    for name, src in job["sources"].items():
        image_path = (path.parent / src["image"]).resolve()
        with Image.open(image_path) as image:
            width, height = image.size
        sources[name] = {**src, "image": str(image_path), "sha256": digest(image_path),
                         "size": [width, height]}
    panels = copy.deepcopy(job["panels"])
    for name, panel in panels.items():
        src = sources[panel["source"]]
        box = list(map(float, panel["bounds"]))
        if len(box) != 4 or not (0 <= box[0] < box[2] < src["size"][0] and
                                 0 <= box[1] < box[3] < src["size"][1]):
            raise ValueError(f"invalid panel bounds: {name}")
        panel["bounds"] = box
        panel["calibration"] = axis_fit(panel["axis"])
        for field in ('context_bounds', 'legend_bounds'):
            if field in panel:
                crop = list(map(float, panel[field]))
                if (len(crop) != 4 or not (0 <= crop[0] < crop[2] < src['size'][0]
                        and 0 <= crop[1] < crop[3] < src['size'][1])):
                    raise ValueError(f'invalid {field}: {name}')
                if field == 'context_bounds' and not (inside(box[:2], crop) and inside(box[2:], crop)):
                    raise ValueError(f'context_bounds must contain panel: {name}')
                panel[field] = crop
    seen = set()
    for item in job["items"]:
        ident = item["id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("item IDs must be unique nonempty strings")
        seen.add(ident)
        panel = panels[item["panel"]]
        roi = list(map(int, item["roi"]))
        if len(roi) != 4 or not (roi[0] < roi[2] and roi[1] < roi[3]) or not (
                inside(roi[:2], panel["bounds"]) and inside(roi[2:], panel["bounds"])):
            raise ValueError(f"ROI outside panel: {ident}")
        item["roi"] = roi
        item["anchor"] = pair(item["anchor"])
        if not inside(item["anchor"], roi) or not item.get("identity") or not item.get("roles"):
            raise ValueError(f"missing identity/roles or anchor outside crop: {ident}")
        for role, candidates in item["roles"].items():
            if not isinstance(role, str) or not role:
                raise ValueError("roles must have names")
            for candidate in candidates:
                if not inside(pair(candidate), roi):
                    raise ValueError(f"candidate outside crop: {ident}/{role}")
    return path, job, sources, panels


def initial_result(job_path, job, sources, panels):
    items = []
    for item in job["items"]:
        roles = {}
        for role, candidates in item["roles"].items():
            pixel = pair(candidates[0]) if len(candidates) == 1 else None
            roles[role] = {"status": "automatic_candidate" if pixel is not None else "needs_review",
                           "pixel": pixel,
                           "value": value_at(pixel, panels[item["panel"]]["calibration"]) if pixel else None,
                           "method": "script_proposal", "review": None}
        items.append({**copy.deepcopy(item), "roles": roles})
    return {"schema_version": 1, "job": str(job_path), "job_sha256": digest(job_path),
            "sources": sources, "panels": panels, "items": items, "history": []}


def load_result(job_path, job, sources, panels, resume=None):
    result = initial_result(job_path, job, sources, panels)
    if resume:
        old = read_json(resume)
        if old["job_sha256"] != result["job_sha256"] or old["sources"] != sources:
            raise ValueError("resume source/job changed; regenerate the review batch")
        expected = [(x["id"], x["panel"], x["identity"], list(x["roles"])) for x in result["items"]]
        actual = [(x["id"], x["panel"], x["identity"], list(x["roles"])) for x in old["items"]]
        if expected != actual:
            raise ValueError("resume inventory/identity mismatch")
        result = old
    return result


def summary(result):
    states = {"automatic_candidate": 0, "needs_review": 0, "reviewed": 0, "explicit_missing": 0}
    for item in result["items"]:
        for role in item["roles"].values():
            states[role["status"]] += 1
    return {"items": len(result["items"]), "roles": sum(states.values()), **states,
            "numeric_roles_reviewed": states["reviewed"],
            "numeric_roles_proposed": states["automatic_candidate"] + states["reviewed"],
            "numeric_roles_unresolved": states["needs_review"] + states["explicit_missing"],
            "visual_roles_unreviewed": states["automatic_candidate"] + states["needs_review"],
            "review_scope": "visual self-check; not independent source validation"}


def font(size):
    for filename in ("DejaVuSans.ttf", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(filename, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_sheet(items, sources, panels, path, zoom=3, columns=3, context=False):
    """Unmarked ink on left; candidate overlay on right, with source-pixel rulers."""
    tile_w = max(330, max((i["roi"][2] - i["roi"][0]) * zoom * 2 + 90 for i in items))
    tile_h = max((i["roi"][3] - i["roi"][1]) * zoom + 110 for i in items)
    width = tile_w * min(columns, len(items))
    images = {}
    context_rows = []
    if context:
        for panel_id in dict.fromkeys(i['panel'] for i in items):
            panel = panels[panel_id]
            source_id = panel['source']
            if source_id not in images:
                with Image.open(sources[source_id]['image']) as im:
                    images[source_id] = im.convert('RGB')
            for field in ('context_bounds', 'legend_bounds'):
                if field == 'legend_bounds' and field not in panel:
                    continue
                bounds = panel.get(field, panel['bounds'])
                crop = images[source_id].crop(tuple(bounds))
                scale = min(1.0, (width - 20) / crop.width, 420 / crop.height)
                crop = crop.resize((max(1, round(crop.width * scale)),
                                    max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
                if field == 'context_bounds':
                    overlay = ImageDraw.Draw(crop)
                    for item in items:
                        if item['panel'] == panel_id:
                            x0, y0, x1, y1 = item['roi']
                            overlay.rectangle(((x0-bounds[0])*scale, (y0-bounds[1])*scale,
                                               (x1-bounds[0])*scale, (y1-bounds[1])*scale),
                                              outline=(210,35,35), width=2)
                label = f'{panel_id}: {field}; overview only, use local original pixels below'
                if field == 'context_bounds' and 'legend_bounds' not in panel:
                    label += ' | no separate legend crop supplied'
                context_rows.append((label, crop))
    header_height = sum(crop.height + 32 for _, crop in context_rows)
    canvas = Image.new("RGB", (width,
                               header_height + tile_h * math.ceil(len(items) / columns)), "white")
    draw = ImageDraw.Draw(canvas)
    context_y = 0
    for label, crop in context_rows:
        draw.text((10, context_y + 4), label, font=font(11), fill='black')
        canvas.paste(crop, (10, context_y + 24))
        context_y += crop.height + 32
    placements = {}
    colors = [(210, 35, 35), (0, 105, 190), (125, 30, 180), (0, 125, 70)]
    for n, item in enumerate(items):
        ox, oy = (n % columns) * tile_w, header_height + (n // columns) * tile_h
        x0, y0, x1, y1 = item["roi"]
        source_id = panels[item["panel"]]["source"]
        if source_id not in images:
            with Image.open(sources[source_id]["image"]) as im:
                images[source_id] = im.convert("RGB")
        crop = images[source_id].crop((x0, y0, x1 + 1, y1 + 1))
        crop = crop.resize((crop.width * zoom, crop.height * zoom), Image.Resampling.NEAREST)
        raw_origin = [ox + 38, oy + 70]
        center_offset = (zoom - 1) / 2
        marked_origin = [raw_origin[0] + crop.width + 20, raw_origin[1]]
        canvas.paste(crop, tuple(raw_origin))
        canvas.paste(crop, tuple(marked_origin))
        draw.text((ox + 4, oy + 4), item["id"], font=font(13), fill="black")
        label = "; ".join(f"{k}={v}" for k, v in item["identity"].items())
        # Full identity remains in manifest; wrapping avoids overwriting the next tile.
        for line in range(2):
            draw.text((ox + 4, oy + 22 + line * 15), label[line * 40:(line + 1) * 40],
                      font=font(12), fill="black")
        draw.text((raw_origin[0], oy + 54), "original", font=font(11), fill="black")
        draw.text((marked_origin[0], oy + 54), "proposals", font=font(11), fill="black")
        for y in range(math.ceil(y0 / 20) * 20, y1 + 1, 20):
            sy = raw_origin[1] + (y - y0) * zoom + center_offset
            draw.text((ox + 2, sy - 6), str(y), font=font(11), fill="black")
            draw.line((raw_origin[0] - 3, sy, raw_origin[0] - 1, sy), fill="black")
        for x in range(math.ceil(x0 / 10) * 10, x1 + 1, 10):
            sx = raw_origin[0] + (x - x0) * zoom + center_offset
            draw.text((sx - 8, raw_origin[1] + crop.height + 2), str(x), font=font(10), fill="black")
        ax = marked_origin[0] + (item["anchor"][0] - x0) * zoom + center_offset
        ay = marked_origin[1] + (item["anchor"][1] - y0) * zoom + center_offset
        draw.line((ax, marked_origin[1], ax, marked_origin[1] + crop.height - 1), fill=(40, 160, 40))
        draw.rectangle((ax - 3, ay - 3, ax + 3, ay + 3), outline=(0, 110, 0), width=1)
        for ri, (role, candidates) in enumerate(item["roles"].items()):
            for ci, p in enumerate(candidates):
                px = marked_origin[0] + (p[0] - x0) * zoom + center_offset
                py = marked_origin[1] + (p[1] - y0) * zoom + center_offset
                color = colors[ri % len(colors)]
                draw.line((px - 5, py, px + 5, py), fill=color, width=2)
                draw.text((px + 7, py - 7), f"{role[0]}{ci}", font=font(11), fill=color)
        draw.rectangle((ox, oy, ox + tile_w - 1, oy + tile_h - 1), outline=(180, 180, 180))
        placements[item["id"]] = {"sheet": path.name, "source_roi": item["roi"],
                                    "raw_origin": raw_origin, "marked_origin": marked_origin,
                                    "zoom": zoom, "pixel_center_offset": center_offset}
    canvas.save(path)
    return placements


def prepare(job_path, out_dir, limit=6, offset=0, resume=None, sample_every=20, zoom=3, plan_only=False, context=False):
    if limit < 1 or offset < 0 or sample_every < 0 or not 1 <= zoom <= 8:
        raise ValueError("invalid batch size/offset/sample interval/zoom")
    job_path, job, sources, panels = load_job(job_path)
    result = load_result(job_path, job, sources, panels, resume)
    lookup = {x["id"]: x for x in result["items"]}
    queue = []
    sampling = job.get('review_sampling', {})
    if not isinstance(sampling, dict):
        raise ValueError('review_sampling must be an object')
    fields = sampling.get('identity_fields', ['series', 'scenario', 'model', 'statistic'])
    if (not isinstance(fields, list) or any(not isinstance(f, str) or not f for f in fields)
            or len(set(fields)) != len(fields)):
        raise ValueError('review_sampling.identity_fields must contain distinct nonempty field names')
    counts, groups = {}, set()
    for item in job["items"]:
        identity = item['identity']
        if 'identity_fields' in sampling and any(f not in identity for f in fields):
            raise ValueError(f'sampling identity field missing: {item["id"]}')
        group = json.dumps([item['panel'], sorted(item['roles']),
                            {f: identity[f] for f in fields if f in identity}],
                           sort_keys=True, ensure_ascii=False)
        groups.add(group)
        raw = list(item['roles'].values())
        stable = (not item.get('flags') and all(len(c) == 1 for c in raw)
                  and not any(math.dist(a[0], b[0]) <= 1.0
                              for i, a in enumerate(raw) for b in raw[i+1:]))
        # Stable sample positions depend on the original job, not the shrinking
        # unfinished queue, so resume cannot silently move the sample targets.
        sample_index = counts.get(group, 0) if stable else None
        if stable:
            counts[group] = sample_index + 1
        roles = lookup[item["id"]]["roles"]
        unfinished = [r for r in roles.values() if r["status"] in ("automatic_candidate", "needs_review")]
        if not unfinished:
            continue
        reasons = []
        if any(r["status"] == "needs_review" for r in unfinished):
            reasons.append("missing_or_ambiguous")
        if item.get("flags"):
            reasons.append("proposal_flags")
        # A pooled detector may assign one trajectory to two identities. Queue
        # coincidences for visual judgment; genuine overlap remains allowed.
        names=list(roles)
        for i,left in enumerate(names):
            for right in names[i+1:]:
                a,b=roles[left],roles[right]
                if (a['pixel'] is not None and b['pixel'] is not None
                        and (a['status']=='automatic_candidate' or b['status']=='automatic_candidate')
                        and math.dist(a['pixel'],b['pixel'])<=1.0):
                    reasons.append(f'coincident_roles:{left}/{right}')
        if sample_every == 1 or (sample_every and sample_index is not None
                                 and sample_index % sample_every == 0):
            reasons.append("automatic_sample")
        if reasons:
            card = {**copy.deepcopy(item), "review_reasons": reasons,
                    "review_state": {name: r["status"] for name, r in roles.items()}}
            # Show saved corrections when another role of the same object is
            # still being inspected; do not redraw the superseded proposal.
            for name, state in roles.items():
                if state["status"] in ("reviewed", "explicit_missing"):
                    card["roles"][name] = [state["pixel"]] if state["pixel"] is not None else []
            queue.append(card)
    # Exceptions remain complete and take precedence over routine samples.
    queue.sort(key=lambda item: item['review_reasons'] == ['automatic_sample'])
    selected = queue[offset:offset + limit]
    if plan_only:
        return {'summary': summary(result), 'queue_items': len(queue),
                'batch_items': len(selected), 'plan_only': True,
                'exception_items': sum(i['review_reasons'] != ['automatic_sample'] for i in queue),
                'sample_only_items': sum(i['review_reasons'] == ['automatic_sample'] for i in queue),
                'sampling_groups': len(groups), 'identity_fields': fields,
                'output_written': False}
    if not selected:
        return {"summary": summary(result), "queue_items": len(queue), "batch_items": 0}
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Offset is only for a fixed queue. With resume, start at offset=0 again.
    base_tag = digest(resume)[:8] if resume else digest(job_path)[:8]
    sheet_path = out_dir / f"review_{base_tag}_{offset:05d}.png"
    manifest_path = out_dir / f"review_{base_tag}_{offset:05d}.json"
    if sheet_path.exists() or manifest_path.exists():
        raise ValueError("batch output exists; use another directory to preserve earlier evidence")
    placements = render_sheet(selected, sources, panels, sheet_path, zoom=zoom, context=context)
    manifest = {"schema_version": 1, "job": str(job_path), "job_sha256": digest(job_path),
                "sources": sources, "panels": panels,
                "base_result": str(Path(resume).resolve()) if resume else None,
                "base_result_sha256": digest(resume) if resume else None,
                "summary": summary(result), "queue_items": len(queue), "context_requested": context,
                "items": [{**i, "view": placements[i["id"]]} for i in selected],
                "sheets": {sheet_path.name: digest(sheet_path)}}
    manifest["batch_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    write_json(manifest_path, manifest)
    return {"manifest": str(manifest_path), "batch_id": manifest["batch_id"],
            "images": [str(sheet_path)], "queue_items": len(queue), "batch_items": len(selected),
            "summary": summary(result)}


def apply(manifest_path, decisions_path, output_path, resume=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    batch_id = manifest["batch_id"]
    payload = {k: v for k, v in manifest.items() if k != "batch_id"}
    if hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest() != batch_id:
        raise ValueError("manifest changed; prepare a new batch")
    job_path, job, sources, panels = load_job(manifest["job"])
    if digest(job_path) != manifest["job_sha256"] or sources != manifest["sources"]:
        raise ValueError("source/job changed; reject stale decisions")
    for name, sha in manifest["sheets"].items():
        if digest(manifest_path.parent / name) != sha:
            raise ValueError("review image changed")
    if resume is None:
        resume = manifest.get("base_result")
    if manifest.get("base_result_sha256") and (not resume or digest(resume) != manifest["base_result_sha256"]):
        raise ValueError("review base changed; prepare a fresh batch from the latest result")
    result = load_result(job_path, job, sources, panels, resume)
    decisions = read_json(decisions_path)
    reviewer = decisions.get("reviewer", {})
    if decisions.get("batch_id") != batch_id or not decisions.get("decisions"):
        raise ValueError("decisions must be nonempty and target this batch_id")
    if reviewer.get("kind") not in ("agent", "human") or not reviewer.get("name"):
        raise ValueError("explicit reviewer kind/name required")
    if any(h["batch_id"] == batch_id for h in result["history"]):
        raise ValueError("batch already applied; regenerate before changing decisions")
    cards = {i["id"]: i for i in manifest["items"]}
    records = {i["id"]: i for i in result["items"]}
    seen = set()
    for decision in decisions["decisions"]:
        ident, role = decision["id"], decision["role"]
        if ident not in cards or role not in cards[ident]["roles"] or (ident, role) in seen:
            raise ValueError("unknown/duplicate item-role or item not shown in batch")
        seen.add((ident, role))
        item = cards[ident]
        action = decision["action"]
        note = decision.get("note", "")
        if action == "missing":
            if not note:
                raise ValueError("missing decisions need a specific reason")
            pixel, method, status = None, "visual_missing", "explicit_missing"
        elif action == "accept":
            index = decision["candidate_index"]
            if type(index) is not int or not 0 <= index < len(item["roles"][role]):
                raise ValueError("invalid candidate index")
            pixel = pair(item["roles"][role][index])
            method, status = "candidate_visually_checked", "reviewed"
        elif action == "set":
            pixel = pair(decision["pixel"])
            space = decision.get("space", "source")
            if space == "sheet":
                view = item["view"]
                if decision.get("sheet") != view["sheet"] or decision.get("view") not in ("raw", "marked"):
                    raise ValueError("sheet decisions need matching sheet and raw/marked view")
                origin = view[decision["view"] + "_origin"]
                pixel = [(pixel[k] - origin[k] - view["pixel_center_offset"]) / view["zoom"]
                         + item["roi"][k] for k in (0, 1)]
            elif space != "source":
                raise ValueError("space must be source or sheet")
            if not inside(pixel, item["roi"]):
                raise ValueError("selected pixel outside shown crop; enlarge the crop first")
            method, status = "visual_pixel_correction", "reviewed"
        else:
            raise ValueError("action must be accept, set or missing")
        cal = panels[item["panel"]]["calibration"]
        old = copy.deepcopy(records[ident]["roles"][role])
        records[ident]["roles"][role] = {
            "status": status, "pixel": pixel, "value": value_at(pixel, cal) if pixel is not None else None,
            "method": method, "review": {"reviewer": reviewer, "note": note,
                "batch_id": batch_id, "manifest": str(manifest_path), "view": item["view"],
                "evidence_scope": "visual self-check; not independent validation", "previous": old}}
    result["history"].append({"batch_id": batch_id, "decision_sha256": digest(decisions_path),
                               "decisions": len(seen), "reviewer": reviewer})
    result["summary"] = summary(result)
    # Produce a sparse overlay for this reviewed batch; original image is never rewritten.
    out = Path(output_path).resolve()
    if out.exists():
        raise ValueError("output exists; preserve prior evidence and use a fresh output path")
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay_paths = []
    for source_id in {panels[cards[ident]["panel"]]["source"] for ident, _ in seen}:
        with Image.open(sources[source_id]["image"]) as image:
            overlay = image.convert("RGB")
        draw = ImageDraw.Draw(overlay)
        for ident, role in sorted(seen):
            item = records[ident]
            if panels[item["panel"]]["source"] != source_id:
                continue
            pixel = item["roles"][role]["pixel"]
            if pixel is not None:
                x, y = pixel
                draw.line((x - 3, y, x + 3, y), fill=(220, 0, 0))
                draw.line((x, y - 3, x, y + 3), fill=(220, 0, 0))
        # Source names are identifiers, never interpolated into an output path.
        tag = hashlib.sha256(source_id.encode()).hexdigest()[:8]
        overlay_path = out.with_name(out.stem + f"_check_{batch_id[:8]}_{tag}.png")
        overlay.save(overlay_path)
        overlay_paths.append({"path": str(overlay_path), "sha256": digest(overlay_path),
                              "source": source_id, "viewed": False})
    result["last_check_images"] = overlay_paths
    write_json(out, result)
    return {"output": str(out), "summary": result["summary"], "check_images": overlay_paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("job"); p.add_argument("out_dir")
    p.add_argument("--limit", type=int, default=6); p.add_argument("--offset", type=int, default=0)
    p.add_argument("--sample-every", type=int, default=20); p.add_argument("--zoom", type=int, default=3)
    p.add_argument('--plan-only', action='store_true', help='report exception/sample queue without rendering or writing outputs')
    p.add_argument('--context', action='store_true', help='experimental panel overview and optional explicit legend crop')
    p.add_argument("--resume")
    p = sub.add_parser("apply")
    p.add_argument("manifest"); p.add_argument("decisions"); p.add_argument("output")
    p.add_argument("--resume")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.job, args.out_dir, args.limit, args.offset, args.resume, args.sample_every, args.zoom, args.plan_only, args.context)
    else:
        result = apply(args.manifest, args.decisions, args.output, args.resume)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
