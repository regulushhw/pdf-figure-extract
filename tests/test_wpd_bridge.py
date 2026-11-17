import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

spec = importlib.util.spec_from_file_location("wpd_bridge_prototype", Path(__file__).resolve().parents[1]/"tools/wpd_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        Image.new("RGB", (100, 100), "white").save(self.root/"source.png")
        (self.root/"engine").mkdir()
        (self.root/"engine"/"wpd.js").write_text("// fixed test engine", encoding="utf-8")
        self.cfg = {"schema_version": 1, "source": {"image": "source.png"},
                    "engine_sources_dir": "engine", "plot_bounds": [10, 10, 90, 90],
                    "axes": {"x": {"kind": "linear", "unit": "%", "ticks": [[10, 0], [90, 100]]},
                             "y": {"kind": "linear", "unit": "Ml/d", "ticks": [[10, 100], [90, 0]]}},
                    "sample_at": [25, 50],
                    "series": [{"id": "obs", "name": "Observed", "selector": {"color": [0,0,0], "distance": 20},
                                "mask": {"polygons": []}, "domain": [0,100]}]}
        self.config = self.root/"config.json"
        self.write_config()

    def write_config(self):
        bridge.write_json(self.config, self.cfg)

    def engine_result(self, points=None):
        if points is None:
            points = [{"sample_x": 25, "pixel": [30, 50], "wpd_data": [25, 50], "status": "candidate"},
                      {"sample_x": 50, "pixel": [50, 58], "wpd_data": [50, 40], "status": "candidate"}]
        result = {"source": {"image_sha256": bridge.sha(self.root/"source.png"), "width":100, "height":100},
                  "config_sha256": bridge.sha(self.config), "sample_at": copy.deepcopy(self.cfg["sample_at"]),
                  "engine": {"sources": [{"file": "wpd.js", "sha256": bridge.sha(self.root/"engine"/"wpd.js")}]},
                  "series": [{"id": "obs", "name": "Observed", "points": points}]}
        path = self.root/"engine-result.json"
        bridge.write_json(path, result)
        return path

    def job(self, points=None):
        job = self.root/"review_job.json"
        bridge.bridge(self.config, self.engine_result(points), job)
        return job

    def state(self, job_path):
        job = bridge.read_json(job_path)
        items = []
        for item in job["items"]:
            item = copy.deepcopy(item)
            pixel = item["roles"]["value"][0] if item["roles"]["value"] else None
            item["roles"] = {"value": {"status": "automatic_candidate" if pixel else "needs_review", "pixel": pixel,
                                      "value": 112.5-1.25*pixel[1] if pixel else None}}
            items.append(item)
        result = {"job": str(job_path), "job_sha256": bridge.sha(job_path), "items": items}
        old = self.root/"old-result.json"
        bridge.write_json(old, result)
        latest = self.root/"latest-result.json"
        result["items"][0]["roles"]["value"].update(pixel=[30, 42], value=60, status="reviewed")
        bridge.write_json(latest, result)
        task = self.root/"task.json"
        import fitz
        from run_task import Runner, write
        with fitz.open() as pdf:
            pdf.new_page()
            pdf.save(self.root/"paper.pdf")
        bridge.write_json(task, {"version":1, "work_dir":"work", "pdf":"paper.pdf",
                                "parts":[{"id":"f6", "method":"review_candidates", "job":str(job_path),
                                          "scope":{"series":["Observed"],"sampling":"two frozen test points"}}]})
        runner = Runner(task)
        write(runner.state_path, runner.state)
        self.set_latest(latest)
        return task, latest

    def set_latest(self, latest):
        path = self.root/"work"/"task_state.json"
        state = bridge.read_json(path)
        state['parts'].setdefault('f6', {})['result'] = {'file': str(latest), 'sha256': bridge.sha(latest)}
        bridge.write_json(path, state)

    def test_export_rejects_changed_task_after_edit(self):
        task, _ = self.state(self.job())
        changed = bridge.read_json(task)
        changed['parts'][0]['scope']['sampling'] = 'changed sample declaration'
        bridge.write_json(task, changed)
        with self.assertRaisesRegex(ValueError, 'task definition or tool code changed'):
            bridge.export_results(task, 'f6', self.root/'stale.csv')

    def test_prepare_rgba_and_normalized_protocol(self):
        info = bridge.prepare(self.config, self.root/"out")
        result = bridge.read_json(info["engine_input"])
        self.assertTrue(Path(result["source"]["image"]).is_absolute())
        self.assertTrue(Path(result["engine_sources_dir"]).is_absolute())
        self.assertEqual(result["config_sha256"], bridge.sha(self.config))
        self.assertEqual(Path(result["image"]["rgba_file"]).stat().st_size, 100*100*4)

    def test_exclusion_passes_through_without_editing_source_pixels(self):
        self.cfg['series'][0]['mask']['exclude_boxes'] = [[29,10,31,90]]
        self.write_config()
        before = bridge.sha(self.root/'source.png')
        info = bridge.prepare(self.config, self.root/'out')
        normalized = bridge.read_json(info['engine_input'])
        self.assertEqual(normalized['series'][0]['mask']['exclude_boxes'], [[29,10,31,90]])
        self.assertEqual(bridge.sha(self.root/'source.png'), before)
        self.assertEqual(Path(info['rgba']).read_bytes(), Image.open(self.root/'source.png').convert('RGBA').tobytes())

    def test_exclusion_outside_plot_or_malformed_rejected(self):
        for boxes in ([[9,10,31,90]], [[31,10,29,90]], [[29,10,31]], 'invalid'):
            with self.subTest(boxes=boxes):
                self.cfg['series'][0]['mask']['exclude_boxes'] = boxes
                self.write_config()
                with self.assertRaisesRegex(ValueError, 'exclu'):
                    bridge.prepare(self.config, self.root/'out')

    def test_missing_sample_is_retained_and_not_unreadable(self):
        job = bridge.read_json(self.job([{"sample_x":25,"pixel":[30,50],"wpd_data":[25,50],"status":"candidate"}]))
        self.assertEqual(len(job["items"]), 2)
        missing = job["items"][1]
        self.assertEqual(missing["roles"]["value"], [])
        self.assertEqual(missing["origin"]["engine_status"], "no_output")
        self.assertIn("not a finding", missing["limitations"][0])

    def test_duplicate_config_x_rejected(self):
        self.cfg["sample_at"] = [25,25]
        self.write_config()
        with self.assertRaisesRegex(ValueError,"duplicate x"):
            bridge.prepare(self.config,self.root/"out")

    def test_duplicate_engine_x_rejected(self):
        p={"sample_x":25,"pixel":[30,50],"wpd_data":[25,50],"status":"candidate"}
        with self.assertRaisesRegex(ValueError,"duplicate"):
            self.job([p,p])

    def test_source_hash_changed_rejected(self):
        engine = self.engine_result()
        Image.new("RGB",(100,100),"black").save(self.root/"source.png")
        with self.assertRaisesRegex(ValueError,"source hash"):
            bridge.bridge(self.config,engine,self.root/"review_job.json")

    def test_engine_code_hash_changed_rejected(self):
        engine=self.engine_result()
        (self.root/"engine"/"wpd.js").write_text("changed",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"engine source hash"):
            bridge.bridge(self.config,engine,self.root/"review_job.json")

    def test_export_uses_latest_runner_result(self):
        task, latest = self.state(self.job())
        out = self.root/"current.csv"
        result=bridge.export_results(task,"f6",out)
        with out.open(encoding="utf-8-sig",newline="") as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(float(rows[0]["value"]),60)
        self.assertEqual(rows[0]["status"],"reviewed")
        self.assertEqual(result["result_sha256"],bridge.sha(latest))

    def test_export_rejects_x_drift(self):
        task, latest = self.state(self.job())
        result = bridge.read_json(latest)
        result["items"][0]["roles"]["value"]["pixel"][0] = 35
        bridge.write_json(latest,result)
        self.set_latest(latest)
        with self.assertRaisesRegex(ValueError,"x drifted"):
            bridge.export_results(task,"f6",self.root/"current.csv")

    def test_export_rejects_changed_result_hash(self):
        task, latest = self.state(self.job())
        latest.write_text(latest.read_text(encoding="utf-8")+" ",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"result hash"):
            bridge.export_results(task,"f6",self.root/"current.csv")

    def test_nonlinear_axis_rejected(self):
        self.cfg["axes"]["y"]["kind"]="log"
        self.write_config()
        with self.assertRaisesRegex(ValueError,"linear/log10"):
            bridge.prepare(self.config,self.root/"out")

    def test_outside_domain_keeps_grid_role(self):
        self.cfg["series"][0]["domain"]=[0,30]
        self.write_config()
        job=bridge.read_json(self.job([
            {"sample_x":25,"pixel":[30,50],"wpd_data":[25,50],"status":"candidate"},
            {"sample_x":50,"pixel":None,"wpd_data":None,"status":"outside_declared_domain"}]))
        self.assertEqual(len(job["items"]),2)
        self.assertEqual(job["items"][1]["roles"]["value"],[])
        self.assertIn("not proof",job["items"][1]["limitations"][0])

    def test_existing_readonly_core_accepts_bridge_contract(self):
        core_path = Path(__file__).resolve().parents[1]/"tools/review_points.py"
        core_spec = importlib.util.spec_from_file_location("existing_review_points_readonly",core_path)
        core = importlib.util.module_from_spec(core_spec)
        core_spec.loader.exec_module(core)
        path = self.job([{"sample_x":25,"pixel":[30,50],"wpd_data":[25,50],"status":"candidate"}])
        jp, job, sources, panels = core.load_job(path)
        result = core.initial_result(jp,job,sources,panels)
        self.assertEqual(result["items"][0]["roles"]["value"]["status"],"automatic_candidate")
        self.assertEqual(result["items"][1]["roles"]["value"]["status"],"needs_review")
        self.assertAlmostEqual(result["items"][0]["roles"]["value"]["value"],50)

    def test_large_vertical_correction_stays_inside_sampling_strip(self):
        job_path = self.job()
        job = bridge.read_json(job_path)
        first = job["items"][0]
        self.assertEqual(first["roi"],[24,10,36,90])
        # Engine point (30,50) may be 20px away from the true curve at (30,70).
        self.assertTrue(first["roi"][0] <= 30 <= first["roi"][2])
        self.assertTrue(first["roi"][1] <= 70 <= first["roi"][3])
        task, latest = self.state(job_path)
        result = bridge.read_json(latest)
        result["items"][0]["roles"]["value"].update(pixel=[30,70],value=25,status="reviewed")
        bridge.write_json(latest,result)
        self.set_latest(latest)
        exported=bridge.export_results(task,"f6",self.root/"large-correction.csv")
        self.assertEqual(exported["rows"],2)

    def test_check_ticks_pass_through_without_changing_fit(self):
        self.cfg["axes"]["x"]["check_ticks"]=[[50,50]]
        self.cfg["axes"]["y"]["check_ticks"]=[[50,50]]
        self.write_config()
        job=bridge.read_json(self.job())
        self.assertEqual(job["wpd_import_contract"]["axes"]["x"]["check_ticks"],[[50,50]])
        self.assertEqual(job["panels"]["p"]["axis"]["check_ticks"],[[50,50]])

    def test_floating_boundary_is_clamped_for_missing_anchor_and_roi(self):
        self.cfg["plot_bounds"][0] = 69
        self.cfg["axes"]["x"]["ticks"] = [[69,70],[554,95]]
        self.cfg["sample_at"] = [70,71]
        self.write_config()
        raw = bridge.pixel_at(70,bridge.axis_fit(self.cfg["axes"]["x"]))
        self.assertAlmostEqual(raw,69,places=10)
        job=bridge.read_json(self.job([]))
        self.assertEqual(job["items"][0]["anchor"][0],69)
        self.assertEqual(job["items"][0]["roi"][0],69)
        # Core uses a strict inside() test: the candidate contract must already
        # contain in-bound numbers rather than asking core to widen its limits.
        self.assertGreaterEqual(job["items"][0]["anchor"][0],job["items"][0]["roi"][0])

    def test_real_boundary_outside_is_not_sampling_tolerance(self):
        self.cfg["plot_bounds"][0] = 69
        self.cfg["axes"]["x"]["ticks"] = [[69,70],[554,95]]
        self.cfg["sample_at"] = [69.9999,71]
        self.write_config()
        with self.assertRaisesRegex(ValueError,"frozen sample.*outside"):
            bridge.prepare(self.config,self.root/"out")

    def test_candidate_boundary_roundoff_clamps_without_expanding_plot(self):
        self.cfg["sample_at"]=[0,50]
        self.write_config()
        job=bridge.read_json(self.job([
            {"sample_x":0,"pixel":[10-1e-12,50],"wpd_data":[0,50],"status":"candidate"},
            {"sample_x":50,"pixel":None,"wpd_data":None,"status":"no_output"}]))
        self.assertEqual(job["items"][0]["anchor"][0],10)
        with self.assertRaisesRegex(ValueError,"candidate x.*outside"):
            self.job([
                {"sample_x":0,"pixel":[10-1e-5,50],"wpd_data":[0,50],"status":"candidate"}])


if __name__ == "__main__":
    unittest.main()
