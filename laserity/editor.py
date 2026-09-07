"""The nesting editor: a dedicated window for arranging parts on sheets.

Opening the editor spawns a new Blender window, switches its main area to the
Image Editor and starts a modal operator there. The Image Editor is used simply
because it gives a clean, empty region with a sidebar; nothing of its own is
drawn. Everything the user sees is painted by this module in region pixel space,
and every event inside the main region belongs to the modal operator.

Sheets are laid out side by side in one continuous world space measured in
millimetres, so dragging a part from one sheet to the next is just a drag.
"""

from __future__ import annotations

import math

import blf
import bpy
import gpu
from bpy.props import EnumProperty
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from . import cache, nest, props
from .core import geom2d, lightburn

# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------

BACKGROUND = (0.13, 0.13, 0.14, 1.0)
SHEET_FILL = (0.19, 0.19, 0.21, 1.0)
SHEET_BORDER = (0.42, 0.42, 0.46, 1.0)
SHEET_EMPTY = (0.155, 0.155, 0.17, 1.0)
SHEET_FAINT = (0.27, 0.27, 0.30, 1.0)
MARGIN_LINE = (0.30, 0.30, 0.34, 1.0)
GRID_LINE = (0.22, 0.22, 0.25, 1.0)
SELECTED = (1.00, 0.62, 0.15, 1.0)
CONFLICT = (0.95, 0.25, 0.20, 1.0)
BOX_SELECT = (0.85, 0.85, 0.90, 0.9)
TEXT = (0.85, 0.85, 0.88, 1.0)
TEXT_DIM = (0.55, 0.55, 0.60, 1.0)
TEXT_FAINT = (0.38, 0.38, 0.42, 1.0)

FILL_ALPHA = 0.28
OUTLINE_WIDTH = 1.6
SELECTED_WIDTH = 2.6

#: Gap left between sheets, as a fraction of sheet width.
SHEET_GAP = 0.06
MIN_SHEET_GAP = 15.0

MIN_ZOOM = 0.02
MAX_ZOOM = 40.0
DRAG_THRESHOLD = 4  # pixels before a click becomes a drag

#: Areas currently hosting an editor, so a second one cannot be started there.
_running = set()


def is_running(area=None):
  if area is None:
    return bool(_running)
  return area.as_pointer() in _running


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


class _View:
  """Pan and zoom between world millimetres and region pixels."""

  __slots__ = ("zoom", "ox", "oy")

  def __init__(self):
    self.zoom = 1.0
    self.ox = 0.0
    self.oy = 0.0

  def to_screen(self, wx, wy):
    return (wx * self.zoom + self.ox, wy * self.zoom + self.oy)

  def to_world(self, sx, sy):
    return ((sx - self.ox) / self.zoom, (sy - self.oy) / self.zoom)

  def zoom_at(self, sx, sy, factor):
    new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
    if new_zoom == self.zoom:
      return
    wx, wy = self.to_world(sx, sy)
    self.zoom = new_zoom
    self.ox = sx - wx * new_zoom
    self.oy = sy - wy * new_zoom


def sheet_origin(settings, index):
  gap = max(settings.sheet_width * SHEET_GAP, MIN_SHEET_GAP)
  return (index * (settings.sheet_width + gap), 0.0)


def spare_sheet_count(settings):
  """Sheets in use plus one empty spare for parts to be dragged onto."""
  return max(settings.sheet_count(), 1) + 1


def world_bounds(settings, sheets):
  if sheets <= 0:
    return (0.0, 0.0, settings.sheet_width, settings.sheet_height)
  last_x, _ = sheet_origin(settings, sheets - 1)
  return (0.0, 0.0, last_x + settings.sheet_width, settings.sheet_height)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def placement_xform(settings, placement, view):
  """Pack a placement plus the view into ``(a, b, tx, ty, mirror)``.

  Applying it is ``sx = x*m*a - y*b + tx``, ``sy = x*m*b + y*a + ty``, which folds
  the mirror, the rotation, the sheet offset and the view transform into two
  multiplies per coordinate.
  """
  zoom = view.zoom
  angle = placement.rotation
  a = math.cos(angle) * zoom
  b = math.sin(angle) * zoom
  origin_x, origin_y = sheet_origin(settings, placement.sheet)
  tx = (placement.offset_x + origin_x) * zoom + view.ox
  ty = (placement.offset_y + origin_y) * zoom + view.oy
  return (a, b, tx, ty, -1.0 if placement.mirrored else 1.0)


def _project(points, xform):
  a, b, tx, ty, m = xform
  return [(x * m * a - y * b + tx, x * m * b + y * a + ty) for x, y in points]


def _unproject(sx, sy, xform):
  """Screen pixel back into the part's own local space."""
  a, b, tx, ty, m = xform
  scale = a * a + b * b
  if scale < 1e-12:
    return (0.0, 0.0)
  dx = sx - tx
  dy = sy - ty
  u = (dx * a + dy * b) / scale
  v = (-dx * b + dy * a) / scale
  return (u * m, v)


