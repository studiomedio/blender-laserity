"""Glue between the scene's properties and the Blender-independent core.

Everything that translates property values into core option objects, turns
placements into exportable parts, arranges them and validates the result lives
here, so the operators and the interactive editor share one implementation.
"""

from __future__ import annotations

from . import cache
from .core import detect, flatten, geom2d, nesting, svg
from .core.part import PlacedPart


# ---------------------------------------------------------------------------
# Property blocks -> core option objects
# ---------------------------------------------------------------------------


def detect_settings(context):
  settings = context.scene.laserity
  return detect.DetectSettings(
    max_thickness=settings.max_thickness,
    min_planarity=settings.min_planarity,
    min_flatness_ratio=settings.min_flatness_ratio,
    min_area=settings.min_area,
    normal_angle_tolerance=settings.normal_angle,
    unit_to_mm=detect.scene_unit_to_mm(context.scene),
  )


def flatten_options(settings):
  return flatten.FlattenOptions(
    method=settings.method,
    normal_angle_tolerance=settings.normal_angle,
    engrave_material_prefix=settings.engrave_material_prefix,
    engrave_edge_mark=settings.engrave_edge_mark,
    engrave_top_only=settings.engrave_top_only,
    simplify_tolerance=settings.simplify_tolerance,
  )


def svg_options(settings):
  return svg.SvgOptions(
    sheet_width=settings.sheet_width,
    sheet_height=settings.sheet_height,
    stroke_width=settings.stroke_width,
    engrave_layer=int(settings.engrave_layer),
    cut_start_layer=int(settings.cut_start_layer),
    kerf=settings.kerf,
    precision=settings.precision,
    draw_sheet_outline=settings.draw_sheet_outline,
  )


# ---------------------------------------------------------------------------
# Placements
# ---------------------------------------------------------------------------


def placed_parts(settings):
  """Every placement as an exportable :class:`PlacedPart`, skipping stale ones."""
  out = []
  for placement in settings.placements:
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    geometry = cache.geometry(part)
    if geometry is None:
      continue
    out.append(
      PlacedPart(
        name=part.name,
        geometry=geometry,
        thickness=part.thickness,
        thickness_key=part.thickness_key,
        sheet=placement.sheet,
        rotation=placement.rotation,
        offset=(placement.offset_x, placement.offset_y),
        mirrored=placement.mirrored,
      )
    )
  return out


def local_outline(part, mirrored):
  """A part's outer points in local space, mirrored if the copy is flipped.

  The nester works on plain point lists and knows nothing about mirroring, so
  the flip has to be baked in before packing. Because the stored transform also
  mirrors first, the angle and offset that come back stay valid.
  """
  cached = cache.get(part)
  if cached is None:
    return []
  points = cached.geometry.outer_points()
  return geom2d.mirror_x(points) if mirrored else points


def usable_rect(settings):
  """``(min_x, min_y, max_x, max_y)`` of the printable area inside one sheet."""
  margin = settings.margin
  return (margin, margin, settings.sheet_width - margin, settings.sheet_height - margin)


# ---------------------------------------------------------------------------
# Arranging
# ---------------------------------------------------------------------------


def arrange(settings, mode="PACK"):
  """Lay every placement out on sheets, one stock thickness per sheet.

  Mixing thicknesses on a sheet would be meaningless — they are different pieces
  of material — so each thickness group gets its own run of sheet indices.

  Returns ``(placed_count, unplaced_count)``.
  """
  groups = {}
  for index, placement in enumerate(settings.placements):
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    points = local_outline(part, placement.mirrored)
    if len(points) < 3:
      continue
    groups.setdefault(part.thickness_key, []).append((index, points))

  next_sheet = 0
  placed_count = 0
  unplaced_count = 0

  for key in sorted(groups):
    items = groups[key]
    if mode == "GRID":
      placements = nesting.grid_layout(
        items,
        settings.sheet_width,
        settings.sheet_height,
        spacing=settings.spacing,
        margin=settings.margin,
      )
      unplaced = []
    else:
      placements, unplaced = nesting.auto_nest(
        items,
        settings.sheet_width,
        settings.sheet_height,
        spacing=settings.spacing,
        margin=settings.margin,
        allow_rotation=settings.allow_rotation,
        use_min_rect=settings.use_min_rect,
        max_sheets=settings.max_sheets,
      )

    used = _apply(settings, placements, next_sheet)
    placed_count += len(placements)
    next_sheet += used

    if unplaced:
      # Parts too big for the sheet still need somewhere to live. Park them on
      # their own sheet in a plain row so they are visible and obviously wrong,
      # rather than silently stacked on top of the arrangement.
      overflowing = set(unplaced)
      leftovers = [(i, pts) for i, pts in items if i in overflowing]
      overflow = nesting.grid_layout(
        leftovers,
        settings.sheet_width,
        settings.sheet_height,
        spacing=settings.spacing,
        margin=settings.margin,
      )
      next_sheet += _apply(settings, overflow, next_sheet)
      unplaced_count += len(unplaced)

  recompute_conflicts(settings)
  return (placed_count, unplaced_count)


