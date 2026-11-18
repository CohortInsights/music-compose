
'use strict';

const fs = require('fs');
const path = require('path');
const { Abc } = require('abc2svg/abc2svg-1');
const coordinates = require('./svg-coordinates');

const showFootprint = process.argv.includes('--show-footprint');
const showMeasures = process.argv.includes('--show-measures');
const showNoteheads = process.argv.includes('--show-noteheads');

const projectRoot = path.resolve(__dirname, '..');

if (!process.argv[2]) {
  console.error(
    'Usage: node scripts/engrave.js <file.abc> ' +
    '[--show-footprint] [--show-measures] [--show-noteheads]'
  );
  process.exit(1);
}

const input = path.resolve(process.argv[2]);

if (path.extname(input).toLowerCase() !== '.abc') {
  throw new Error('Input file must have an .abc extension');
}

const source = fs.readFileSync(input, 'utf8');
const baseName = path.basename(input, '.abc');
const outputDir = path.join(projectRoot, 'out');

fs.mkdirSync(outputDir, { recursive: true });

const outputSvg = path.join(outputDir, `${baseName}.svg`);
const outputEvents = path.join(outputDir, `${baseName}.json`);
const outputMeasures = path.join(outputDir, `${baseName}-measures.json`);
const outputNoteheads = path.join(outputDir, `${baseName}-noteheads.json`);

// ------------------------------------------------------------
// 1. Engrave ABC and collect annotations
// ------------------------------------------------------------

const chunks = [];
const events = [];
const errors = [];
const noteheads = [];

let pendingNoteheads = [];
let pending = [];
let abc;

// Musical-model information collected independently of SVG
// fragments. This distinguishes real musical changes from
// notation repeated automatically at system boundaries.

const modelChanges = [];
const modelNotes = [];
const modelStaffs = new Set();

function symbolStaff(symbol) {
  return Number.isInteger(symbol.st) ? symbol.st : 0;
}

function symbolSource(symbol) {
  return Number.isInteger(symbol.istart)
    ? symbol.istart
    : null;
}

function symbolTime(symbol) {
  return Number.isFinite(symbol.time)
    ? symbol.time
    : null;
}

function keySignature(symbol) {
  if (!Number.isInteger(symbol.k_sf)) return null;

  return JSON.stringify({
    sharps: symbol.k_sf,
    accidentals: symbol.k_a_acc || []
  });
}

function clefSignature(symbol) {
  if (typeof symbol.clef_type !== 'string') return null;

  return JSON.stringify({
    type: symbol.clef_type,
    line: symbol.clef_line ?? null,
    octave: symbol.clef_octave ?? 0
  });
}

function collectMusicalModel(tsfirst, voiceTable) {
  // Traverse the time-ordered musical symbols. These represent
  // the parsed score, not the extra symbols introduced when
  // abc2svg begins a new engraved system.

  const staffStates = new Map();
  const seen = new Set();

  let symbol = tsfirst;

  while (symbol && !seen.has(symbol)) {
    seen.add(symbol);

    const staff = symbolStaff(symbol);
    const position = symbolSource(symbol);
    const time = symbolTime(symbol);

    modelStaffs.add(staff);

    if (!staffStates.has(staff)) {
      staffStates.set(staff, {
        clef: null,
        key: null
      });
    }

    const state = staffStates.get(staff);

    // Note symbols contain a notes array. Their source
    // positions help associate musical changes with measures.

    if (Array.isArray(symbol.notes) && position !== null) {
      modelNotes.push({
        source_start: position,
        time,
        staff
      });
    }

    const clef = clefSignature(symbol);

    if (clef !== null) {
      if (
        state.clef !== null &&
        state.clef !== clef &&
        position !== null
      ) {
        modelChanges.push({
          kind: 'clef',
          staff,
          source_start: position,
          time
        });
      }

      state.clef = clef;
    }

    const key = keySignature(symbol);

    if (key !== null) {
      if (
        state.key !== null &&
        state.key !== key &&
        position !== null
      ) {
        modelChanges.push({
          kind: 'key',
          staff,
          source_start: position,
          time
        });
      }

      state.key = key;
    }

    symbol = symbol.ts_next;
  }

  // Also account for staffs declared in the voice table.
  if (Array.isArray(voiceTable)) {
    for (const voice of voiceTable) {
      if (voice && Number.isInteger(voice.st)) {
        modelStaffs.add(voice.st);
      }
    }
  }
}

