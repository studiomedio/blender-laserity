"""Operators: scanning the scene, arranging sheets and writing the SVG."""

from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper

from . import cache, nest, props
from .core import detect, flatten, svg


def _source_objects(context, scope):
  if scope == "SELECTED":
    return list(context.selected_objects)
  if scope == "ALL":
    return list(context.scene.objects)
  return [obj for obj in context.view_layer.objects if obj.visible_get()]


class LASERITY_OT_scan(Operator):
  """Look through the scene for objects that are flat enough to cut from sheet material"""

  bl_idname = "laserity.scan"
  bl_label = "Scan Scene"
  bl_options = {"REGISTER", "UNDO"}

  keep_arrangement: BoolProperty(
    name="Keep Arrangement",
    description="Leave the current sheet layout alone instead of re-packing after the scan",
    default=False,
  )

  def execute(self, context):
    settings = context.scene.laserity
    depsgraph = context.evaluated_depsgraph_get()
    detect_settings = nest.detect_settings(context)
    options = nest.flatten_options(settings)

    # Remember what the user had already decided about each part so a re-scan
    # after editing the model does not throw those choices away.
    previous = {p.name: (p.enabled, p.quantity) for p in settings.parts}

    settings.parts.clear()
    settings.skipped.clear()
    cache.clear()

    found = 0
    for obj in _source_objects(context, settings.scan_scope):
      if obj.type not in detect.CONVERTIBLE_TYPES:
        continue
      try:
        analysis, geometry = flatten.build(obj, depsgraph, detect_settings, options)
      except Exception as error:  # a broken mesh must not abort the whole scan
        entry = settings.skipped.add()
        entry.name = obj.name
        entry.reason = f"failed to flatten ({error})"
        continue

      if geometry is None:
        entry = settings.skipped.add()
        entry.name = obj.name
        entry.reason = analysis.reason if analysis is not None else "nothing to convert"
        continue

      part = settings.parts.add()
      part.name = obj.name
      part.obj = obj
      part.thickness = analysis.thickness
      part.thickness_key = detect.thickness_key(analysis.thickness, settings.thickness_tolerance)
      part.width, part.height = geometry.size()
      part.hole_count = geometry.hole_count()
      part.method = geometry.method
      part.geometry_json = geometry.to_json()
      part.enabled, part.quantity = previous.get(obj.name, (True, 1))
      found += 1

    settings.active_part = min(settings.active_part, max(found - 1, 0))
    settings.placements.clear()
    props.sync_placements(settings)

    if found and not self.keep_arrangement:
      nest.arrange(settings, mode="PACK")

    skipped = len(settings.skipped)
    settings.last_report = f"{found} flat part{'' if found == 1 else 's'}, {skipped} skipped"
    if not found:
      self.report({"WARNING"}, f"No flat parts found ({skipped} objects did not qualify)")
    else:
      self.report({"INFO"}, f"Laserity: {settings.last_report}")
    return {"FINISHED"}


class LASERITY_OT_clear(Operator):
  """Forget every part found so far"""

  bl_idname = "laserity.clear"
  bl_label = "Clear Parts"
  bl_options = {"REGISTER", "UNDO"}

  def execute(self, context):
    settings = context.scene.laserity
    settings.parts.clear()
    settings.skipped.clear()
    settings.placements.clear()
    settings.last_report = ""
    cache.clear()
    return {"FINISHED"}


class LASERITY_OT_set_all(Operator):
  """Include or exclude every part at once"""

  bl_idname = "laserity.set_all"
  bl_label = "Set All"
  bl_options = {"REGISTER", "UNDO"}

  value: BoolProperty(name="Include", default=True)

  def execute(self, context):
    settings = context.scene.laserity
    with props.muted_sync():
      for part in settings.parts:
        part.enabled = self.value
    props.sync_placements(settings)
    nest.recompute_conflicts(settings)
    return {"FINISHED"}


class LASERITY_OT_select_source(Operator):
  """Select this part's object in the scene and make it active"""

  bl_idname = "laserity.select_source"
  bl_label = "Select Object"
  bl_options = {"REGISTER", "UNDO"}

  part_name: StringProperty(name="Part", default="")

  def execute(self, context):
    settings = context.scene.laserity
    part = settings.parts.get(self.part_name)
    if part is None or part.obj is None:
      self.report({"WARNING"}, "That part's object is gone from the scene")
      return {"CANCELLED"}

    for obj in context.selected_objects:
      obj.select_set(False)
    try:
      part.obj.select_set(True)
      context.view_layer.objects.active = part.obj
    except RuntimeError:
      self.report({"WARNING"}, f"{part.obj.name} is not selectable in this view layer")
      return {"CANCELLED"}
    return {"FINISHED"}


