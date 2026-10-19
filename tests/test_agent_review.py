"""Synthetic review-loop invariants; not paper extraction accuracy."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_points as review


class AgentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        image = Image.new("RGB", (200, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.line((80, 50, 80, 140), fill="black", width=1)
        draw.line((75, 50, 85, 50), fill="black")
        draw.line((75, 140, 85, 140), fill="black")
        image.save(self.root / "figure.png")
        self.job = {"schema_version": 1, "sources": {"s": {"image": "figure.png", "figure": "test"}},
            "panels": {"a": {"source": "s", "bounds": [5, 5, 195, 195],
                "axis": {"coordinate": "y", "ticks": [[20, 100], [100, 50], [180, 0]], "unit": "%"}}},
            "items": [{"id": "a", "panel": "a", "identity": {"series": "x", "month": 1},
                       "anchor": [80, 100], "roi": [60, 30, 100, 175],
                       "roles": {"upper": [[80, 50]], "lower": []}, "limitations": ["identity limit"]},
                      {"id": "b", "panel": "a", "identity": {"series": "x", "month": 2},
                       "anchor": [120, 100], "roi": [110, 30, 130, 175],
                       "roles": {"upper": [[120, 60]], "lower": [[120, 130]]}}]}
        self.jp = self.root / "job.json"
        review.write_json(self.jp, self.job)
        prepared = review.prepare(self.jp, self.root / "batch", limit=1, sample_every=0)
        self.mp = Path(prepared["manifest"])
        self.manifest = review.read_json(self.mp)
        self.decisions = {"batch_id": prepared["batch_id"], "reviewer": {"kind": "agent", "name": "test"},
                          "decisions": [{"id": "a", "role": "lower", "action": "set", "pixel": [80, 140]}]}

    def run_apply(self, decisions=None, output="result.json"):
        dp = self.root / "decisions.json"
        review.write_json(dp, self.decisions if decisions is None else decisions)
        return review.apply(self.mp, dp, self.root / output)

    def test_sparse_correction_preserves_inventory_and_asymmetry(self):
        out = self.run_apply()
        result = review.read_json(out["output"])
        a, b = result["items"]
        self.assertEqual(a["roles"]["lower"]["pixel"], [80, 140])
        self.assertAlmostEqual(a["roles"]["lower"]["value"], 25)
        self.assertEqual(a["roles"]["upper"]["status"], "automatic_candidate")
        self.assertEqual(b["roles"]["lower"]["status"], "automatic_candidate")
        self.assertEqual(a["limitations"], ["identity limit"])
        self.assertEqual(out["summary"]["numeric_roles_reviewed"], 1)
        self.assertEqual(out["summary"]["visual_roles_unreviewed"], 3)
        self.assertEqual(out["summary"]["numeric_roles_unresolved"], 0)
        self.assertFalse(out["check_images"][0]["viewed"])

    def test_sheet_coordinate_roundtrip(self):
        v = self.manifest["items"][0]["view"]
        source = [80.25, 140.5]
        sheet = [v["raw_origin"][k] + (source[k] - v["source_roi"][k]) * v["zoom"]
                 + v["pixel_center_offset"] for k in (0, 1)]
        d = copy.deepcopy(self.decisions)
        d["decisions"][0].update(pixel=sheet, space="sheet", sheet=v["sheet"], view="raw")
        result = review.read_json(self.run_apply(d)["output"])
        self.assertEqual(result["items"][0]["roles"]["lower"]["pixel"], source)

    def test_coincident_roles_are_queued_without_claiming_false_overlap(self):
        self.job['items'][0]['roles']={'black':[[80,90]],'gray':[[80,90]]}
        review.write_json(self.jp,self.job)
        out=review.prepare(self.jp,self.root/'coincident',limit=6,sample_every=0)
        self.assertEqual(out['batch_items'],1)
        card=review.read_json(out['manifest'])['items'][0]
        self.assertIn('coincident_roles:black/gray',card['review_reasons'])
        self.assertEqual(card['roles']['black'],card['roles']['gray'])
        self.assertEqual(out['summary']['reviewed'],0)

    def test_resume_shows_corrected_pixel_without_dropping_other_roles(self):
        out = self.run_apply()
        prepared = review.prepare(self.jp, self.root / "batch", resume=out["output"], sample_every=1)
        card = review.read_json(prepared["manifest"])["items"][0]
        self.assertEqual(card["roles"]["lower"], [[80, 140]])
        self.assertEqual(card["review_state"]["lower"], "reviewed")
        self.assertEqual(card["roles"]["upper"], [[80, 50]])

    def test_reject_stale_sources_and_rendered_images(self):
        Image.new("RGB", (200, 200), "red").save(self.root / "figure.png")
        with self.assertRaisesRegex(ValueError, "source/job changed"):
            self.run_apply()

    def test_reject_changed_sheet(self):
        sheet = self.mp.parent / next(iter(self.manifest["sheets"]))
        Image.new("RGB", (20, 20), "red").save(sheet)
        with self.assertRaisesRegex(ValueError, "image changed"):
            self.run_apply()

    def test_reject_duplicate_outside_nonfinite_and_unshown(self):
        for patch in ({"pixel": [0, 0]}, {"pixel": [80, float("inf")]},
                      {"id": "b"}, {"role": "unknown"}):
            d = copy.deepcopy(self.decisions)
            d["decisions"][0].update(patch)
            dp = self.root / "invalid.json"
            dp.write_text(json.dumps(d), encoding="utf-8")
            with self.assertRaises(ValueError):
                review.apply(self.mp, dp, self.root / "invalid_result.json")
        d = copy.deepcopy(self.decisions)
        d["decisions"] *= 2
        with self.assertRaises(ValueError):
            self.run_apply(d)
        self.assertFalse((self.root / "result.json").exists())

    def test_missing_and_resume_do_not_become_zero_or_complete(self):
        d = copy.deepcopy(self.decisions)
        d["decisions"][0] = {"id": "a", "role": "lower", "action": "missing", "note": "occluded"}
        out = self.run_apply(d)
        result = review.read_json(out["output"])
        self.assertIsNone(result["items"][0]["roles"]["lower"]["value"])
        self.assertEqual(result["summary"]["numeric_roles_unresolved"], 1)
        self.assertEqual(result["summary"]["visual_roles_unreviewed"], 3)
        prepared = review.prepare(self.jp, self.root / "batch", resume=out["output"], sample_every=1)
        self.assertNotEqual(Path(prepared["manifest"]), self.mp)
        self.assertEqual(prepared["summary"]["explicit_missing"], 1)

    def test_reject_tick_anomaly_and_nonlinear_axis(self):
        for patch in ({"ticks": [[20, 100], [100, 80], [180, 0]]}, {"kind": "log"}):
            axis = copy.deepcopy(self.job["panels"]["a"]["axis"])
            axis.update(patch)
            with self.assertRaises(ValueError):
                review.axis_fit(axis)

    def test_cli_prepare_and_apply(self):
        tool = Path(review.__file__)
        r = subprocess.run([sys.executable, "-B", str(tool), "prepare", str(self.jp),
                            str(self.root / "cli"), "--limit", "1", "--sample-every", "0"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        prepared = json.loads(r.stdout)
        self.decisions["batch_id"] = prepared["batch_id"]
        dp = self.root / "cli_decisions.json"
        review.write_json(dp, self.decisions)
        r = subprocess.run([sys.executable, "-B", str(tool), "apply", prepared["manifest"], str(dp),
                            str(self.root / "cli_result.json")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["summary"]["numeric_roles_reviewed"], 1)


if __name__ == "__main__":
    unittest.main()
