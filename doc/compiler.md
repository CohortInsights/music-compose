# Music Composition Compiler

## Purpose

The compiler translates our extended ABC composition language into standard ABC notation suitable for engraving with `scripts/engrave.js`.

The source language separates musical intent (melody, harmony, accompaniment patterns) from the mechanics of generating notation.

## Project Files

- `scores/emmanuel.abc` — composition source
- `scores/patterns-4-4.abc` — reusable accompaniment patterns
- `scripts/compile.py` — composition compiler (planned)
- `scripts/engrave.js` — existing ABC engraving script
- `out/` — generated output

## Composition Structure

A composition begins with standard ABC headers:

```text
X:1
T:O Come, O Come, Emmanuel
M:4/4
L:1/4
K:Em

#include "patterns-4-4.abc"
```

`#include` is a compiler directive. Paths are relative to the including file. Pattern definitions are loaded before compilation and are not emitted in the resulting ABC.

## Melody, Lyrics, and Harmony

Each `m:` line defines one measure. Associated `w:` and `h:` lines provide lyrics and chord harmony.

```text
m: G3 A |
w: el, and
h: Em Am
```

Standard ABC pitch and duration conventions apply to melody notes.

Each measure must account for its full duration, including rests. For example, `m: z3 E |` places a melody pickup on beat 4 of an otherwise complete 4/4 measure.

## Right Hand

The right hand references the melody:

```text
rh:
  transpose:
    part: melody
    shift: 0
```

A shift of zero preserves the original melody.

## Left Hand

Each `lh:` line defines one complete measure of harmonic targets:

```text
lh: E4
lh: E2 A2
```

With `L:1/4`, `E4` means four beats of E harmony; `E2 A2` means two beats of E followed by two beats of A.

These notes are **harmonic roots**, not literal accompaniment notes. Their durations determine how long an accompaniment pattern is applied.

## Accompaniment Patterns

Patterns are reusable, named, four-beat musical sequences.

```text
pattern Bass-Blk <E>:
    E,2 [EGB]2
    pedal: measure
```

`<E>` specifies the reference pitch used for transposition. Pattern notes specify rhythm, register, and voicing.

The compiler transposes pattern notes diatonically to each target root, preserving rhythmic durations and octave relationships.

The extended pattern notation supports apostrophes for upper octaves, such as `G'` for an octave above `G`. These are translated into standard ABC notation during compilation.

`pedal: measure` requests sustain pedal over the applied pattern duration, using ABC pedal decorations.

## Pattern Application

A pattern is selected with:

```text
pattern: Arp
lh: E4
lh: A2 E2

pattern: Bass-Blk
lh: G4
```

The selected pattern remains active until another `pattern:` directive changes it.

For each harmonic target:

1. Transpose the pattern to the target root.
2. Start the pattern at its beginning.
3. Play only the portion matching the target duration.
4. Truncate any note or chord extending beyond the duration.
5. Restart the pattern for the next target.

For example, `Bass-Blk` applied to `E2` produces `E,2`, while applied to `E4` produces `E,2 [EGB]2`.

## Compilation

The compiler will:

1. Read the source and resolve includes.
2. Parse and validate the composition and pattern definitions.
3. Resolve persistent pattern assignments.
4. Expand left-hand targets into accompaniment notes.
5. Generate standard ABC with separate right- and left-hand staves, including lyrics and pedal markings.
6. Write the compiled ABC for engraving.

## Initial Scope

- 4/4 compositions with `L:1/4`
- Reusable patterns defined in `patterns-4-4.abc`
- Melody-based right hand
- Pattern-generated left hand
- Diatonic transposition
- Pattern truncation and inheritance
- Sustain pedal instructions

The initial test composition is **O Come, O Come, Emmanuel**, covering measures M0–M19.