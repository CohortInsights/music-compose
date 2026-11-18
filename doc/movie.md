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
- **Engraving geometry JSON:** Exact SVG-coordinate geometry for noteheads, musical events, and measures, produced by `scripts/engrave.js`.
- **MIDI file:** Required timing source, produced from the ABC score by the project's ABC-to-MIDI conversion. Its note events and tempo map determine elapsed times, including tempo changes.

The movie generator must not independently reinterpret ABC tempo directives to establish playback timing. MIDI note events must correspond to the engraved score, including when a measure range is selected.

The engraving output must distinguish between individual engraved noteheads and musical events. Musical event positions are used for scrolling synchronization; notehead positions are retained for engraving diagnostics.

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

### 3.3 Musical events and scrolling anchors

The scrolling timeline is based on **musical events**, not individual rendered noteheads.

- **Single notes:** Use the horizontal position of the parent musical symbol (`symbol.x`) as the scrolling anchor.
- **Chords:** Treat the entire chord as one event at its parent symbol's horizontal position. Ignore individual notehead displacements (`note.shhd`), including displacements introduced to avoid overlapping noteheads.
- **Grace notes:** Exclude grace notes from the scrolling event timeline. Preserve their notation in the rendered score.
- **Simultaneous notes:** Treat simultaneous note onsets across voices and staves as one musical time point. Use one shared scrolling anchor rather than separate anchors for each note or voice.
- **Rests:** Preserve their contribution to musical time, but do not require separate scrolling anchors. The elapsed MIDI time between note onsets accounts for rests.

The distinction between noteheads and musical events must be preserved in the engraving output:

| Geometry | Purpose | Horizontal position |
|---|---|---|
| Noteheads | Engraving diagnostics and visual verification | Actual engraved notehead center, including displacement |
| Musical events | MIDI synchronization and score scrolling | Parent musical symbol's horizontal position, without individual notehead displacement |
| Measures | Score assembly and measure selection | Exact engraved measure boundaries |

The event timeline should contain one synchronization point per distinct musical onset time, with the corresponding horizontal position in the engraved score.

Where simultaneous symbols have slightly different engraved positions because of collision avoidance, they must still produce one scrolling anchor for that musical time. The anchor should be derived from the engraver's symbol alignment rather than from individual notehead geometry.

### 3.4 Note-on alignment and scrolling

- A musical onset is aligned when the stationary hairline intersects its **musical event anchor**, rather than necessarily the geometric center of every engraved notehead.
- Scroll the score **right to left** beneath the guide.
- Preserve the engraved horizontal distances between musical events; do not redistribute them to enforce constant speed.
- Between consecutive distinct onset times, scroll linearly at constant speed:

  \[
  v_i = \frac{x_{i+1} - x_i}{t_{i+1} - t_i}
  \]

  Here `x` is the musical event's horizontal position in the assembled continuous score and `t` is the onset time derived from MIDI. The score moves left at this speed.
- Speed may change at onset boundaries. **Exact onset timing takes priority over uniform scrolling speed.** Do not apply easing between onsets.
- Rests are reflected in the elapsed MIDI time between onsets and do not require a separate scrolling mode.
- Grace notes move with the engraved score but do not independently affect scrolling speed or event timing.
- Avoid visual jumps, page turns, or resets at measure or engraved-system boundaries.

### 3.5 Measure-based score assembly

- Assemble engraved measures in musical order into a single continuous horizontal score, **independently of how many systems the source SVG contains**.
- Extend the existing footprint mechanism in `scripts/engrave.js` to emit **measure footprints** and **musical event coordinates** alongside notehead footprints.
- Each measure footprint must provide its exact bounding rectangle in the SVG coordinate system, its musical measure number, and sufficient geometry to position the measure and align corresponding staves. The source system association may be recorded as metadata, but must not determine the movie assembly algorithm.
- Measure boundaries describe the allocated musical layout, not merely the outermost visible glyphs.
- Use the ordered measure footprints as the sole basis for extracting, translating, and concatenating measure graphics, preserving their engraved widths and internal spacing. The same algorithm applies to a one-system engraving and a multi-system engraving.
- Maintain continuous, precisely aligned staff lines across joined measures. Preserve all rendered graphics within each measure rectangle, including ledger lines, ties, and slurs. Artwork split by an original engraved system break may be evaluated if it causes a visible artifact; no special reconstruction is required initially.
- Translate musical event coordinates into the assembled score's coordinate system for synchronization with MIDI.
- Retain exact notehead coordinates for diagnostics, but do not use displaced notehead centers as scrolling anchors.
- A future single-system engraving mode must be usable without changes to `movie.py`.

### 3.6 Engraving event data

`scripts/engrave.js` must export sufficient musical event metadata to allow `movie.py` to construct the scrolling timeline without independently interpreting SVG note geometry.

Each event should provide:

- Musical measure number.
- Musical event type.
- Horizontal parent-symbol coordinate in the engraved SVG.
- Source system association for coordinate transformation.
- Musical onset position or equivalent timing information sufficient to associate the event with its corresponding MIDI onset.
- Voice or staff association where needed to resolve simultaneous events.

The event data must distinguish ordinary note and chord events from grace notes.

Musical event positions must come directly from `abc2svg`'s engraving model wherever possible. Avoid estimating event anchors from SVG glyph dimensions, visual notehead centers, or font-specific measurements.

The engraving pipeline should retain separate geometry datasets for:

1. **Noteheads:** Exact rendered notehead positions for diagnostics.
2. **Events:** Parent-symbol positions and musical metadata for synchronization.
3. **Measures:** Exact engraved measure boundaries for assembly.

`movie.py` is responsible for transforming these coordinates into the assembled continuous score and associating musical events with elapsed MIDI times. It should not need to identify chord displacements, grace notes, or notehead glyph geometry.

## 4. Timing, count-in, and measure range

### 4.1 MIDI timing

- The **MIDI file is authoritative for note-on times and tempo changes**.
- Account for the MIDI tempo map when converting musical ticks to elapsed seconds.
- Each video frame represents a deterministic time on that timeline.
- The score remains visually synchronized with the scheduled onsets, even across tempo changes.
- Associate MIDI note-on events with the corresponding engraved musical events, not individual notehead positions.
- Multiple MIDI note-on events belonging to the same chord or simultaneous musical onset share one scrolling time point.
- Excluded grace-note anchors must not introduce additional scrolling synchronization points.

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
- Use musical event anchors rather than displaced notehead centers to establish scrolling positions.
- Preserve all engraved grace notes and chord noteheads visually without allowing their individual positions to alter the scrolling timeline.
- Do not add independent note highlights or effects that compete with the fixed guide.