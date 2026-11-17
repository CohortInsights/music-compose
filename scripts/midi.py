
#!/usr/bin/env python3
"""Convert scores/<name>.abc to out/<name>.mid using abc2midi.

Usage:
    poetry run python scripts/midi.py emmanuel
    poetry run python scripts/midi.py emmanuel --ignore Voice
    poetry run python scripts/midi.py emmanuel --ignore Voice 'Piano RH'
    poetry run python scripts/midi.py emmanuel --ignore Voice --sustain 2

Requires: abc2midi on PATH, and the Python package mido.
"""

import argparse
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import mido

ROOT = Path(__file__).resolve().parent.parent
SCORES = ROOT / 'scores'
OUT = ROOT / 'out'

VOICE_DECL = re.compile(r'^\s*V:\s*([^\s]+)(.*)$')
VOICE_INLINE = re.compile(r'^\s*\[V:\s*([^\]\s]+)\s*\]')
VOICE_NAME = re.compile(r'\bname\s*=\s*(?:"([^"]*)"|([^\s]+))')
SCORE_DIRECTIVE = re.compile(r'^(\s*%%score\s+)(.*)$')


def filter_voices(source: str, ignored_names: list[str]) -> str:
    """Remove block-organized ABC voices identified by their display names."""
    if not ignored_names:
        return source

    ignored = set(ignored_names)
    voice_names = {}
    for line in source.splitlines():
        match = VOICE_DECL.match(line)
        if match:
            name_match = VOICE_NAME.search(match.group(2))
            if name_match:
                voice_names[match.group(1)] = (
                    name_match.group(1) or name_match.group(2)
                )

    unknown = ignored - set(voice_names.values())
    if unknown:
        raise ValueError(
            f'Unknown voice name(s): {", ".join(sorted(unknown))}'
        )

    excluded_ids = {
        vid for vid, name in voice_names.items() if name in ignored
    }
    retained_ids = set(voice_names) - excluded_ids
    if not retained_ids:
        raise ValueError('Cannot ignore every voice')

    result = []
    current_id = None

    for line in source.splitlines(keepends=True):
        decl = VOICE_DECL.match(line)
        inline = VOICE_INLINE.match(line)

        if decl:
            current_id = decl.group(1)
            if current_id in excluded_ids:
                continue
        elif inline:
            current_id = inline.group(1)

        score = SCORE_DIRECTIVE.match(line)
        if score:
            # Keep only surviving voice IDs, with a flat score layout.
            order = re.findall(r'[A-Za-z0-9_-]+', score.group(2))
            order = [vid for vid in order if vid in retained_ids]
            if order:
                result.append(score.group(1) + ' '.join(order) + '\n')
            continue

        if current_id in excluded_ids:
            continue

        result.append(line)

    return ''.join(result)


def _note_on_velocities(track: mido.MidiTrack) -> list[int]:
    return [
        msg.velocity
        for msg in track
        if msg.type == 'note_on' and msg.velocity > 0
    ]




