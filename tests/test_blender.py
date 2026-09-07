"""End-to-end tests that need Blender. Run them with::

    /path/to/blender --background --python tests/test_blender.py

They build a small scene, scan it, arrange the parts and write the SVG, which is
the whole path a user takes. ``tests/test_core.py`` covers the geometry in
isolation and runs under a plain interpreter.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import bpy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import laserity  # noqa: E402
from laserity import cache, editor, nest  # noqa: E402
from laserity.core import lightburn  # noqa: E402


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_slab(
    name, width, height, thickness, location=(0.0, 0.0, 0.0), rotation=None, apply_scale=False
):
    """A box ``width x height x thickness`` metres, i.e. millimetres over 1000."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (width, height, thickness)
    if apply_scale:
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if rotation is not None:
        obj.rotation_euler = rotation
    return obj


def punch_hole(plate, radius, location):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=1.0, location=location)
    cutter = bpy.context.active_object
    cutter.name = f"{plate.name}_cutter"
    modifier = plate.modifiers.new("Holes", "BOOLEAN")
    modifier.object = cutter
    modifier.operation = "DIFFERENCE"
    cutter.hide_set(True)
    return cutter


class LaserityTestCase(unittest.TestCase):
    """Registers the add-on around a freshly built scene."""

    def setUp(self):
        reset_scene()
        cache.clear()
        laserity.register()
        self.settings = bpy.context.scene.laserity
        self.settings.scan_scope = "VISIBLE"

    def tearDown(self):
        laserity.unregister()


class TestDetection(LaserityTestCase):
    def setUp(self):
        super().setUp()
        # 100 x 60 x 3 mm, left unapplied so the world-space path is exercised.
        self.plate = add_slab("Plate", 0.1, 0.06, 0.003)
        punch_hole(self.plate, 0.008, (0.02, 0.0, 0.0))
        # 80 x 40 x 6 mm with its scale baked in.
        add_slab("Rib", 0.08, 0.04, 0.006, location=(0.3, 0.0, 0.0), apply_scale=True)
        # Same 3 mm plate, tumbled to an arbitrary orientation.
        add_slab("Tilted", 0.1, 0.06, 0.003, location=(0.0, 0.4, 0.0), rotation=(0.31, 0.72, 1.13))
        # A solid that must be rejected.
        add_slab("Block", 0.05, 0.05, 0.05, location=(0.6, 0.0, 0.0))
        bpy.ops.laserity.scan()

    def test_flat_plates_are_found_and_the_block_is_not(self):
        names = {part.name for part in self.settings.parts}
        self.assertEqual(names, {"Plate", "Rib", "Tilted"})
        skipped = {entry.name for entry in self.settings.skipped}
        self.assertIn("Block", skipped)

    def test_thickness_is_measured_in_millimetres(self):
        self.assertAlmostEqual(self.settings.parts["Plate"].thickness, 3.0, places=3)
        self.assertAlmostEqual(self.settings.parts["Rib"].thickness, 6.0, places=3)

    def test_thicknesses_group_into_stock_sizes(self):
        self.assertAlmostEqual(self.settings.parts["Plate"].thickness_key, 3.0, places=3)
        self.assertAlmostEqual(self.settings.parts["Tilted"].thickness_key, 3.0, places=3)
        self.assertNotAlmostEqual(self.settings.parts["Rib"].thickness_key, 3.0, places=3)

    def test_outline_size_survives_an_arbitrary_rotation(self):
        flat = self.settings.parts["Tilted"]
        self.assertAlmostEqual(max(flat.width, flat.height), 100.0, delta=0.05)
        self.assertAlmostEqual(min(flat.width, flat.height), 60.0, delta=0.05)

    def test_modifier_result_is_used_so_the_hole_shows_up(self):
        self.assertEqual(self.settings.parts["Plate"].hole_count, 1)

    def test_block_rejection_explains_itself(self):
        reason = next(e.reason for e in self.settings.skipped if e.name == "Block")
        self.assertTrue(reason, "a skipped object must carry a reason")

    def test_scan_leaves_a_usable_arrangement(self):
        self.assertEqual(len(self.settings.placements), 3)
        self.assertTrue(all(p.part_name for p in self.settings.placements))


