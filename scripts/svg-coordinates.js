
'use strict';

// Coordinate and layout utilities for abc2svg output.
// abc2svg annotation Y coordinates point upward; SVG Y points downward.
// sx/sy are abc2svg's own conversion functions, evaluated at annotation time.

const SYSTEM_GAP = 40;

function annotationToSvg(abc, x, y, width, height) {
  if (![x, y, width, height].every(Number.isFinite) || width < 0 || height < 0) {
    throw new Error('Invalid abc2svg annotation rectangle');
  }

  const x1 = abc.sx(x);
  const x2 = abc.sx(x + width);
  const y1 = abc.sy(y);
  const y2 = abc.sy(y - height);

  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1)
  };
}

function fragmentDimensions(svg, index) {
  const match = svg.match(/viewBox=["']\s*0\s+0\s+([\d.]+)\s+([\d.]+)["']/);
  if (!match) throw new Error(`Missing or unsupported viewBox in SVG fragment ${index + 1}`);

  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!(width > 0 && height > 0)) {
    throw new Error(`Invalid dimensions in SVG fragment ${index + 1}`);
  }
  return { width, height };
}

function layoutSystems(chunks, gap = SYSTEM_GAP) {
  let width = 0;
  let totalHeight = 0;

  const systems = chunks.map((svg, system) => {
    const dimensions = fragmentDimensions(svg, system);
    if (system > 0) totalHeight += gap;

    const result = { system, ...dimensions, y_offset: totalHeight };
    width = Math.max(width, dimensions.width);
    totalHeight += dimensions.height;
    return result;
  });

  return { systems, width, totalHeight };
}

function placeEvents(events, systems) {
  for (const event of events) {
    const system = systems[event.system];
    if (!system) {
      throw new Error(`Invalid system for annotation at source offset ${event.source_start}`);
    }
    if (!event.fragment_bbox) {
      throw new Error(`Missing converted annotation at source offset ${event.source_start}`);
    }

    const bbox = {
      ...event.fragment_bbox,
      y: event.fragment_bbox.y + system.y_offset
    };
    event.x = bbox.x;
    event.y = bbox.y;
    event.system_y_offset = system.y_offset;
    event.bbox = bbox;
    delete event.fragment_bbox;
  }
}

// This deliberately supports the SVG path forms observed in the supplied
// abc2svg engraving. Unexpected forms fail explicitly instead of guessing.
function extractMeasures(chunks, systems) {
  const measures = [];
  let number = 1;
  const numeric = '-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)';

  chunks.forEach((svg, systemIndex) => {
    const system = systems[systemIndex];
    if (!system || system.system !== systemIndex) {
      throw new Error(`Missing layout for system ${systemIndex}`);
    }

    // abc2svg writes all barlines for a system into a bW path.
    const barPaths = [...svg.matchAll(/<path\b[^>]*\bclass=["']bW["'][^>]*>/g)];
    if (barPaths.length !== 1) {
      throw new Error(`Expected one bW barline path in system ${systemIndex}`);
    }
    const dMatch = barPaths[0][0].match(/\bd=["']([^"']+)["']/);
    if (!dMatch) throw new Error(`Missing barline path data in system ${systemIndex}`);

    const barSegment = new RegExp(`M(${numeric})\\s+(${numeric})v(${numeric})`, 'g');
    const segments = [...dMatch[1].matchAll(barSegment)];
    const residue = dMatch[1].replace(barSegment, '').replace(/[\s,]/g, '');
    if (!segments.length || residue) {
      throw new Error(`Unsupported barline path commands in system ${systemIndex}`);
    }

    const barXs = [...new Set(segments.map(m => Number(m[1])))].sort((a, b) => a - b);
    const top = Math.min(...segments.map(m => Math.min(Number(m[2]), Number(m[2]) + Number(m[3]))));
    const bottom = Math.max(...segments.map(m => Math.max(Number(m[2]), Number(m[2]) + Number(m[3]))));

    // Staff starts appear either as <use ... href="#stdef"> or as a
    // translated <g> containing the first system's explicit slW path.
    const useStarts = [...svg.matchAll(/<use\b[^>]*\bx=["']([^"']+)["'][^>]*\by=["'][^"']+["'][^>]*\b(?:xlink:href|href)=["']#stdef["'][^>]*\/?\s*>/g)]
      .map(m => Number(m[1]));
    const directStaff = [...svg.matchAll(/<g\s+transform=["']translate\(\s*([^,\s]+)[,\s]+([^\s)]+)\s*\)["']>\s*<path\b[^>]*\bclass=["']slW["']/g)]
      .map(m => Number(m[1]));
    const starts = [...useStarts, ...directStaff];
    if (!starts.length || starts.some(x => !Number.isFinite(x))) {
      throw new Error(`Cannot locate engraved staff start in system ${systemIndex}`);
    }
    const left = Math.min(...starts);
    if (!(bottom > top) || !(barXs[0] > left)) {
      throw new Error(`Invalid engraved staff/barline geometry in system ${systemIndex}`);
    }

    const boundaries = [left, ...barXs];
    for (let i = 1; i < boundaries.length; i++) {
      const x = boundaries[i - 1];
      const right = boundaries[i];
      if (!(right > x)) throw new Error(`Non-increasing measure boundaries in system ${systemIndex}`);
      const bbox = {
        x,
        y: top + system.y_offset,
        width: Number((right - x).toFixed(3)),
        height: Number((bottom - top).toFixed(3))
      };
      measures.push({
        measure: number++,
        system: systemIndex,
        ...bbox,
        bbox
      });
    }
  });

  return measures;
}

function footprintOverlay(events) {
  return `<g id="note-footprints" fill="none" stroke="red" stroke-width="0.8" pointer-events="none">\n${events.map(({ bbox }) =>
    `<rect x="${bbox.x}" y="${bbox.y}" width="${bbox.width}" height="${bbox.height}"/>`
  ).join('\n')}\n</g>`;
}

function measureFootprintOverlay(measures) {
  return `<g id="measure-footprints" fill="none" stroke="blue" stroke-width="1" pointer-events="none">\n${measures.map(({ bbox }) =>
    `<rect x="${bbox.x}" y="${bbox.y}" width="${bbox.width}" height="${bbox.height}"/>`
  ).join('\n')}\n</g>`;
}

// Centers in the engraving coordinate system, captured by abc2svg.psxygl.
// No SVG glyph parsing or font-specific ink measurements are required.
function noteheadOverlay(noteheads) {
  const arm = 5;
  return `<g id="notehead-crosshairs" fill="none" stroke="#00B000" stroke-width="1.5" pointer-events="none">\n${noteheads.map(({x, y}) =>
    `<path d="M${x-arm} ${y}H${x+arm}M${x} ${y-arm}V${y+arm}"/>`
  ).join('\n')}\n</g>`;
}

module.exports = {
  annotationToSvg,
  layoutSystems,
  placeEvents,
  extractMeasures,
  footprintOverlay,
  measureFootprintOverlay,
  noteheadOverlay
};
