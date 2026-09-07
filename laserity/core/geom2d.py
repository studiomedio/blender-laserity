"""Pure 2D geometry helpers.

Deliberately free of any Blender import so it can be exercised with a plain
``python`` interpreter (see ``tests/test_geom2d.py``). Everything here works on
plain ``(x, y)`` float tuples and all lengths are millimetres by the time they
reach this module.
"""

from __future__ import annotations

import math

EPS = 1e-9


# ---------------------------------------------------------------------------
# Basic measures
# ---------------------------------------------------------------------------


def signed_area(pts):
  """Shoelace area. Positive for counter-clockwise winding."""
  n = len(pts)
  if n < 3:
    return 0.0
  total = 0.0
  for i in range(n):
    x1, y1 = pts[i]
    x2, y2 = pts[(i + 1) % n]
    total += x1 * y2 - x2 * y1
  return total * 0.5


def area(pts):
  return abs(signed_area(pts))


def ensure_winding(pts, counter_clockwise=True):
  """Return ``pts`` reordered so the winding matches the requested direction."""
  if not pts:
    return pts
  is_ccw = signed_area(pts) >= 0.0
  if is_ccw == counter_clockwise:
    return list(pts)
  return list(reversed(pts))


def centroid(pts):
  """Area centroid, falling back to the vertex average for degenerate loops."""
  a = signed_area(pts)
  if abs(a) < EPS:
    if not pts:
      return (0.0, 0.0)
    return (
      sum(p[0] for p in pts) / len(pts),
      sum(p[1] for p in pts) / len(pts),
    )
  cx = cy = 0.0
  n = len(pts)
  for i in range(n):
    x1, y1 = pts[i]
    x2, y2 = pts[(i + 1) % n]
    cross = x1 * y2 - x2 * y1
    cx += (x1 + x2) * cross
    cy += (y1 + y2) * cross
  factor = 1.0 / (6.0 * a)
  return (cx * factor, cy * factor)


def bbox(pts):
  """Axis-aligned bounds as ``(min_x, min_y, max_x, max_y)``."""
  if not pts:
    return (0.0, 0.0, 0.0, 0.0)
  xs = [p[0] for p in pts]
  ys = [p[1] for p in pts]
  return (min(xs), min(ys), max(xs), max(ys))


def bbox_union(boxes):
  boxes = [b for b in boxes if b is not None]
  if not boxes:
    return (0.0, 0.0, 0.0, 0.0)
  return (
    min(b[0] for b in boxes),
    min(b[1] for b in boxes),
    max(b[2] for b in boxes),
    max(b[3] for b in boxes),
  )


def bboxes_overlap(a, b, gap=0.0):
  return not (
    a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
  )


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def translate(pts, dx, dy):
  return [(x + dx, y + dy) for x, y in pts]


def rotate(pts, angle, origin=(0.0, 0.0)):
  c = math.cos(angle)
  s = math.sin(angle)
  ox, oy = origin
  out = []
  for x, y in pts:
    px = x - ox
    py = y - oy
    out.append((ox + px * c - py * s, oy + px * s + py * c))
  return out


def mirror_x(pts, axis=0.0):
  """Mirror across the vertical line ``x = axis``."""
  return [(2.0 * axis - x, y) for x, y in pts]


def transform_loop(pts, angle, offset, mirrored=False, pivot=(0.0, 0.0)):
  """Apply the part placement transform: optional mirror, then rotate, then move.

  ``pivot`` is the point the rotation happens around, expressed in the loop's
  own local space.
  """
  out = list(pts)
  if mirrored:
    out = mirror_x(out, pivot[0])
  if abs(angle) > EPS:
    out = rotate(out, angle, pivot)
  return translate(out, offset[0], offset[1])


# ---------------------------------------------------------------------------
# Containment / intersection
# ---------------------------------------------------------------------------


