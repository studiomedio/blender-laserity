"""Property definitions and the placement bookkeeping that goes with them.

The scene owns a single :class:`LaseritySettings` block holding the material and
export options, the list of flat parts found by a scan, and one *placement* per
copy of a part that will actually be cut. Keeping placements separate from parts
is what lets a part be cut several times and lets each copy be moved, rotated and
mirrored on its own.
"""

from __future__ import annotations

import contextlib

import bpy
from bpy.props import (
  BoolProperty,
  CollectionProperty,
  EnumProperty,
  FloatProperty,
  IntProperty,
  PointerProperty,
  StringProperty,
)
from bpy.types import PropertyGroup

from .core import lightburn
from .core.part import METHOD_AUTO, METHOD_SECTION, METHOD_TOP_FACES

# Enum item tuples are kept at module level: Blender does not keep a reference to
# strings returned from a callback, and stale pointers show up as garbled labels.
_LAYER_ITEMS = tuple(lightburn.enum_items())

_SHEET_PRESETS = (
  ("CUSTOM", "Custom", "Sheet size entered by hand"),
  ("300X200", "300 x 200 mm", "Small diode bed"),
  ("400X400", "400 x 400 mm", "Common diode bed"),
  ("600X400", "600 x 400 mm", "K40 / entry level CO2 bed"),
  ("900X600", "900 x 600 mm", "Mid size CO2 bed"),
  ("1300X900", "1300 x 900 mm", "Large CO2 bed"),
  ("1220X610", "1220 x 610 mm", "4 x 2 ft plywood sheet"),
  ("2440X1220", "2440 x 1220 mm", "8 x 4 ft plywood sheet"),
)

_PRESET_SIZES = {
  "300X200": (300.0, 200.0),
  "400X400": (400.0, 400.0),
  "600X400": (600.0, 400.0),
  "900X600": (900.0, 600.0),
  "1300X900": (1300.0, 900.0),
  "1220X610": (1220.0, 610.0),
  "2440X1220": (2440.0, 1220.0),
}

METHOD_ITEMS = (
  (METHOD_AUTO, "Auto", "Trace the top faces, and fall back to a mid-thickness section"),
  (METHOD_TOP_FACES, "Top Faces", "Trace the outline of the faces on the slab's upper plane"),
  (METHOD_SECTION, "Section", "Slice the solid at mid-thickness and use the cross-section"),
)

EDGE_MARK_ITEMS = (
  ("NONE", "None", "Do not turn marked edges into engraving"),
  ("FREESTYLE", "Freestyle Mark", "Edges marked for Freestyle become engraved lines"),
  ("SEAM", "UV Seam", "Edges marked as UV seams become engraved lines"),
  ("SHARP", "Sharp", "Edges marked sharp become engraved lines"),
)

SCAN_SCOPE_ITEMS = (
  ("VISIBLE", "Visible", "Every visible object in the view layer"),
  ("SELECTED", "Selected", "Only the selected objects"),
  ("ALL", "All", "Every object in the scene, hidden ones included"),
)


# ---------------------------------------------------------------------------
# Placement synchronisation
# ---------------------------------------------------------------------------

_sync_muted = False


@contextlib.contextmanager
def muted_sync():
  """Suppress :func:`sync_placements` while placements are edited directly.

  The editor removes individual copies and then writes the surviving count back
  to the part; without this the property update would immediately undo that.
  """
  global _sync_muted
  previous = _sync_muted
  _sync_muted = True
  try:
    yield
  finally:
    _sync_muted = previous


def sync_placements(settings):
  """Make the placement list match each part's enabled flag and quantity.

  Existing placements keep their position, so changing one part's quantity never
  disturbs an arrangement the user has already tuned.
  """
  if _sync_muted:
    return

  wanted = {}
  for part in settings.parts:
    wanted[part.name] = part.quantity if (part.enabled and part.geometry_json) else 0

  kept = {}
  for index in range(len(settings.placements) - 1, -1, -1):
    placement = settings.placements[index]
    seen = kept.get(placement.part_name, 0)
    if seen >= wanted.get(placement.part_name, 0):
      settings.placements.remove(index)
    else:
      kept[placement.part_name] = seen + 1

  for part in settings.parts:
    for _ in range(wanted[part.name] - kept.get(part.name, 0)):
      placement = settings.placements.add()
      placement.part_name = part.name


def _sync_update(self, context):
  settings = getattr(context.scene, "laserity", None)
  if settings is not None:
    sync_placements(settings)


