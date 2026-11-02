
'use strict';

// ------------------------------------------------------------
// Configuration
// ------------------------------------------------------------

const fs = require('fs');
const path = require('path');
const { Abc } = require('abc2svg/abc2svg-1');

const SYSTEM_GAP = 40;
const showFootprint = process.argv.includes('--show-footprint');

// Project root is the parent of the scripts directory.
const projectRoot = path.resolve(__dirname, '..');

if (!process.argv[2]) {
  console.error('Usage: node scripts/engrave.js <file.abc>');
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

      // Original annotation rectangle from abc2svg.
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

if (!events.length) {
  throw new Error(
    'No note annotations produced. ' +
    'Check that the ABC contains music and that ' +
    'the tune is not terminated prematurely.'
  );
}

// ------------------------------------------------------------
// 2. Parse and stack SVG fragments
//
// Preserve the original SVG root class so that abc2svg's
// font assignments are retained.
// ------------------------------------------------------------

let width = 0;
let totalHeight = 0;

let definitions = '';
let rootClass = '';

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

  // Preserve only the first fragment's definitions.
  if (system === 0) {

    // Capture the root class assigned by abc2svg.
    const rootTag = svg.match(/<svg\b[^>]*>/);

    if (!rootTag) {
      throw new Error('Missing SVG root element');
    }

    const classMatch = rootTag[0].match(
      /\bclass=["']([^"']+)["']/
    );

    rootClass = classMatch ? classMatch[1] : 'tune0';

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

  // Add a gap before every system except the first.
  if (system > 0) {
    totalHeight += SYSTEM_GAP;
  }

  const yOffset = totalHeight;

  systems.push({
    system,
    width: w,
    height: h,
    y_offset: yOffset
  });

  groups.push(
    `<g data-system="${system}" ` +
    `transform="translate(0, ${yOffset})">\n` +
    body +
    '\n</g>'
  );

  // Advance to the bottom of the current system.
  totalHeight += h;
});

// ------------------------------------------------------------
// 3. Convert annotation rectangles to combined SVG coordinates
//
// No notehead extraction or matching is required.
// ------------------------------------------------------------

for (const event of events) {
  const system = systems[event.system];

  if (!system) {
    throw new Error(
      `Invalid system for annotation at ` +
      `source offset ${event.source_start}`
    );
  }

  const values = [
    event.x_annotation,
    event.y_annotation,
    event.width,
    event.height
  ];

  if (!values.every(Number.isFinite)) {
    throw new Error(
      `Invalid annotation rectangle at ` +
      `source offset ${event.source_start}`
    );
  }

  if (event.width < 0 || event.height < 0) {
    throw new Error(
      `Negative annotation dimensions at ` +
      `source offset ${event.source_start}`
    );
  }

  event.x = event.x_annotation;
  event.y = event.y_annotation + system.y_offset;

  event.system_y_offset = system.y_offset;

  event.bbox = {
    x: event.x,
    y: event.y,
    width: event.width,
    height: event.height
  };
}

// Preserve ABC source ordering.
events.sort(
  (a, b) => a.source_start - b.source_start
);

// ------------------------------------------------------------
// Optional diagnostic overlay: note annotation footprints
// ------------------------------------------------------------

const footprintOverlay = showFootprint
  ? `<g id="note-footprints"
       fill="none"
       stroke="red"
       stroke-width="0.8"
       pointer-events="none">
${events.map(event => `
  <rect
    x="${event.bbox.x}"
    y="${event.bbox.y}"
    width="${event.bbox.width}"
    height="${event.bbox.height}"
  />`).join('\n')}
</g>`
  : '';

// ------------------------------------------------------------
// 4. Generate combined SVG
// ------------------------------------------------------------

const output = `<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 viewBox="0 0 ${width} ${totalHeight}"
 width="${width}px" height="${totalHeight}px"
 fill="currentColor" stroke-width=".7" class="${rootClass}">
 
${definitions}
${groups.join('\n')}
${footprintOverlay}

</svg>
`;

// ------------------------------------------------------------
// 5. Write outputs
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
