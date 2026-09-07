"""SVG output tuned for LightBurn.

LightBurn decides which cut layer a shape belongs to by matching its **stroke**
colour against its own palette, and ignores fill when a stroke is present. So
the writer keeps every path unfilled, strokes it with an exact palette colour,
and gives each material thickness its own colour. Grouping is purely
organisational — it carries part names through for humans, not for the machine.

Everything written is in millimetres, with the SVG Y axis flipped relative to
Blender's so parts appear the right way up.
"""

from __future__ import annotations

import os

from . import geom2d, lightburn
from .part import PlacedPart

__all__ = ["SvgOptions", "PlacedPart", "build_layer_map", "render_sheet", "write"]

SVG_NS = "http://www.w3.org/2000/svg"


class SvgOptions:
    def __init__(
        self,
        sheet_width=600.0,
        sheet_height=400.0,
        stroke_width=0.1,
        engrave_layer=2,
        cut_start_layer=1,
        kerf=0.0,
        precision=3,
        draw_sheet_outline=False,
        sheet_outline_layer=8,
    ):
        self.sheet_width = sheet_width
        self.sheet_height = sheet_height
        self.stroke_width = stroke_width
        self.engrave_layer = engrave_layer
        self.cut_start_layer = cut_start_layer
        self.kerf = kerf
        self.precision = precision
        self.draw_sheet_outline = draw_sheet_outline
        self.sheet_outline_layer = sheet_outline_layer


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _num(value, precision):
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _slug(text):
    out = []
    for ch in str(text):
        out.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    slug = "".join(out).strip("_")
    return slug or "unnamed"


def _path_data(loops, sheet_height, precision, closed=True):
    """Build a single ``d`` attribute containing every loop as a subpath."""
    chunks = []
    for loop in loops:
        if len(loop) < (3 if closed else 2):
            continue
        commands = []
        for index, (x, y) in enumerate(loop):
            prefix = "M" if index == 0 else "L"
            commands.append(f"{prefix}{_num(x, precision)},{_num(sheet_height - y, precision)}")
        chunk = "".join(commands)
        if closed:
            chunk += "Z"
        chunks.append(chunk)
    return " ".join(chunks)


def _apply_kerf(outer, holes, kerf):
    """Offset the cut path outwards by half the kerf so the part comes out to size."""
    if kerf <= 0.0:
        return (outer, holes)
    half = kerf * 0.5
    grown_outer = geom2d.offset_polygon(geom2d.ensure_winding(outer, True), half)
    grown_holes = []
    for hole in holes:
        ccw = geom2d.ensure_winding(hole, True)
        grown_holes.append(list(reversed(geom2d.offset_polygon(ccw, half))))
    return (grown_outer, grown_holes)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def build_layer_map(placed_parts, options):
    """Assign one LightBurn cut layer per distinct thickness, thinnest first."""
    keys = sorted({p.thickness_key for p in placed_parts})
    return lightburn.assign_cut_layers(
        keys, options.engrave_layer, start_index=options.cut_start_layer
    )


def render_sheet(placed_parts, options, layer_map, sheet_index=0, sheet_count=1):
    """Render one sheet's worth of parts to an SVG document string."""
    precision = options.precision
    width = options.sheet_width
    height = options.sheet_height
    stroke = _num(options.stroke_width, 4)

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        f'<svg xmlns="{SVG_NS}" version="1.1" '
        f'width="{_num(width, precision)}mm" height="{_num(height, precision)}mm" '
        f'viewBox="0 0 {_num(width, precision)} {_num(height, precision)}">',
    ]

    legend = ", ".join(
        f"{_num(key, 2)}mm={lightburn.name_for(layer_map[key])}"
        for key in sorted({p.thickness_key for p in placed_parts})
    )
    lines.append(
        f"  <desc>Laserity sheet {sheet_index + 1} of {sheet_count}. "
        f"Units: mm. Cut layers: {_escape(legend)}. "
        f"Engrave layer: {lightburn.name_for(options.engrave_layer)}.</desc>"
    )

    if options.draw_sheet_outline:
        colour = lightburn.hex_for(options.sheet_outline_layer)
        lines.append(
            f'  <rect x="0" y="0" width="{_num(width, precision)}" '
            f'height="{_num(height, precision)}" fill="none" stroke="{colour}" '
            f'stroke-width="{stroke}" id="sheet-outline"/>'
        )

    # --- cut paths, grouped by thickness then by part -----------------------
    by_thickness = {}
    for part in placed_parts:
        by_thickness.setdefault(part.thickness_key, []).append(part)

    for key in sorted(by_thickness):
        colour = lightburn.hex_for(layer_map[key])
        label = f"{_num(key, 2)}mm"
        lines.append(
            f'  <g id="cut-{_slug(label)}" data-laserity-thickness="{_num(key, 3)}" '
            f'data-laserity-layer="{lightburn.name_for(layer_map[key])}" '
            f'fill="none" stroke="{colour}" stroke-width="{stroke}">'
        )
        for part in by_thickness[key]:
            loops = []
            for outer, holes in part.placed_contours():
                outer, holes = _apply_kerf(outer, holes, options.kerf)
                loops.append(outer)
                loops.extend(holes)
            data = _path_data(loops, height, precision)
            if not data:
                continue
            lines.append(f'    <g id="part-{_slug(part.name)}">')
            lines.append(f'      <path d="{data}"/>')
            lines.append("    </g>")
        lines.append("  </g>")

    # --- engrave features ---------------------------------------------------
    engrave_colour = lightburn.hex_for(options.engrave_layer)
    engrave_bodies = []
    for part in placed_parts:
        loops = []
        for outer, holes in part.placed_engrave_regions():
            loops.append(outer)
            loops.extend(holes)
        region_data = _path_data(loops, height, precision)
        line_data = _path_data(part.placed_engrave_lines(), height, precision, closed=False)
        if not region_data and not line_data:
            continue
        body = [f'    <g id="engrave-{_slug(part.name)}">']
        if region_data:
            body.append(f'      <path d="{region_data}"/>')
        if line_data:
            body.append(f'      <path d="{line_data}"/>')
        body.append("    </g>")
        engrave_bodies.extend(body)

    if engrave_bodies:
        lines.append(
            f'  <g id="engrave" data-laserity-layer="{lightburn.name_for(options.engrave_layer)}" '
            f'fill="none" stroke="{engrave_colour}" stroke-width="{stroke}">'
        )
        lines.extend(engrave_bodies)
        lines.append("  </g>")

    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def sheet_filepath(base_path, sheet_index, sheet_count):
    if sheet_count <= 1:
        return base_path
    root, ext = os.path.splitext(base_path)
    return f"{root}_sheet{sheet_index + 1}{ext or '.svg'}"


def write(base_path, placed_parts, options):
    """Write one SVG per sheet. Returns the list of paths actually written."""
    if not placed_parts:
        return []

    layer_map = build_layer_map(placed_parts, options)

    by_sheet = {}
    for part in placed_parts:
        by_sheet.setdefault(part.sheet, []).append(part)

    sheet_indices = sorted(by_sheet)
    written = []
    for position, sheet_index in enumerate(sheet_indices):
        document = render_sheet(
            by_sheet[sheet_index],
            options,
            layer_map,
            sheet_index=position,
            sheet_count=len(sheet_indices),
        )
        path = sheet_filepath(base_path, position, len(sheet_indices))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(document)
        written.append(path)
    return written
