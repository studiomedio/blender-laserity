"""Draw-ready part geometry, cached per part.

Rebuilding a :class:`~laserity.core.part.PartGeometry` from its stored JSON on
every redraw would be wasteful, and so would re-triangulating it. Both are done
once here and reused until the part's stored geometry actually changes.

Everything cached lives in the part's own local millimetre space, so it survives
any amount of moving, rotating and mirroring on a sheet. Triangle indices in
particular are invariant under those transforms, which is why the editor can
transform points on the fly and still reuse the tessellation.
"""

from __future__ import annotations

from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from .core import geom2d
from .core.part import PartGeometry

#: ``part name -> (payload hash, PartCache)``
_CACHE = {}

#: Outlines are decimated this much before the pairwise clearance test, which
#: is O(n*m) per pair. It has to stay well under the slack the clearance check
#: allows itself (``nest.TOLERANCE``), or a decimated corner would read as a
#: spacing violation.
_COLLISION_TOLERANCE = 0.02


class PartCache:
  """One part's geometry in every form the add-on needs to draw or test it."""

  __slots__ = (
    "geometry",
    "fills",
    "outlines",
    "engrave_outlines",
    "engrave_lines",
    "collision",
    "bounds",
    "size",
  )

  def __init__(self, geometry):
    self.geometry = geometry
    self.fills = []
    self.outlines = []
    self.engrave_outlines = []
    self.engrave_lines = []
    self.collision = []
    self.bounds = geometry.bounds()
    self.size = geometry.size()

    for outer, holes in geometry.contours:
      self.outlines.append(outer)
      self.outlines.extend(holes)
      self.fills.append(_triangulate(outer, holes))
      simplified = geom2d.simplify(outer, _COLLISION_TOLERANCE)
      self.collision.append(simplified if len(simplified) >= 3 else outer)

    for outer, holes in geometry.engrave_regions:
      self.engrave_outlines.append(outer)
      self.engrave_outlines.extend(holes)

    self.engrave_lines = list(geometry.engrave_lines)


def _triangulate(outer, holes):
  """``(points, triangles)`` for filling a contour, holes punched out.

  ``triangles`` indexes into ``points``, which is the outer loop followed by
  every hole. A tessellation failure degrades to an unfilled part rather than
  taking the whole redraw down with it.
  """
  points = list(outer)
  for hole in holes:
    points.extend(hole)
  loops = [[Vector((x, y, 0.0)) for x, y in outer]]
  loops.extend([Vector((x, y, 0.0)) for x, y in hole] for hole in holes)
  try:
    triangles = tessellate_polygon(loops)
  except (ValueError, RuntimeError):
    triangles = []
  return (points, triangles)


def get(part):
  """Cached draw data for ``part``, or ``None`` if it has no usable geometry."""
  payload = part.geometry_json
  if not payload:
    return None

  digest = hash(payload)
  entry = _CACHE.get(part.name)
  if entry is not None and entry[0] == digest:
    return entry[1]

  geometry = PartGeometry.from_json(payload)
  if geometry is None or geometry.is_empty:
    _CACHE.pop(part.name, None)
    return None

  cached = PartCache(geometry)
  _CACHE[part.name] = (digest, cached)
  return cached


def geometry(part):
  """The part's :class:`PartGeometry`, or ``None``."""
  cached = get(part)
  return cached.geometry if cached is not None else None


def clear():
  _CACHE.clear()
