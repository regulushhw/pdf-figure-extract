"""Isolated WPD bridge. No production workbook or production tool code writes.

prepare CONFIG OUT_DIR
bridge CONFIG ENGINE_RESULT REVIEW_JOB
export TASK_JSON PART OUTPUT_CSV

WPD candidates remain unreviewed. A missing engine candidate is not a finding
that the source image is unreadable. The existing runner owns all review state.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

X_TOLERANCE_PX = 0.75  # accommodates integer column selection, not a movable grid
FLOAT_EPSILON_PX = 1e-8  # arithmetic boundary noise only, not sampling tolerance


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve(base, value):
    return (Path(base) / value).resolve()


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def pair(values, label):
    if not isinstance(values, list) or len(values) != 2:
        raise ValueError(f"{label} must have two values")
    return [number(v, label) for v in values]


def axis_fit(axis):
    # Reuse the exact same calibrated transform as editing and overlays.
    import sys
    tools = Path(__file__).resolve().parent
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from review_points import axis_fit as core_fit
    return core_fit({**axis, "coordinate": "y"})


def pixel_at(value, fit):
    from review_points import axis_forward
    pos = (axis_forward(value, fit["kind"])-fit["intercept"])/fit["slope"]
    return float(round(pos)) if abs(pos-round(pos)) <= FLOAT_EPSILON_PX else pos


def value_at(pixel, fit):
    from review_points import value_at_position
    return value_at_position(pixel, fit)


def clamp_roundoff(value, lower, upper, label):
    """Clamp only a demonstrable floating-point overshoot at a boundary."""
    if value < lower:
        if lower-value <= FLOAT_EPSILON_PX:
            return lower
        raise ValueError(f"{label} lies outside the declared plot")
    if value > upper:
        if value-upper <= FLOAT_EPSILON_PX:
            return upper
        raise ValueError(f"{label} lies outside the declared plot")
    return value


def config_load(config_path):
    from PIL import Image
    path = Path(config_path).resolve()
    cfg = read_json(path)
    if cfg.get("schema_version") != 1:
        raise ValueError("config schema_version must be 1")
    image = resolve(path.parent, cfg["source"]["image"])
    engines = resolve(path.parent, cfg["engine_sources_dir"])
    if not engines.is_dir():
        raise ValueError("engine_sources_dir does not exist")
    with Image.open(image) as im:
        width, height = im.size
    bounds = [number(v, "plot_bounds") for v in cfg["plot_bounds"]]
    if len(bounds) != 4 or not (0 <= bounds[0] < bounds[2] < width and
                                0 <= bounds[1] < bounds[3] < height):
        raise ValueError("plot_bounds must lie within source pixels")
    fits = {a: axis_fit(cfg["axes"][a]) for a in ["x", "y"]}
    samples = [number(v, "sample_at") for v in cfg["sample_at"]]
    if not samples or len(set(samples)) != len(samples):
        raise ValueError("sample_at must be nonempty with no duplicate x")
    for x in samples:
        clamp_roundoff(pixel_at(x, fits["x"]), bounds[0], bounds[2], "a frozen sample")
    series = cfg["series"]
    if not series or len({s["id"] for s in series}) != len(series):
        raise ValueError("series ids must be nonempty and unique")
    for s in series:
        if not isinstance(s["id"], str) or not s["id"] or not isinstance(s["name"], str) or not s["name"]:
            raise ValueError("each series needs id and name")
        lo, hi = pair(s["domain"], "series domain")
        if lo > hi:
            raise ValueError("series domain is reversed")
        rgb = s["selector"]["color"]
        if len(rgb) != 3 or any(number(v, "selector color") < 0 or v > 255 for v in rgb):
            raise ValueError("selector color requires RGB in 0..255")
        if number(s["selector"]["distance"], "selector distance") < 0:
            raise ValueError("selector distance must be nonnegative")
        for polygon in s["mask"]["polygons"]:
            if len(polygon) < 3:
                raise ValueError("mask polygon needs at least three points")
            for p in polygon:
                x, y = pair(p, "mask point")
                if not 0 <= x < width or not 0 <= y < height:
                    raise ValueError("mask point lies outside source")
        excluded = s['mask'].get('exclude_boxes', [])
        if not isinstance(excluded, list):
            raise ValueError('exclude_boxes must be a list')
        for raw in excluded:
            if not isinstance(raw, list) or len(raw) != 4:
                raise ValueError('excluded mask box needs four coordinates')
            left, top, right, bottom = [number(v, 'excluded mask box') for v in raw]
            if not (bounds[0] <= left <= right <= bounds[2] and
                    bounds[1] <= top <= bottom <= bounds[3]):
                raise ValueError('excluded mask box must stay inside plot bounds')
    return path, cfg, image, engines, bounds, fits, samples, width, height


def prepare(config_path, out_dir):
    from PIL import Image
    path, cfg, image, engines, _b, _f, _s, width, height = config_load(config_path)
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rgba_path = out / "image.rgba"
    with Image.open(image) as im:
        rgba_path.write_bytes(im.convert("RGBA").tobytes())
    normalized = json.loads(json.dumps(cfg))
    normalized["source"]["image"] = str(image)
    normalized["source"]["image_sha256"] = sha(image)
    normalized["engine_sources_dir"] = str(engines)
    normalized["config_sha256"] = sha(path)
    normalized["image"] = {"width": width, "height": height, "rgba_file": str(rgba_path)}
    write_json(out/"engine-input.json", normalized)
    return {"engine_input": str(out/"engine-input.json"), "rgba": str(rgba_path),
            "source_sha256": sha(image), "config_sha256": sha(path)}


def bridge(config_path, engine_result, review_job):
    path, cfg, image, engines, bounds, fits, samples, width, height = config_load(config_path)
    result_path = Path(engine_result).resolve()
    result = read_json(result_path)
    source = result["source"]
    if source.get("image_sha256") != sha(image) or [source.get("width"), source.get("height")] != [width, height]:
        raise ValueError("engine source hash or dimensions changed")
    if result.get("config_sha256") != sha(path):
        raise ValueError("engine config hash changed")
    if result.get("sample_at") != cfg["sample_at"]:
        raise ValueError("engine output does not preserve frozen sample_at")
    engine_files = result.get("engine", {}).get("sources", [])
    if not engine_files:
        raise ValueError("engine source provenance is required")
    checked_files = []
    for entry in engine_files:
        file = resolve(engines, entry["file"])
        if not file.is_relative_to(engines) or sha(file) != entry["sha256"]:
            raise ValueError("engine source hash changed or path escapes engine_sources_dir")
        checked_files.append({"file": str(file), "sha256": sha(file)})
    actual = result.get("series", [])
    if len({s["id"] for s in actual}) != len(actual):
        raise ValueError("engine output has duplicate series ids")
    actual_map = {s["id"]: s for s in actual}
    expected_ids = {s["id"] for s in cfg["series"]}
    if set(actual_map) - expected_ids:
        raise ValueError("unknown series in engine output")
    items = []
    for series in cfg["series"]:
        incoming = actual_map.get(series["id"], {"points": []})
        if "name" in incoming and incoming["name"] != series["name"]:
            raise ValueError("engine series identity changed")
        by_x = {}
        for point in incoming["points"]:
            x = number(point["sample_x"], "engine sample_x")
            if x in by_x or x not in samples:
                raise ValueError("duplicate or undeclared engine sample x")
            by_x[x] = point
        for index, x in enumerate(samples):
            point = by_x.get(x, {"status": "no_output", "pixel": None, "wpd_data": None})
            status = point["status"]
            outside = not series["domain"][0] <= x <= series["domain"][1]
            if status not in {"candidate", "no_output", "outside_declared_domain"}:
                raise ValueError("unknown engine point status")
            if status == "outside_declared_domain" and not outside:
                raise ValueError("engine domain status contradicts declared domain")
            pixel = None
            if status == "candidate":
                if outside:
                    raise ValueError("engine candidate outside declared series domain")
                pixel = pair(point["pixel"], "engine pixel")
                pixel = [clamp_roundoff(pixel[0], bounds[0], bounds[2], "engine candidate x"),
                         clamp_roundoff(pixel[1], bounds[1], bounds[3], "engine candidate y")]
                if abs(pixel[0]-pixel_at(x, fits["x"])) > X_TOLERANCE_PX:
                    raise ValueError("engine point x drifted from frozen grid")
                data = pair(point["wpd_data"], "wpd_data")
                for v, p, a in zip(data, pixel, ["x", "y"]):
                    expected = value_at(p, fits[a])
                    if not math.isclose(v, expected, rel_tol=1e-6, abs_tol=1e-6):
                        raise ValueError("WPD data and pixel calibration disagree")
            elif point.get("pixel") is not None or point.get("wpd_data") is not None:
                raise ValueError("noncandidate output must not contain a numeric point")
            fixed_x = clamp_roundoff(pixel_at(x, fits["x"]), bounds[0], bounds[2], "a frozen sample")
            anchor = pixel or [fixed_x, (bounds[1]+bounds[3])/2]
            # This is a frozen-x sampling strip, not a small box around the
            # engine guess. After inspecting a destination, the existing point
            # editor must be able to correct a large vertical extraction error.
            sample_pixel_x = fixed_x
            roi = [max(math.ceil(bounds[0]), math.floor(sample_pixel_x)-6),
                   math.ceil(bounds[1]),
                   min(math.floor(bounds[2]), math.ceil(sample_pixel_x)+6),
                   math.floor(bounds[3])]
            reason = ("Outside configured series domain; this is not proof that the curve is absent."
                      if outside else "No engine candidate; not a finding that the image is unreadable.")
            items.append({"id": f"{series['id']}__s{index:04d}", "panel": "p", "roi": roi,
                          "anchor": anchor, "identity": {"series": series["name"], "series_id": series["id"], "x": x},
                          "roles": {"value": [pixel] if pixel else []},
                          "origin": {"method": "webplotdigitizer", "engine_status": status,
                                     "wpd_data": point.get("wpd_data")},
                          "limitations": [] if pixel else [reason]})
    contract = {"config": str(path), "config_sha256": sha(path),
                "source": {"image": str(image), "image_sha256": sha(image)},
                "engine_result": str(result_path), "engine_result_sha256": sha(result_path),
                "engine_sources": checked_files, "sample_at": cfg["sample_at"],
                "axes": cfg["axes"], "series": [{"id": s["id"], "name": s["name"]} for s in cfg["series"]],
                "x_tolerance_px": X_TOLERANCE_PX,
                "status": "unreviewed_candidates_not_independent_validation"}
    job = {"schema_version": 1, "sources": {"s": {"image": str(image)}},
           "panels": {"p": {"source": "s", "bounds": bounds,
                              "axis": {**cfg["axes"]["y"], "coordinate": "y"}}},
           "items": items, "wpd_import_contract": contract}
    write_json(review_job, job)
    return {"review_job": str(Path(review_job).resolve()), "items": len(items),
            "candidates": sum(bool(i["roles"]["value"]) for i in items),
            "needs_review": sum(not i["roles"]["value"] for i in items),
            "provenance": contract}


def export_results(task_json, part_id, output_csv):
    from run_task import Runner, digest
    task_path = Path(task_json).resolve()
    runner = Runner(task_path)  # Enforce task/tool fingerprint on edited exports too.
    task = read_json(task_path)
    part = next((p for p in task["parts"] if p["id"] == part_id), None)
    if part is None or part["method"] != "review_candidates":
        raise ValueError("part must be an existing review_candidates task")
    state = runner.state
    reference = state["parts"][part_id].get("result")
    if not reference:
        # An untouched candidate set is legitimate current state. Reuse the
        # runner's validated run snapshot; do not invent a dummy point edit.
        record = runner.current(part, 'run')
        job_path = Path(record['folder']) / 'candidates' / 'review_job.json'
        _, _, _, _, _, _, result, _ = runner.load_points(part, job_path)
        reference = {'file': None, 'sha256': digest(result)}
        result_path = None
    else:
        result_path = resolve(task_path.parent, reference["file"])
        if sha(result_path) != reference["sha256"]:
            raise ValueError("current runner result hash changed")
        result = read_json(result_path)
        job_path = resolve(result_path.parent, result["job"])
    if sha(job_path) != result["job_sha256"]:
        raise ValueError("current review job hash changed")
    job = read_json(job_path)
    contract = job["wpd_import_contract"]
    if sha(contract["config"]) != contract["config_sha256"]:
        raise ValueError("config hash changed after bridge")
    source = contract["source"]
    if sha(source["image"]) != source["image_sha256"]:
        raise ValueError("source image hash changed after bridge")
    if sha(contract["engine_result"]) != contract["engine_result_sha256"]:
        raise ValueError("engine result hash changed after bridge")
    for engine_file in contract["engine_sources"]:
        if sha(engine_file["file"]) != engine_file["sha256"]:
            raise ValueError("engine source hash changed after bridge")
    xfit = axis_fit(contract["axes"]["x"])
    yfit = axis_fit(contract["axes"]["y"])
    expected = {i["id"]: i for i in job["items"]}
    actual = {i["id"]: i for i in result["items"]}
    if len(actual) != len(result["items"]) or set(actual) != set(expected):
        raise ValueError("current result must preserve every frozen sample identity")
    rows = []
    for ident, original in expected.items():
        item = actual[ident]
        if item["identity"] != original["identity"]:
            raise ValueError("current sample identity changed")
        role = item["roles"]["value"]
        pixel = role.get("pixel")
        value = role.get("value")
        if pixel is not None:
            pixel = pair(pixel, "current pixel")
            if abs(pixel[0]-pixel_at(item["identity"]["x"], xfit)) > contract["x_tolerance_px"]:
                raise ValueError("current point x drifted from frozen sample grid")
            computed = value_at(pixel[1], yfit)
            if value is None or not math.isclose(number(value, "current value"), computed, rel_tol=1e-9, abs_tol=1e-7):
                raise ValueError("current result value disagrees with its pixel")
        elif value is not None:
            raise ValueError("a missing point must not export a numeric value")
        rows.append({"id": ident, "series_id": item["identity"]["series_id"],
                     "series": item["identity"]["series"], "sample_x": item["identity"]["x"],
                     "value": value, "pixel_x": pixel[0] if pixel else None,
                     "pixel_y": pixel[1] if pixel else None, "status": role["status"],
                     "unit": contract["axes"]["y"]["unit"],
                     "result_sha256": reference["sha256"]})
    out = Path(output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"csv": str(out.resolve()), "rows": len(rows), "result": str(result_path) if result_path else None,
            "result_sha256": reference["sha256"], "scope": "current runner state, not old WPD export"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare"); p.add_argument("config"); p.add_argument("out_dir")
    p = commands.add_parser("bridge"); p.add_argument("config"); p.add_argument("engine_result"); p.add_argument("review_job")
    p = commands.add_parser("export"); p.add_argument("task_json"); p.add_argument("part"); p.add_argument("output_csv")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.config, args.out_dir)
    elif args.command == "bridge":
        result = bridge(args.config, args.engine_result, args.review_job)
    else:
        result = export_results(args.task_json, args.part, args.output_csv)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
