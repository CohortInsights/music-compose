"""Continuous score scrolling renderer."""

from render_utils import rasterize_chrome

def make_smooth_scroll(times, xs):
    import numpy as np
    from scipy.interpolate import CubicHermiteSpline

    t = np.asarray(times, dtype=float)
    x = np.asarray(xs, dtype=float)

    if len(t) < 2:
        raise ValueError('At least two scroll events required')

    dt = np.diff(t)
    dx = np.diff(x)

    if np.any(dt <= 0):
        raise ValueError('Scroll times must strictly increase')

    if np.any(dx < 0):
        raise ValueError('Scroll positions must not decrease')

    slopes = dx / dt
    velocities = np.empty(len(t), dtype=float)

    # Smooth velocities across neighboring intervals.
    for i in range(len(t)):
        left = max(0, i - 2)
        right = min(len(slopes), i + 2)
        velocities[i] = np.mean(slopes[left:right])

    # Limit velocities to preserve forward movement.
    for i, slope in enumerate(slopes):
        if slope == 0:
            velocities[i] = 0.0
            velocities[i + 1] = 0.0
        else:
            limit = 3.0 * slope
            velocities[i] = min(velocities[i], limit)
            velocities[i + 1] = min(velocities[i + 1], limit)

    return CubicHermiteSpline(
        t,
        x,
        velocities,
        extrapolate=False
    )


def render_movie(
    strip_path,
    width,
    height,
    xs,
    times,
    output,
    fps,
    resolution,
    visible,
    measure_starts,
    chrome=None,
    reset=4,
):
    import math
    import shutil
    import subprocess
    import tempfile
    import time

    from PIL import Image, ImageDraw

    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg not found')

    if not xs or len(xs) != len(times):
        raise ValueError('Invalid scroll synchronization points')

    if len(xs) < 2:
        raise ValueError('At least two synchronization points required')

    if any(times[i] <= times[i - 1] for i in range(1, len(times))):
        raise ValueError('Scroll times must be strictly increasing')

    if visible <= 0:
        raise ValueError('Visible measures must be positive')

    if not measure_starts:
        raise ValueError('No measure positions provided')

    out_w, out_h = resolution

    count = min(visible, len(measure_starts))
    end_x = (
        measure_starts[count]
        if count < len(measure_starts)
        else width
    )

    visible_width = end_x - measure_starts[0]

    if visible_width <= 0:
        raise ValueError('Invalid visible measure width')

    scale = out_w / visible_width

    image_w = max(1, math.ceil(width * scale))
    image_h = max(1, math.ceil(height * scale))

    print(
        f'Rendering {count} visible measures '
        f'at scale {scale:.3f}...',
        flush=True
    )

    # Rasterize the complete score once.
    score = rasterize_chrome(
        strip_path,
        image_w,
        image_h,
        chrome
    )

    # Smooth velocity interpolation.
    scroll = make_smooth_scroll(times, xs)

    guide = round(out_w * 0.25)
    top = (out_h - image_h) // 2

    duration = times[-1] + 2.0
    total_frames = math.ceil(duration * fps)

    print(
        f'Video: {out_w}x{out_h} at {fps} fps\n'
        f'Score raster: {score.width}x{score.height}\n'
        f'Total frames: {total_frames}',
        flush=True
    )

    command = [
        'ffmpeg',
        '-y',
        '-loglevel', 'error',
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

    completed = 0
    total_measures = len(measure_starts)

    with tempfile.TemporaryFile(mode='w+b') as error_file:

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stderr=error_file
        )

        render_start = time.monotonic()

        try:
            for frame in range(total_frames):
                t = frame / fps

                # Smooth cubic Hermite interpolation.
                if t <= times[0]:
                    x = float(xs[0])
                elif t >= times[-1]:
                    x = float(xs[-1])
                else:
                    x = float(scroll(t))

                # Fast integer-pixel positioning.
                canvas = Image.new(
                    'RGB',
                    (out_w, out_h),
                    'white'
                )

                canvas.paste(
                    score,
                    (round(guide - x * scale), top),
                    score if score.mode == 'RGBA' else None
                )

                # Stationary reading guide.
                draw = ImageDraw.Draw(canvas, 'RGBA')

                draw.rectangle(
                    (
                        guide - 9,
                        top,
                        guide + 9,
                        top + image_h
                    ),
                    fill=(70, 115, 190, 18)
                )

                draw.line(
                    (
                        guide,
                        top,
                        guide,
                        top + image_h
                    ),
                    fill=(65, 95, 140, 150),
                    width=1
                )

                process.stdin.write(canvas.tobytes())

                # Musical measure progress.
                while (
                    completed + 1 < total_measures
                    and x >= measure_starts[completed + 1]
                ):
                    completed += 1
                    print(
                        f'Measure {completed}/{total_measures} submitted',
                        flush=True
                    )

                # Rendering clock every 300 frames.
                if (frame + 1) % 300 == 0:
                    elapsed = time.monotonic() - render_start
                    rendered = frame + 1
                    rate = rendered / elapsed
                    remaining = (
                        (total_frames - rendered) / rate
                        if rate > 0 else 0
                    )

                    print(
                        f'Frames {rendered}/{total_frames} '
                        f'({100 * rendered / total_frames:.1f}%) | '
                        f'Elapsed {elapsed / 60:.1f} min | '
                        f'{rate:.1f} fps | '
                        f'ETA {remaining / 60:.1f} min',
                        flush=True
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
            error = error_file.read().decode(
                errors='replace'
            )
            raise RuntimeError(
                'ffmpeg failed:\n' + error[-3000:]
            )

    elapsed = time.monotonic() - render_start

    print(
        f'Rendering completed in {elapsed / 60:.2f} minutes '
        f'({total_frames / elapsed:.1f} fps average)',
        flush=True
    )

    for measure in range(
        completed + 1,
        total_measures + 1
    ):
        print(
            f'Measure {measure}/{total_measures} submitted',
            flush=True
        )
