# Music Score Movie — Functional Requirements

**Status:** Final functional requirements  
**Program:** `scripts/movie.py`

## 1. Purpose

Generate a **silent MP4** that serves as a visual performance guide. An engraved musical score scrolls horizontally from right to left beneath a stationary reading guide. The video establishes a reproducible performance timeline and eliminates page turns.

**Primary design principle: minimize visual distraction.** The musician should be able to keep their gaze near a fixed position while upcoming notation moves into view.

## 2. Inputs and output

### 2.1 Inputs

All musical and engraving inputs must originate from the **same ABC score**:

- **ABC score:** Source for musical notation, voices, staves, and measure structure.
- **Engraved SVG:** Visual score produced by `scripts/engrave.js` using `abc2svg`.
- **Engraving footprint JSON:** Exact SVG-coordinate geometry for noteheads and measures, produced by `scripts/engrave.js`.
- **MIDI file:** Required timing source, produced from the ABC score by the project's ABC-to-MIDI conversion. Its note events and tempo map determine elapsed times, including tempo changes.

The movie generator must not independently reinterpret ABC tempo directives to establish playback timing. MIDI note events must correspond to the engraved score, including when a measure range is selected.

### 2.2 Output

- One silent MP4 containing the continuously scrolling engraved score and stationary reading guide.
- No synthesized audio or recorded performance footage.
- By default, the entire composition is rendered; an optional measure range limits the portion included.

## 3. Score display

### 3.1 Viewport and measure count

- Present the score as one continuous horizontal sequence of measures, with all relevant staves visible and vertically aligned.
- The target number of measures visible at once is configurable by `--measures`, **default 4**.
- At the default setting, the intended composition is approximately one preceding measure, the active measure, and two upcoming measures.
- Place the stationary reading guide approximately **25% from the left edge** of the video.
- The visible measure count is a **layout target**, not a rigid count: engraved measures have different widths. Do not continuously rescale the score to force exactly that many complete measures into view.
- Scale notation uniformly, without stretching or changing its internal spacing.

### 3.2 Stationary reading guide

- Show a fixed, very lightly tinted vertical band spanning the score's staves.
- Draw a thin vertical **hairline at the center** of the band, like the cursor on a slide rule.
- Keep the guide fixed on screen throughout playback.
- Keep the band's width essentially constant, informed by typical engraved spacing for the ABC default note length (for example, `L:1/4`), rather than changing it per note.
- Avoid flashing, pulsing, moving highlights, or per-note highlight rectangles.

### 3.3 Note-on alignment and scrolling

- A note onset is aligned when the stationary hairline intersects the **horizontal center of its engraved notehead ellipse**.
- Scroll the score **right to left** beneath the guide.
- Preserve the engraved horizontal distances between notes; do not redistribute them to enforce constant speed.
- Treat simultaneous note onsets across voices and staves as one musical time point. The engraving already establishes their alignment; no independent staff-by-staff repositioning is required.
- Between consecutive distinct onsets, scroll linearly at constant speed:

  \[
  v_i = \frac{x_{i+1} - x_i}{t_{i+1} - t_i}
  \]

  Here `x` is the notehead-center position in the assembled continuous score and `t` is the onset time derived from MIDI. The score moves left at this speed.
- Speed may change at onset boundaries. **Exact onset timing takes priority over uniform scrolling speed.** Do not apply easing between onsets.
- Rests are reflected in the elapsed MIDI time between onsets and do not require a separate scrolling mode.
- Avoid visual jumps, page turns, or resets at measure or engraved-system boundaries.

### 3.4 Measure-based score assembly

- Assemble engraved measures in musical order into a single continuous horizontal score, **independently of how many systems the source SVG contains**.
- Extend the existing footprint mechanism in `scripts/engrave.js` to emit **measure footprints** alongside note footprints.
- Each measure footprint must provide its exact bounding rectangle in the SVG coordinate system, its musical measure number, and sufficient geometry to position the measure and align corresponding staves. The source system association may be recorded as metadata, but must not determine the movie assembly algorithm.
- Measure boundaries describe the allocated musical layout, not merely the outermost visible glyphs.
- Use the ordered measure footprints as the sole basis for extracting, translating, and concatenating measure graphics, preserving their engraved widths and internal spacing. The same algorithm applies to a one-system engraving and a multi-system engraving.
- Maintain continuous, precisely aligned staff lines across joined measures. Preserve all rendered graphics within each measure rectangle, including ledger lines, ties, and slurs. Artwork split by an original engraved system break may be evaluated if it causes a visible artifact; no special reconstruction is required initially.
- Translate notehead coordinates into the assembled score's coordinate system for synchronization with MIDI.
- A future single-system engraving mode must be usable without changes to `movie.py`.

## 4. Timing, count-in, and measure range

### 4.1 MIDI timing

- The **MIDI file is authoritative for note-on times and tempo changes**.
- Account for the MIDI tempo map when converting musical ticks to elapsed seconds.
- Each video frame represents a deterministic time on that timeline.
- The score remains visually synchronized with the scheduled onsets, even across tempo changes.

### 4.2 Count-in

- If the selected portion begins with a rest, use that opening rest as the count-in; **do not add an extra measure**.
- Otherwise, prepend **one full measure of silent count-in**, using the applicable opening meter and tempo.
- During an inserted count-in, display the score stationary; begin normal scrolling at the first scheduled onset.
- Apply this rule to the beginning of the selected measure range, or to the beginning of the piece when no range is specified.

### 4.3 Measure-range export

- `--range START-END` selects an **inclusive** sequence of measures for the output video.
- If omitted, render the **entire composition**.
- Preserve the original musical tempo and timing within the selected range, including tempo changes established before its start.
- Use the same measure-numbering convention and range semantics as the ABC-to-MIDI conversion. Standardize that converter's existing `--measures` range option to `--range` separately, with a compatibility alias if needed.
- `--range` determines **which measures are rendered**; `--measures` determines **how many measures are targeted on screen**. These are independent options.

## 5. Video format and command-line interface

- Produce a landscape **16:9** MP4 by default, to maximize the number of horizontally visible notes.
- Default resolution: **1920 × 1080 (Full HD)**.
- Support selectable standard landscape resolutions, initially **1280 × 720**, **1920 × 1080**, and **3840 × 2160**.
- Default frame rate: **30 frames per second**.
- Preserve notation proportions and the relative position of the stationary guide at every supported resolution.

Illustrative command-line usage (exact argument names for resolution and frame rate may be finalized during implementation):

```bash
# Entire composition; approximately four measures visible
python scripts/movie.py score.abc

# Measures 9–16; approximately four measures visible
python scripts/movie.py score.abc --range 9-16

# Measures 9–16; approximately six measures visible
python scripts/movie.py score.abc --range 9-16 --measures 6
```

## 6. Visual stability and engraving fidelity

- Favor predictable, minimally distracting motion over decorative animation.
- Keep the reading guide stationary and the score's vertical alignment stable.
- Preserve the engraver's note spacing, symbols, staff relationships, and scale proportions.
- Use SVG geometry to establish spatial positions and MIDI timing to establish the rate of horizontal travel.
- Do not add independent note highlights or effects that compete with the fixed guide.
