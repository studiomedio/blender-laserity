"""Panels for the 3D viewport sidebar and for the nesting editor's sidebar."""

from __future__ import annotations

import bpy
from bpy.types import Panel, UIList

from . import cache, editor, nest
from .core.detect import format_thickness


class LASERITY_UL_parts(UIList):
  def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
    if self.layout_type == "GRID":
      layout.alignment = "CENTER"
      layout.label(text="", icon="MESH_PLANE")
      return

    row = layout.row(align=True)
    row.prop(item, "enabled", text="")
    name = row.row()
    name.active = item.enabled
    name.label(text=item.name, icon="MESH_PLANE")

    tail = row.row(align=True)
    tail.active = item.enabled
    tail.alignment = "RIGHT"
    tail.label(text=format_thickness(item.thickness))
    tail.prop(item, "quantity", text="")


class _Sidebar:
  bl_space_type = "VIEW_3D"
  bl_region_type = "UI"
  bl_category = "Laserity"


class LASERITY_PT_parts(_Sidebar, Panel):
  bl_label = "Flat Parts"
  bl_idname = "LASERITY_PT_parts"

  def draw(self, context):
    layout = self.layout
    settings = context.scene.laserity

    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("laserity.scan", icon="VIEWZOOM")
    row.prop(settings, "scan_scope", text="")

    if settings.last_report:
      layout.label(text=settings.last_report, icon="INFO")

    layout.template_list(
      "LASERITY_UL_parts", "", settings, "parts", settings, "active_part", rows=6
    )

    row = layout.row(align=True)
    row.operator("laserity.set_all", text="Include All").value = True
    row.operator("laserity.set_all", text="Exclude All").value = False
    row.operator("laserity.clear", text="", icon="TRASH")

    if 0 <= settings.active_part < len(settings.parts):
      self._draw_details(layout, settings, settings.parts[settings.active_part])

  def _draw_details(self, layout, settings, part):
    box = layout.box()
    column = box.column(align=True)
    column.label(text=part.name, icon="OBJECT_DATA")

    grid = box.grid_flow(columns=2, even_columns=True, align=True)
    grid.label(text="Thickness")
    grid.label(text=format_thickness(part.thickness))
    grid.label(text="Stock")
    grid.label(text=format_thickness(part.thickness_key))
    grid.label(text="Size")
    grid.label(text=f"{part.width:.1f} × {part.height:.1f} mm")
    if part.hole_count:
      grid.label(text="Holes")
      grid.label(text=str(part.hole_count))
    grid.label(text="Traced by")
    grid.label(text=part.method.replace("_", " ").title())

    cached = cache.get(part)
    if cached is not None:
      grid.label(text="Material")
      grid.label(text=f"{cached.geometry.net_area() / 100.0:.1f} cm²")

    box.operator("laserity.select_source", icon="RESTRICT_SELECT_OFF").part_name = part.name


class LASERITY_PT_material(_Sidebar, Panel):
  bl_label = "Sheet"
  bl_idname = "LASERITY_PT_material"

  def draw(self, context):
    layout = self.layout
    layout.use_property_split = True
    settings = context.scene.laserity

    layout.prop(settings, "sheet_preset")
    column = layout.column(align=True)
    column.prop(settings, "sheet_width")
    column.prop(settings, "sheet_height")
    column = layout.column(align=True)
    column.prop(settings, "margin")
    column.prop(settings, "spacing")


class LASERITY_PT_detection(_Sidebar, Panel):
  bl_label = "Detection"
  bl_idname = "LASERITY_PT_detection"
  bl_parent_id = "LASERITY_PT_material"
  bl_options = {"DEFAULT_CLOSED"}

  def draw(self, context):
    layout = self.layout
    layout.use_property_split = True
    settings = context.scene.laserity

    layout.prop(settings, "method")
    column = layout.column(align=True)
    column.prop(settings, "max_thickness")
    column.prop(settings, "thickness_tolerance")
    column = layout.column(align=True)
    column.prop(settings, "min_planarity")
    column.prop(settings, "min_flatness_ratio")
    column.prop(settings, "min_area")
    column = layout.column(align=True)
    column.prop(settings, "normal_angle")
    column.prop(settings, "simplify_tolerance")


