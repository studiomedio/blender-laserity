"""LightBurn layer colour palette.

LightBurn assigns an imported shape to a cut layer by matching its **stroke**
colour against this palette (fill is ignored when a stroke is present), so the
values below have to be exact for layers to land where the user expects.

Source: LightBurn's documented default palette, cross-checked against the
published GIMP/Inkscape palette file.
  https://docs.lightburnsoftware.com/latest/Reference/UI/ColorPalette/
"""

from __future__ import annotations

#: Layers C00..C29 in LightBurn's own order.
PALETTE = (
    (0, 0, 0),  # C00
    (0, 0, 255),  # C01
    (255, 0, 0),  # C02
    (0, 224, 0),  # C03
    (208, 208, 0),  # C04
    (255, 128, 0),  # C05
    (0, 224, 224),  # C06
    (255, 0, 255),  # C07
    (180, 180, 180),  # C08
    (0, 0, 160),  # C09
    (160, 0, 0),  # C10
    (0, 160, 0),  # C11
    (160, 160, 0),  # C12
    (192, 128, 0),  # C13
    (0, 160, 255),  # C14
    (160, 0, 160),  # C15
    (128, 128, 128),  # C16
    (125, 135, 185),  # C17
    (187, 119, 132),  # C18
    (74, 111, 227),  # C19
    (211, 63, 106),  # C20
    (140, 215, 140),  # C21
    (240, 185, 141),  # C22
    (246, 196, 225),  # C23
    (250, 158, 212),  # C24
    (80, 10, 120),  # C25
    (180, 90, 0),  # C26
    (0, 71, 84),  # C27
    (134, 250, 136),  # C28
    (255, 219, 102),  # C29
)

#: Tool layers. T1/T2 are non-output layers in LightBurn.
TOOL_LAYERS = {
    "T1": (243, 105, 38),
    "T2": (12, 150, 217),
}

LAYER_COUNT = len(PALETTE)


def hex_for(index):
    """``#RRGGBB`` for LightBurn layer ``index`` (wraps past C29)."""
    r, g, b = PALETTE[index % LAYER_COUNT]
    return f"#{r:02X}{g:02X}{b:02X}"


def name_for(index):
    return f"C{index % LAYER_COUNT:02d}"


def enum_items():
    """Blender ``EnumProperty`` items for picking a layer."""
    items = []
    for i in range(LAYER_COUNT):
        items.append((str(i), f"{name_for(i)}  {hex_for(i)}", f"LightBurn layer {name_for(i)}"))
    return items


def assign_cut_layers(thickness_keys, engrave_index, start_index=1):
    """Map each distinct material thickness to its own LightBurn layer.

    ``thickness_keys`` is an ordered iterable of thickness identifiers. The layer
    reserved for engraving is skipped so cut and engrave never collide.
    Returns ``{thickness_key: layer_index}``.
    """
    mapping = {}
    cursor = start_index
    for key in thickness_keys:
        while cursor % LAYER_COUNT == engrave_index % LAYER_COUNT:
            cursor += 1
        mapping[key] = cursor % LAYER_COUNT
        cursor += 1
    return mapping
