
'use strict';

// Coordinate and layout utilities for abc2svg output.
// abc2svg annotation Y coordinates point upward; SVG Y points downward.
// sx/sy are abc2svg's own conversion functions, evaluated at annotation time.

const SYSTEM_GAP = 40;

function annotationToSvg(abc, x, y, width, height) {
  if (![x, y, width, height].every(Number.isFinite) || width < 0 || height < 0) {
    throw new Error('Invalid abc2svg annotation rectangle');
  }

  // Annotation (x,y) is the upper-left corner in abc2svg's Y-up space.
  // Its opposite corner is (x+width,y-height).
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

    const result = {
      system,
      ...dimensions,
      y_offset: totalHeight
    };

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
      throw new Error(
        `Invalid system for annotation at source offset ${event.source_start}`
      );
    }

    if (!event.fragment_bbox) {
      throw new Error(
        `Missing converted annotation at source offset ${event.source_start}`
      );
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

function footprintOverlay(events) {
  return `<g id="note-footprints" fill="none" stroke="red" stroke-width="0.8" pointer-events="none">\n${events.map(({ bbox }) =>
    `<rect x="${bbox.x}" y="${bbox.y}" width="${bbox.width}" height="${bbox.height}"/>`
  ).join('\n')}\n</g>`;
}

module.exports = {
  annotationToSvg,
  layoutSystems,
  placeEvents,
  footprintOverlay
};