def placement_world_bounds(settings, placement, cached):
  """Bounding box of one placed part in sheet-continuous world space."""
  identity = _View()
  points = _project(cached.geometry.outer_points(), placement_xform(settings, placement, identity))
  return geom2d.bbox(points)


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------

_uniform = None
_polyline = None


def _shaders():
  """Built-in shaders, created lazily — there is no GPU context at import time."""
  global _uniform, _polyline
  if _uniform is None:
    _uniform = gpu.shader.from_builtin("UNIFORM_COLOR")
    _polyline = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
  return (_uniform, _polyline)


def _draw_tris(coords, indices, colour):
  if not indices:
    return
  # Blending is set per draw rather than once per frame: ``blf`` resets the GPU
  # state as a side effect of drawing text, so anything painted after a label
  # would otherwise come out opaque.
  gpu.state.blend_set("ALPHA")
  shader, _ = _shaders()
  batch = batch_for_shader(
    shader, "TRIS", {"pos": [(x, y, 0.0) for x, y in coords]}, indices=indices
  )
  shader.bind()
  shader.uniform_float("color", colour)
  batch.draw(shader)


def _draw_segments(segments, colour, width, region):
  """``segments`` is a flat list of point pairs.

  Everything goes through the polyline shader rather than plain ``LINES``:
  hardware line width is not available on every backend Blender ships, and on
  macOS in particular a fixed one-pixel outline looks broken next to a thick
  selection highlight.
  """
  if not segments:
    return
  gpu.state.blend_set("ALPHA")
  _, shader = _shaders()
  batch = batch_for_shader(shader, "LINES", {"pos": [(x, y, 0.0) for x, y in segments]})
  shader.bind()
  shader.uniform_float("viewportSize", (region.width, region.height))
  shader.uniform_float("lineWidth", width)
  shader.uniform_float("color", colour)
  batch.draw(shader)


def _loop_segments(points, closed=True):
  out = []
  count = len(points)
  if count < 2:
    return out
  span = count if closed else count - 1
  for i in range(span):
    out.append(points[i])
    out.append(points[(i + 1) % count])
  return out


