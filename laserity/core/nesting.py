"""Automatic sheet nesting.

Parts are reduced to their minimum-area oriented bounding rectangle and packed
with the MaxRects algorithm using the best-short-side-fit heuristic. Rectangle
packing of the oriented bounds is a deliberate simplification over true
irregular nesting: it is fast, deterministic and good enough for the plate-like
parts this add-on targets, and the user can always nudge parts afterwards in the
nesting editor.

All units are millimetres.
"""

from __future__ import annotations

import math

from . import geom2d

EPS = 1e-6


class Placement:
  """Where a part ended up, expressed for :func:`laserity.core.flatten.place`."""

  __slots__ = ("key", "sheet", "rotation", "offset", "size")

  def __init__(self, key, sheet, rotation, offset, size):
    self.key = key
    self.sheet = sheet
    self.rotation = rotation
    self.offset = offset
    self.size = size

  def __repr__(self):  # pragma: no cover - debugging aid
    return (
      f"Placement({self.key!r}, sheet={self.sheet}, "
      f"rot={math.degrees(self.rotation):.1f}deg, offset={self.offset})"
    )


# ---------------------------------------------------------------------------
# MaxRects
# ---------------------------------------------------------------------------


class _MaxRectsBin:
  def __init__(self, width, height):
    self.width = width
    self.height = height
    self.free = [(0.0, 0.0, width, height)]
    self.used = []

  def insert(self, width, height, allow_rotation):
    node = self._find_position(width, height, allow_rotation)
    if node is None:
      return None
    x, y, w, h, rotated = node
    self._split_free(x, y, w, h)
    self._prune()
    self.used.append((x, y, w, h))
    return (x, y, rotated)

  def _find_position(self, width, height, allow_rotation):
    best = None
    best_score = (float("inf"), float("inf"))
    for fx, fy, fw, fh in self.free:
      for w, h, rotated in ((width, height, False), (height, width, True)):
        if rotated and not allow_rotation:
          continue
        if w > fw + EPS or h > fh + EPS:
          continue
        leftover_h = abs(fw - w)
        leftover_v = abs(fh - h)
        score = (min(leftover_h, leftover_v), max(leftover_h, leftover_v))
        if score < best_score:
          best_score = score
          best = (fx, fy, w, h, rotated)
    return best

  def _split_free(self, x, y, w, h):
    new_free = []
    for rect in self.free:
      pieces = _split_rect(rect, (x, y, w, h))
      if pieces is None:
        new_free.append(rect)
      else:
        new_free.extend(pieces)
    self.free = new_free

  def _prune(self):
    keep = []
    for i, a in enumerate(self.free):
      contained = False
      for j, b in enumerate(self.free):
        if i == j:
          continue
        if _contains(b, a) and not (i > j and _contains(a, b)):
          contained = True
          break
      if not contained and a[2] > EPS and a[3] > EPS:
        keep.append(a)
    self.free = keep


def _contains(outer, inner):
  return (
    inner[0] >= outer[0] - EPS
    and inner[1] >= outer[1] - EPS
    and inner[0] + inner[2] <= outer[0] + outer[2] + EPS
    and inner[1] + inner[3] <= outer[1] + outer[3] + EPS
  )


def _split_rect(free, used):
  """Split ``free`` around ``used``; returns None when they do not intersect."""
  fx, fy, fw, fh = free
  ux, uy, uw, uh = used
  if ux >= fx + fw - EPS or ux + uw <= fx + EPS:
    return None
  if uy >= fy + fh - EPS or uy + uh <= fy + EPS:
    return None

  pieces = []
  if uy > fy + EPS:
    pieces.append((fx, fy, fw, uy - fy))
  if uy + uh < fy + fh - EPS:
    pieces.append((fx, uy + uh, fw, fy + fh - (uy + uh)))
  if ux > fx + EPS:
    pieces.append((fx, fy, ux - fx, fh))
  if ux + uw < fx + fw - EPS:
    pieces.append((ux + uw, fy, fx + fw - (ux + uw), fh))
  return pieces


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def canonical_form(points, use_min_rect=True):
  """Reduce a part outline to an axis-aligned form anchored at the origin.

  Returns ``(angle, offset, width, height)`` such that rotating ``points`` by
  ``angle`` about the origin and then translating by ``offset`` places the
  shape's bounding box at ``(0, 0)`` with size ``width x height``.
  """
  if not points:
    return (0.0, (0.0, 0.0), 0.0, 0.0)

  if use_min_rect:
    rect = geom2d.min_area_rect(points)
    angle = -rect.angle
  else:
    angle = 0.0

  rotated = geom2d.rotate(points, angle) if abs(angle) > EPS else list(points)
  min_x, min_y, max_x, max_y = geom2d.bbox(rotated)
  return (angle, (-min_x, -min_y), max_x - min_x, max_y - min_y)


