"""Shared SVG rasterization utilities for movie renderers."""

import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

def find_chrome(explicit=None):
    """Locate Google Chrome, including its standard macOS app bundle."""
    candidates = [explicit] if explicit else [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        str(Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
        shutil.which('google-chrome'),
        shutil.which('google-chrome-stable'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError('Google Chrome not found. Use --chrome /path/to/Google Chrome')


def rasterize_chrome(svg_path, image_w, image_h, chrome=None):
    """Render the full SVG strip with Chrome, preserving browser music glyphs.

    Chrome screenshots are viewport-sized; request the entire scaled strip.
    Check actual PNG dimensions so a truncated screenshot cannot silently
    become a truncated movie. Very long strips need tiled rendering later.
    """
    from PIL import Image
    if image_w > 16000 or image_h > 16000:
        raise RuntimeError(
            f'Chrome single-shot strip is {image_w}x{image_h}; exceeds the '
            'safe 16000px screenshot limit. Tiled rendering is needed.'
        )
    browser = find_chrome(chrome)
    with tempfile.TemporaryDirectory(prefix='music-movie-') as temp:
        png_path = Path(temp) / 'strip.png'
        page = Path(temp) / 'render.html'
        page.write_text(
            '<!doctype html><html><head><meta charset=\"utf-8\">'
            '<style>html,body{margin:0;padding:0;background:white;overflow:hidden}'
            'img{display:block}</style></head><body>'
            f'<img src=\"{escape(svg_path.resolve().as_uri(), quote=True)}\" '
            f'width=\"{image_w}\" height=\"{image_h}\">'
            '</body></html>', encoding='utf-8'
        )
        # Chrome may enforce a minimum window size, so crop surplus pixels
        # after validating the screenshot covers the requested SVG extent.
        command = [browser, '--headless', '--no-first-run', '--no-default-browser-check',
                   '--disable-gpu', '--hide-scrollbars', '--allow-file-access-from-files',
                   '--force-device-scale-factor=1',
                   f'--window-size={max(image_w, 500)},{max(image_h, 300)}',
                   f'--screenshot={png_path}', page.as_uri()]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode or not png_path.exists():
            raise RuntimeError('Chrome SVG screenshot failed:\n' + result.stderr[-3000:])
        with Image.open(png_path) as png:
            if png.width < image_w or png.height < image_h:
                raise RuntimeError(
                    f'Chrome screenshot was {png.width}x{png.height}, expected at least '
                    f'{image_w}x{image_h}. Refusing to crop the score.'
                )
            return png.convert('RGBA').crop((0, 0, image_w, image_h))