class LASERITY_PT_engraving(_Sidebar, Panel):
  bl_label = "Engraving"
  bl_idname = "LASERITY_PT_engraving"
  bl_parent_id = "LASERITY_PT_material"
  bl_options = {"DEFAULT_CLOSED"}

  def draw(self, context):
    layout = self.layout
    layout.use_property_split = True
    settings = context.scene.laserity

    layout.prop(settings, "engrave_material_prefix")
    layout.prop(settings, "engrave_edge_mark")
    sub = layout.column()
    sub.active = settings.engrave_edge_mark != "NONE"
    sub.prop(settings, "engrave_top_only")


class LASERITY_PT_skipped(_Sidebar, Panel):
  bl_label = "Skipped Objects"
  bl_idname = "LASERITY_PT_skipped"
  bl_parent_id = "LASERITY_PT_parts"
  bl_options = {"DEFAULT_CLOSED"}

  @classmethod
  def poll(cls, context):
    return bool(context.scene.laserity.skipped)

  def draw(self, context):
    layout = self.layout
    column = layout.column(align=True)
    for entry in context.scene.laserity.skipped:
      row = column.row()
      row.label(text=entry.name, icon="DOT")
      sub = row.row()
      sub.alignment = "RIGHT"
      sub.label(text=entry.reason)


def _draw_arrange(layout, settings):
  column = layout.column(align=True)
  column.scale_y = 1.2
  column.operator("laserity.arrange", text="Auto Nest", icon="MOD_ARRAY").mode = "PACK"
  row = column.row(align=True)
  row.operator("laserity.arrange", text="Grid").mode = "GRID"
  row.operator("laserity.check", text="Check")

  column = layout.column(align=True)
  column.use_property_split = True
  column.prop(settings, "allow_rotation")
  column.prop(settings, "use_min_rect")


def _draw_sheet_stats(layout, settings):
  stats = nest.statistics(settings)
  if not stats:
    return
  conflicts = sum(1 for placement in settings.placements if placement.conflict)

  box = layout.box()
  column = box.column(align=True)
  for sheet in sorted(stats):
    count, thickness, fill = stats[sheet]
    row = column.row()
    row.label(text=f"Sheet {sheet + 1}")
    sub = row.row()
    sub.alignment = "RIGHT"
    sub.label(text=f"{format_thickness(thickness)} · {count} parts · {fill * 100.0:.0f}% used")
  if conflicts:
    box.label(text=f"{conflicts} parts need attention", icon="ERROR")


class LASERITY_PT_arrange(_Sidebar, Panel):
  bl_label = "Arrange"
  bl_idname = "LASERITY_PT_arrange"

  def draw(self, context):
    layout = self.layout
    settings = context.scene.laserity

    row = layout.row()
    row.scale_y = 1.4
    row.enabled = bool(settings.placements)
    row.operator("laserity.open_editor", icon="WINDOW")

    _draw_arrange(layout, settings)
    _draw_sheet_stats(layout, settings)


class LASERITY_PT_export(_Sidebar, Panel):
  bl_label = "Export"
  bl_idname = "LASERITY_PT_export"

  def draw(self, context):
    layout = self.layout
    layout.use_property_split = True
    settings = context.scene.laserity

    layout.prop(settings, "export_path", text="Path")
    column = layout.column(align=True)
    column.prop(settings, "cut_start_layer")
    column.prop(settings, "engrave_layer")
    column = layout.column(align=True)
    column.prop(settings, "stroke_width")
    column.prop(settings, "kerf")
    column.prop(settings, "precision")
    layout.prop(settings, "draw_sheet_outline")

    row = layout.row()
    row.scale_y = 1.4
    row.operator("laserity.export_svg", icon="EXPORT")


# ---------------------------------------------------------------------------
# Nesting editor sidebar
# ---------------------------------------------------------------------------


