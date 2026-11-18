#!/usr/bin/env python3
"""Render a silent scrolling score from ABC, engraved SVG/JSON, and MIDI.

Requires mido, Pillow, Google Chrome, and ffmpeg on PATH.
The engraver and its JSON outputs are not modified.
"""
import argparse
import bisect
from collections import defaultdict
from fractions import Fraction
import tempfile
from html import escape
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from scipy.interpolate import PchipInterpolator

SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')


def svg_tag(tag):
    return f'{{{SVG_NS}}}{tag}'


def read_json(path):
    if not path.exists():
        raise ValueError(f'Missing {path}. Re-run scripts/engrave.js.')
    return json.loads(path.read_text(encoding='utf-8'))



def make_strip(svg_path, measures, noteheads, pad=20, opening_margin=25):
    root = ET.parse(svg_path).getroot()

    if not root.get('viewBox'):
        raise ValueError('Engraved SVG lacks viewBox')

    if not measures:
        raise ValueError('No measure rectangles')

    measures = sorted(measures, key=lambda m: m['measure'])

    if len({m['measure'] for m in measures}) != len(measures):
        raise ValueError('Duplicate measure numbers')

    h = max(float(m['height']) for m in measures) + 2 * pad

    # Identify the first measure of each engraved system.
    first_by_system = {}
    for m in measures:
        first_by_system.setdefault(m['system'], m['measure'])

    # Index noteheads by system to avoid repeatedly searching
    # the entire collection.
    heads_by_system = defaultdict(list)

    for head in noteheads:
        if head.get('glyph') != 'ghd':
            heads_by_system[head['system']].append(head)

    # Calculate adjusted crop rectangles before constructing
    # the output SVG.
    crops = []

    for m in measures:
        x = float(m['x'])
        y = float(m['y'])
        w = float(m['width'])

        is_system_start = (
            m['measure'] == first_by_system[m['system']]
        )

        is_first_measure = (
            m['measure'] == measures[0]['measure']
        )

        preserve_clef = m.get('preserve_clef', [])
        preserve_key = m.get('preserve_key', [])

        preserve_opening = (
            any(preserve_clef) or any(preserve_key)
        )

        trim = 0.0

        if (
            is_system_start
            and not is_first_measure
            and not preserve_opening
        ):
            # Locate the first non-grace notehead inside
            # this measure.
            head_x = [
                float(head['x'])
                for head in heads_by_system[m['system']]
                if x <= float(head['x']) < x + w
            ]

            if head_x:
                first_note_x = min(head_x)

                # Retain space before the notehead for
                # accidentals and other nearby notation.
                trim = max(
                    0.0,
                    first_note_x - opening_margin - x
                )

                # Never remove the entire measure.
                trim = min(trim, max(0.0, w - 1.0))

        crops.append({
            'measure': m['measure'],
            'system': m['system'],
            'x': x + trim,
            'y': y,
            'width': w - trim,
            'trim': trim
        })

    strip_w = sum(crop['width'] for crop in crops)

    out = ET.Element(svg_tag('svg'), {
        'viewBox': f'0 0 {strip_w:.3f} {h:.3f}',
        'width': f'{strip_w:.3f}px',
        'height': f'{h:.3f}px',
        'fill': 'currentColor',
        'stroke-width': '.7'
    })

    if root.get('class'):
        out.set('class', root.get('class'))

    for child in root:
        if child.tag in (svg_tag('defs'), svg_tag('style')):
            out.append(child)

    body = [
        child for child in root
        if child.tag not in (
            svg_tag('defs'),
            svg_tag('style')
        )
    ]

    if not body:
        raise ValueError('No SVG drawing content')

    positions = defaultdict(list)
    offset = 0.0

    for crop in crops:
        x = crop['x']
        y = crop['y']
        w = crop['width']

        pane = ET.SubElement(out, svg_tag('svg'), {
            'x': f'{offset:.5f}',
            'y': '0',
            'width': f'{w:.5f}',
            'height': f'{h:.5f}',
            'viewBox': (
                f'{x:.5f} {y - pad:.5f} '
                f'{w:.5f} {h:.5f}'
            ),
            'overflow': 'hidden'
        })

        ET.SubElement(
            pane,
            svg_tag('use'),
            {'href': '#movie-source'}
        )

        for head in heads_by_system[crop['system']]:
            hx = float(head['x'])

            if x <= hx < x + w:
                positions[crop['measure']].append(
                    offset + hx - x
                )

        offset += w

    defs = out.find(svg_tag('defs'))

    if defs is None:
        defs = ET.Element(svg_tag('defs'))
        out.insert(0, defs)

    drawing = ET.SubElement(
        defs,
        svg_tag('g'),
        {'id': 'movie-source'}
    )

    for child in body:
        drawing.append(child)

    return (
        ET.tostring(out, encoding='unicode'),
        positions,
        strip_w,
        h
    )