def point_in_polygon(pt, poly):
  """Ray-casting containment test. Points exactly on an edge are unspecified."""
  x, y = pt
  inside = False
  n = len(poly)
  if n < 3:
    return False
  j = n - 1
  for i in range(n):
    xi, yi = poly[i]
    xj, yj = poly[j]
    if (yi > y) != (yj > y):
      denom = yj - yi
      if abs(denom) > EPS:
        x_cross = xi + (y - yi) * (xj - xi) / denom
        if x_cross > x:
          inside = not inside
    j = i
  return inside


def _orient(a, b, c):
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
  return (
    min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
    and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
  )


def segments_intersect(p1, p2, p3, p4):
  """Proper or improper intersection of segments p1p2 and p3p4."""
  d1 = _orient(p3, p4, p1)
  d2 = _orient(p3, p4, p2)
  d3 = _orient(p1, p2, p3)
  d4 = _orient(p1, p2, p4)
  if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and (
    (d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)
  ):
    return True
  if abs(d1) <= EPS and _on_segment(p3, p4, p1):
    return True
  if abs(d2) <= EPS and _on_segment(p3, p4, p2):
    return True
  if abs(d3) <= EPS and _on_segment(p1, p2, p3):
    return True
  if abs(d4) <= EPS and _on_segment(p1, p2, p4):
    return True
  return False


def polygons_overlap(a, b):
  """True when two closed polygons share interior area or cross each other.

  Only the outer contours are considered; a part nested inside another part's
  hole is therefore still reported as overlapping. That is the conservative
  answer for laser nesting.
  """
  if len(a) < 3 or len(b) < 3:
    return False
  if not bboxes_overlap(bbox(a), bbox(b)):
    return False
  na, nb = len(a), len(b)
  for i in range(na):
    a1 = a[i]
    a2 = a[(i + 1) % na]
    for j in range(nb):
      b1 = b[j]
      b2 = b[(j + 1) % nb]
      if segments_intersect(a1, a2, b1, b2):
        return True
  # No crossings: one may still be fully contained in the other.
  return point_in_polygon(a[0], b) or point_in_polygon(b[0], a)


def segment_distance(p1, p2, p3, p4):
  """Shortest distance between two segments; 0 when they intersect."""
  if segments_intersect(p1, p2, p3, p4):
    return 0.0
  return min(
    point_segment_distance(p1, p3, p4),
    point_segment_distance(p2, p3, p4),
    point_segment_distance(p3, p1, p2),
    point_segment_distance(p4, p1, p2),
  )


def polygon_distance(a, b, limit=None):
  """Shortest distance between two polygon boundaries.

  Returns 0.0 when they overlap. ``limit`` allows an early exit once the
  distance is known to be below the caller's threshold, which keeps the
  interactive clearance check cheap.
  """
  if len(a) < 2 or len(b) < 2:
    return float("inf")
  best = float("inf")
  na, nb = len(a), len(b)
  for i in range(na):
    a1 = a[i]
    a2 = a[(i + 1) % na]
    for j in range(nb):
      b1 = b[j]
      b2 = b[(j + 1) % nb]
      d = segment_distance(a1, a2, b1, b2)
      if d < best:
        best = d
        if limit is not None and best < limit:
          return best
  return best


def parts_conflict(a, b, clearance=0.0):
  """Whether two placed outlines overlap or sit closer than ``clearance``.

  This is what the nesting editor flags in red: an exact edge-to-edge touch is
  not an overlap, but it does leave no room for the kerf, so it still counts as
  a conflict once a clearance is required.
  """
  if len(a) < 3 or len(b) < 3:
    return False
  if not bboxes_overlap(bbox(a), bbox(b), gap=clearance):
    return False
  if polygons_overlap(a, b):
    return True
  if clearance <= 0.0:
    return False
  return polygon_distance(a, b, limit=clearance) < clearance


# ---------------------------------------------------------------------------
# Convex hull and minimum-area rectangle
# ---------------------------------------------------------------------------


def convex_hull(pts):
  """Andrew's monotone chain. Returns the hull counter-clockwise."""
  unique = sorted(set((round(p[0], 9), round(p[1], 9)) for p in pts))
  if len(unique) < 3:
    return list(unique)

  def build(points):
    chain = []
    for p in points:
      while len(chain) >= 2 and _orient(chain[-2], chain[-1], p) <= 0:
        chain.pop()
      chain.append(p)
    return chain

  lower = build(unique)
  upper = build(list(reversed(unique)))
  return lower[:-1] + upper[:-1]


