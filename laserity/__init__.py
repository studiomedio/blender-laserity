"""Laserity — find flat parts in a Blender scene and export them as SVG for a laser.

The add-on is deliberately split so that the geometry work can be tested without
Blender running:

``core/``
    Pure Python. Slab detection, flattening, 2D geometry, sheet nesting and the
    SVG writer. Only ``detect`` and ``flatten`` touch ``bmesh``.
``props`` / ``operators`` / ``ui``
    The Blender-facing layer: scene properties, the scan and export operators,
    and the viewport panels.
``editor``
    The nesting editor — a separate window where parts are arranged by hand.
"""

from . import editor, operators, props, ui

# props first: everything else reads ``Scene.laserity``.
_MODULES = (props, operators, editor, ui)


def register():
  for module in _MODULES:
    module.register()


def unregister():
  for module in reversed(_MODULES):
    module.unregister()
