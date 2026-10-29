
'use strict';

// ------------------------------------------------------------
// Configuration
// ------------------------------------------------------------

const fs = require('fs');
const path = require('path');
const { Abc } = require('abc2svg/abc2svg-1');

// Project root is the parent of the js directory.
const projectRoot = path.resolve(__dirname, '..');

if (!process.argv[2]) {
  console.error('Usage: node js/engrave.js <file.abc>');
  process.exit(1);
}

// Resolve input relative to the current working directory.
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

// ------------------------------------------------------------
// 1. Engrave ABC and collect annotations
// ------------------------------------------------------------

const chunks = [];
const events = [];
const errors = [];

let pending = [];

const user = {
  read_file: () => null,

  errmsg: message => errors.push(message),

  anno_start: (type, start, stop, x, y, w, h) => {
    if (type !== 'note') return;

    const event = {
      type,
      source_start: start,
      source_end: stop,
      abc: source.slice(start, stop),

      // Original annotation coordinates
      x_annotation: x,
      y_annotation: y,
      width: w,
      height: h
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
  }
};

new Abc(user).tosvg(input, source);

if (errors.length) {
  throw new Error(
    'Engraving errors:\n' + errors.join('\n')
  );
}

if (!chunks.length) {
  throw new Error('No SVG fragments produced');
}

if (pending.length) {
  throw new Error(
    `${pending.length} note annotations remained ` +
    'after the final SVG fragment'
  );
}

// ------------------------------------------------------------
// 2. Parse and stack SVG fragments
//
// This section preserves the original working
// engrave-consolidated.js combination behavior.
// ------------------------------------------------------------

let width = 0;
let totalHeight = 0;

let definitions = '';

const groups = [];
const systems = [];

chunks.forEach((svg, system) => {
  const match = svg.match(
    /viewBox=["']\s*0\s+0\s+([\d.]+)\s+([\d.]+)["']/
  );

  if (!match) {
    throw new Error(
      `Missing or unsupported viewBox in ` +
      `SVG fragment ${system + 1}`
    );
  }

  const w = Number(match[1]);
  const h = Number(match[2]);

  if (!(w > 0 && h > 0)) {
    throw new Error(
      `Invalid dimensions in SVG fragment ${system + 1}`
    );
  }

  width = Math.max(width, w);

  let body = svg
    .replace(/^[\s\S]*?<svg\b[^>]*>/, '')
    .replace(/<\/svg>[\s\S]*$/, '');

  // IMPORTANT:
  // Preserve only the first fragment's definitions.
  // This is how our original working script operated.
  if (system === 0) {
    const styles = body.match(
      /<style\b[^>]*>[\s\S]*?<\/style>/g
    ) || [];

    const defs = body.match(
      /<defs\b[^>]*>[\s\S]*?<\/defs>/g
    ) || [];

    definitions = [...styles, ...defs].join('\n');
  }

  // Remove definitions from each system body.
  body = body
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, '')
    .replace(/<defs\b[^>]*>[\s\S]*?<\/defs>/g, '');

  const yOffset = totalHeight;

  systems.push({
    system,
    width: w,
    height: h,
    y_offset: yOffset,
    body
  });

  groups.push(
    `<g data-system="${system}" ` +
    `transform="translate(0, ${yOffset})">\n` +
    body +
    '\n</g>'
  );

  totalHeight += h;
});

// ------------------------------------------------------------
// 3. Extract rendered notehead coordinates
//
// Uses the same glyph-matching approach verified
// in calibrate-events.js.
// ------------------------------------------------------------

// abc2svg notehead glyphs for our test score.
const NOTEHEADS = new Set([
  '\uE0A4',
  '\uE0A3'
]);

function getAttribute(attrs, name) {
  const match = attrs.match(
    new RegExp(`\\b${name}=["']([^"']*)["']`)
  );

  return match ? match[1] : null;
}

function extractNoteheads(system) {
  const positions = [];

  const textRegex =
    /<text\b([^>]*)>([\s\S]*?)<\/text>/g;

  let match;

  while (
    (match = textRegex.exec(system.body)) !== null
  ) {
    const attrs = match[1];
    const content = match[2];

    const xValue = getAttribute(attrs, 'x');
    const yValue = getAttribute(attrs, 'y');

    if (xValue === null || yValue === null) {
      continue;
    }

    const xs = xValue.split(',').map(Number);
    const ys = yValue.split(',').map(Number);

    const chars = Array.from(
      content.replace(/\s+/g, '')
    );

    // Ignore text that is not a positioned
    // musical glyph sequence.
    if (
      xs.length !== chars.length ||
      ys.length !== chars.length
    ) {
      continue;
    }

    for (let i = 0; i < chars.length; i++) {
      if (!NOTEHEADS.has(chars[i])) {
        continue;
      }

      if (
        !Number.isFinite(xs[i]) ||
        !Number.isFinite(ys[i])
      ) {
        throw new Error(
          `Invalid notehead coordinate in ` +
          `system ${system.system}`
        );
      }

      positions.push({
        x: xs[i],
        y: ys[i] + system.y_offset
      });
    }
  }

  // Match noteheads in left-to-right order.
  positions.sort((a, b) => a.x - b.x);

  return positions;
}

// ------------------------------------------------------------
// 4. Calibrate annotation coordinates
// ------------------------------------------------------------

for (const system of systems) {
  const noteheads = extractNoteheads(system);

  const systemEvents = events.filter(
    event => event.system === system.system
  );

  console.log(
    `System ${system.system}: ` +
    `${systemEvents.length} events, ` +
    `${noteheads.length} rendered noteheads`
  );

  if (systemEvents.length !== noteheads.length) {
    throw new Error(
      `Note count mismatch in system ${system.system}: ` +
      `${systemEvents.length} annotations vs. ` +
      `${noteheads.length} rendered noteheads`
    );
  }

  systemEvents.forEach((event, i) => {
    const position = noteheads[i];

    // Final calibrated coordinates in the
    // combined SVG coordinate system.
    event.x = position.x;
    event.y = position.y;

    event.system_y_offset = system.y_offset;
  });
}

// Preserve ABC source ordering.
events.sort(
  (a, b) => a.source_start - b.source_start
);

// ------------------------------------------------------------
// 5. Generate combined SVG
//
// Keep the same root attributes as the original
// working engrave-consolidated.js.
// ------------------------------------------------------------

const output = `<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 viewBox="0 0 ${width} ${totalHeight}"
 width="${width}px" height="${totalHeight}px"
 fill="currentColor" stroke-width=".7" class="f1 tune0">
${definitions}
${groups.join('\n')}
</svg>\n`;

// ------------------------------------------------------------
// 6. Write outputs
// ------------------------------------------------------------

// Write only after every system passes calibration.
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

console.log(
  `Wrote ${outputSvg} (${width} x ${totalHeight})`
);

console.log(
  `Wrote ${outputEvents} ` +
  `(${events.length} notes, ` +
  `${systems.length} systems)`
);

console.table(
  systems.map(s => ({
    system: s.system,
    width: s.width,
    height: s.height,
    y_offset: s.y_offset
  }))
);