class MinRect:
  """Minimum-area oriented bounding rectangle."""

  __slots__ = ("angle", "width", "height", "center")

  def __init__(self, angle, width, height, center):
    self.angle = angle
    self.width = width
    self.height = height
    self.center = center

  @property
  def area(self):
    return self.width * self.height

  def __repr__(self):  # pragma: no cover - debugging aid
    return (
      f"MinRect(angle={math.degrees(self.angle):.2f}deg, "
      f"{self.width:.3f}x{self.height:.3f}, center={self.center})"
    )


def min_area_rect(pts):
  """Rotating-calipers minimum-area rectangle over the convex hull of ``pts``.

  ``angle`` is the rotation that was applied to the input to axis-align it, so
  rotating the source points by ``-angle`` yields an axis-aligned shape.
  """
  hull = convex_hull(pts)
  if len(hull) < 3:
    x0, y0, x1, y1 = bbox(pts)
    return MinRect(0.0, x1 - x0, y1 - y0, ((x0 + x1) * 0.5, (y0 + y1) * 0.5))

  best = None
  n = len(hull)
  for i in range(n):
    x1p, y1p = hull[i]
    x2p, y2p = hull[(i + 1) % n]
    edge_angle = math.atan2(y2p - y1p, x2p - x1p)
    c = math.cos(edge_angle)
    s = math.sin(edge_angle)
    # Rotate the hull by -edge_angle so this edge lies on the X axis.
    xs = [p[0] * c + p[1] * s for p in hull]
    ys = [-p[0] * s + p[1] * c for p in hull]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max_x - min_x
    h = max_y - min_y
    a = w * h
    if best is None or a < best[0] - 1e-12:
      cx = (min_x + max_x) * 0.5
      cy = (min_y + max_y) * 0.5
      # Rotate the centre back into the source frame.
      center = (cx * c - cy * s, cx * s + cy * c)
      best = (a, edge_angle, w, h, center)

  _, angle, width, height, center = best
  return MinRect(angle, width, height, center)


# ---------------------------------------------------------------------------
# Edge soup -> closed loops
# ---------------------------------------------------------------------------


def chain_edges_to_loops(edges):
  """Turn an unordered set of index edges into vertex-index loops.

  ``edges`` is an iterable of ``(i, j)`` index pairs. Returns a list of index
  lists; closed loops do not repeat their first index at the end. Open chains
  (which happen on non-manifold or partially selected geometry) are returned
  too, so callers should check whether a loop is closed before treating it as a
  contour.
  """
  adjacency = {}
  edge_set = set()
  for i, j in edges:
    if i == j:
      continue
    key = (i, j) if i < j else (j, i)
    if key in edge_set:
      continue
    edge_set.add(key)
    adjacency.setdefault(i, []).append(j)
    adjacency.setdefault(j, []).append(i)

  remaining = set(edge_set)
  loops = []

  def take_edge(a, b):
    key = (a, b) if a < b else (b, a)
    if key in remaining:
      remaining.discard(key)
      return True
    return False

  # Walk from odd-degree vertices first so open chains do not fragment loops.
  starts = [v for v, nb in adjacency.items() if len(nb) % 2 == 1]
  starts.extend(v for v in adjacency if v not in starts)

  for start in starts:
    while True:
      nxt = None
      for cand in adjacency.get(start, ()):
        key = (start, cand) if start < cand else (cand, start)
        if key in remaining:
          nxt = cand
          break
      if nxt is None:
        break
      loop = [start]
      current = start
      following = nxt
      while following is not None and take_edge(current, following):
        loop.append(following)
        current = following
        following = None
        for cand in adjacency.get(current, ()):
          key = (current, cand) if current < cand else (cand, current)
          if key in remaining:
            following = cand
            break
      if len(loop) > 2 and loop[0] == loop[-1]:
        loops.append(loop[:-1])
      elif len(loop) > 2:
        loops.append(loop)
  return loops