class TestArrangement(LaserityTestCase):
    def setUp(self):
        super().setUp()
        add_slab("ThinA", 0.1, 0.06, 0.003)
        add_slab("ThinB", 0.09, 0.05, 0.003, location=(0.3, 0.0, 0.0))
        add_slab("ThickA", 0.08, 0.04, 0.006, location=(0.6, 0.0, 0.0))
        bpy.ops.laserity.scan()

    def test_each_thickness_gets_its_own_sheets(self):
        sheets = {}
        for placement in self.settings.placements:
            part = self.settings.parts[placement.part_name]
            sheets.setdefault(part.thickness_key, set()).add(placement.sheet)
        keys = list(sheets)
        self.assertEqual(len(keys), 2)
        self.assertFalse(
            sheets[keys[0]] & sheets[keys[1]], "stock thicknesses must not share a sheet"
        )

    def test_a_clean_layout_reports_no_conflicts(self):
        self.assertEqual(nest.recompute_conflicts(self.settings), 0)

    def test_overlapping_parts_are_flagged(self):
        thin = [
            p
            for p in self.settings.placements
            if self.settings.parts[p.part_name].thickness_key < 4.0
        ]
        self.assertGreaterEqual(len(thin), 2)
        thin[1].sheet = thin[0].sheet
        thin[1].offset_x = thin[0].offset_x
        thin[1].offset_y = thin[0].offset_y
        self.assertGreater(nest.recompute_conflicts(self.settings), 0)
        self.assertTrue(thin[0].conflict and thin[1].conflict)

    def test_parts_hanging_off_the_sheet_are_flagged(self):
        placement = self.settings.placements[0]
        placement.offset_x = self.settings.sheet_width * 2.0
        nest.recompute_conflicts(self.settings)
        self.assertTrue(placement.conflict)

    def test_quantity_creates_more_copies(self):
        self.settings.parts["ThinA"].quantity = 4
        self.assertEqual(sum(1 for p in self.settings.placements if p.part_name == "ThinA"), 4)
        bpy.ops.laserity.arrange(mode="PACK")
        self.assertEqual(nest.recompute_conflicts(self.settings), 0)

    def test_excluding_a_part_drops_its_placements(self):
        self.settings.parts["ThinA"].enabled = False
        self.assertFalse(any(p.part_name == "ThinA" for p in self.settings.placements))

    def test_grid_mode_leaves_every_part_unrotated(self):
        bpy.ops.laserity.arrange(mode="GRID")
        for placement in self.settings.placements:
            self.assertAlmostEqual(placement.rotation, 0.0, places=6)


class TestEditorLogic(LaserityTestCase):
    """The editor's geometry, exercised without opening a window."""

    def setUp(self):
        super().setUp()
        add_slab("Panel", 0.1, 0.06, 0.003)
        add_slab("Strut", 0.09, 0.02, 0.003, location=(0.3, 0.0, 0.0))
        bpy.ops.laserity.scan()
        self.view = editor._View()  # zoom 1, no offset: screen units are millimetres

    def _centre_of(self, placement):
        part = self.settings.parts[placement.part_name]
        x0, y0, x1, y1 = editor.placement_world_bounds(self.settings, placement, cache.get(part))
        return ((x0 + x1) * 0.5, (y0 + y1) * 0.5)

    def test_picking_hits_the_part_under_the_cursor(self):
        placement = self.settings.placements[0]
        self.assertEqual(editor.pick(self.settings, self.view, *self._centre_of(placement)), 0)

    def test_picking_empty_space_hits_nothing(self):
        self.assertIsNone(editor.pick(self.settings, self.view, -500.0, -500.0))

    def test_rotating_keeps_the_selection_centred(self):
        placement = self.settings.placements[0]
        placement.select = True
        before = self._centre_of(placement)
        was = placement.rotation
        editor.rotate_selected(self.settings, math.pi * 0.5)
        after = self._centre_of(placement)
        self.assertAlmostEqual(before[0], after[0], places=4)
        self.assertAlmostEqual(before[1], after[1], places=4)
        self.assertAlmostEqual(placement.rotation - was, math.pi * 0.5, places=5)

    def test_mirroring_keeps_the_part_where_it_sits(self):
        placement = self.settings.placements[0]
        placement.select = True
        before = self._centre_of(placement)
        editor.mirror_selected(self.settings)
        after = self._centre_of(placement)
        self.assertTrue(placement.mirrored)
        self.assertAlmostEqual(before[0], after[0], places=4)
        self.assertAlmostEqual(before[1], after[1], places=4)

    def test_dragging_past_a_sheet_edge_rehomes_the_part(self):
        placement = self.settings.placements[0]
        origin_x, _ = editor.sheet_origin(self.settings, 1)
        placement.offset_x += origin_x
        self.assertEqual(editor.rebase_sheets(self.settings), 1)
        self.assertEqual(placement.sheet, 1)
        # Rebasing must move the sheet without moving the part on screen.
        self.assertAlmostEqual(placement.offset_x, self.settings.placements[0].offset_x, places=6)

    def test_removing_a_copy_writes_the_count_back_to_the_part(self):
        self.settings.parts["Panel"].quantity = 3
        for placement in self.settings.placements:
            placement.select = placement.part_name == "Panel"
        removed = editor.remove_selected(self.settings)
        self.assertEqual(removed, 3)
        self.assertFalse(self.settings.parts["Panel"].enabled)
        self.assertFalse(any(p.part_name == "Panel" for p in self.settings.placements))

    def test_selection_pivot_spans_the_whole_selection(self):
        for placement in self.settings.placements:
            placement.select = True
        self.assertIsNotNone(editor.selection_pivot(self.settings))


