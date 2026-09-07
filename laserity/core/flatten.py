"""Turning a measured slab into flat 2D contours ready for nesting and export.

Two extraction strategies are available:

``TOP_FACES``
    Take the faces lying on the slab's upper plane and trace the boundary of
    that face set. Interior holes fall out for free, and the result is the exact
    profile of the material at full thickness. This is the right answer for
    almost every plate-like part.

``SECTION``
    Slice the solid at mid-thickness and use the resulting cross-section. More
    tolerant of chamfered or otherwise non-planar top surfaces, at the cost of
    reporting the mid-thickness profile rather than the widest one.

``AUTO`` tries the first and falls back to the second when it yields nothing
usable.
"""

from __future__ import annotations

import math

import bmesh

from . import detect, geom2d
from .part import (  # re-exported for convenience
    METHOD_AUTO,
    METHOD_SECTION,
    METHOD_TOP_FACES,
    PartGeometry,
    place,
    place_contours,
)

__all__ = [
    "METHOD_AUTO",
    "METHOD_SECTION",
    "METHOD_TOP_FACES",
    "PartGeometry",
    "FlattenOptions",
    "place",
    "place_contours",
    "extract",
    "build",
    "engrave_material_slots",
]

DEFAULT_NORMAL_TOLERANCE = detect.DEFAULT_NORMAL_TOLERANCE

MARK_FLAGS = {
    "NONE": 0,
    "FREESTYLE": detect.MARK_FREESTYLE,
    "SEAM": detect.MARK_SEAM,
    "SHARP": detect.MARK_SHARP,
}


class FlattenOptions:
    def __init__(
        self,
        method=METHOD_AUTO,
        normal_angle_tolerance=DEFAULT_NORMAL_TOLERANCE,
        engrave_material_prefix="LZ_ENGRAVE",
        engrave_edge_mark="FREESTYLE",
        engrave_top_only=True,
        simplify_tolerance=0.01,
    ):
        self.method = method
        self.normal_angle_tolerance = normal_angle_tolerance
        self.engrave_material_prefix = engrave_material_prefix
        self.engrave_edge_mark = engrave_edge_mark
        self.engrave_top_only = engrave_top_only
        self.simplify_tolerance = simplify_tolerance


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract(bm, analysis, options, name="", engrave_slots=frozenset()):
    """Flatten a world-space bmesh into a :class:`PartGeometry`."""
    method = options.method
    loops = []
    used_method = method

    if method in (METHOD_TOP_FACES, METHOD_AUTO):
        loops = _top_face_polys(bm, analysis, options.normal_angle_tolerance)
        used_method = METHOD_TOP_FACES

    if not loops and method in (METHOD_SECTION, METHOD_AUTO):
        loops = _section_polys(bm, analysis)
        used_method = METHOD_SECTION

    contours = geom2d.classify_loops(_simplify_all(loops, options.simplify_tolerance))
    if not contours:
        return PartGeometry(name=name, thickness=analysis.thickness, method=used_method)

    engrave_loops = []
    if engrave_slots:
        engrave_loops = _material_region_polys(bm, analysis, engrave_slots)
    engrave_regions = geom2d.classify_loops(
        _simplify_all(engrave_loops, options.simplify_tolerance)
    )

    engrave_lines = _marked_edge_polylines(bm, analysis, options)
    engrave_lines = _simplify_all(engrave_lines, options.simplify_tolerance, closed=False)

    # Anchor local space at the cut outline's lower-left corner.
    min_x = min(p[0] for outer, _ in contours for p in outer)
    min_y = min(p[1] for outer, _ in contours for p in outer)
    shift = (-min_x, -min_y)

    def move(loop):
        return geom2d.translate(loop, shift[0], shift[1])

    return PartGeometry(
        name=name,
        thickness=analysis.thickness,
        method=used_method,
        contours=[(move(o), [move(h) for h in hs]) for o, hs in contours],
        engrave_regions=[(move(o), [move(h) for h in hs]) for o, hs in engrave_regions],
        engrave_lines=[move(line) for line in engrave_lines],
    )


def _simplify_all(loops, tolerance, closed=True):
    if tolerance <= 0.0:
        return loops
    out = []
    for loop in loops:
        simplified = geom2d.simplify(loop, tolerance, closed=closed)
        if len(simplified) >= (3 if closed else 2):
            out.append(simplified)
    return out


def _boundary_edges(bm, selected_face_indices):
    """Edges with exactly one face in the selection: the border of that region."""
    edges = []
    for edge in bm.edges:
        count = 0
        for face in edge.link_faces:
            if face.index in selected_face_indices:
                count += 1
                if count > 1:
                    break
        if count == 1:
            edges.append((edge.verts[0].index, edge.verts[1].index))
    return edges