def is_closed_chain(loop, edges_lookup):
  """Whether the first and last index of ``loop`` are joined by an edge."""
  if len(loop) < 3:
    return False
  a, b = loop[0], loop[-1]
  key = (a, b) if a < b else (b, a)
  return key in edges_lookup


def dedupe_consecutive(pts, tol=1e-7):
  """Drop consecutive duplicate points, including a wrapped final duplicate."""
  out = []
  for p in pts:
    if out and abs(p[0] - out[-1][0]) < tol and abs(p[1] - out[-1][1]) < tol:
      continue
    out.append(p)
  while (
    len(out) > 1
    and abs(out[0][0] - out[-1][0]) < tol
    and abs(out[0][1] - out[-1][1]) < tol
  ):
    out.pop()
  return out


# ---------------------------------------------------------------------------
# Loop nesting classification
# ---------------------------------------------------------------------------


def classify_loops(loops):
  """Split closed loops into outer contours with their holes.

  Uses even-odd nesting depth: a loop contained by an even number of other
  loops is an outer contour, an odd number makes it a hole. Each hole is
  attached to the smallest contour that contains it.

  Returns a list of ``(outer, [holes...])`` tuples, largest contour first.
  """
  usable = [lp for lp in loops if len(lp) >= 3 and area(lp) > EPS]
  if not usable:
    return []

  infos = []
  for idx, lp in enumerate(usable):
    infos.append(
      {
        "index": idx,
        "pts": lp,
        "area": area(lp),
        "bbox": bbox(lp),
        "probe": _interior_probe(lp),
      }
    )

  containers = {}
  for a_info in infos:
    holding = []
    for b_info in infos:
      if a_info is b_info:
        continue
      if b_info["area"] <= a_info["area"]:
        continue
      if not bboxes_overlap(a_info["bbox"], b_info["bbox"]):
        continue
      if point_in_polygon(a_info["probe"], b_info["pts"]):
        holding.append(b_info)
    containers[a_info["index"]] = holding

  result = []
  hole_map = {}
  for info in infos:
    depth = len(containers[info["index"]])
    if depth % 2 == 0:
      hole_map[info["index"]] = []

  for info in infos:
    depth = len(containers[info["index"]])
    if depth % 2 == 1:
      # Attach to the smallest enclosing outer contour.
      candidates = [c for c in containers[info["index"]] if c["index"] in hole_map]
      if candidates:
        parent = min(candidates, key=lambda c: c["area"])
        hole_map[parent["index"]].append(info["pts"])

  for info in sorted(infos, key=lambda i: -i["area"]):
    if info["index"] in hole_map:
      outer = ensure_winding(info["pts"], counter_clockwise=True)
      holes = [ensure_winding(h, counter_clockwise=False) for h in hole_map[info["index"]]]
      result.append((outer, holes))
  return result


def _interior_probe(poly):
  """A point guaranteed to lie inside ``poly`` (used for containment tests).

  The centroid is not reliable for concave shapes, so fall back to scanning a
  horizontal ray through the polygon and taking the midpoint of the first
  interior span.
  """
  cand = centroid(poly)
  if point_in_polygon(cand, poly):
    return cand

  min_x, min_y, max_x, max_y = bbox(poly)
  n = len(poly)
  for step in range(1, 12):
    y = min_y + (max_y - min_y) * (step / 12.0)
    crossings = []
    for i in range(n):
      x1, y1 = poly[i]
      x2, y2 = poly[(i + 1) % n]
      if (y1 > y) != (y2 > y):
        denom = y2 - y1
        if abs(denom) > EPS:
          crossings.append(x1 + (y - y1) * (x2 - x1) / denom)
    crossings.sort()
    for i in range(0, len(crossings) - 1, 2):
      if crossings[i + 1] - crossings[i] > EPS:
        return ((crossings[i] + crossings[i + 1]) * 0.5, y)
  return cand


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------


