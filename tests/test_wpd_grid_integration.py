"""Opt-in check against unchanged external WPD core; no network or installs."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

TOOLS = Path(__file__).resolve().parents[1]/'tools'
spec = importlib.util.spec_from_file_location('grid_wpd_bridge', TOOLS/'wpd_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@unittest.skipUnless(os.environ.get('WPD_CORE_SOURCES'), 'Set WPD_CORE_SOURCES to pinned external WPD sources')
class WpdGridIntegration(unittest.TestCase):
    def test_excluding_grid_uses_interpolation_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.png'
            image = Image.new('RGB', (121,121), 'white')
            draw = ImageDraw.Draw(image)
            draw.line([(x,round(10+.5*x)) for x in range(121)], fill='black', width=1)
            for x in (20,60):
                draw.line((x,0,x,120),fill='black',width=1)
            image.save(source)
            before = bridge.sha(source)
            config = {'schema_version':1, 'engine_sources_dir':os.environ['WPD_CORE_SOURCES'],
                'source':{'image':str(source)}, 'plot_bounds':[0,0,120,120],
                'axes':{a:{'kind':'linear','unit':'synthetic pixel units','ticks':[[0,0],[120,120]]} for a in ('x','y')},
                'sample_at':[20,40,60,80,100],
                'series':[{'id':'target','name':'target','selector':{'color':[0,0,0],'distance':20},
                    'domain':[0,120], 'curve_width_px':1,
                    'mask':{'polygons':[[[0,0],[120,0],[120,120],[0,120]]]}}]}
            outputs = {}
            for mode in ('raw','excluded'):
                if mode == 'excluded':
                    config['series'][0]['mask']['exclude_boxes'] = [[19,0,21,120],[59,0,61,120]]
                path = root/f'{mode}.json'
                bridge.write_json(path,config)
                prepared = bridge.prepare(path,root/mode)
                output = root/f'{mode}-result.json'
                subprocess.run([os.environ.get('FIGURE_NODE') or shutil.which('node'),
                    str(TOOLS/'wpd_engine.mjs'),prepared['engine_input'],str(output)],
                    check=True,capture_output=True,text=True,timeout=30)
                outputs[mode] = bridge.read_json(output)['series'][0]
            self.assertGreater(outputs['raw']['ink_pixels'],outputs['excluded']['ink_pixels'])
            self.assertGreater(max(abs(p['pixel'][1]-(10+.5*p['sample_x'])) for p in outputs['raw']['points']),10)
            for p in outputs['excluded']['points']:
                self.assertEqual(p['status'],'candidate')
                self.assertAlmostEqual(p['pixel'][0],p['sample_x'],places=7)
                self.assertLessEqual(abs(p['pixel'][1]-(10+.5*p['sample_x'])),2)
            self.assertEqual(bridge.sha(source),before)


if __name__ == '__main__':
    unittest.main()
