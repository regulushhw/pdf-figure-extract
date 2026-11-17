import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from page_overview import create_overviews


class PageOverviewTests(unittest.TestCase):
    def test_pagination_and_output_safety(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'package'
            pages = package / 'pages'
            pages.mkdir(parents=True)
            for page in (1, 2, 13):
                Image.new('RGB', (100, 200), 'white').save(pages / f'p{page:03}.png')
            results = create_overviews(package, root / 'output', 2)
            self.assertEqual(len(results), 2)
            with Image.open(results[0]) as im:
                self.assertEqual(im.size, (620, 435))
            with self.assertRaises(ValueError):
                create_overviews(package, root / 'output')
            with self.assertRaises(ValueError):
                create_overviews(package, package / 'new')

    def test_empty_and_invalid_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'package' / 'pages').mkdir(parents=True)
            with self.assertRaises(ValueError):
                create_overviews(root / 'package', root / 'out')
            with self.assertRaises(ValueError):
                create_overviews(root / 'package', root / 'out', 0)


if __name__ == '__main__':
    unittest.main()