def point_segment_distance(p, a, b):
  ax, ay = a
  bx, by = b
  dx = bx - ax
  dy = by - ay
  length_sq = dx * dx + dy * dy
  if length_sq < EPS:
    return math.hypot(p[0] - ax, p[1] - ay)
  t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / length_sq
  t = max(0.0, min(1.0, t))
  return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _rdp(pts, tolerance):
  """Ramer-Douglas-Peucker over an open chain."""
  n = len(pts)
  if n < 3:
    return list(pts)
  keep = [False] * n
  keep[0] = keep[n - 1] = True
  stack = [(0, n - 1)]
  while stack:
    start, end = stack.pop()
    if end <= start + 1:
      continue
    best_dist = -1.0
    best_index = -1
    a = pts[start]
    b = pts[end]
    for i in range(start + 1, end):
      d = point_segment_distance(pts[i], a, b)
      if d > best_dist:
        best_dist = d
        best_index = i
    if best_dist > tolerance and best_index > 0:
      keep[best_index] = True
      stack.append((start, best_index))
      stack.append((best_index, end))
  return [p for p, k in zip(pts, keep) if k]


def simplify(pts, tolerance, closed=True):
  """Drop points that sit within ``tolerance`` of the line they lie on.

  Meshes generated from curves or CAD imports carry far more points than a
  laser needs; trimming them keeps the SVG small without visibly changing the
  cut path.
  """
  if tolerance <= 0.0 or len(pts) < 3:
    return list(pts)

  if not closed:
    return _rdp(pts, tolerance)

  # A closed loop has no natural endpoints, so anchor it on the point furthest
  # from the first vertex and simplify the two resulting chains independently.
  origin = pts[0]
  far_index = max(
    range(len(pts)), key=lambda i: (pts[i][0] - origin[0]) ** 2 + (pts[i][1] - origin[1]) ** 2
  )
  if far_index == 0:
    return list(pts)

  first = _rdp(pts[: far_index + 1], tolerance)
  second = _rdp(pts[far_index:] + [pts[0]], tolerance)
  merged = first[:-1] + second[:-1]
  return merged if len(merged) >= 3 else list(pts)


# ---------------------------------------------------------------------------
# Kerf compensation
# ---------------------------------------------------------------------------


def offset_polygon(pts, distance, miter_limit=3.0):
  """Naive miter offset of a closed polygon by ``distance``.

  Positive ``distance`` grows a counter-clockwise polygon outwards. This does
  *not* resolve the self-intersections that appear when the offset exceeds a
  local concave radius, so it is only appropriate for the small distances used
  for kerf compensation. Callers should keep it optional and off by default.
  """
  if abs(distance) < EPS or len(pts) < 3:
    return list(pts)

  n = len(pts)
  out = []
  for i in range(n):
    prev_pt = pts[(i - 1) % n]
    cur = pts[i]
    nxt = pts[(i + 1) % n]

    e1 = (cur[0] - prev_pt[0], cur[1] - prev_pt[1])
    e2 = (nxt[0] - cur[0], nxt[1] - cur[1])
    l1 = math.hypot(*e1)
    l2 = math.hypot(*e2)
    if l1 < EPS or l2 < EPS:
      out.append(cur)
      continue
    # Outward normals for CCW winding are (dy, -dx) normalised.
    n1 = (e1[1] / l1, -e1[0] / l1)
    n2 = (e2[1] / l2, -e2[0] / l2)

    bis = (n1[0] + n2[0], n1[1] + n2[1])
    bl = math.hypot(*bis)
    if bl < EPS:
      out.append((cur[0] + n1[0] * distance, cur[1] + n1[1] * distance))
      continue
    bis = (bis[0] / bl, bis[1] / bl)
    cos_half = bis[0] * n1[0] + bis[1] * n1[1]
    scale = 1.0 / cos_half if abs(cos_half) > EPS else miter_limit
    scale = max(-miter_limit, min(miter_limit, scale))
    out.append((cur[0] + bis[0] * distance * scale, cur[1] + bis[1] * distance * scale))
  return out