class TestExport(LaserityTestCase):
    def setUp(self):
        super().setUp()
        plate = add_slab("Plate", 0.1, 0.06, 0.003)
        punch_hole(plate, 0.008, (0.02, 0.0, 0.0))
        add_slab("Rib", 0.08, 0.04, 0.006, location=(0.3, 0.0, 0.0))
        bpy.ops.laserity.scan()
        self.directory = tempfile.mkdtemp(prefix="laserity-")

    def _export(self, name="sheets.svg"):
        target = os.path.join(self.directory, name)
        bpy.ops.laserity.export_svg(filepath=target)
        return sorted(
            os.path.join(self.directory, f)
            for f in os.listdir(self.directory)
            if f.endswith(".svg")
        )

    def test_one_file_per_sheet(self):
        written = self._export()
        self.assertEqual(len(written), self.settings.sheet_count())
        self.assertGreaterEqual(len(written), 2, "two thicknesses need two sheets")

    def test_documents_are_valid_millimetre_svg(self):
        for path in self._export():
            root = ET.parse(path).getroot()
            self.assertTrue(root.attrib["width"].endswith("mm"))
            self.assertEqual(
                root.attrib["viewBox"],
                f"0 0 {self.settings.sheet_width:g} {self.settings.sheet_height:g}",
            )

    def test_strokes_are_exact_lightburn_colours(self):
        found = 0
        for path in self._export():
            for element in ET.parse(path).getroot().iter():
                stroke = element.attrib.get("stroke")
                if stroke:
                    rgb = tuple(int(stroke[i : i + 2], 16) for i in (1, 3, 5))
                    self.assertIn(rgb, lightburn.PALETTE)
                    found += 1
        self.assertGreater(found, 0)

    def test_the_hole_is_written_as_a_second_subpath(self):
        for path in self._export():
            for element in ET.parse(path).getroot().iter():
                if element.attrib.get("id") == "part-Plate":
                    self.assertEqual(list(element)[0].attrib["d"].count("Z"), 2)
                    return
        self.fail("the plate was never written")

    def test_every_part_lands_inside_its_sheet(self):
        options = nest.svg_options(self.settings)
        for placed in nest.placed_parts(self.settings):
            x0, y0, x1, y1 = placed.placed_bounds()
            self.assertGreaterEqual(x0, 0.0)
            self.assertGreaterEqual(y0, 0.0)
            self.assertLessEqual(x1, options.sheet_width)
            self.assertLessEqual(y1, options.sheet_height)


class TestRegistration(LaserityTestCase):
    def test_settings_are_attached_to_the_scene(self):
        self.assertIsNotNone(bpy.context.scene.laserity)

    def test_every_operator_is_available(self):
        for name in (
            "scan",
            "clear",
            "set_all",
            "select_source",
            "arrange",
            "check",
            "export_svg",
            "open_editor",
            "editor",
            "placement_action",
        ):
            self.assertTrue(hasattr(bpy.ops.laserity, name), f"laserity.{name} is missing")

    def test_reregistering_is_clean(self):
        laserity.unregister()
        laserity.register()
        self.assertIsNotNone(bpy.context.scene.laserity)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