def _rect_fill(x0, y0, x1, y1, colour):
  _draw_tris([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [(0, 1, 2), (0, 2, 3)], colour)


def _rect_outline(x0, y0, x1, y1, colour, width, region):
  corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
  _draw_segments(_loop_segments(corners), colour, width, region)


def _text(x, y, message, colour, size=11):
  ui_scale = bpy.context.preferences.system.ui_scale
  blf.size(0, size * ui_scale)
  blf.color(0, *colour)
  blf.position(0, x, y, 0.0)
  blf.draw(0, message)


def _text_width(message, size=11):
  ui_scale = bpy.context.preferences.system.ui_scale
  blf.size(0, size * ui_scale)
  return blf.dimensions(0, message)[0]


def _display_colour(index, alpha=1.0):
  """A LightBurn palette colour, lifted enough to stay visible on a dark sheet."""
  r, g, b = (channel / 255.0 for channel in lightburn.PALETTE[index % lightburn.LAYER_COUNT])
  brightest = max(r, g, b)
  if brightest < 0.45:
    lift = 0.45 - brightest
    r, g, b = min(r + lift, 1.0), min(g + lift, 1.0), min(b + lift, 1.0)
  return (r, g, b, alpha)


def layer_map(settings):
  """``{thickness key: LightBurn layer}``, matching what the SVG writer will do."""
  keys = sorted({p.thickness_key for p in settings.parts if p.enabled})
  return lightburn.assign_cut_layers(
    keys, int(settings.engrave_layer), start_index=int(settings.cut_start_layer)
  )


# ---------------------------------------------------------------------------
# The draw handler
# ---------------------------------------------------------------------------


def sidebar_inset(area):
  """Width the sidebar covers, since it floats over the drawing region."""
  if area is None:
    return 0.0
  for region in area.regions:
    if region.type == "UI" and region.width > 1:
      return float(region.width)
  return 0.0


def _dress_area(area):
  """Hide the Image Editor's own furniture and open the add-on's sidebar tab.

  The area is only borrowed as a drawing surface, so its toolbar and image
  controls are noise here. This runs from the modal operator rather than from
  the operator that created the window, because a region that does not exist
  yet cannot be configured.
  """
  try:
    space = area.spaces.active
    space.show_region_toolbar = False
    space.show_region_tool_header = False
    space.show_region_header = False
    space.show_region_ui = True
  except (AttributeError, TypeError):
    pass
  for region in area.regions:
    if region.type == "UI":
      try:
        region.active_panel_category = "Laserity"
      except (AttributeError, TypeError, ValueError):
        pass


def _draw_callback(op):
  context = bpy.context
  region = context.region
  settings = getattr(context.scene, "laserity", None)
  if region is None or settings is None:
    return

  gpu.state.blend_set("ALPHA")
  _rect_fill(0.0, 0.0, region.width, region.height, BACKGROUND)

  view = op.view
  colours = layer_map(settings)
  sheets = max(settings.sheet_count(), 1)

  _draw_sheets(op, region, settings, view, spare_sheet_count(settings), colours)
  _draw_parts(op, region, settings, view, colours)

  if op.mode == "BOX":
    x0, y0 = op.box_start
    x1, y1 = op.mouse
    _rect_outline(x0, y0, x1, y1, BOX_SELECT, 1.2, region)

  _draw_hud(op, region, settings, sheets)
  gpu.state.blend_set("NONE")


def _draw_sheets(op, region, settings, view, sheets, colours):
  thickness_by_sheet = {}
  for placement in settings.placements:
    part = settings.parts.get(placement.part_name)
    if part is not None and part.enabled:
      thickness_by_sheet.setdefault(placement.sheet, set()).add(part.thickness_key)

  for index in range(sheets):
    ox, oy = sheet_origin(settings, index)
    x0, y0 = view.to_screen(ox, oy)
    x1, y1 = view.to_screen(ox + settings.sheet_width, oy + settings.sheet_height)
    if x1 < 0 or x0 > region.width:
      continue

    empty = index not in thickness_by_sheet
    _rect_fill(x0, y0, x1, y1, SHEET_EMPTY if empty else SHEET_FILL)
    if settings.grid_snap and view.zoom * settings.snap_size > 6.0:
      _draw_grid(region, settings, view, ox, oy, x0, y0, x1, y1)
    if settings.margin > 0.0 and not empty:
      mx0, my0 = view.to_screen(ox + settings.margin, oy + settings.margin)
      mx1, my1 = view.to_screen(
        ox + settings.sheet_width - settings.margin,
        oy + settings.sheet_height - settings.margin,
      )
      _rect_outline(mx0, my0, mx1, my1, MARGIN_LINE, 1.0, region)
    _rect_outline(x0, y0, x1, y1, SHEET_FAINT if empty else SHEET_BORDER, 1.4, region)

    stock = thickness_by_sheet.get(index)
    if stock:
      label = " + ".join(
        lightburn.name_for(colours.get(key, 0)) + " " + _mm(key) for key in sorted(stock)
      )
      caption = f"Sheet {index + 1}   {label}"
      colour = CONFLICT if len(stock) > 1 else TEXT_DIM
    else:
      caption = f"Sheet {index + 1}   drag a part here to start it"
      colour = TEXT_FAINT
    _text(x0, y1 + 8.0, caption, colour, size=11)


def _draw_grid(region, settings, view, ox, oy, x0, y0, x1, y1):
  step = settings.snap_size
  segments = []
  count = int(settings.sheet_width / step)
  for i in range(1, count + 1):
    sx, _ = view.to_screen(ox + i * step, oy)
    segments.extend([(sx, y0), (sx, y1)])
  count = int(settings.sheet_height / step)
  for i in range(1, count + 1):
    _, sy = view.to_screen(ox, oy + i * step)
    segments.extend([(x0, sy), (x1, sy)])
  if len(segments) < 4000:  # a dense grid at low zoom is noise, not information
    _draw_segments(segments, GRID_LINE, 1.0, region)


def _draw_parts(op, region, settings, view, colours):
  """Draw every part, one pass per layer of the stack.

  Fills go down first for all parts, then outlines, then engraving, then labels.
  Drawing each part start to finish instead would let a neighbour's fill bury
  the outline — and with it the selection highlight — of the part before it.
  """
  visible = []
  for placement in settings.placements:
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    cached = cache.get(part)
    if cached is None:
      continue
    colour = _display_colour(colours.get(part.thickness_key, 0))
    conflicted = settings.show_conflicts and placement.conflict
    visible.append((placement, part, cached, placement_xform(settings, placement, view), colour, conflicted))

  for _placement, _part, cached, xform, colour, conflicted in visible:
    fill = CONFLICT[:3] + (FILL_ALPHA + 0.12,) if conflicted else colour[:3] + (FILL_ALPHA,)
    for points, triangles in cached.fills:
      _draw_tris(_project(points, xform), triangles, fill)

  selected_outline = []
  for placement, _part, cached, xform, colour, conflicted in visible:
    outline = []
    for loop in cached.outlines:
      outline.extend(_loop_segments(_project(loop, xform)))
    if placement.select:
      selected_outline.extend(outline)
    else:
      _draw_segments(outline, CONFLICT if conflicted else colour, OUTLINE_WIDTH, region)
  _draw_segments(selected_outline, SELECTED, SELECTED_WIDTH, region)

  engrave_colour = _display_colour(int(settings.engrave_layer))
  engrave = []
  for _placement, _part, cached, xform, _colour, _conflicted in visible:
    for loop in cached.engrave_outlines:
      engrave.extend(_loop_segments(_project(loop, xform)))
    for line in cached.engrave_lines:
      engrave.extend(_loop_segments(_project(line, xform), closed=False))
  _draw_segments(engrave, engrave_colour, 1.2, region)

  if not (settings.show_part_names and view.zoom > 0.12):
    return
  for placement, part, cached, xform, _colour, _conflicted in visible:
    bx0, by0, bx1, by1 = geom2d.bbox(_project(cached.geometry.outer_points(), xform))
    if bx1 - bx0 < 30.0 or not 0.0 < (bx0 + bx1) * 0.5 < region.width:
      continue
    _text(
      (bx0 + bx1) * 0.5 - _text_width(part.name, 10) * 0.5,
      (by0 + by1) * 0.5 - 4.0,
      part.name,
      TEXT if placement.select else TEXT_DIM,
      size=10,
    )


def _mm(value):
  text = f"{value:.2f}".rstrip("0").rstrip(".")
  return f"{text or '0'} mm"


_HINTS = (
  "LMB select / drag",
  "G move",
  "R rotate",
  "Shift R 90 deg",
  "M mirror",
  "X remove",
  "A all",
  "MMB pan",
  "Wheel zoom",
  "Home frame",
)


def _draw_hud(op, region, settings, sheets):
  selected = sum(1 for p in settings.placements if p.select)
  conflicts = sum(1 for p in settings.placements if p.conflict) if settings.show_conflicts else 0
  total = len(settings.placements)

  y = region.height - 22.0
  _text(14.0, y, f"{total} part{'' if total == 1 else 's'} on {sheets} sheet{'' if sheets == 1 else 's'}", TEXT, size=12)
  y -= 18.0
  if selected:
    _text(14.0, y, f"{selected} selected", SELECTED, size=11)
    y -= 16.0
  if conflicts:
    _text(14.0, y, f"{conflicts} overlapping, too close or off the sheet", CONFLICT, size=11)
    y -= 16.0
  if op.mode in {"MOVE", "ROTATE"}:
    hint = "Move — click to confirm, Esc to cancel" if op.mode == "MOVE" else "Rotate — Ctrl snaps, Esc to cancel"
    _text(14.0, y, hint, SELECTED, size=11)

  available = region.width - sidebar_inset(op._area) - 28.0
  hints = list(_HINTS)
  while hints and _text_width("   ·   ".join(hints), 10) > available:
    hints.pop()
  if hints:
    _text(14.0, 12.0, "   ·   ".join(hints), TEXT_DIM, size=10)


# ---------------------------------------------------------------------------
# Placement editing
# ---------------------------------------------------------------------------


def select_all(settings, value):
  for placement in settings.placements:
    placement.select = value


def selection_pivot(settings):
  """World-space centre of the selected placements' combined bounds."""
  boxes = []
  for placement in settings.placements:
    if not placement.select:
      continue
    part = settings.parts.get(placement.part_name)
    cached = cache.get(part) if part is not None else None
    if cached is None:
      continue
    boxes.append(placement_world_bounds(settings, placement, cached))
  if not boxes:
    return None
  x0, y0, x1, y1 = geom2d.bbox_union(boxes)
  return ((x0 + x1) * 0.5, (y0 + y1) * 0.5)


def rotate_selected(settings, angle):
  """Turn the selection by ``angle`` about its own centre, sheets held fixed."""
  pivot = selection_pivot(settings)
  if pivot is None:
    return 0
  cos_a = math.cos(angle)
  sin_a = math.sin(angle)
  px, py = pivot
  count = 0
  for placement in settings.placements:
    if not placement.select:
      continue
    origin_x, origin_y = sheet_origin(settings, placement.sheet)
    dx = placement.offset_x + origin_x - px
    dy = placement.offset_y + origin_y - py
    placement.offset_x = px + dx * cos_a - dy * sin_a - origin_x
    placement.offset_y = py + dx * sin_a + dy * cos_a - origin_y
    placement.rotation += angle
    count += 1
  return count


def mirror_selected(settings):
  """Flip the selection, keeping each part centred where it already sits."""
  count = 0
  for placement in settings.placements:
    if not placement.select:
      continue
    part = settings.parts.get(placement.part_name)
    cached = cache.get(part) if part is not None else None
    if cached is None:
      continue
    before = placement_world_bounds(settings, placement, cached)
    placement.mirrored = not placement.mirrored
    after = placement_world_bounds(settings, placement, cached)
    placement.offset_x += (before[0] + before[2] - after[0] - after[2]) * 0.5
    placement.offset_y += (before[1] + before[3] - after[1] - after[3]) * 0.5
    count += 1
  return count


def remove_selected(settings):
  """Drop the selected copies and write the surviving counts back to the parts."""
  doomed = [i for i, placement in enumerate(settings.placements) if placement.select]
  if not doomed:
    return 0
  with props.muted_sync():
    for index in reversed(doomed):
      settings.placements.remove(index)
    counts = {}
    for placement in settings.placements:
      counts[placement.part_name] = counts.get(placement.part_name, 0) + 1
    for part in settings.parts:
      remaining = counts.get(part.name, 0)
      if remaining:
        part.quantity = remaining
      else:
        part.enabled = False
  compact_sheets(settings)
  return len(doomed)


def sheet_at(settings, wx, wy, sheets):
  """Which sheet a world point falls on, or the nearest one when it falls between."""
  best = 0
  best_distance = None
  for index in range(max(sheets, 1)):
    ox, oy = sheet_origin(settings, index)
    if ox <= wx <= ox + settings.sheet_width and oy <= wy <= oy + settings.sheet_height:
      return index
    cx = ox + settings.sheet_width * 0.5
    cy = oy + settings.sheet_height * 0.5
    distance = (wx - cx) ** 2 + (wy - cy) ** 2
    if best_distance is None or distance < best_distance:
      best_distance = distance
      best = index
  return best


def compact_sheets(settings):
  """Close up sheet numbering after a sheet has been emptied."""
  used = sorted({placement.sheet for placement in settings.placements})
  remap = {old: new for new, old in enumerate(used)}
  if all(old == new for old, new in remap.items()):
    return
  for placement in settings.placements:
    placement.sheet = remap[placement.sheet]


def rebase_sheets(settings):
  """Re-home every placement onto the sheet it now visually sits on.

  Offsets are stored relative to a sheet, so a part dragged across a sheet
  boundary has to change sheet *and* have its offset rebased, or it would jump
  back a sheet's width on the next redraw. One spare sheet past the last used
  one is always in play, which is how a new sheet gets started: drag a part onto
  the empty one at the end. Sheets left empty are then closed up again.
  """
  sheets = spare_sheet_count(settings)
  moved = 0
  for placement in settings.placements:
    part = settings.parts.get(placement.part_name)
    cached = cache.get(part) if part is not None else None
    if cached is None:
      continue
    x0, y0, x1, y1 = placement_world_bounds(settings, placement, cached)
    target = sheet_at(settings, (x0 + x1) * 0.5, (y0 + y1) * 0.5, sheets)
    if target == placement.sheet:
      continue
    old_x, old_y = sheet_origin(settings, placement.sheet)
    new_x, new_y = sheet_origin(settings, target)
    placement.offset_x += old_x - new_x
    placement.offset_y += old_y - new_y
    placement.sheet = target
    moved += 1
  if moved:
    compact_sheets(settings)
  return moved


def pick(settings, view, sx, sy):
  """Topmost placement under a pixel, or ``None``.

  Later placements are drawn on top, so they are tested first.
  """
  for index in range(len(settings.placements) - 1, -1, -1):
    placement = settings.placements[index]
    part = settings.parts.get(placement.part_name)
    if part is None or not part.enabled:
      continue
    cached = cache.get(part)
    if cached is None:
      continue
    local = _unproject(sx, sy, placement_xform(settings, placement, view))
    for outer, holes in cached.geometry.contours:
      if not geom2d.point_in_polygon(local, outer):
        continue
      if any(geom2d.point_in_polygon(local, hole) for hole in holes):
        continue
      return index
  return None


def tag_redraw():
  for window in bpy.context.window_manager.windows:
    for area in window.screen.areas:
      if area.type in {"IMAGE_EDITOR", "VIEW_3D"}:
        area.tag_redraw()


# ---------------------------------------------------------------------------
# Modal operator
# ---------------------------------------------------------------------------


class LASERITY_OT_editor(Operator):
  """Arrange the flat parts on their sheets"""

  bl_idname = "laserity.editor"
  bl_label = "Laserity Nesting Editor"
  bl_options = {"REGISTER"}

  @classmethod
  def poll(cls, context):
    return (
      context.area is not None
      and context.area.type == "IMAGE_EDITOR"
      and not is_running(context.area)
    )

  # -- lifecycle -----------------------------------------------------------

  def invoke(self, context, event):
    settings = context.scene.laserity
    props.sync_placements(settings)
    nest.recompute_conflicts(settings)

    self._handle = None
    self.view = _View()
    self.mode = "IDLE"
    self.mouse = (event.mouse_region_x, event.mouse_region_y)
    self.box_start = (0.0, 0.0)
    self.drag_prev = self.mouse
    self.press_at = self.mouse
    self.grab_origin = (0.0, 0.0)
    self.grab_start = {}
    self.grab_by_drag = False
    self.rotate_pivot = (0.0, 0.0)
    self.rotate_start = 0.0

    self._area = context.area
    self._area_ptr = context.area.as_pointer()
    _running.add(self._area_ptr)
    _dress_area(context.area)

    self.frame(context.region, settings)
    self._handle = bpy.types.SpaceImageEditor.draw_handler_add(
      _draw_callback, (self,), "WINDOW", "POST_PIXEL"
    )
    context.window_manager.modal_handler_add(self)
    context.area.tag_redraw()
    return {"RUNNING_MODAL"}

  def _finish(self, context):
    if self._handle is not None:
      bpy.types.SpaceImageEditor.draw_handler_remove(self._handle, "WINDOW")
      self._handle = None
    _running.discard(self._area_ptr)
    tag_redraw()
    return {"FINISHED"}

  def _area_alive(self, context):
    for window in context.window_manager.windows:
      for area in window.screen.areas:
        if area.as_pointer() == self._area_ptr:
          return area.type == "IMAGE_EDITOR"
    return False

  # -- view ----------------------------------------------------------------

  def frame(self, region, settings, only_selected=False):
    if region is None:
      return
    inset = sidebar_inset(self._area)
    boxes = []
    if only_selected:
      for placement in settings.placements:
        if not placement.select:
          continue
        part = settings.parts.get(placement.part_name)
        cached = cache.get(part) if part is not None else None
        if cached is not None:
          boxes.append(placement_world_bounds(settings, placement, cached))
    if not boxes:
      boxes = [world_bounds(settings, spare_sheet_count(settings))]

    x0, y0, x1, y1 = geom2d.bbox_union(boxes)
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    pad = 60.0
    usable_w = max(region.width - inset - pad * 2.0, 50.0)
    usable_h = max(region.height - pad * 2.0, 50.0)
    zoom = min(usable_w / width, usable_h / height)
    self.view.zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
    self.view.ox = (region.width - inset) * 0.5 - (x0 + x1) * 0.5 * self.view.zoom
    self.view.oy = region.height * 0.5 - (y0 + y1) * 0.5 * self.view.zoom

  # -- events --------------------------------------------------------------

  def modal(self, context, event):
    if not self._area_alive(context):
      return self._finish(context)

    area = context.area
    if area is None or area.as_pointer() != self._area_ptr:
      return {"PASS_THROUGH"}
    region = context.region
    if region is None or region.type != "WINDOW":
      return {"PASS_THROUGH"}

    area.tag_redraw()
    settings = context.scene.laserity
    self.mouse = (event.mouse_region_x, event.mouse_region_y)

    if self.mode == "PAN":
      return self._modal_pan(event)
    if self.mode == "MOVE":
      return self._modal_move(context, settings, event)
    if self.mode == "ROTATE":
      return self._modal_rotate(context, settings, event)
    if self.mode == "BOX":
      return self._modal_box(settings, event)
    return self._modal_idle(context, region, settings, event)

  def _modal_pan(self, event):
    if event.type == "MOUSEMOVE":
      self.view.ox += self.mouse[0] - self.drag_prev[0]
      self.view.oy += self.mouse[1] - self.drag_prev[1]
      self.drag_prev = self.mouse
    elif event.type == "MIDDLEMOUSE" and event.value == "RELEASE":
      self.mode = "IDLE"
    return {"RUNNING_MODAL"}

  def _modal_idle(self, context, region, settings, event):
    if event.type in {"WHEELUPMOUSE", "NUMPAD_PLUS"} and event.value == "PRESS":
      self.view.zoom_at(*self.mouse, 1.15)
      return {"RUNNING_MODAL"}
    if event.type in {"WHEELDOWNMOUSE", "NUMPAD_MINUS"} and event.value == "PRESS":
      self.view.zoom_at(*self.mouse, 1.0 / 1.15)
      return {"RUNNING_MODAL"}
    if event.type == "TRACKPADZOOM":
      delta = event.mouse_y - event.mouse_prev_y
      self.view.zoom_at(*self.mouse, 1.0 + delta * 0.01)
      return {"RUNNING_MODAL"}
    if event.type == "TRACKPADPAN":
      self.view.ox += event.mouse_x - event.mouse_prev_x
      self.view.oy += event.mouse_y - event.mouse_prev_y
      return {"RUNNING_MODAL"}
    if event.type == "MIDDLEMOUSE" and event.value == "PRESS":
      self.mode = "PAN"
      self.drag_prev = self.mouse
      return {"RUNNING_MODAL"}

    if event.type == "LEFTMOUSE" and event.value == "PRESS":
      return self._on_click(settings, event)

    if event.value != "PRESS":
      return {"RUNNING_MODAL"}

    if event.type == "HOME":
      self.frame(region, settings)
    elif event.type == "F":
      self.frame(region, settings, only_selected=True)
    elif event.type == "A":
      select_all(settings, not event.alt)
    elif event.type == "ESC":
      select_all(settings, False)
    elif event.type == "G":
      self._begin_move(settings, by_drag=False)
    elif event.type == "R":
      if event.shift:
        if rotate_selected(settings, -math.pi * 0.5):
          self._commit(context, settings, "Rotate Parts")
      else:
        self._begin_rotate(settings)
    elif event.type == "M":
      if mirror_selected(settings):
        self._commit(context, settings, "Mirror Parts")
    elif event.type in {"X", "DEL"}:
      if remove_selected(settings):
        self._commit(context, settings, "Remove Parts")
    else:
      return {"PASS_THROUGH"}
    return {"RUNNING_MODAL"}

  def _on_click(self, settings, event):
    hit = pick(settings, self.view, *self.mouse)
    self.press_at = self.mouse
    if hit is None:
      if not event.shift:
        select_all(settings, False)
      self.mode = "BOX"
      self.box_start = self.mouse
      return {"RUNNING_MODAL"}

    placement = settings.placements[hit]
    if event.shift:
      placement.select = not placement.select
      return {"RUNNING_MODAL"}
    if not placement.select:
      select_all(settings, False)
      placement.select = True
    self._begin_move(settings, by_drag=True)
    return {"RUNNING_MODAL"}

  # -- transform sub-modes -------------------------------------------------

  def _begin_move(self, settings, by_drag):
    self.grab_start = {
      index: (placement.offset_x, placement.offset_y)
      for index, placement in enumerate(settings.placements)
      if placement.select
    }
    if not self.grab_start:
      return
    self.grab_origin = self.view.to_world(*self.mouse)
    self.grab_by_drag = by_drag
    self.mode = "MOVE"

  def _modal_move(self, context, settings, event):
    if event.type == "MOUSEMOVE":
      wx, wy = self.view.to_world(*self.mouse)
      dx = wx - self.grab_origin[0]
      dy = wy - self.grab_origin[1]
      if settings.grid_snap and settings.snap_size > 0.0:
        step = settings.snap_size
        dx = round(dx / step) * step
        dy = round(dy / step) * step
      for index, (start_x, start_y) in self.grab_start.items():
        placement = settings.placements[index]
        placement.offset_x = start_x + dx
        placement.offset_y = start_y + dy
      return {"RUNNING_MODAL"}

    confirmed = (
      (event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.grab_by_drag)
      or (event.type == "LEFTMOUSE" and event.value == "PRESS" and not self.grab_by_drag)
      or (event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS")
    )
    if confirmed:
      # A click that never moved is a plain selection click, not a nudge.
      travelled = abs(self.mouse[0] - self.press_at[0]) + abs(self.mouse[1] - self.press_at[1])
      self.mode = "IDLE"
      if self.grab_by_drag and travelled < DRAG_THRESHOLD:
        self._restore_move(settings)
        return {"RUNNING_MODAL"}
      rebase_sheets(settings)
      self._commit(context, settings, "Move Parts")
      return {"RUNNING_MODAL"}

    if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
      self._restore_move(settings)
      self.mode = "IDLE"
      return {"RUNNING_MODAL"}
    return {"RUNNING_MODAL"}

  def _restore_move(self, settings):
    for index, (start_x, start_y) in self.grab_start.items():
      placement = settings.placements[index]
      placement.offset_x = start_x
      placement.offset_y = start_y

  def _begin_rotate(self, settings):
    pivot = selection_pivot(settings)
    if pivot is None:
      return
    self.rotate_pivot = pivot
    self.grab_start = {
      index: (placement.offset_x, placement.offset_y, placement.rotation)
      for index, placement in enumerate(settings.placements)
      if placement.select
    }
    wx, wy = self.view.to_world(*self.mouse)
    self.rotate_start = math.atan2(wy - pivot[1], wx - pivot[0])
    self.mode = "ROTATE"

  def _modal_rotate(self, context, settings, event):
    if event.type == "MOUSEMOVE":
      wx, wy = self.view.to_world(*self.mouse)
      angle = math.atan2(wy - self.rotate_pivot[1], wx - self.rotate_pivot[0])
      delta = angle - self.rotate_start
      if event.ctrl:
        step = math.radians(15.0)
        delta = round(delta / step) * step
      self._apply_rotation(settings, delta)
      return {"RUNNING_MODAL"}

    if (event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"}) and event.value == "PRESS":
      self.mode = "IDLE"
      rebase_sheets(settings)
      self._commit(context, settings, "Rotate Parts")
      return {"RUNNING_MODAL"}

    if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
      self._apply_rotation(settings, 0.0)
      self.mode = "IDLE"
      return {"RUNNING_MODAL"}
    return {"RUNNING_MODAL"}

  def _apply_rotation(self, settings, delta):
    cos_a = math.cos(delta)
    sin_a = math.sin(delta)
    px, py = self.rotate_pivot
    for index, (start_x, start_y, start_rot) in self.grab_start.items():
      placement = settings.placements[index]
      origin_x, origin_y = sheet_origin(settings, placement.sheet)
      dx = start_x + origin_x - px
      dy = start_y + origin_y - py
      placement.offset_x = px + dx * cos_a - dy * sin_a - origin_x
      placement.offset_y = py + dx * sin_a + dy * cos_a - origin_y
      placement.rotation = start_rot + delta

  def _modal_box(self, settings, event):
    if event.type == "MOUSEMOVE":
      return {"RUNNING_MODAL"}
    if event.type == "LEFTMOUSE" and event.value == "RELEASE":
      self.mode = "IDLE"
      x0, x1 = sorted((self.box_start[0], self.mouse[0]))
      y0, y1 = sorted((self.box_start[1], self.mouse[1]))
      if (x1 - x0) + (y1 - y0) < DRAG_THRESHOLD:
        return {"RUNNING_MODAL"}
      for placement in settings.placements:
        part = settings.parts.get(placement.part_name)
        cached = cache.get(part) if part is not None else None
        if cached is None:
          continue
        points = _project(
          cached.geometry.outer_points(), placement_xform(settings, placement, self.view)
        )
        bx0, by0, bx1, by1 = geom2d.bbox(points)
        if geom2d.bboxes_overlap((x0, y0, x1, y1), (bx0, by0, bx1, by1)):
          placement.select = True
      return {"RUNNING_MODAL"}
    if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
      self.mode = "IDLE"
    return {"RUNNING_MODAL"}

  def _commit(self, context, settings, label):
    nest.recompute_conflicts(settings)
    tag_redraw()
    try:
      bpy.ops.ed.undo_push(message=label)
    except RuntimeError:
      pass


# ---------------------------------------------------------------------------
# Opening the editor window
# ---------------------------------------------------------------------------


def _deferred_start(window_ptr, area_ptr):
  """Start the modal operator once Blender has finished building the new window.

  The screen layout of a freshly opened window is not complete during the
  operator that created it, so its regions cannot be overridden yet. A timer
  gives Blender a chance to catch up.
  """
  attempts = [0]

  def run():
    attempts[0] += 1
    for window in bpy.context.window_manager.windows:
      if window.as_pointer() != window_ptr:
        continue
      for area in window.screen.areas:
        if area.as_pointer() != area_ptr:
          continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None or region.width <= 1:
          return None if attempts[0] > 20 else 0.05
        with bpy.context.temp_override(window=window, area=area, region=region):
          bpy.ops.laserity.editor("INVOKE_DEFAULT")
        return None
    return None

  return run


class LASERITY_OT_open_editor(Operator):
  """Open the nesting editor in its own window"""

  bl_idname = "laserity.open_editor"
  bl_label = "Open Nesting Editor"
  bl_options = {"REGISTER"}

  def execute(self, context):
    settings = context.scene.laserity
    props.sync_placements(settings)
    if not settings.placements:
      self.report({"WARNING"}, "Nothing to arrange — scan the scene first")
      return {"CANCELLED"}

    known = {window.as_pointer() for window in context.window_manager.windows}
    bpy.ops.wm.window_new()
    opened = [w for w in context.window_manager.windows if w.as_pointer() not in known]
    if not opened:
      self.report({"ERROR"}, "Blender did not open a new window")
      return {"CANCELLED"}

    window = opened[0]
    area = max(window.screen.areas, key=lambda a: a.width * a.height)
    area.type = "IMAGE_EDITOR"
    try:
      area.ui_type = "IMAGE_EDITOR"
      space = area.spaces.active
      space.show_region_ui = True
      space.show_region_tool_header = False
    except (AttributeError, TypeError):
      pass

    bpy.app.timers.register(
      _deferred_start(window.as_pointer(), area.as_pointer()), first_interval=0.05
    )
    return {"FINISHED"}


class LASERITY_OT_placement_action(Operator):
  """Act on the parts selected in the nesting editor"""

  bl_idname = "laserity.placement_action"
  bl_label = "Placement Action"
  bl_options = {"REGISTER", "UNDO"}

  action: EnumProperty(
    name="Action",
    items=(
      ("SELECT_ALL", "Select All", "Select every part"),
      ("SELECT_NONE", "Select None", "Clear the selection"),
      ("SELECT_INVERT", "Invert", "Invert the selection"),
      ("ROTATE_CCW", "Rotate 90 CCW", "Turn the selection a quarter turn anticlockwise"),
      ("ROTATE_CW", "Rotate 90 CW", "Turn the selection a quarter turn clockwise"),
      ("MIRROR", "Mirror", "Flip the selection"),
      ("REMOVE", "Remove", "Remove the selected copies"),
    ),
    default="SELECT_ALL",
  )

  def execute(self, context):
    settings = context.scene.laserity
    action = self.action

    if action == "SELECT_ALL":
      select_all(settings, True)
    elif action == "SELECT_NONE":
      select_all(settings, False)
    elif action == "SELECT_INVERT":
      for placement in settings.placements:
        placement.select = not placement.select
    elif action == "ROTATE_CCW":
      rotate_selected(settings, math.pi * 0.5)
    elif action == "ROTATE_CW":
      rotate_selected(settings, -math.pi * 0.5)
    elif action == "MIRROR":
      mirror_selected(settings)
    elif action == "REMOVE":
      remove_selected(settings)

    if action not in {"SELECT_ALL", "SELECT_NONE", "SELECT_INVERT"}:
      rebase_sheets(settings)
      nest.recompute_conflicts(settings)
    tag_redraw()
    return {"FINISHED"}


CLASSES = (
  LASERITY_OT_editor,
  LASERITY_OT_open_editor,
  LASERITY_OT_placement_action,
)


def register():
  for cls in CLASSES:
    bpy.utils.register_class(cls)


def unregister():
  _running.clear()
  for cls in reversed(CLASSES):
    bpy.utils.unregister_class(cls)
