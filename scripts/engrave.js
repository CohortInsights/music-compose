
'use strict';

const fs = require('fs');
const path = require('path');
const { Abc } = require('abc2svg/abc2svg-1');
const coordinates = require('./svg-coordinates');

const showFootprint = process.argv.includes('--show-footprint');
const projectRoot = path.resolve(__dirname, '..');

if (!process.argv[2]) {
  console.error('Usage: node scripts/engrave.js <file.abc> [--show-footprint]');
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

// ------------------------------------------------------------
// 1. Engrave ABC and collect annotations
// ------------------------------------------------------------

const chunks = [];
const events = [];
const errors = [];

let pending = [];
let abc;

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

      // Original abc2svg annotation rectangle.
      x_annotation: x,
      y_annotation: y,
      width: w,
      height: h,

      // Convert before abc2svg changes its drawing state.
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
  }
};

abc = new Abc(user);
abc.tosvg(input, source);

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
// 2. Calculate system layout and event coordinates
// ------------------------------------------------------------

const { systems, width, totalHeight } =
  coordinates.layoutSystems(chunks);

coordinates.placeEvents(events, systems);

// Preserve ABC source ordering.
events.sort(
  (a, b) => a.source_start - b.source_start
);

// ------------------------------------------------------------
// 3. Assemble SVG fragments
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

    const styles = body.match(
      /<style\b[^>]*>[\s\S]*?<\/style>/g
    ) || [];

    const defs = body.match(
      /<defs\b[^>]*>[\s\S]*?<\/defs>/g
    ) || [];

    definitions = [...styles, ...defs].join('\n');
  }

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
// 4. Optional diagnostic footprint overlay
// ------------------------------------------------------------

const overlay = showFootprint
  ? coordinates.footprintOverlay(events)
  : '';

// ------------------------------------------------------------
// 5. Generate combined SVG
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
// 6. Write outputs
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
  `(${events.length} notes, ${systems.length} systems)`
);

console.table(
  systems.map(s => ({
    system: s.system,
    width: s.width,
    height: s.height,
    y_offset: s.y_offset
  }))
);