def _fits_sheet(width, height, usable_w, usable_h, allow_rotation):
  """Whether a part can fit on an empty sheet at all, in either orientation."""
  if width <= usable_w + EPS and height <= usable_h + EPS:
    return True
  if allow_rotation and height <= usable_w + EPS and width <= usable_h + EPS:
    return True
  return False


def auto_nest(
  items,
  sheet_width,
  sheet_height,
  spacing=2.0,
  margin=5.0,
  allow_rotation=True,
  use_min_rect=True,
  max_sheets=64,
):
  """Pack ``items`` onto as many sheets as needed.

  ``items`` is a sequence of ``(key, points)`` where ``points`` is the part's
  outer contour in its own local 2D space (already mirrored if the part is
  flipped). Returns ``(placements, unplaced_keys)``.
  """
  usable_w = sheet_width - 2.0 * margin
  usable_h = sheet_height - 2.0 * margin
  if usable_w <= EPS or usable_h <= EPS:
    return ([], [key for key, _ in items])

  prepared = []
  unplaced = []
  for key, points in items:
    angle, offset, w, h = canonical_form(points, use_min_rect=use_min_rect)
    if w <= EPS or h <= EPS:
      unplaced.append(key)
      continue
    prepared.append(
      {
        "key": key,
        "angle": angle,
        "offset": offset,
        "width": w,
        "height": h,
      }
    )

  # Largest first: the classic ordering heuristic for shelf/MaxRects packers.
  prepared.sort(key=lambda it: (-max(it["width"], it["height"]), -it["width"] * it["height"]))

  placements = []
  bins = []
  for item in prepared:
    padded_w = item["width"] + spacing
    padded_h = item["height"] + spacing

    if not _fits_sheet(padded_w, padded_h, usable_w, usable_h, allow_rotation):
      unplaced.append(item["key"])
      continue

    result = None
    bin_index = -1
    for bin_index, packer in enumerate(bins):
      result = packer.insert(padded_w, padded_h, allow_rotation)
      if result is not None:
        break
    if result is None:
      if len(bins) >= max_sheets:
        unplaced.append(item["key"])
        continue
      packer = _MaxRectsBin(usable_w, usable_h)
      bins.append(packer)
      bin_index = len(bins) - 1
      result = packer.insert(padded_w, padded_h, allow_rotation)
      if result is None:
        unplaced.append(item["key"])
        continue

    x, y, rotated = result
    placements.append(
      _compose(item, bin_index, x + margin, y + margin, rotated)
    )

  return (placements, unplaced)


def _compose(item, sheet, x, y, rotated):
  """Fold the alignment rotation, the 90 degree pack flip and the sheet position
  into the single ``rotate then translate`` transform the rest of the add-on uses.
  """
  extra = math.pi * 0.5 if rotated else 0.0
  angle = item["angle"] + extra

  ox, oy = item["offset"]
  if rotated:
    # Rotating the canonical form 90 degrees CCW moves its box to
    # [-height, 0] x [0, width], so shift it back by the part height.
    ox, oy = -oy + item["height"], ox
    size = (item["height"], item["width"])
  else:
    size = (item["width"], item["height"])

  return Placement(item["key"], sheet, angle, (ox + x, oy + y), size)


def grid_layout(items, sheet_width, sheet_height, spacing=2.0, margin=5.0):
  """Trivial row-by-row layout, used as the initial arrangement after a scan."""
  placements = []
  cursor_x = margin
  cursor_y = margin
  row_height = 0.0
  sheet = 0

  for key, points in items:
    _, offset, w, h = canonical_form(points, use_min_rect=False)
    if w <= EPS or h <= EPS:
      continue
    if cursor_x + w > sheet_width - margin and cursor_x > margin:
      cursor_x = margin
      cursor_y += row_height + spacing
      row_height = 0.0
    if cursor_y + h > sheet_height - margin and cursor_y > margin:
      sheet += 1
      cursor_x = margin
      cursor_y = margin
      row_height = 0.0
    placements.append(
      Placement(key, sheet, 0.0, (offset[0] + cursor_x, offset[1] + cursor_y), (w, h))
    )
    cursor_x += w + spacing
    row_height = max(row_height, h)

  return placements


def sheet_utilisation(placements, parts_area, sheet_width, sheet_height):
  """Fraction of each sheet covered by actual part area, keyed by sheet index."""
  totals = {}
  for placement in placements:
    totals.setdefault(placement.sheet, 0.0)
    totals[placement.sheet] += parts_area.get(placement.key, 0.0)
  sheet_area = sheet_width * sheet_height
  if sheet_area <= EPS:
    return {}
  return {sheet: total / sheet_area for sheet, total in totals.items()}