def _apply(settings, placements, base_sheet):
  """Write core placements back onto the property collection. Returns sheets used."""
  highest = -1
  for placement in placements:
    target = settings.placements[placement.key]
    target.sheet = base_sheet + placement.sheet
    target.rotation = placement.rotation
    target.offset_x, target.offset_y = placement.offset
    highest = max(highest, placement.sheet)
  return highest + 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


#: Slack allowed by the geometric checks, in millimetres. Placement offsets and
#: angles are stored as single-precision floats and collision outlines are
#: decimated, so a perfectly packed sheet still lands a few microns out. At 50
#: microns this is an order of magnitude below any laser's kerf, and it stops
#: the check from crying wolf over every automatic layout.
TOLERANCE = 0.05


class _Entry:
  __slots__ = ("index", "thickness_key", "loops", "bounds")

  def __init__(self, index, thickness_key, loops):
    self.index = index
    self.thickness_key = thickness_key
    self.loops = loops
    self.bounds = geom2d.bbox_union([geom2d.bbox(loop) for loop in loops])


def recompute_conflicts(settings):
  """Flag every placement that a laser operator would have to fix by hand.

  Three things count as a conflict: parts that overlap or sit closer than the
  spacing, parts hanging outside the sheet's usable area, and a sheet carrying
  more than one stock thickness.

  Returns the number of flagged placements.
  """
  entries_by_sheet = {}
  for index, placement in enumerate(settings.placements):
    placement.conflict = False
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    cached = cache.get(part)
    if cached is None:
      continue
    loops = [
      geom2d.transform_loop(
        loop,
        placement.rotation,
        (placement.offset_x, placement.offset_y),
        mirrored=placement.mirrored,
      )
      for loop in cached.collision
    ]
    entries_by_sheet.setdefault(placement.sheet, []).append(
      _Entry(index, part.thickness_key, loops)
    )

  if not settings.show_conflicts:
    return 0

  flagged = set()
  min_x, min_y, max_x, max_y = usable_rect(settings)
  clearance = max(settings.spacing - TOLERANCE, 0.0)

  for entries in entries_by_sheet.values():
    if len({entry.thickness_key for entry in entries}) > 1:
      flagged.update(entry.index for entry in entries)

    for entry in entries:
      bx0, by0, bx1, by1 = entry.bounds
      if (
        bx0 < min_x - TOLERANCE
        or by0 < min_y - TOLERANCE
        or bx1 > max_x + TOLERANCE
        or by1 > max_y + TOLERANCE
      ):
        flagged.add(entry.index)

    for i in range(len(entries)):
      for j in range(i + 1, len(entries)):
        a, b = entries[i], entries[j]
        if a.index in flagged and b.index in flagged:
          continue
        if not geom2d.bboxes_overlap(a.bounds, b.bounds, gap=clearance):
          continue
        if _conflict(a.loops, b.loops, clearance):
          flagged.add(a.index)
          flagged.add(b.index)

  for index in flagged:
    settings.placements[index].conflict = True
  return len(flagged)


def _conflict(loops_a, loops_b, clearance):
  for loop_a in loops_a:
    for loop_b in loops_b:
      if geom2d.parts_conflict(loop_a, loop_b, clearance):
        return True
  return False


def statistics(settings):
  """Per-sheet summary for the UI: ``{sheet: (part count, thickness, utilisation)}``."""
  sheet_area = settings.sheet_width * settings.sheet_height
  totals = {}
  for placement in settings.placements:
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    cached = cache.get(part)
    if cached is None:
      continue
    count, thickness, area = totals.get(placement.sheet, (0, part.thickness_key, 0.0))
    totals[placement.sheet] = (
      count + 1,
      thickness,
      area + cached.geometry.net_area(),
    )
  if sheet_area <= 0.0:
    return {}
  return {
    sheet: (count, thickness, area / sheet_area) for sheet, (count, thickness, area) in totals.items()
  }