def normalize_overlay_velocities(
    midi_path: Path,
    abc_source: str,
) -> None:
    """Normalize ABC voice velocities while preserving dynamics.

    Primary tracks retain abc2midi's velocities.

    Overlay tracks without explicit dynamics are scaled to
    their parent voice's default velocity, preserving relative
    velocity differences.

    Overlays with explicit dynamics are left unchanged so
    abc2midi's interpretation of those markings is retained.
    """
    midi = mido.MidiFile(str(midi_path))

    voice_order = []
    overlay_order = []
    defaults = {}
    dynamic_overlays = set()

    current_voice = None

    dynamic_pattern = re.compile(
        r'!(?:ppp|pp|p|mp|mf|f|ff|fff)!'
    )

    for line in abc_source.splitlines():
        match = VOICE_INLINE.match(line)

        if match:
            current_voice = match.group(1)

            if current_voice not in voice_order:
                voice_order.append(current_voice)

        if current_voice is None:
            continue

        beat_match = re.match(
            r'^\s*%%MIDI\s+beat\s+'
            r'(\d+)\s+(\d+)\s+(\d+)\s+\d+',
            line,
        )

        if beat_match:
            values = [int(v) for v in beat_match.groups()]

            if len(set(values)) != 1:
                raise ValueError(
                    f'Voice {current_voice} has multiple '
                    'beat velocities; unsupported'
                )

            defaults[current_voice] = values[0]

        if line.lstrip().startswith('%'):
            continue

        if '&' in line:
            if current_voice not in overlay_order:
                overlay_order.append(current_voice)

            overlay_text = line.split('&', 1)[1]

            if dynamic_pattern.search(overlay_text):
                dynamic_overlays.add(current_voice)

    note_tracks = []

    for index, track in enumerate(midi.tracks):
        if any(
            msg.type == 'note_on' and msg.velocity > 0
            for msg in track
        ):
            note_tracks.append(index)

    expected = len(voice_order) + len(overlay_order)

    if len(note_tracks) != expected:
        raise ValueError(
            f'Track mapping mismatch: found '
            f'{len(note_tracks)} note tracks, '
            f'expected {expected}'
        )

    track_voices = (
        [(voice, False) for voice in voice_order]
        + [(voice, True) for voice in overlay_order]
    )

    changed = 0

    for track_index, (voice, is_overlay) in zip(
        note_tracks, track_voices
    ):
        track = midi.tracks[track_index]

        notes = [
            msg for msg in track
            if msg.type == 'note_on' and msg.velocity > 0
        ]

        target_default = defaults.get(voice)

        if target_default is None:
            print(
                f'Track {track_index}: {voice}, '
                'no default velocity; unchanged'
            )
            continue

        if not is_overlay:
            print(
                f'Track {track_index}: {voice}, '
                'primary voice; velocities preserved'
            )
            continue

        if voice in dynamic_overlays:
            print(
                f'Track {track_index}: {voice}, '
                'explicit overlay dynamics; '
                'velocities preserved'
            )
            continue

        if not notes:
            continue

        # Most common velocity is the assumed baseline
        # for an overlay without explicit dynamics.
        baseline = Counter(
            msg.velocity for msg in notes
        ).most_common(1)[0][0]

        scale = target_default / baseline

        track_changes = 0

        for msg in notes:
            target = max(
                1,
                min(127, round(msg.velocity * scale))
            )

            if msg.velocity != target:
                msg.velocity = target
                track_changes += 1

        changed += track_changes

        print(
            f'Track {track_index}: {voice} overlay, '
            f'baseline {baseline} -> {target_default}, '
            f'{track_changes} notes adjusted'
        )

    if changed:
        midi.save(str(midi_path))

    print(
        f'Normalized {changed} velocities '
        'without flattening dynamics'
    )



