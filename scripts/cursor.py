"""Two stationary score rows with overlapping measures and a two-stage dissolve.

The cursor alternates between the top and bottom rows. After a row
becomes inactive, its old system fades to white, then the next fades in while
the active row and cursor remain entirely unchanged.
"""

import bisect
import math
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw
from render_utils import rasterize_chrome


def render_movie(
    strip_path, width, height, xs, times, output, fps, resolution,
    visible, measure_starts, chrome=None, dissolve=2.0, overlap=1,
):
    if dissolve < 0 or not math.isfinite(dissolve):
        raise ValueError('Dissolve duration must be a finite, nonnegative number of seconds')
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg not found')
    if len(xs) < 2 or len(xs) != len(times):
        raise ValueError('At least two matching synchronization points required')
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('Synchronization times must be strictly increasing')
    if any(b < a for a, b in zip(xs, xs[1:])):
        raise ValueError('Synchronization positions must not decrease')
    if not measure_starts or any(
        b <= a for a, b in zip(measure_starts, measure_starts[1:])
    ):
        raise ValueError('Invalid measure boundaries')
    if visible <= 0 or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError('Invalid visible count, frame rate, or score dimensions')
    out_w, out_h = resolution
    if out_w <= 0 or out_h <= 0:
        raise ValueError('Invalid resolution')

    total_measures = len(measure_starts)
    if not isinstance(overlap, int) or not 0 <= overlap < visible:
        raise ValueError('Overlap must be an integer between 0 and --measures - 1')

    # Subsequent systems repeat the last `overlap` measures.
    stride = visible - overlap
    page_firsts = [0]
    while page_firsts[-1] + visible < total_measures:
        page_firsts.append(page_firsts[-1] + stride)
    page_count = len(page_firsts)

    # A consistent 5% margin at either end. All systems use the same
    # horizontal scale so notation does not change size at page turns.
    margin = round(out_w * 0.05)
    usable_width = out_w - 2 * margin
    if usable_width <= 0:
        raise ValueError('Resolution too narrow for margins')

    # Preserve a small strip of SVG before each system's first measure.
    # This keeps opening barlines and nearby notation away from the crop edge.
    opening_pad = 12.0
    pages = []
    for page, first in enumerate(page_firsts):
        last = min(first + visible, total_measures)
        left = measure_starts[first]
        right = measure_starts[last] if last < total_measures else width
        if right <= left:
            raise ValueError(f'Invalid width for system {page + 1}')
        crop_left = max(0.0, left - opening_pad) if first else left
        pages.append((first, last, crop_left, right))

    # Each system gets its own fixed row. Keep the same scale throughout.
    row_h = out_h // 2
    scale = min(
        usable_width / max(right - left for _, _, left, right in pages),
        (row_h * 0.80) / height,
    )
    image_h = max(1, math.ceil(height * scale))
    row_inset = (row_h - image_h) // 2

    # Each page is a cropped SVG view of the original assembled strip.
    # Rasterizing pages separately avoids Chrome's 16000-pixel strip limit.
    root = ET.parse(strip_path).getroot()
    raster_pages = []
    with tempfile.TemporaryDirectory(prefix='music-cursor-') as directory:
        temp = Path(directory)
        for page, (first, last, left, right) in enumerate(pages):
            page_width = right - left
            pixel_width = max(1, math.ceil(page_width * scale))
            root.set('viewBox', f'{left:.6f} 0 {page_width:.6f} {height:.6f}')
            root.set('width', f'{pixel_width}px')
            root.set('height', f'{image_h}px')
            svg_page = temp / f'system-{page:04d}.svg'
            ET.ElementTree(root).write(svg_page, encoding='utf-8', xml_declaration=True)
            print(
                f'Rasterizing system {page + 1}/{page_count} '
                f'(measures {first + 1}–{last})',
                flush=True,
            )
            raster_pages.append(
                rasterize_chrome(svg_page, pixel_width, image_h, chrome)
            )

    # Switch just after the last note onset in the outgoing system.
    # For overlap > 0, both cursors follow the shared measures until
    # that switch. For overlap == 0, there is no duplicated cursor.
    page_events = []
    for first, last, _, _ in pages:
        start = bisect.bisect_left(xs, measure_starts[first])
        end_x = measure_starts[last] if last < total_measures else width
        end = bisect.bisect_left(xs, end_x)
        if start >= end:
            raise ValueError(f'No synchronized notes in measures {first + 1}–{last}')
        page_events.append((start, end))

    page_start_times = [0.0]
    shared_intervals = []
    for page in range(page_count - 1):
        start_event, end_event = page_events[page]
        last_onset = times[end_event - 1]
        following = times[end_event] if end_event < len(times) else float('inf')
        switch = min(last_onset + 1.0 / fps, following)
        page_start_times.append(switch)

        if overlap:
            shared_measure = pages[page + 1][0]
            shared_start = bisect.bisect_left(xs, measure_starts[shared_measure])
            if shared_start >= end_event:
                raise ValueError(
                    f'Overlapping measures starting at {shared_measure + 1} '
                    'have no synchronized notes'
                )
            shared_intervals.append((times[shared_start], switch))
        else:
            shared_intervals.append((switch, switch))

    # Earliest moment the NEXT page must be fully visible. For overlap,
    # this is the first note of its shared section; without overlap,
    # this is the first note of the new system.
    next_read_times = [times[start] for start, _ in page_events]

    duration = times[-1] + 2.0
    total_frames = math.ceil(duration * fps)
    print(
        f'Cursor renderer: {page_count} systems, up to {visible} measures each, '
        f'{overlap} overlapping measures\n'
        f'Video: {out_w}x{out_h} at {fps} fps; total frames: {total_frames}',
        flush=True,
    )

    command = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-s', f'{out_w}x{out_h}', '-r', str(fps),
        '-i', '-', '-an', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output),
    ]
    completed = 0
    previous_page = -1

    with tempfile.TemporaryFile(mode='w+b') as error_file:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=error_file)
        render_start = time.monotonic()
        try:
            for frame in range(total_frames):
                t = frame / fps
                page = max(0, min(page_count - 1, bisect.bisect_right(page_start_times, t) - 1))
                first, last, left, right = pages[page]

                def position_for_system(index):
                    """Score x at time t, including count-in before first note."""
                    f, l, crop_left, crop_right = pages[index]
                    start_event, end_event = page_events[index]
                    first_note_time = times[start_event]
                    first_note_x = xs[start_event]
                    measure_left = measure_starts[f]
                    if t < first_note_time:
                        # For the first system, start at t=0. Subsequent
                        # systems' shared measure is already tracked by
                        # the duplicate cursor before the handoff.
                        start_time = 0.0 if index == 0 else shared_intervals[index - 1][0]
                        available = first_note_time - start_time
                        fraction = (
                            max(0.0, min(1.0, (t - start_time) / available))
                            if available > 0 else 1.0
                        )
                        x = measure_left + fraction * (first_note_x - measure_left)
                    elif t >= times[end_event - 1]:
                        x = xs[end_event - 1]
                    else:
                        i = min(end_event - 2, bisect.bisect_right(times, t) - 1)
                        fraction = (t - times[i]) / (times[i + 1] - times[i])
                        x = xs[i] + fraction * (xs[i + 1] - xs[i])
                    x = min(max(x, measure_left), crop_right)
                    return round(margin + (x - crop_left) * scale)

                cursor_x = position_for_system(page)

                # Alternate the active system between two FIXED rows.
                # When the cursor changes rows, the abandoned row is
                # dissolved into the next system. Never fade the cursor.
                active_row = page % 2
                inactive_row = 1 - active_row
                canvas = Image.new('RGB', (out_w, out_h), 'white')

                def paste_system(index, row, opacity=1.0):
                    if index < 0 or index >= page_count or opacity <= 0:
                        return
                    score = raster_pages[index]
                    y = row * row_h + row_inset
                    if opacity >= 1.0:
                        canvas.paste(
                            score, (margin, y),
                            score if score.mode == 'RGBA' else None,
                        )
                    else:
                        # Composite onto white first so transparency in the
                        # SVG doesn't introduce dark halos during the fade.
                        layer = Image.new('RGB', score.size, 'white')
                        layer.paste(
                            score, (0, 0),
                            score if score.mode == 'RGBA' else None,
                        )
                        white = Image.new('RGB', score.size, 'white')
                        blended = Image.blend(white, layer, opacity)
                        canvas.paste(blended, (margin, y))

                paste_system(page, active_row)
                if page == 0:
                    # At startup the lower row already shows System 2.
                    paste_system(1, inactive_row)
                else:
                    # At the transition, the former active row contains
                    # page-1. Fade it to white, then fade in page+1.
                    # The replacement is ready within two requested phase durations, or
                    # sooner if the next page turn is unusually close.
                    fade_start = page_start_times[page]
                    fade_span = 2.0 * dissolve
                    if page + 1 < page_count:
                        fade_span = min(
                            fade_span,
                            max(0.0, next_read_times[page + 1] - fade_start),
                        )
                    alpha = (
                        min(1.0, max(0.0, (t - fade_start) / fade_span))
                        if fade_span > 0 else 1.0
                    )
                    # Two-stage dissolve: old -> white -> new. The duration
                    # is twice --dissolve: one full duration for each stage.
                    def row_image(index):
                        layer = Image.new('RGB', (out_w, row_h), 'white')
                        if 0 <= index < page_count:
                            score = raster_pages[index]
                            layer.paste(
                                score, (margin, row_inset),
                                score if score.mode == 'RGBA' else None,
                            )
                        return layer

                    old_row = row_image(page - 1)
                    new_row = row_image(page + 1)
                    blank_row = Image.new('RGB', (out_w, row_h), 'white')
                    if alpha < 0.5:
                        dissolved = Image.blend(old_row, blank_row, alpha * 2.0)
                    else:
                        dissolved = Image.blend(blank_row, new_row, (alpha - 0.5) * 2.0)
                    canvas.paste(dissolved, (0, inactive_row * row_h))

                draw = ImageDraw.Draw(canvas, 'RGBA')

                def draw_cursor(index, row):
                    cx = position_for_system(index)
                    cy = row * row_h + row_inset
                    draw.rectangle(
                        (cx - 9, cy, cx + 9, cy + image_h),
                        fill=(70, 115, 190, 18),
                    )
                    draw.line(
                        (cx, cy, cx, cy + image_h),
                        fill=(65, 95, 140, 150), width=1,
                    )

                draw_cursor(page, active_row)

                # During the shared measure, duplicate the cursor on the
                # upcoming system. Both follow the SAME musical timeline.
                # Never fade either cursor.
                if overlap and page + 1 < page_count:
                    overlap_start, overlap_end = shared_intervals[page]
                    if overlap_start <= t < overlap_end:
                        draw_cursor(page + 1, inactive_row)

                process.stdin.write(canvas.tobytes())

                if page != previous_page:
                    print(f'Displaying measures {first + 1}–{last}', flush=True)
                    previous_page = page

                # Progress reporting follows the actual active score position.
                active_x = measure_starts[first] + (
                    (cursor_x - margin) / scale + left - measure_starts[first]
                )
                current_measure = min(
                    total_measures - 1,
                    max(0, bisect.bisect_right(measure_starts, active_x) - 1),
                )
                while completed < current_measure:
                    completed += 1
                    print(
                        f'Measure {completed}/{total_measures} submitted',
                        flush=True,
                    )

                if (frame + 1) % 300 == 0:
                    elapsed = time.monotonic() - render_start
                    rate = (frame + 1) / max(elapsed, 1e-9)
                    remaining = (total_frames - frame - 1) / rate
                    print(
                        f'Frames {frame + 1}/{total_frames} '
                        f'({100 * (frame + 1) / total_frames:.1f}%) | '
                        f'Elapsed {elapsed / 60:.1f} min | '
                        f'{rate:.1f} fps | ETA {remaining / 60:.1f} min',
                        flush=True,
                    )
        except Exception:
            process.kill()
            process.wait()
            raise
        finally:
            if process.stdin:
                process.stdin.close()

        print('Finalizing MP4...', flush=True)
        return_code = process.wait()
        if return_code:
            error_file.seek(0)
            raise RuntimeError(
                'ffmpeg failed:\\n' + error_file.read().decode(errors='replace')[-3000:]
            )

    elapsed = time.monotonic() - render_start
    print(
        f'Rendering completed in {elapsed / 60:.2f} minutes '
        f'({total_frames / max(elapsed, 1e-9):.1f} fps average)',
        flush=True,
    )
    for measure in range(completed + 1, total_measures + 1):
        print(f'Measure {measure}/{total_measures} submitted', flush=True)