class _EditorSidebar:
  bl_space_type = "IMAGE_EDITOR"
  bl_region_type = "UI"
  bl_category = "Laserity"


class LASERITY_PT_editor_tools(_EditorSidebar, Panel):
  bl_label = "Nesting"
  bl_idname = "LASERITY_PT_editor_tools"

  def draw(self, context):
    layout = self.layout
    settings = context.scene.laserity

    if not editor.is_running(context.area):
      column = layout.column()
      column.scale_y = 1.4
      column.operator("laserity.editor", text="Start Editor Here", icon="PLAY")
      layout.label(text="Or close this window and reopen", icon="INFO")
      layout.label(text="the editor from the 3D viewport.")
      return

    selected = sum(1 for placement in settings.placements if placement.select)
    layout.label(text=f"{selected} of {len(settings.placements)} selected")

    row = layout.row(align=True)
    row.operator("laserity.placement_action", text="All").action = "SELECT_ALL"
    row.operator("laserity.placement_action", text="None").action = "SELECT_NONE"
    row.operator("laserity.placement_action", text="Invert").action = "SELECT_INVERT"

    column = layout.column(align=True)
    column.enabled = selected > 0
    row = column.row(align=True)
    row.operator("laserity.placement_action", text="Rotate 90°", icon="LOOP_BACK").action = "ROTATE_CCW"
    row.operator("laserity.placement_action", text="", icon="LOOP_FORWARDS").action = "ROTATE_CW"
    row = column.row(align=True)
    row.operator("laserity.placement_action", text="Mirror", icon="MOD_MIRROR").action = "MIRROR"
    row.operator("laserity.placement_action", text="Remove", icon="X").action = "REMOVE"


class LASERITY_PT_editor_layout(_EditorSidebar, Panel):
  bl_label = "Layout"
  bl_idname = "LASERITY_PT_editor_layout"

  def draw(self, context):
    layout = self.layout
    settings = context.scene.laserity

    _draw_arrange(layout, settings)

    column = layout.column(align=True)
    column.use_property_split = True
    column.prop(settings, "grid_snap")
    sub = column.column(align=True)
    sub.active = settings.grid_snap
    sub.prop(settings, "snap_size")
    column.prop(settings, "show_conflicts")
    column.prop(settings, "show_part_names")

    _draw_sheet_stats(layout, settings)


class LASERITY_PT_editor_sheet(_EditorSidebar, Panel):
  bl_label = "Sheet"
  bl_idname = "LASERITY_PT_editor_sheet"
  bl_options = {"DEFAULT_CLOSED"}

  def draw(self, context):
    layout = self.layout
    layout.use_property_split = True
    settings = context.scene.laserity

    layout.prop(settings, "sheet_preset")
    column = layout.column(align=True)
    column.prop(settings, "sheet_width")
    column.prop(settings, "sheet_height")
    column = layout.column(align=True)
    column.prop(settings, "margin")
    column.prop(settings, "spacing")


class LASERITY_PT_editor_export(_EditorSidebar, Panel):
  bl_label = "Export"
  bl_idname = "LASERITY_PT_editor_export"

  def draw(self, context):
    layout = self.layout
    settings = context.scene.laserity
    layout.use_property_split = True
    layout.prop(settings, "kerf")
    row = layout.row()
    row.scale_y = 1.4
    row.operator("laserity.export_svg", icon="EXPORT")


CLASSES = (
  LASERITY_UL_parts,
  LASERITY_PT_parts,
  LASERITY_PT_skipped,
  LASERITY_PT_material,
  LASERITY_PT_detection,
  LASERITY_PT_engraving,
  LASERITY_PT_arrange,
  LASERITY_PT_export,
  LASERITY_PT_editor_tools,
  LASERITY_PT_editor_layout,
  LASERITY_PT_editor_sheet,
  LASERITY_PT_editor_export,
)


def register():
  for cls in CLASSES:
    bpy.utils.register_class(cls)


def unregister():
  for cls in reversed(CLASSES):
    bpy.utils.unregister_class(cls)