def add_sustain_pedal(
    midi_path: Path,
    beats: float,
    gap_ms: float = 10.0,
) -> None:
    """Cycle CC64 sustain with a short pedal-up interval.

    The pedal releases at each beat boundary and re-engages
    gap_ms milliseconds later.

    Original note pitches, velocities, and timings are preserved.
    """
    midi = mido.MidiFile(str(midi_path))

    interval = round(beats * midi.ticks_per_beat)

    if interval < 1:
        raise ValueError("Sustain interval must be at least one tick")

    # Use the MIDI tempo to convert milliseconds to ticks.
    tempo = 500000  # Default MIDI tempo: 120 BPM

    for track in midi.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo = msg.tempo
                break

    gap_ticks = max(
        1,
        round(
            (gap_ms * 1000)
            * midi.ticks_per_beat
            / tempo
        ),
    )

    if gap_ticks >= interval:
        raise ValueError(
            "Pedal gap must be shorter than sustain interval"
        )

    channel_tracks = {}
    channel_ends = {}

    for track_index, track in enumerate(midi.tracks):
        tick = 0

        for msg in track:
            tick += msg.time

            if (
                msg.type in ("note_on", "note_off")
                and msg.channel != 9
            ):
                channel_tracks.setdefault(msg.channel, track_index)
                channel_ends[msg.channel] = max(
                    channel_ends.get(msg.channel, 0),
                    tick,
                )

    for channel, track_index in sorted(channel_tracks.items()):
        track = midi.tracks[track_index]

        events = []
        tick = 0

        for order, msg in enumerate(track):
            tick += msg.time

            if msg.type != "end_of_track":
                events.append((tick, 2, order, msg.copy()))

        original_end = tick
        final_tick = channel_ends[channel]

        pedal_events = [
            (
                0, 1, 0,
                mido.Message(
                    "control_change",
                    channel=channel,
                    control=64,
                    value=127,
                    time=0,
                ),
            )
        ]

        boundary = interval

        while boundary < final_tick:
            # Release the pedal at the boundary.
            pedal_events.append(
                (
                    boundary, 0, 0,
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=64,
                        value=0,
                        time=0,
                    ),
                )
            )

            # Re-engage after the short gap.
            pedal_events.append(
                (
                    boundary + gap_ticks, 1, 0,
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=64,
                        value=127,
                        time=0,
                    ),
                )
            )

            boundary += interval

        # Release the pedal at the end of the part.
        pedal_events.append(
            (
                final_tick, 3, 0,
                mido.Message(
                    "control_change",
                    channel=channel,
                    control=64,
                    value=0,
                    time=0,
                ),
            )
        )

        combined = sorted(
            events + pedal_events,
            key=lambda event: (
                event[0],
                event[1],
                event[2],
            ),
        )

        rebuilt = mido.MidiTrack()
        previous_tick = 0

        for absolute_tick, _, _, msg in combined:
            msg.time = absolute_tick - previous_tick
            rebuilt.append(msg)
            previous_tick = absolute_tick

        rebuilt.append(
            mido.MetaMessage(
                "end_of_track",
                time=max(original_end, previous_tick) - previous_tick,
            )
        )

        midi.tracks[track_index] = rebuilt

        print(
            f"Channel {channel + 1}: "
            f"sustain every {beats:g} beats, "
            f"pedal gap {gap_ms:g} ms "
            f"({gap_ticks} ticks)"
        )

    midi.save(str(midi_path))


