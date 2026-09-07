"""Flat-part data types and the sheet placement transform.

Kept free of Blender imports so the nester, the SVG writer and the tests can all
use it without a running Blender.
"""

from __future__ import annotations

import json

from . import geom2d

METHOD_TOP_FACES = "TOP_FACES"
METHOD_SECTION = "SECTION"
METHOD_AUTO = "AUTO"

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def place(points, rotation=0.0, offset=(0.0, 0.0), mirrored=False):
    """Apply a part's sheet placement to points in its local space.

    The transform order is fixed for the whole add-on: mirror about the local Y
    axis, rotate about the local origin, then translate. The nester, the
    interactive editor and the SVG writer all go through this one function, so
    they cannot drift apart.
    """
    return geom2d.transform_loop(points, rotation, offset, mirrored=mirrored)


def place_contours(contours, rotation=0.0, offset=(0.0, 0.0), mirrored=False):
    return [
        (
            place(outer, rotation, offset, mirrored),
            [place(hole, rotation, offset, mirrored) for hole in holes],
        )
        for outer, holes in contours
    ]


# ---------------------------------------------------------------------------
# Geometry container
# ---------------------------------------------------------------------------


class PartGeometry:
    """A flattened part in its own local 2D millimetre space.

    Local space is anchored so the cut outline's bounding box starts at the
    origin, keeping coordinates small no matter where the object sits in the
    scene.
    """

    __slots__ = ("name", "thickness", "contours", "engrave_regions", "engrave_lines", "method")

    def __init__(
        self,
        name="",
        thickness=0.0,
        contours=None,
        engrave_regions=None,
        engrave_lines=None,
        method=METHOD_AUTO,
    ):
        self.name = name
        self.thickness = thickness
        self.contours = contours or []
        self.engrave_regions = engrave_regions or []
        self.engrave_lines = engrave_lines or []
        self.method = method

    @property
    def is_empty(self):
        return not self.contours

    def outer_points(self):
        """Every outer-contour point, for hull, bounds and nesting purposes."""
        points = []
        for outer, _holes in self.contours:
            points.extend(outer)
        return points

    def hole_count(self):
        return sum(len(holes) for _outer, holes in self.contours)

    def net_area(self):
        """Material area in mm², holes subtracted."""
        total = 0.0
        for outer, holes in self.contours:
            total += geom2d.area(outer)
            for hole in holes:
                total -= geom2d.area(hole)
        return max(total, 0.0)

    def bounds(self):
        return geom2d.bbox(self.outer_points())

    def size(self):
        min_x, min_y, max_x, max_y = self.bounds()
        return (max_x - min_x, max_y - min_y)

    def has_engraving(self):
        return bool(self.engrave_regions or self.engrave_lines)

    # -- serialisation -------------------------------------------------------

    def to_json(self, precision=4):
        def pack(loop):
            return [[round(x, precision), round(y, precision)] for x, y in loop]

        return json.dumps(
            {
                "v": SCHEMA_VERSION,
                "name": self.name,
                "thickness": round(self.thickness, precision),
                "method": self.method,
                "contours": [
                    [pack(outer), [pack(h) for h in holes]] for outer, holes in self.contours
                ],
                "engrave_regions": [
                    [pack(outer), [pack(h) for h in holes]] for outer, holes in self.engrave_regions
                ],
                "engrave_lines": [pack(line) for line in self.engrave_lines],
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text):
        """Rebuild from :meth:`to_json`. Returns ``None`` for missing or stale data."""
        if not text:
            return None
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        if data.get("v") != SCHEMA_VERSION:
            return None

        def unpack(loop):
            return [(float(p[0]), float(p[1])) for p in loop]

        return cls(
            name=data.get("name", ""),
            thickness=float(data.get("thickness", 0.0)),
            method=data.get("method", METHOD_AUTO),
            contours=[(unpack(o), [unpack(h) for h in hs]) for o, hs in data.get("contours", [])],
            engrave_regions=[
                (unpack(o), [unpack(h) for h in hs]) for o, hs in data.get("engrave_regions", [])
            ],
            engrave_lines=[unpack(line) for line in data.get("engrave_lines", [])],
        )


# ---------------------------------------------------------------------------
# Placed part
# ---------------------------------------------------------------------------


class PlacedPart:
    """A flattened part together with where it sits on a sheet."""

    __slots__ = (
        "name",
        "geometry",
        "thickness",
        "thickness_key",
        "sheet",
        "rotation",
        "offset",
        "mirrored",
    )

    def __init__(
        self,
        name,
        geometry,
        thickness,
        thickness_key,
        sheet=0,
        rotation=0.0,
        offset=(0.0, 0.0),
        mirrored=False,
    ):
        self.name = name
        self.geometry = geometry
        self.thickness = thickness
        self.thickness_key = thickness_key
        self.sheet = sheet
        self.rotation = rotation
        self.offset = offset
        self.mirrored = mirrored

    def placed_contours(self):
        return place_contours(self.geometry.contours, self.rotation, self.offset, self.mirrored)

    def placed_engrave_regions(self):
        return place_contours(
            self.geometry.engrave_regions, self.rotation, self.offset, self.mirrored
        )

    def placed_engrave_lines(self):
        return [
            place(line, self.rotation, self.offset, self.mirrored)
            for line in self.geometry.engrave_lines
        ]

    def placed_outer_points(self):
        return place(self.geometry.outer_points(), self.rotation, self.offset, self.mirrored)

    def placed_bounds(self):
        return geom2d.bbox(self.placed_outer_points())