const user = {
  read_file: () => null,

  errmsg: message => errors.push(message),

  get_abcmodel: (tsfirst, voiceTable) => {
    collectMusicalModel(tsfirst, voiceTable);
  },

  anno_start: (type, start, stop, x, y, w, h) => {
    if (type !== 'note') return;

    const event = {
      type,
      source_start: start,
      source_end: stop,
      abc: source.slice(start, stop),

      x_annotation: x,
      y_annotation: y,
      width: w,
      height: h,

      fragment_bbox: coordinates.annotationToSvg(
        abc, x, y, w, h
      )
    };

    events.push(event);
    pending.push(event);
  },

  img_out: svg => {
    const system = chunks.length;

    chunks.push(svg);

    for (const event of pending) {
      event.system = system;
    }

    pending = [];

    for (const head of pendingNoteheads) {
      head.system = system;
    }

    pendingNoteheads = [];
  }
};

abc = new Abc(user);

// abc2svg calls psxygl with the exact engraved notehead
// position, including chord displacement, grace-note offset,
// and staff placement.
//
// Returning false preserves normal rendering.

abc.psxygl = (x, y, glyph) => {
  const head = {
    x: abc.sx(x),
    y_fragment: abc.sy(y),
    glyph
  };

  noteheads.push(head);
  pendingNoteheads.push(head);

  return false;
};

abc.tosvg(input, source);

if (errors.length) {
  throw new Error(
    'Engraving errors:\n' + errors.join('\n')
  );
}

if (!chunks.length) {
  throw new Error('No SVG fragments produced');
}

if (pendingNoteheads.length) {
  throw new Error(
    'Noteheads remained after the final SVG fragment'
  );
}

if (pending.length) {
  throw new Error(
    `${pending.length} note annotations remained ` +
    'after the final SVG fragment'
  );
}

if (!events.length) {
  throw new Error(
    'No note annotations produced. ' +
    'Check that the ABC contains music and that ' +
    'the tune is not terminated prematurely.'
  );
}

// ------------------------------------------------------------
// 2. Calculate system layout and event coordinates
// ------------------------------------------------------------

const { systems, width, totalHeight } =
  coordinates.layoutSystems(chunks);

coordinates.placeEvents(events, systems);

const measures = coordinates.extractMeasures(chunks, systems);

for (const head of noteheads) {
  head.y =
    head.y_fragment + systems[head.system].y_offset;

  delete head.y_fragment;
}

// Preserve ABC source ordering.

events.sort(
  (a, b) => a.source_start - b.source_start
);

// ------------------------------------------------------------
// 3. Associate musical changes with measure footprints
// ------------------------------------------------------------

function eventMeasure(event) {
  const bbox = event.bbox || event.fragment_bbox;

  if (!bbox) return null;

  const x = bbox.x + bbox.width / 2;

  const y = event.bbox
    ? bbox.y + bbox.height / 2
    : bbox.y + bbox.height / 2 +
      systems[event.system].y_offset;

  const candidates = measures.filter(
    measure => measure.system === event.system
  );

  // Find the measure containing the note's horizontal center.
  // Use the nearest measure as a fallback for notes whose
  // annotation extends slightly beyond a barline.

  let best = null;
  let bestDistance = Infinity;

  for (const measure of candidates) {
    const left = measure.x;
    const right = measure.x + measure.width;

    const distance =
      x < left ? left - x :
      x > right ? x - right :
      0;

    if (distance < bestDistance) {
      bestDistance = distance;
      best = measure;
    }
  }

  return best;
}