def select_measures(
    midi_path: Path,
    start_measure: int,
    end_measure: int,
    beats_per_measure: int = 4,
) -> None:
    """Keep only the selected measures in a MIDI file.

    Measure numbers are 1-based and inclusive.
    Assumes a constant meter and no pickup measure.

    Events are shifted so the selected passage starts at tick 0.
    Notes crossing the selection boundaries are clipped.
    """
    if start_measure < 1 or end_measure < start_measure:
        raise ValueError("Invalid measure range")

    midi = mido.MidiFile(str(midi_path))

    ticks_per_measure = (
        midi.ticks_per_beat * beats_per_measure
    )

    start_tick = (start_measure - 1) * ticks_per_measure
    end_tick = end_measure * ticks_per_measure
    duration = end_tick - start_tick

    for track_index, track in enumerate(midi.tracks):
        absolute_events = []
        tick = 0

        for msg in track:
            tick += msg.time
            if msg.type != "end_of_track":
                absolute_events.append((tick, msg.copy()))

        selected = []
        active_notes = {}
        setup_events = {}

        # Determine which notes are already sounding at start_tick.
        for tick, msg in absolute_events:
            if tick >= start_tick:
                break

            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                active_notes.setdefault(key, []).append(
                    msg.velocity
                )

            elif msg.type in ("note_off", "note_on"):
                if msg.type == "note_off" or msg.velocity == 0:
                    key = (msg.channel, msg.note)
                    velocities = active_notes.get(key, [])
                    if velocities:
                        velocities.pop(0)
                        if not velocities:
                            del active_notes[key]

            # Preserve the most recent channel setup.
            if msg.type in (
                "program_change",
                "control_change",
                "pitchwheel",
            ):
                key = (
                    msg.type,
                    getattr(msg, "channel", None),
                    getattr(msg, "control", None),
                )
                setup_events[key] = msg.copy(time=0)

            # Preserve current tempo and time signature.
            if msg.type in ("set_tempo", "time_signature"):
                setup_events[(msg.type,)] = msg.copy(time=0)

        # Restore setup at the beginning of the excerpt.
        for msg in setup_events.values():
            selected.append((0, 0, msg))

        # Restart notes held across the start boundary.
        for (channel, note), velocities in active_notes.items():
            for velocity in velocities:
                selected.append(
                    (
                        0, 2,
                        mido.Message(
                            "note_on",
                            channel=channel,
                            note=note,
                            velocity=velocity,
                            time=0,
                        ),
                    )
                )

        # Keep events within the requested measures.
        for tick, msg in absolute_events:
            if start_tick <= tick < end_tick:
                selected.append(
                    (tick - start_tick, 2, msg)
                )

        # Reconstruct active notes to ensure they end.
        sounding = {}

        for tick, _, msg in sorted(
            selected,
            key=lambda item: (item[0], item[1])
        ):
            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                sounding[key] = sounding.get(key, 0) + 1

            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if sounding.get(key, 0) > 0:
                    sounding[key] -= 1

        for (channel, note), count in sounding.items():
            for _ in range(count):
                selected.append(
                    (
                        duration, 3,
                        mido.Message(
                            "note_off",
                            channel=channel,
                            note=note,
                            velocity=0,
                            time=0,
                        ),
                    )
                )

        # Rebuild the MIDI track using delta times.
        selected.sort(key=lambda item: (item[0], item[1]))

        rebuilt = mido.MidiTrack()
        previous_tick = 0

        for tick, _, msg in selected:
            msg.time = tick - previous_tick
            rebuilt.append(msg)
            previous_tick = tick

        rebuilt.append(
            mido.MetaMessage(
                "end_of_track",
                time=duration - previous_tick,
            )
        )

        midi.tracks[track_index] = rebuilt

    midi.save(str(midi_path))

    print(
        f"Selected measures {start_measure}-{end_measure} "
        f"({beats_per_measure}/4)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        'score',
        help='Score name (e.g. emmanuel) or ABC path'
    )

    parser.add_argument(
        '--ignore',
        nargs='+',
        default=[],
        metavar='VOICE',
        help='Ignore ABC voices by display name'
    )

    parser.add_argument(
        '--sustain',
        type=float,
        metavar='BEATS',
        help='Cycle sustain pedal every BEATS quarter notes (e.g. 2)'
    )

    parser.add_argument(
        '--measure',
        nargs=2,
        type=int,
        metavar=('START', 'END'),
        help='Include only measures START through END',
    )
    args = parser.parse_args()

    if args.sustain is not None and args.sustain <= 0:
        parser.error('--sustain must be greater than zero')

    converter = shutil.which('abc2midi')
    if not converter:
        parser.error('abc2midi not found on PATH')

    input_path = Path(args.score)

    if not input_path.suffix:
        input_path = SCORES / (args.score + '.abc')

    if not input_path.is_file():
        parser.error(f'ABC score not found: {input_path}')

    OUT.mkdir(parents=True, exist_ok=True)
    output_path = OUT / (input_path.stem + '.mid')

    source = input_path.read_text(encoding='utf-8')
    source = filter_voices(source, args.ignore)

    # Disable automatic chord accompaniment and staggered chord attacks.
    source, count = re.subn(
        r'(?m)^(?=K:)',
        '%%MIDI gchordoff\n%%MIDI chordattack 0\n',
        source,
        count=1,
    )

    if not count:
        parser.error('ABC score has no K: field')

    with tempfile.TemporaryDirectory(
        prefix='music-compose-'
    ) as tmp:
        abc_path = Path(tmp) / input_path.name
        abc_path.write_text(source, encoding='utf-8')

        subprocess.run(
            [
                converter,
                str(abc_path),
                '-o',
                str(output_path)
            ],
            check=True
        )

    normalize_overlay_velocities(output_path, source)

    if args.measure:
        select_measures(
            output_path,
            args.measure[0],
            args.measure[1],
        )

    if args.sustain is not None:
        add_sustain_pedal(output_path, args.sustain)

    print(f'Wrote {output_path}')
    if args.sustain is not None:
        add_sustain_pedal(output_path, args.sustain)

    print(f'Wrote {output_path}')


if __name__ == '__main__':
    main()
