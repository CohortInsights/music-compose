"""Two-system score renderer with one-system vertical transitions.

The active system occupies the upper row; the next system is visible below.
At a transition, the two-row stack slides up one row.
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
    visible, measure_starts, chrome=None,
):
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
    page_count = math.ceil(total_measures / visible)

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
    for page in range(page_count):
        first = page * visible
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

    # Each system switches one frame after its final note onset. This
    # gives the performer the remainder of that note to read ahead.
    # The new cursor begins at the first measure boundary and counts in
    # to the next system's first note.
    page_events = []
    for first, last, _, _ in pages:
        start = bisect.bisect_left(xs, measure_starts[first])
        end_x = measure_starts[last] if last < total_measures else width
        end = bisect.bisect_left(xs, end_x)
        if start >= end:
            raise ValueError(
                f'No synchronized notes in measures {first + 1}–{last}'
            )
        page_events.append((start, end))

    page_start_times = [0.0]
    for page in range(page_count - 1):
        _, end = page_events[page]
        next_start, _ = page_events[page + 1]
        last_onset = times[end - 1]
        next_onset = times[next_start]
        # Prefer the first frame after the last onset. If the following
        # note arrives sooner than one frame, switch at that onset.
        transition = min(last_onset + 1.0 / fps, next_onset)
        page_start_times.append(transition)

    duration = times[-1] + 2.0
    total_frames = math.ceil(duration * fps)
    print(
        f'Cursor renderer: {page_count} systems, up to {visible} measures each\n'
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
    # Scroll the stack over this interval. The first frame of the new
    # system starts the transition, keeping the next notation visible.
    scroll_seconds = 0.30

    with tempfile.TemporaryFile(mode='w+b') as error_file:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=error_file)
        render_start = time.monotonic()
        try:
            for frame in range(total_frames):
                t = frame / fps
                page = max(0, min(page_count - 1, bisect.bisect_right(page_start_times, t) - 1))
                first, last, left, right = pages[page]

                start_event, end_event = page_events[page]
                first_note_time = times[start_event]
                first_note_x = xs[start_event]
                measure_left = measure_starts[first]

                if t < first_note_time:
                    # Count in from the measure boundary to the first note.
                    # For the opening system, the count-in starts at t=0;
                    # subsequent systems start at the preceding page turn.
                    start_time = page_start_times[page]
                    available = first_note_time - start_time
                    if available > 0:
                        fraction = max(0.0, min(1.0, (t - start_time) / available))
                        x = measure_left + fraction * (first_note_x - measure_left)
                    else:
                        x = first_note_x
                elif t >= times[end_event - 1]:
                    # Hold the final note until the system changes.
                    x = xs[end_event - 1]
                else:
                    # Interpolate only between notes belonging to this system.
                    i = min(end_event - 2, bisect.bisect_right(times, t) - 1)
                    fraction = (t - times[i]) / (times[i + 1] - times[i])
                    x = xs[i] + fraction * (xs[i + 1] - xs[i])

                # Keep the cursor within its current system. In particular,
                # don't let it drift into the next page before the page turn.
                x = min(max(x, measure_starts[first]), right)
                cursor_x = round(margin + (x - left) * scale)

                # The incoming system has been visible in the lower row.
                # Animate it upward, preserving its appearance and scale.
                transition_start = page_start_times[page]
                if page > 0 and t < transition_start + scroll_seconds:
                    u = max(0.0, min(1.0, (t - transition_start) / scroll_seconds))
                    # Smoothstep eases the vertical motion at both ends.
                    u = u * u * (3.0 - 2.0 * u)
                    shift = round(row_h * (1.0 - u))
                else:
                    shift = 0

                canvas = Image.new('RGB', (out_w, out_h), 'white')
                # Previous row is retained during the slide; active and
                # upcoming systems remain visually continuous.
                for index in (page - 1, page, page + 1):
                    if index < 0 or index >= page_count:
                        continue
                    if index == page - 1:
                        y = row_inset + shift - row_h
                    elif index == page:
                        y = row_inset + shift
                    else:
                        y = row_inset + shift + row_h
                    score = raster_pages[index]
                    if y >= out_h or y + score.height <= 0:
                        continue
                    # PIL clips pasted images to the canvas bounds.
                    canvas.paste(
                        score, (margin, y),
                        score if score.mode == 'RGBA' else None,
                    )

                cursor_y = row_inset + shift
                draw = ImageDraw.Draw(canvas, 'RGBA')
                draw.rectangle(
                    (cursor_x - 9, cursor_y, cursor_x + 9, cursor_y + image_h),
                    fill=(70, 115, 190, 18),
                )
                draw.line(
                    (cursor_x, cursor_y, cursor_x, cursor_y + image_h),
                    fill=(65, 95, 140, 150), width=1,
                )
                process.stdin.write(canvas.tobytes())

                if page != previous_page:
                    print(f'Displaying measures {first + 1}–{last}', flush=True)
                    previous_page = page

                current_measure = min(
                    total_measures - 1,
                    max(0, bisect.bisect_right(measure_starts, x) - 1),
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