def abc_duration(token):
    """Return ABC duration in quarter-note units (L:1/4)."""
    suffix = (token.split(']', 1)[1] if token.startswith('[')
              else re.sub(r"^[\^_=]*[A-Ga-gzZ][,']*", '', token))
    match = re.match(r'(\d+)?(/+\d*)?', suffix)
    numerator, denominator = match.groups()
    value = Fraction(int(numerator) if numerator else 1)
    if denominator:
        value /= (int(denominator[1:]) if denominator[1:].isdigit()
                  else 2 ** len(denominator))
    return value


def abc_scroll_beats(path):
    """Collect sounding score positions, grouped by measure, across ABC voices.

    Supports the project's L:1/4, 4/4 ABC notation, chords, rests,
    grace groups, and & voice overlays. The score, not MIDI proximity,
    decides which attacks share one scrolling position.
    """
    source = path.read_text(encoding='utf-8')
    meter = re.search(r'^M:\s*(\d+)\s*/\s*(\d+)', source, re.M)
    unit = re.search(r'^L:\s*(\d+)\s*/\s*(\d+)', source, re.M)
    if not meter or (int(meter[1]), int(meter[2])) != (4, 4):
        raise ValueError('ABC synchronization currently requires M:4/4')
    if not unit or (int(unit[1]), int(unit[2])) != (1, 4):
        raise ValueError('ABC synchronization currently requires L:1/4')
    voices = defaultdict(list)
    current = None
    for line in source.splitlines():
        line = line.split('%', 1)[0].strip()
        if line.startswith('[V:'):
            current = line[3:line.index(']')]
            line = line[line.index(']') + 1:]
        if (current and line and not line.startswith(('V:', 'w:', 'K:',
                                                     'T:', 'M:', 'L:', 'Q:', 'X:'))):
            voices[current].append(line)
    if not voices:
        raise ValueError('No ABC voice bodies found')
    events = defaultdict(set)
    pattern = r"\[[^\]]+\][0-9/]*-?|[\^_=]*[A-Ga-gzZ][,']*[0-9/]*-?|[|&]"
    for lines in voices.values():
        raw = ' '.join(lines)
        raw = re.sub(r'\{[^}]*\}', '', raw)  # grace notes: no scroll event
        raw = re.sub(r'"[^"]*"', '', raw)  # chord names
        raw = re.sub(r'![^!]*!', '', raw)  # decorations
        measure = 1
        beat = Fraction(0)
        for token in re.findall(pattern, raw):
            if token == '|':
                if beat:
                    measure += 1
                    beat = Fraction(0)
                continue
            if token == '&':
                beat = Fraction(0)  # concurrent overlay starts at bar start
                continue
            # A missing barline does not change the musical 4/4 timeline.
            if beat >= 4:
                measure += int(beat // 4)
                beat %= 4
            if token[0] not in 'zZ':
                events[measure].add(beat)
            beat += abc_duration(token)
    return events


def score_columns(xs, tolerance=8.1):
    """Combine coincident staves and ±7.4 SVG-unit displaced chord heads."""
    groups = []
    for x in sorted(xs):
        if groups and x - groups[-1][-1] < tolerance:
            groups[-1].append(x)
        else:
            groups.append([x])
    # Rightmost notehead is the conventional chord alignment position.
    return [max(group) for group in groups]


def midi_events(path):
    """Return each MIDI note-on's tick position and absolute seconds."""
    import mido
    mid = mido.MidiFile(path)
    events = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == 'set_tempo':
                events.append((tick, 0, msg.tempo))
            elif msg.type == 'note_on' and msg.velocity > 0:
                events.append((tick, 1, msg.note))
    events.sort(key=lambda e: (e[0], e[1]))
    tempo = 500000
    last_tick = 0
    seconds = 0.0
    onsets = []
    for tick, kind, value in events:
        seconds += (tick - last_tick) * tempo / (mid.ticks_per_beat * 1_000_000)
        last_tick = tick
        if kind == 0:
            tempo = value
        else:
            onsets.append((tick / mid.ticks_per_beat, seconds))
    return onsets


def synchronize(positions, abc_beats, midi, measure_count):
    """One (time, x) pair per ABC rhythmic position; no MIDI time clustering."""
    xs, times = [], []
    midi_beats = [beat for beat, _ in midi]
    for measure in range(1, measure_count + 1):
        beats = sorted(abc_beats.get(measure, []))
        columns = score_columns(positions.get(measure, []))
        if len(beats) != len(columns):
            raise ValueError(f'Measure {measure}: {len(beats)} ABC events versus '
                             f'{len(columns)} engraved positions. Alignment needs review.')
        for beat, x in zip(beats, columns):
            expected = (measure - 1) * 4 + float(beat)
            index = bisect.bisect_left(midi_beats, expected - .03)
            if index >= len(midi) or midi_beats[index] > expected + .12:
                raise ValueError(f'No MIDI onset near measure {measure}, beat {beat}')
            xs.append(x)
            times.append(midi[index][1])  # first attack, including arpeggios
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('Scroll-event times are not strictly increasing')
    return xs, times


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




def render_movie(strip_path, width, height, xs, times, output, fps, resolution,
                 visible, measure_starts, chrome=None):
    from PIL import Image, ImageDraw

    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg executable not found on PATH')
    if not xs:
        raise ValueError('No synchronized scroll events')
    if len(xs) != len(times):
        raise ValueError('Scroll positions and times must have equal lengths')
    if visible <= 0:
        raise ValueError('Visible measures must be positive')
    if not measure_starts:
        raise ValueError('No measure boundaries')

    # ------------------------------------------------------------
    # Smooth monotonic interpolation of score position
    # ------------------------------------------------------------

    if len(times) < 2:
        raise ValueError('At least two scroll events required')

    if any(t2 <= t1 for t1, t2 in zip(times, times[1:])):
        raise ValueError('Scroll event times must be strictly increasing')

    if any(x2 < x1 for x1, x2 in zip(xs, xs[1:])):
        raise ValueError('Scroll positions must not move backward')

    scroll = PchipInterpolator(times, xs, extrapolate=False)

    out_w, out_h = resolution

    # Calculate the width of the requested number of measures.
    # Include the end of the last requested measure.
    count = min(visible, len(measure_starts))
    end_x = (
        measure_starts[count]
        if count < len(measure_starts)
        else width
    )
    visible_width = end_x - measure_starts[0]

    if visible_width <= 0:
        raise ValueError('Invalid measure widths')

    # Scale the score so the requested number of measures
    # occupies the available horizontal display.
    scale = out_w / visible_width

    image_w = max(1, math.ceil(width * scale))
    image_h = max(1, math.ceil(height * scale))

    print(
        f'Rendering {count} visible measures '
        f'at scale {scale:.3f}...',
        flush=True
    )

    print('Rendering SVG with Chrome...', flush=True)
    score = rasterize_chrome(strip_path, image_w, image_h, chrome)
    print('Rendering movie...', flush=True)

    guide = round(out_w * .25)
    top = (out_h - image_h) // 2
    duration = times[-1] + 2.0

    command = [
        'ffmpeg', '-y',
        '-f', 'rawvideo',
        '-pix_fmt', 'rgb24',
        '-s', f'{out_w}x{out_h}',
        '-r', str(fps),
        '-i', '-',
        '-an',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        str(output)
    ]

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    completed = 0
    total_measures = len(measure_starts)

    try:
        for frame in range(math.ceil(duration * fps)):
            t = frame / fps

            # PCHIP passes exactly through every synchronization
            # point, with continuous velocity between points.
            # Hold position before the first and after the last.
            if t <= times[0]:
                x = float(xs[0])
            elif t >= times[-1]:
                x = float(xs[-1])
            else:
                x = float(scroll(t))

            canvas = Image.new('RGB', (out_w, out_h), 'white')
            canvas.paste(
                score,
                (round(guide - x * scale), top),
                score
            )

            draw = ImageDraw.Draw(canvas, 'RGBA')
            draw.rectangle(
                (guide - 9, top, guide + 9, top + image_h),
                fill=(70, 115, 190, 18)
            )
            draw.line(
                (guide, top, guide, top + image_h),
                fill=(65, 95, 140, 150),
                width=1
            )

            process.stdin.write(canvas.tobytes())

            # Report measures as their right boundaries pass the guide.
            while (
                completed + 1 < total_measures
                and x >= measure_starts[completed + 1]
            ):
                completed += 1
                print(
                    f'Measure {completed}/{total_measures} submitted',
                    flush=True
                )

    except Exception:
        process.kill()
        raise
    finally:
        if process.stdin:
            process.stdin.close()

    print('Finalizing MP4...', flush=True)
    error = process.stderr.read().decode(errors='replace')

    if process.wait():
        raise RuntimeError('ffmpeg failed:\n' + error[-3000:])

    for measure in range(completed + 1, total_measures + 1):
        print(
            f'Measure {measure}/{total_measures} submitted',
            flush=True
        )




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('score', type=Path)
    parser.add_argument('--out-dir', type=Path, default=None)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--measures', type=int, default=4)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument(
        '--resolution',
        choices=['720p', '1080p', '2160p'],
        default='1080p'
    )
    parser.add_argument('--padding', type=float, default=20)
    parser.add_argument(
        '--chrome',
        type=str,
        default=None,
        help='Path to Google Chrome executable (auto-detected by default)'
    )

    args = parser.parse_args()

    if args.measures <= 0 or args.fps <= 0 or args.padding < 0:
        parser.error('measures/fps must be positive and padding nonnegative')

    project = Path(__file__).resolve().parent.parent
    out = args.out_dir or project / 'out'
    out.mkdir(parents=True, exist_ok=True)

    base = args.score.stem
    svg = out / (base + '.svg')

    measures = read_json(out / (base + '-measures.json'))
    heads = read_json(out / (base + '-noteheads.json'))

    if not svg.exists():
        raise ValueError(f'Missing engraved SVG: {svg}')

    strip, positions, width, height = make_strip(
        svg, measures, heads, args.padding
    )

    preview = out / (base + '-strip.svg')
    preview.write_text(strip, encoding='utf-8')

    print(
        f'Wrote {preview}: '
        f'{len(measures)} measures, {len(heads)} noteheads',
        flush=True
    )

    if args.preview:
        return

    midi_path = out / (base + '.mid')
    if not midi_path.exists():
        raise ValueError(f'Missing MIDI file: {midi_path}')

    abc_path = args.score if args.score.exists() else project / args.score
    if not abc_path.exists():
        raise ValueError(f'Missing ABC source: {abc_path}')

    xs, times = synchronize(
        positions,
        abc_scroll_beats(abc_path),
        midi_events(midi_path),
        len(measures)
    )

    print(
        f'Synchronized {len(xs)} scroll events '
        f'across {len(measures)} measures',
        flush=True
    )

    # Measure boundaries in the assembled horizontal strip.
    # These use the same ordering and widths as make_strip().
    ordered_measures = sorted(measures, key=lambda m: m['measure'])

    measure_starts = []
    offset = 0.0

    for measure in ordered_measures:
        measure_starts.append(offset)
        offset += float(measure['width'])

    resolutions = {
        '720p': (1280, 720),
        '1080p': (1920, 1080),
        '2160p': (3840, 2160)
    }

    video = out / (base + '.mp4')

    render_movie(
        preview,
        width,
        height,
        xs,
        times,
        video,
        args.fps,
        resolutions[args.resolution],
        args.measures,
        measure_starts,
        args.chrome
    )

    print(f'Wrote {video}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        sys.exit(f'Error: {exc}')