def _preset_update(self, context):
  size = _PRESET_SIZES.get(self.sheet_preset)
  if size is not None:
    self.sheet_width, self.sheet_height = size


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


class LaserityPart(PropertyGroup):
  """One flat part discovered in the scene, with its flattened outline cached."""

  obj: PointerProperty(
    type=bpy.types.Object,
    name="Object",
    description="Scene object this part was flattened from",
  )
  enabled: BoolProperty(
    name="Include",
    description="Include this part when arranging and exporting",
    default=True,
    update=_sync_update,
  )
  quantity: IntProperty(
    name="Quantity",
    description="How many copies of this part to cut",
    default=1,
    min=1,
    max=999,
    update=_sync_update,
  )
  thickness: FloatProperty(name="Thickness", description="Measured thickness in mm", default=0.0)
  thickness_key: FloatProperty(
    name="Stock Thickness",
    description="Thickness rounded to the grouping tolerance; parts sharing it are cut together",
    default=0.0,
  )
  width: FloatProperty(name="Width", default=0.0)
  height: FloatProperty(name="Height", default=0.0)
  hole_count: IntProperty(name="Holes", default=0)
  method: StringProperty(name="Method", default=METHOD_AUTO)
  geometry_json: StringProperty(name="Geometry", default="", options={"HIDDEN"})


class LaseritySkipped(PropertyGroup):
  """An object the scan looked at and rejected, kept so the UI can explain why."""

  reason: StringProperty(name="Reason", default="")