function annotateMeasureChanges() {
  const staffCount = Math.max(
    1,
    ...Array.from(modelStaffs, staff => staff + 1)
  );

  for (const measure of measures) {
    measure.preserve_clef = Array(staffCount).fill(false);
    measure.preserve_key = Array(staffCount).fill(false);
  }

  if (!modelChanges.length) return;

  // Associate engraved note events with their measures.

  const noteLocations = events
    .map(event => ({
      source_start: event.source_start,
      measure: eventMeasure(event)
    }))
    .filter(item => item.measure !== null)
    .sort((a, b) => a.source_start - b.source_start);

  // For each parsed musical change, locate the next engraved
  // note in ABC source order. The change belongs to that
  // note's measure.

  for (const change of modelChanges) {
    const nextNote = noteLocations.find(
      item => item.source_start >= change.source_start
    );

    if (!nextNote) continue;

    const measure = nextNote.measure;

    if (change.kind === 'clef') {
      measure.preserve_clef[change.staff] = true;
    } else if (change.kind === 'key') {
      measure.preserve_key[change.staff] = true;
    }
  }
}

annotateMeasureChanges();

// ------------------------------------------------------------
// 4. Assemble SVG fragments
// ------------------------------------------------------------

let definitions = '';
let rootClass = '';

const groups = [];

chunks.forEach((svg, system) => {
  const rootTag = svg.match(/<svg\b[^>]*>/);

  if (!rootTag) {
    throw new Error(
      `Missing SVG root element in fragment ${system + 1}`
    );
  }

  let body = svg
    .replace(/^[\s\S]*?<svg\b[^>]*>/, '')
    .replace(/<\/svg>[\s\S]*$/, '');

  if (system === 0) {
    const classMatch = rootTag[0].match(
      /\bclass=["']([^"']+)["']/
    );

    rootClass = classMatch ? classMatch[1] : 'tune0';
  }

  // Collect styles and definitions from every SVG fragment.

  const styles = body.match(
    /<style\b[^>]*>[\s\S]*?<\/style>/g
  ) || [];

  const defs = body.match(
    /<defs\b[^>]*>[\s\S]*?<\/defs>/g
  ) || [];

  definitions += '\n' + [...styles, ...defs].join('\n');

  // Definitions belong only in the combined SVG root.

  body = body
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, '')
    .replace(/<defs\b[^>]*>[\s\S]*?<\/defs>/g, '');

  groups.push(
    `<g data-system="${system}" ` +
    `transform="translate(0, ${systems[system].y_offset})">\n` +
    body +
    '\n</g>'
  );
});

// ------------------------------------------------------------
// 5. Optional diagnostic overlays
// ------------------------------------------------------------

const overlay = [
  showFootprint
    ? coordinates.footprintOverlay(events)
    : '',

  showMeasures
    ? coordinates.measureFootprintOverlay(measures)
    : '',

  showNoteheads
    ? coordinates.noteheadOverlay(noteheads)
    : ''
].join('\n');

// ------------------------------------------------------------
// 6. Generate combined SVG
// ------------------------------------------------------------

const output = `<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  viewBox="0 0 ${width} ${totalHeight}"
  width="${width}px" height="${totalHeight}px"
  fill="currentColor" stroke-width=".7" class="${rootClass}">
${definitions}
${groups.join('\n')}
${overlay}
</svg>`;

// ------------------------------------------------------------
// 7. Write outputs
// ------------------------------------------------------------

fs.writeFileSync(
  outputSvg,
  output,
  'utf8'
);

fs.writeFileSync(
  outputEvents,
  JSON.stringify(events, null, 2),
  'utf8'
);

fs.writeFileSync(
  outputMeasures,
  JSON.stringify(measures, null, 2),
  'utf8'
);

fs.writeFileSync(
  outputNoteheads,
  JSON.stringify(noteheads, null, 2),
  'utf8'
);

console.log(
  `Wrote ${outputSvg} (${width} x ${totalHeight})`
);

console.log(
  `Wrote ${outputEvents} ` +
  `(${events.length} notes, ${systems.length} systems)`
);

console.log(
  `Wrote ${outputMeasures} (${measures.length} measures)`
);

console.log(
  `Wrote ${outputNoteheads} (${noteheads.length} noteheads)`
);

console.log(
  `Detected ${modelChanges.length} parsed clef/key changes`
);

console.table(
  systems.map(s => ({
    system: s.system,
    width: s.width,
    height: s.height,
    y_offset: s.y_offset
  }))
);