def _polys_from_edges(edges, coords):
    polys = []
    for loop in geom2d.chain_edges_to_loops(edges):
        pts = [coords[i] for i in loop if i in coords]
        pts = geom2d.dedupe_consecutive(pts)
        if len(pts) >= 3:
            polys.append(pts)
    return polys


def _top_face_polys(bm, analysis, angle_tolerance):
    normal = analysis.normal
    cos_tol = math.cos(angle_tolerance)
    is_sheet = analysis.thickness <= 1e-6
    plane_tolerance = max(analysis.thickness * 0.02, 1e-3)

    selected = set()
    for face in bm.faces:
        alignment = face.normal.dot(normal)
        if is_sheet:
            # A zero-thickness object has no "up" side; take every coplanar face.
            if abs(alignment) < cos_tol:
                continue
        else:
            if alignment < cos_tol:
                continue
            centre = face.calc_center_median()
            if abs(centre.dot(normal) - analysis.top_offset) > plane_tolerance:
                continue
        selected.add(face.index)

    if not selected:
        return []

    coords = {vert.index: analysis.project(vert.co) for vert in bm.verts}
    return _polys_from_edges(_boundary_edges(bm, selected), coords)


def _section_polys(bm, analysis):
    work = bm.copy()
    try:
        normal = analysis.normal
        mid = (analysis.top_offset + analysis.bottom_offset) * 0.5
        geom = work.verts[:] + work.edges[:] + work.faces[:]
        bmesh.ops.bisect_plane(
            work,
            geom=geom,
            dist=1e-6,
            plane_co=normal * mid,
            plane_no=normal,
            clear_inner=True,
            clear_outer=True,
        )
        work.verts.index_update()
        coords = {vert.index: analysis.project(vert.co) for vert in work.verts}
        edges = [(e.verts[0].index, e.verts[1].index) for e in work.edges]
        return _polys_from_edges(edges, coords)
    finally:
        work.free()


def _material_region_polys(bm, analysis, engrave_slots):
    """Boundaries of faces carrying an engrave material, on the slab's top side."""
    normal = analysis.normal
    selected = set()
    for face in bm.faces:
        if face.material_index not in engrave_slots:
            continue
        if face.normal.dot(normal) <= 0.0:
            continue
        selected.add(face.index)
    if not selected:
        return []
    coords = {vert.index: analysis.project(vert.co) for vert in bm.verts}
    return _polys_from_edges(_boundary_edges(bm, selected), coords)


def _marked_edge_polylines(bm, analysis, options):
    flag = MARK_FLAGS.get(options.engrave_edge_mark, 0)
    if not flag:
        return []
    layer = bm.edges.layers.int.get("laserity_mark")
    if layer is None:
        return []

    normal = analysis.normal
    plane_tolerance = max(analysis.thickness * 0.05, 1e-3)

    edges = []
    for edge in bm.edges:
        if not (edge[layer] & flag):
            continue
        if options.engrave_top_only and analysis.thickness > 1e-6:
            if any(
                abs(vert.co.dot(normal) - analysis.top_offset) > plane_tolerance
                for vert in edge.verts
            ):
                continue
        edges.append((edge.verts[0].index, edge.verts[1].index))

    if not edges:
        return []

    coords = {vert.index: analysis.project(vert.co) for vert in bm.verts}
    lines = []
    for chain in geom2d.chain_edges_to_loops(edges):
        pts = [coords[i] for i in chain if i in coords]
        pts = geom2d.dedupe_consecutive(pts)
        if len(pts) >= 2:
            lines.append(pts)
    return lines


def engrave_material_slots(obj, prefix):
    """Material slot indices on ``obj`` whose material name starts with ``prefix``."""
    if not prefix:
        return frozenset()
    needle = prefix.lower()
    slots = set()
    for index, slot in enumerate(obj.material_slots):
        material = slot.material
        if material is not None and material.name.lower().startswith(needle):
            slots.add(index)
    return frozenset(slots)


def build(obj, depsgraph, detect_settings, options):
    """Full pipeline for one object: measure, then flatten.

    Returns ``(analysis, geometry)``. ``geometry`` is ``None`` when the object is
    not a usable flat part; ``analysis.reason`` then says why.
    """
    with detect.object_bmesh(obj, depsgraph, detect_settings.unit_to_mm) as bm:
        if bm is None:
            return (None, None)
        analysis = detect.analyze(bm, detect_settings, reference_matrix=obj.matrix_world)
        if not analysis.is_flat:
            return (analysis, None)
        slots = engrave_material_slots(obj, options.engrave_material_prefix)
        geometry = extract(bm, analysis, options, name=obj.name, engrave_slots=slots)
        if geometry.is_empty:
            analysis.is_flat = False
            analysis.reason = "could not trace an outline"
            return (analysis, None)
        return (analysis, geometry)
