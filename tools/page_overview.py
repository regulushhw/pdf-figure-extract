"""Build numbered contact sheets from a prepared PDF's page previews."""
import argparse
import math
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageOps


def create_overviews(package, output, per_sheet=12):
    package, output = Path(package).resolve(), Path(output).resolve()
    if output == package or package in output.parents:
        raise ValueError('output must be outside prepared package')
    if per_sheet < 1 or per_sheet > 36:
        raise ValueError('pages per sheet must be between 1 and 36')
    pages = sorted((p for p in (package / 'pages').glob('p*.png')
                    if re.fullmatch(r'p\d+\.png', p.name)),
                   key=lambda p: int(p.stem[1:]))
    if not pages:
        raise ValueError('no page previews found')
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory is not empty')
    output.mkdir(parents=True, exist_ok=True)
    columns = min(4, per_sheet)
    files = []
    for offset in range(0, len(pages), per_sheet):
        group = pages[offset:offset + per_sheet]
        result = Image.new('RGB', (columns * 310, math.ceil(len(group) / columns) * 435), 'white')
        for i, page in enumerate(group):
            with Image.open(page) as source:
                thumb = ImageOps.contain(source.convert('RGB'), (300, 405))
            x, y = (i % columns) * 310, (i // columns) * 435
            result.paste(thumb, (x + (310 - thumb.width) // 2, y + 25))
            ImageDraw.Draw(result).text((x + 5, y + 5), f'PDF page {int(page.stem[1:])}', fill='black')
        dest = output / f'overview_{offset // per_sheet + 1:03}.png'
        result.save(dest)
        files.append(str(dest.resolve()))
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', help='prepared task directory')
    parser.add_argument('output', help='new, empty output directory outside the package')
    parser.add_argument('--pages-per-sheet', type=int, default=12)
    args = parser.parse_args()
    import json
    print(json.dumps({'overviews': create_overviews(args.package, args.output, args.pages_per_sheet)}, ensure_ascii=False))