class LaserityPlacement(PropertyGroup):
  """One copy of a part, positioned on a sheet."""

  part_name: StringProperty(name="Part", default="")
  sheet: IntProperty(name="Sheet", default=0, min=0)
  rotation: FloatProperty(name="Rotation", default=0.0, subtype="ANGLE")
  offset_x: FloatProperty(name="X", default=0.0, unit="NONE")
  offset_y: FloatProperty(name="Y", default=0.0, unit="NONE")
  mirrored: BoolProperty(name="Mirrored", default=False)
  select: BoolProperty(name="Selected", default=False)
  conflict: BoolProperty(name="Conflict", default=False)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class LaseritySettings(PropertyGroup):
  # -- material sheet ------------------------------------------------------
  sheet_preset: EnumProperty(
    name="Sheet",
    description="Pick a common bed or stock size",
    items=_SHEET_PRESETS,
    default="600X400",
    update=_preset_update,
  )
  sheet_width: FloatProperty(
    name="Width", description="Sheet width in millimetres", default=600.0, min=1.0, soft_max=3000.0
  )
  sheet_height: FloatProperty(
    name="Height", description="Sheet height in millimetres", default=400.0, min=1.0, soft_max=3000.0
  )
  margin: FloatProperty(
    name="Margin",
    description="Unusable border kept clear around the sheet, in millimetres",
    default=5.0,
    min=0.0,
    soft_max=50.0,
  )
  spacing: FloatProperty(
    name="Spacing",
    description="Gap kept between neighbouring parts, in millimetres",
    default=2.0,
    min=0.0,
    soft_max=50.0,
  )

  # -- detection -----------------------------------------------------------
  scan_scope: EnumProperty(
    name="Scan", description="Which objects to look at", items=SCAN_SCOPE_ITEMS, default="VISIBLE"
  )
  max_thickness: FloatProperty(
    name="Max Thickness",
    description="Objects thicker than this are not treated as sheet material",
    default=50.0,
    min=0.01,
    soft_max=200.0,
  )
  thickness_tolerance: FloatProperty(
    name="Group Tolerance",
    description="Thicknesses within this distance are treated as the same stock",
    default=0.1,
    min=0.0,
    soft_max=2.0,
  )
  min_planarity: FloatProperty(
    name="Slab Strictness",
    description="Fraction of vertices that must sit on the two outer faces",
    default=0.8,
    min=0.0,
    max=1.0,
    subtype="FACTOR",
  )
  min_flatness_ratio: FloatProperty(
    name="Flatness Ratio",
    description="Shortest in-plane side must be at least this many times the thickness",
    default=1.5,
    min=0.0,
    soft_max=10.0,
  )
  min_area: FloatProperty(
    name="Min Area",
    description="Ignore parts whose face area is under this, in square millimetres",
    default=1.0,
    min=0.0,
    soft_max=1000.0,
  )
  normal_angle: FloatProperty(
    name="Normal Tolerance",
    description="How far a face normal may stray and still count as part of the flat surface",
    default=0.0349066,  # 2 degrees
    min=0.0,
    max=0.5236,  # 30 degrees
    subtype="ANGLE",
  )
  method: EnumProperty(
    name="Method", description="How the outline is traced", items=METHOD_ITEMS, default=METHOD_AUTO
  )
  simplify_tolerance: FloatProperty(
    name="Simplify",
    description="Drop outline points that stray less than this from the line, in millimetres",
    default=0.01,
    min=0.0,
    soft_max=1.0,
  )

  # -- engraving -----------------------------------------------------------
  engrave_material_prefix: StringProperty(
    name="Material Prefix",
    description="Faces whose material name starts with this are exported as engraved regions",
    default="LZ_ENGRAVE",
  )
  engrave_edge_mark: EnumProperty(
    name="Edge Mark",
    description="Which edge marking becomes an engraved line",
    items=EDGE_MARK_ITEMS,
    default="FREESTYLE",
  )
  engrave_top_only: BoolProperty(
    name="Top Face Only",
    description="Only take marked edges lying on the part's upper surface",
    default=True,
  )

  # -- nesting -------------------------------------------------------------
  allow_rotation: BoolProperty(
    name="Allow Rotation", description="Let the packer turn parts 90 degrees", default=True
  )
  use_min_rect: BoolProperty(
    name="Align to Min Rect",
    description="Rotate each part so its tightest bounding rectangle is axis aligned before packing",
    default=True,
  )
  max_sheets: IntProperty(
    name="Max Sheets", description="Stop after this many sheets", default=16, min=1, max=64
  )

  # -- export --------------------------------------------------------------
  export_path: StringProperty(
    name="Export Path",
    description="Where the SVG sheets are written. One file per sheet",
    default="//laserity.svg",
    subtype="FILE_PATH",
  )
  stroke_width: FloatProperty(
    name="Stroke Width",
    description="Hairline width written into the SVG, in millimetres",
    default=0.1,
    min=0.001,
    soft_max=1.0,
  )
  kerf: FloatProperty(
    name="Kerf",
    description=(
      "Beam width to compensate for. Cut paths grow outwards by half of this. "
      "Leave at zero unless you know your kerf"
    ),
    default=0.0,
    min=0.0,
    soft_max=1.0,
  )
  cut_start_layer: EnumProperty(
    name="First Cut Layer",
    description="LightBurn layer for the thinnest stock; further thicknesses take the next layers",
    items=_LAYER_ITEMS,
    default="1",
  )
  engrave_layer: EnumProperty(
    name="Engrave Layer",
    description="LightBurn layer used for engraved regions and lines",
    items=_LAYER_ITEMS,
    default="2",
  )
  draw_sheet_outline: BoolProperty(
    name="Draw Sheet Outline",
    description="Write the sheet border into the SVG, on its own layer",
    default=False,
  )
  precision: IntProperty(
    name="Precision", description="Decimal places written into the SVG", default=3, min=1, max=6
  )

  # -- editor --------------------------------------------------------------
  grid_snap: BoolProperty(
    name="Snap", description="Snap parts to a grid while dragging", default=False
  )
  snap_size: FloatProperty(
    name="Snap Size", description="Grid step in millimetres", default=1.0, min=0.01, soft_max=50.0
  )
  show_conflicts: BoolProperty(
    name="Highlight Conflicts",
    description="Mark parts that overlap, break the spacing or share a sheet with other stock",
    default=True,
  )
  show_part_names: BoolProperty(name="Part Names", description="Label parts in the editor", default=True)

  # -- data ----------------------------------------------------------------
  parts: CollectionProperty(type=LaserityPart)
  active_part: IntProperty(name="Active Part", default=0)
  skipped: CollectionProperty(type=LaseritySkipped)
  placements: CollectionProperty(type=LaserityPlacement)
  last_report: StringProperty(name="Report", default="")

  # -- derived helpers -----------------------------------------------------

  def part_by_name(self, name):
    return self.parts.get(name)

  def sheet_count(self):
    return max((p.sheet for p in self.placements), default=-1) + 1

  def selected_placements(self):
    return [p for p in self.placements if p.select]


CLASSES = (
  LaserityPart,
  LaseritySkipped,
  LaserityPlacement,
  LaseritySettings,
)


def register():
  for cls in CLASSES:
    bpy.utils.register_class(cls)
  bpy.types.Scene.laserity = PointerProperty(type=LaseritySettings)


def unregister():
  del bpy.types.Scene.laserity
  for cls in reversed(CLASSES):
    bpy.utils.unregister_class(cls)