class LASERITY_OT_arrange(Operator):
  """Lay the parts out on sheets, keeping each stock thickness on its own sheets"""

  bl_idname = "laserity.arrange"
  bl_label = "Arrange"
  bl_options = {"REGISTER", "UNDO"}

  mode: EnumProperty(
    name="Mode",
    items=(
      ("PACK", "Auto Nest", "Pack parts tightly using their tightest bounding rectangle"),
      ("GRID", "Grid", "Lay parts out in plain rows, unrotated"),
    ),
    default="PACK",
  )

  def execute(self, context):
    settings = context.scene.laserity
    props.sync_placements(settings)
    if not settings.placements:
      self.report({"WARNING"}, "Nothing to arrange — scan the scene first")
      return {"CANCELLED"}

    placed, unplaced = nest.arrange(settings, mode=self.mode)
    sheets = settings.sheet_count()
    message = f"{placed} part{'' if placed == 1 else 's'} on {sheets} sheet{'' if sheets == 1 else 's'}"
    if unplaced:
      self.report({"WARNING"}, f"{message} — {unplaced} too large for the sheet")
    else:
      self.report({"INFO"}, message)
    settings.last_report = message
    _redraw(context)
    return {"FINISHED"}


class LASERITY_OT_check(Operator):
  """Re-check the layout for overlaps, spacing and off-sheet parts"""

  bl_idname = "laserity.check"
  bl_label = "Check Layout"
  bl_options = {"REGISTER"}

  def execute(self, context):
    settings = context.scene.laserity
    count = nest.recompute_conflicts(settings)
    if count:
      self.report({"WARNING"}, f"{count} part{'' if count == 1 else 's'} need attention")
    else:
      self.report({"INFO"}, "Layout is clear")
    _redraw(context)
    return {"FINISHED"}


class LASERITY_OT_export_svg(Operator, ExportHelper):
  """Write the arranged sheets to SVG, one file per sheet"""

  bl_idname = "laserity.export_svg"
  bl_label = "Export SVG"
  bl_options = {"REGISTER"}

  filename_ext = ".svg"
  filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

  @classmethod
  def poll(cls, context):
    settings = getattr(context.scene, "laserity", None)
    return bool(settings and settings.placements)

  def invoke(self, context, event):
    settings = context.scene.laserity
    if settings.export_path:
      self.filepath = bpy.path.abspath(settings.export_path)
    elif bpy.data.filepath:
      self.filepath = os.path.splitext(bpy.data.filepath)[0] + ".svg"
    context.window_manager.fileselect_add(self)
    return {"RUNNING_MODAL"}

  def execute(self, context):
    settings = context.scene.laserity
    props.sync_placements(settings)
    parts = nest.placed_parts(settings)
    if not parts:
      self.report({"ERROR"}, "Nothing to export — scan the scene and arrange the parts first")
      return {"CANCELLED"}

    conflicts = nest.recompute_conflicts(settings)
    try:
      written = svg.write(self.filepath, parts, nest.svg_options(settings))
    except OSError as error:
      self.report({"ERROR"}, f"Could not write the SVG: {error}")
      return {"CANCELLED"}

    settings.export_path = self.filepath
    count = len(written)
    message = f"Wrote {count} SVG file{'' if count == 1 else 's'} to {os.path.dirname(written[0])}"
    if conflicts:
      self.report({"WARNING"}, f"{message} — {conflicts} parts still overlap or hang off the sheet")
    else:
      self.report({"INFO"}, message)
    return {"FINISHED"}


def _redraw(context):
  for window in context.window_manager.windows:
    for area in window.screen.areas:
      if area.type in {"IMAGE_EDITOR", "VIEW_3D"}:
        area.tag_redraw()


def menu_export(self, context):
  self.layout.operator(LASERITY_OT_export_svg.bl_idname, text="Laser Sheets (.svg)")


CLASSES = (
  LASERITY_OT_scan,
  LASERITY_OT_clear,
  LASERITY_OT_set_all,
  LASERITY_OT_select_source,
  LASERITY_OT_arrange,
  LASERITY_OT_check,
  LASERITY_OT_export_svg,
)


def register():
  for cls in CLASSES:
    bpy.utils.register_class(cls)
  bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
  bpy.types.TOPBAR_MT_file_export.remove(menu_export)
  for cls in reversed(CLASSES):
    bpy.utils.unregister_class(cls)
