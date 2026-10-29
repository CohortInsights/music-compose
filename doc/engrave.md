
# engrave.js — ABC Music Engraving

## Overview

`engrave.js` converts a musical score written in ABC notation into a single SVG image and a corresponding JSON file containing note positions.

The script uses the `abc2svg` engraving library to render conventional musical notation. It also extracts note annotations and matches them to the actual rendered noteheads, allowing other programs to locate individual notes precisely within the SVG.

The script is part of the `music-compose` project and provides the engraving foundation for future score animation and synchronization.

## Project Structure

```text
music-compose/
├── js/
│   └── engrave.js
├── doc/
│   └── engrave.md
├── out/
│   ├── amazing-grace.svg
│   └── amazing-grace.json
├── amazing-grace.abc
├── package.json
└── package-lock.json
```

The `out/` directory is created automatically if it does not exist.

## Usage

From the project root:

```bash
node js/engrave.js amazing-grace.abc
```

The script accepts a single command-line argument specifying the input ABC file.

Input paths are resolved relative to the current working directory. Output files are always written to the project's `out/` directory.

For example:

**Input**

`amazing-grace.abc`

**Outputs**

- `out/amazing-grace.svg`
- `out/amazing-grace.json`

The output filenames are derived from the input filename, without its `.abc` extension.

The script can also be executed from a PyCharm Node.js run configuration, with the working directory set to the `music-compose` project root.

## Dependencies

- Node.js (validated with version 22.23.4)
- `abc2svg` (validated with version 1.22.1)

Install dependencies from the project root:

```bash
npm install
```

For a new project without a package manifest:

```bash
npm init -y
npm install abc2svg@1.22.1
```

## Processing Pipeline

### 1. Read the ABC source

The script reads the specified `.abc` file into memory.

The original source text is retained so each note annotation can be associated with its corresponding ABC substring.

### 2. Engrave the score

The script invokes:

```javascript
new Abc(user).tosvg(input, source);
```

The `abc2svg` library uses callbacks to provide SVG fragments and engraving annotations.

Two callbacks are particularly important:

**`img_out(svg)`**

Receives an SVG fragment representing a rendered portion of the score. Each fragment is assigned a sequential system number.

**`anno_start(type, start, stop, x, y, w, h)`**

Provides engraving annotations, including source positions and approximate graphical bounds.

The script retains annotations whose type is `note`.

Each annotation records the corresponding ABC source substring, source offsets, and original annotation geometry.

### 3. Combine SVG systems

`abc2svg` may generate multiple SVG fragments for a score.

The script combines these fragments into a single vertically stacked SVG.

For each fragment, it:

1. Extracts its width and height from the SVG `viewBox`.
2. Preserves the music font, styling, and graphical definitions required for rendering.
3. Removes duplicated definitions from individual system bodies.
4. Wraps the system in an SVG `<g>` element.
5. Applies a vertical translation equal to the combined height of preceding systems.

The resulting SVG has a single coordinate system.

The overall width is the maximum width of the individual systems. The overall height is the sum of their heights.

### 4. Extract rendered notehead positions

The coordinates supplied by `anno_start` do not correspond exactly to the positions of the visible noteheads.

To obtain accurate positions, the script examines the SVG text elements produced by `abc2svg`.

Musical symbols are represented using font glyphs. Some SVG text elements contain comma-separated X and Y coordinate arrays, with one coordinate pair per glyph.

The script identifies the two notehead glyphs used in the validated test score:

```javascript
'\uE0A4'
'\uE0A3'
```

It extracts their coordinates, adds the appropriate system vertical offset, and sorts the noteheads from left to right within each system.

These are the rendered coordinates used for subsequent processing.

### 5. Match annotations to noteheads

For each system, the script matches the original ABC note annotations to the extracted rendered noteheads in sequence.

It verifies that the number of annotations equals the number of rendered noteheads.

If the counts differ, the script throws an error rather than producing potentially incorrect coordinate data.

For each successful match, the event receives:

- `x`: rendered notehead X coordinate
- `y`: rendered notehead Y coordinate in the combined SVG
- `system`: zero-based system number
- `system_y_offset`: vertical position of the system

The original ABC source information and annotation geometry are preserved.

The completed event list is sorted by ABC source position.

### 6. Write output files

After all systems pass validation, the script writes:

**SVG file**

`out/<file>.svg`

Contains the complete engraved score as one vertically stacked SVG image.

**JSON file**

`out/<file>.json`

Contains the note events, their ABC source references, original annotation geometry, system assignments, and calibrated rendered coordinates.

## JSON Event Structure

A note event has the following structure:

```json
{
  "type": "note",
  "source_start": 120,
  "source_end": 122,
  "abc": "D2",
  "x_annotation": 34.5,
  "y_annotation": -25,
  "width": 13.4,
  "height": 6,
  "system": 1,
  "x": 94.6,
  "y": 186,
  "system_y_offset": 139
}
```

*The numeric values above are illustrative.*

The important distinction is between the original annotation coordinates (`x_annotation`, `y_annotation`) and the calibrated rendered coordinates (`x`, `y`).

Downstream applications should use `x` and `y` when positioning graphical elements over the SVG score.

## Validation

The implementation was validated using an Amazing Grace test score containing 91 notes across four systems.

| System | Note events | Rendered noteheads | Vertical offset |
|---|---:|---:|---:|
| 0 | 23 | 23 | 0 |
| 1 | 23 | 23 | 139 |
| 2 | 22 | 22 | 209 |
| 3 | 23 | 23 | 279 |
| **Total** | **91** | **91** | |

The combined SVG dimensions were 794 × 349.

Visual verification using red markers confirmed that the extracted coordinates aligned with the displayed noteheads across all four systems.

Correct rendering of the musical symbols was also verified.

## Current Limitations

The coordinate-matching implementation has been validated for a single melodic voice with one notehead per note annotation.

It assumes that note annotations and rendered noteheads appear in corresponding left-to-right order within each system.

Additional work may be required for:

- Chords containing multiple noteheads per event
- Multiple simultaneous voices
- Grace notes and other special notation
- Additional notehead glyph types
- Complex notation where source order and horizontal position differ

The script does not currently calculate musical timing, note onset times, or durations.

## Future Development

The next planned component will calculate musical timing from ABC notation and associate each note with a start time and duration.

That timing data can be combined with the calibrated SVG coordinates to support a cursor or other graphical indicator that follows the score during playback.

The engraving component should remain independent of the timing and animation components, allowing each to be developed and tested separately.
