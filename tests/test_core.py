"""Tests for Laserity's Blender-independent core.

Run with a plain interpreter from the repository root::

    python3 tests/test_core.py

``laserity/__init__.py`` imports ``bpy``, so instead of importing the add-on
package we mount ``laserity/core`` under a synthetic package name. Relative
imports inside the core modules resolve against that, which keeps the
production code free of test-only import guards.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(REPO_ROOT, "laserity", "core")

_pkg = types.ModuleType("lzcore")
_pkg.__path__ = [CORE_DIR]
sys.modules["lzcore"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"lzcore.{name}", os.path.join(CORE_DIR, f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"lzcore.{name}"] = module
    spec.loader.exec_module(module)
    return module


geom2d = _load("geom2d")
lightburn = _load("lightburn")
part = _load("part")
nesting = _load("nesting")
svg = _load("svg")


def square(size, origin=(0.0, 0.0)):
    ox, oy = origin
    return [(ox, oy), (ox + size, oy), (ox + size, oy + size), (ox, oy + size)]


def rect(width, height, origin=(0.0, 0.0)):
    ox, oy = origin
    return [(ox, oy), (ox + width, oy), (ox + width, oy + height), (ox, oy + height)]


# ---------------------------------------------------------------------------


class TestMeasures(unittest.TestCase):
    def test_signed_area_sign_follows_winding(self):
        ccw = square(10.0)
        self.assertAlmostEqual(geom2d.signed_area(ccw), 100.0)
        self.assertAlmostEqual(geom2d.signed_area(list(reversed(ccw))), -100.0)

    def test_ensure_winding(self):
        cw = list(reversed(square(4.0)))
        self.assertGreater(geom2d.signed_area(geom2d.ensure_winding(cw, True)), 0.0)
        self.assertLess(geom2d.signed_area(geom2d.ensure_winding(cw, False)), 0.0)

    def test_centroid_of_square(self):
        cx, cy = geom2d.centroid(square(10.0))
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 5.0)

    def test_interior_probe_lands_inside_concave_shape(self):
        # A U shape, whose area centroid falls in the notch rather than the material.
        u_shape = [
            (0.0, 0.0),
            (30.0, 0.0),
            (30.0, 30.0),
            (20.0, 30.0),
            (20.0, 10.0),
            (10.0, 10.0),
            (10.0, 30.0),
            (0.0, 30.0),
        ]
        self.assertFalse(geom2d.point_in_polygon(geom2d.centroid(u_shape), u_shape))
        probe = geom2d._interior_probe(u_shape)
        self.assertTrue(geom2d.point_in_polygon(probe, u_shape))


class TestContainment(unittest.TestCase):
    def test_point_in_polygon(self):
        poly = square(10.0)
        self.assertTrue(geom2d.point_in_polygon((5.0, 5.0), poly))
        self.assertFalse(geom2d.point_in_polygon((15.0, 5.0), poly))
        self.assertFalse(geom2d.point_in_polygon((5.0, -1.0), poly))

    def test_polygons_overlap_crossing(self):
        a = square(10.0)
        b = square(10.0, origin=(5.0, 5.0))
        self.assertTrue(geom2d.polygons_overlap(a, b))

    def test_polygons_overlap_disjoint(self):
        a = square(10.0)
        b = square(10.0, origin=(20.0, 0.0))
        self.assertFalse(geom2d.polygons_overlap(a, b))

    def test_polygons_overlap_fully_contained(self):
        outer = square(100.0)
        inner = square(10.0, origin=(20.0, 20.0))
        self.assertTrue(geom2d.polygons_overlap(outer, inner))
        self.assertTrue(geom2d.polygons_overlap(inner, outer))

    def test_touching_edges_share_no_interior_area(self):
        a = square(10.0)
        b = square(10.0, origin=(10.0, 0.0))
        self.assertFalse(geom2d.polygons_overlap(a, b))


class TestClearance(unittest.TestCase):
    def test_touching_parts_conflict_once_clearance_is_required(self):
        a = square(10.0)
        b = square(10.0, origin=(10.0, 0.0))
        self.assertFalse(geom2d.parts_conflict(a, b, clearance=0.0))
        self.assertTrue(geom2d.parts_conflict(a, b, clearance=2.0))

    def test_well_separated_parts_never_conflict(self):
        a = square(10.0)
        b = square(10.0, origin=(50.0, 0.0))
        self.assertFalse(geom2d.parts_conflict(a, b, clearance=2.0))

    def test_overlapping_parts_always_conflict(self):
        a = square(10.0)
        b = square(10.0, origin=(5.0, 5.0))
        self.assertTrue(geom2d.parts_conflict(a, b, clearance=0.0))

    def test_polygon_distance_measures_the_gap(self):
        a = square(10.0)
        b = square(10.0, origin=(13.0, 0.0))
        self.assertAlmostEqual(geom2d.polygon_distance(a, b), 3.0, places=6)

    def test_polygon_distance_is_zero_when_overlapping(self):
        a = square(10.0)
        b = square(10.0, origin=(5.0, 5.0))
        self.assertAlmostEqual(geom2d.polygon_distance(a, b), 0.0, places=6)


class TestHullAndMinRect(unittest.TestCase):
    def test_convex_hull_ignores_interior_points(self):
        pts = square(10.0) + [(5.0, 5.0), (3.0, 7.0)]
        hull = geom2d.convex_hull(pts)
        self.assertEqual(len(hull), 4)

    def test_min_area_rect_recovers_rotated_rectangle(self):
        base = rect(40.0, 10.0)
        angle = math.radians(31.0)
        rotated = geom2d.rotate(base, angle)
        result = geom2d.min_area_rect(rotated)
        dims = sorted((result.width, result.height))
        self.assertAlmostEqual(dims[0], 10.0, places=6)
        self.assertAlmostEqual(dims[1], 40.0, places=6)

    def test_min_area_rect_beats_axis_aligned_bounds(self):
        diamond = [(0.0, 10.0), (10.0, 0.0), (20.0, 10.0), (10.0, 20.0)]
        result = geom2d.min_area_rect(diamond)
        min_x, min_y, max_x, max_y = geom2d.bbox(diamond)
        self.assertLess(result.area, (max_x - min_x) * (max_y - min_y) - 1e-6)


class TestLoopChaining(unittest.TestCase):
    def test_chain_closed_square(self):
        edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        loops = geom2d.chain_edges_to_loops(edges)
        self.assertEqual(len(loops), 1)
        self.assertEqual(sorted(loops[0]), [0, 1, 2, 3])

    def test_chain_two_independent_loops(self):
        edges = [(0, 1), (1, 2), (2, 0), (10, 11), (11, 12), (12, 10)]
        loops = geom2d.chain_edges_to_loops(edges)
        self.assertEqual(len(loops), 2)
        self.assertEqual({len(loop) for loop in loops}, {3})

    def test_dedupe_consecutive_removes_wrapped_duplicate(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        self.assertEqual(len(geom2d.dedupe_consecutive(pts)), 3)


class TestClassifyLoops(unittest.TestCase):
    def test_square_with_hole(self):
        outer = square(100.0)
        hole = square(20.0, origin=(40.0, 40.0))
        contours = geom2d.classify_loops([outer, hole])
        self.assertEqual(len(contours), 1)
        outer_result, holes = contours[0]
        self.assertEqual(len(holes), 1)
        self.assertGreater(geom2d.signed_area(outer_result), 0.0, "outer must be CCW")
        self.assertLess(geom2d.signed_area(holes[0]), 0.0, "hole must be CW")

    def test_island_inside_hole_is_its_own_contour(self):
        outer = square(100.0)
        hole = square(60.0, origin=(20.0, 20.0))
        island = square(20.0, origin=(40.0, 40.0))
        contours = geom2d.classify_loops([outer, hole, island])
        self.assertEqual(len(contours), 2)
        self.assertEqual(len(contours[0][1]), 1)  # big square keeps the hole
        self.assertEqual(len(contours[1][1]), 0)  # island stands alone

    def test_two_holes_attach_to_correct_parent(self):
        a = square(50.0)
        a_hole = square(10.0, origin=(20.0, 20.0))
        b = square(50.0, origin=(100.0, 0.0))
        b_hole = square(10.0, origin=(120.0, 20.0))
        contours = geom2d.classify_loops([a, a_hole, b, b_hole])
        self.assertEqual(len(contours), 2)
        for outer, holes in contours:
            self.assertEqual(len(holes), 1)
            self.assertTrue(geom2d.point_in_polygon(geom2d.centroid(holes[0]), outer))


class TestSimplify(unittest.TestCase):
    def test_collinear_points_collapse(self):
        line = [(x, 0.0) for x in range(0, 11)] + [(10.0, 10.0), (0.0, 10.0)]
        simplified = geom2d.simplify(line, 0.01)
        self.assertLess(len(simplified), len(line))
        self.assertGreaterEqual(len(simplified), 3)

    def test_shape_is_preserved_within_tolerance(self):
        circle = [
            (50.0 * math.cos(t * math.tau / 200), 50.0 * math.sin(t * math.tau / 200))
            for t in range(200)
        ]
        simplified = geom2d.simplify(circle, 0.05)
        self.assertLess(len(simplified), len(circle))
        self.assertAlmostEqual(geom2d.area(simplified), geom2d.area(circle), delta=20.0)

    def test_zero_tolerance_is_a_no_op(self):
        pts = square(10.0)
        self.assertEqual(geom2d.simplify(pts, 0.0), pts)


class TestKerf(unittest.TestCase):
    def test_positive_offset_grows_ccw_polygon(self):
        grown = geom2d.offset_polygon(square(10.0), 1.0)
        min_x, min_y, max_x, max_y = geom2d.bbox(grown)
        self.assertAlmostEqual(min_x, -1.0, places=6)
        self.assertAlmostEqual(max_x, 11.0, places=6)
        self.assertAlmostEqual(min_y, -1.0, places=6)
        self.assertAlmostEqual(max_y, 11.0, places=6)


class TestLightBurn(unittest.TestCase):
    def test_palette_entries_are_known_values(self):
        self.assertEqual(lightburn.hex_for(0), "#000000")
        self.assertEqual(lightburn.hex_for(1), "#0000FF")
        self.assertEqual(lightburn.hex_for(2), "#FF0000")
        self.assertEqual(lightburn.hex_for(3), "#00E000")
        self.assertEqual(lightburn.LAYER_COUNT, 30)

    def test_cut_layers_skip_the_engrave_layer(self):
        mapping = lightburn.assign_cut_layers([3.0, 6.0, 12.0], engrave_index=2, start_index=1)
        self.assertNotIn(2, mapping.values())
        self.assertEqual(len(set(mapping.values())), 3)

    def test_layer_names_wrap(self):
        self.assertEqual(lightburn.name_for(0), "C00")
        self.assertEqual(lightburn.name_for(29), "C29")
        self.assertEqual(lightburn.name_for(30), "C00")


class TestPlacement(unittest.TestCase):
    def test_rotation_then_translation(self):
        pts = [(0.0, 0.0), (2.0, 0.0)]
        placed = part.place(pts, math.pi / 2, (10.0, 5.0))
        self.assertAlmostEqual(placed[0][0], 10.0, places=6)
        self.assertAlmostEqual(placed[0][1], 5.0, places=6)
        self.assertAlmostEqual(placed[1][0], 10.0, places=6)
        self.assertAlmostEqual(placed[1][1], 7.0, places=6)

    def test_mirror_happens_before_rotation(self):
        pts = [(1.0, 0.0)]
        placed = part.place(pts, 0.0, (0.0, 0.0), mirrored=True)
        self.assertAlmostEqual(placed[0][0], -1.0, places=6)

    def test_net_area_subtracts_holes(self):
        geometry = part.PartGeometry(
            name="plate",
            thickness=6.0,
            contours=[(square(100.0), [list(reversed(square(20.0, origin=(10.0, 10.0))))])],
        )
        self.assertAlmostEqual(geometry.net_area(), 100.0 * 100.0 - 400.0)

    def test_json_round_trip(self):
        geometry = part.PartGeometry(
            name="plate",
            thickness=6.0,
            contours=[(square(50.0), [list(reversed(square(10.0, origin=(20.0, 20.0))))])],
            engrave_lines=[[(1.0, 1.0), (2.0, 2.0)]],
        )
        restored = part.PartGeometry.from_json(geometry.to_json())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.name, "plate")
        self.assertAlmostEqual(restored.thickness, 6.0)
        self.assertEqual(len(restored.contours[0][1]), 1)
        self.assertEqual(len(restored.engrave_lines), 1)

    def test_from_json_rejects_garbage(self):
        self.assertIsNone(part.PartGeometry.from_json(""))
        self.assertIsNone(part.PartGeometry.from_json("not json"))
        self.assertIsNone(part.PartGeometry.from_json('{"v":999}'))


class TestNesting(unittest.TestCase):
    def test_canonical_form_axis_aligns_and_anchors(self):
        base = rect(40.0, 10.0)
        rotated = geom2d.rotate(geom2d.translate(base, 130.0, -70.0), math.radians(23.0))
        angle, offset, w, h = nesting.canonical_form(rotated)
        placed = part.place(rotated, angle, offset)
        min_x, min_y, max_x, max_y = geom2d.bbox(placed)
        self.assertAlmostEqual(min_x, 0.0, places=6)
        self.assertAlmostEqual(min_y, 0.0, places=6)
        self.assertAlmostEqual(max_x, w, places=6)
        self.assertAlmostEqual(max_y, h, places=6)
        self.assertAlmostEqual(sorted((w, h))[1], 40.0, places=5)

    def _placed_boxes(self, placements, items):
        lookup = dict(items)
        boxes = {}
        for placement in placements:
            pts = part.place(lookup[placement.key], placement.rotation, placement.offset)
            boxes[placement.key] = (placement.sheet, geom2d.bbox(pts))
        return boxes

    def test_placements_match_reported_size_and_sit_on_sheet(self):
        items = [(f"p{i}", rect(30.0 + i * 5, 20.0)) for i in range(8)]
        placements, unplaced = nesting.auto_nest(items, 400.0, 300.0, spacing=2.0, margin=5.0)
        self.assertEqual(unplaced, [])
        boxes = self._placed_boxes(placements, items)
        for placement in placements:
            _sheet, (min_x, min_y, max_x, max_y) = boxes[placement.key]
            self.assertAlmostEqual(max_x - min_x, placement.size[0], places=6)
            self.assertAlmostEqual(max_y - min_y, placement.size[1], places=6)
            self.assertGreaterEqual(min_x, 5.0 - 1e-6)
            self.assertGreaterEqual(min_y, 5.0 - 1e-6)
            self.assertLessEqual(max_x, 400.0 - 5.0 + 1e-6)
            self.assertLessEqual(max_y, 300.0 - 5.0 + 1e-6)

    def test_nested_parts_do_not_collide(self):
        items = [(f"p{i}", rect(45.0, 28.0)) for i in range(20)]
        placements, unplaced = nesting.auto_nest(items, 400.0, 300.0, spacing=2.0, margin=5.0)
        self.assertEqual(unplaced, [])
        lookup = dict(items)
        by_sheet = {}
        for placement in placements:
            pts = part.place(lookup[placement.key], placement.rotation, placement.offset)
            by_sheet.setdefault(placement.sheet, []).append(pts)
        for sheet_parts in by_sheet.values():
            for i in range(len(sheet_parts)):
                for j in range(i + 1, len(sheet_parts)):
                    self.assertFalse(
                        geom2d.polygons_overlap(sheet_parts[i], sheet_parts[j]),
                        f"parts {i} and {j} overlap on the same sheet",
                    )

    def test_rotation_lets_a_tall_part_fit_a_wide_sheet(self):
        items = [("tall", rect(20.0, 380.0))]
        placements, unplaced = nesting.auto_nest(
            items, 400.0, 100.0, spacing=0.0, margin=5.0, allow_rotation=True
        )
        self.assertEqual(unplaced, [])
        self.assertEqual(len(placements), 1)

    def test_oversized_part_is_reported_unplaced(self):
        items = [("huge", rect(900.0, 900.0))]
        placements, unplaced = nesting.auto_nest(items, 400.0, 300.0)
        self.assertEqual(placements, [])
        self.assertEqual(unplaced, ["huge"])

    def test_overflow_spills_onto_more_sheets(self):
        items = [(f"p{i}", rect(190.0, 140.0)) for i in range(9)]
        placements, unplaced = nesting.auto_nest(items, 400.0, 300.0, spacing=2.0, margin=5.0)
        self.assertEqual(unplaced, [])
        self.assertGreater(len({p.sheet for p in placements}), 1)


class TestSvg(unittest.TestCase):
    def _sample_parts(self):
        plate = part.PartGeometry(
            name="Plate",
            thickness=6.0,
            contours=[(square(100.0), [list(reversed(square(20.0, origin=(40.0, 40.0))))])],
            engrave_lines=[[(10.0, 10.0), (90.0, 10.0)]],
        )
        rib = part.PartGeometry(name="Rib", thickness=3.0, contours=[(rect(80.0, 20.0), [])])
        return [
            part.PlacedPart("Plate", plate, 6.0, 6.0, sheet=0, offset=(10.0, 10.0)),
            part.PlacedPart("Rib", rib, 3.0, 3.0, sheet=0, offset=(150.0, 10.0)),
        ]

    def test_document_is_well_formed_and_millimetre_sized(self):
        options = svg.SvgOptions(sheet_width=600.0, sheet_height=400.0)
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        self.assertEqual(root.attrib["width"], "600mm")
        self.assertEqual(root.attrib["height"], "400mm")
        self.assertEqual(root.attrib["viewBox"], "0 0 600 400")

    def test_each_thickness_gets_its_own_stroke_colour(self):
        options = svg.SvgOptions()
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        groups = [
            g for g in root if g.tag.endswith("g") and g.attrib.get("id", "").startswith("cut-")
        ]
        self.assertEqual(len(groups), 2)
        colours = {g.attrib["stroke"] for g in groups}
        self.assertEqual(len(colours), 2)
        for colour in colours:
            self.assertIn(
                tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5)),
                lightburn.PALETTE,
                "stroke must be an exact LightBurn palette colour",
            )

    def test_paths_are_unfilled(self):
        options = svg.SvgOptions()
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        for group in root:
            if group.tag.endswith("g"):
                self.assertEqual(group.attrib.get("fill"), "none")

    def test_holes_become_extra_subpaths(self):
        options = svg.SvgOptions()
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        plate_path = None
        for group in root.iter():
            if group.attrib.get("id") == "part-Plate":
                plate_path = list(group)[0]
        self.assertIsNotNone(plate_path)
        self.assertEqual(plate_path.attrib["d"].count("Z"), 2, "outline plus one hole")

    def test_y_axis_is_flipped(self):
        options = svg.SvgOptions(sheet_width=600.0, sheet_height=400.0)
        geometry = part.PartGeometry(name="P", thickness=3.0, contours=[(square(10.0), [])])
        placed = [part.PlacedPart("P", geometry, 3.0, 3.0, offset=(0.0, 0.0))]
        document = svg.render_sheet(placed, options, svg.build_layer_map(placed, options))
        # The part sits at Blender Y 0..10, so in SVG it must sit at 390..400.
        self.assertIn("400", document)
        self.assertIn("390", document)

    def test_engrave_uses_its_own_group_and_colour(self):
        options = svg.SvgOptions(engrave_layer=2)
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        engrave = [g for g in root if g.attrib.get("id") == "engrave"]
        self.assertEqual(len(engrave), 1)
        self.assertEqual(engrave[0].attrib["stroke"], lightburn.hex_for(2))

    def test_engrave_lines_are_open_paths(self):
        options = svg.SvgOptions()
        parts = self._sample_parts()
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        root = ET.fromstring(document)
        for group in root.iter():
            if group.attrib.get("id") == "engrave-Plate":
                for path in group:
                    self.assertNotIn("Z", path.attrib["d"])

    def test_write_splits_multiple_sheets_into_files(self):
        options = svg.SvgOptions()
        geometry = part.PartGeometry(name="P", thickness=3.0, contours=[(square(10.0), [])])
        parts = [
            part.PlacedPart("A", geometry, 3.0, 3.0, sheet=0),
            part.PlacedPart("B", geometry, 3.0, 3.0, sheet=1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            written = svg.write(os.path.join(tmp, "out.svg"), parts, options)
            self.assertEqual(len(written), 2)
            self.assertTrue(written[0].endswith("out_sheet1.svg"))
            self.assertTrue(written[1].endswith("out_sheet2.svg"))
            for path in written:
                self.assertTrue(os.path.exists(path))

    def test_write_single_sheet_keeps_the_given_name(self):
        options = svg.SvgOptions()
        geometry = part.PartGeometry(name="P", thickness=3.0, contours=[(square(10.0), [])])
        parts = [part.PlacedPart("A", geometry, 3.0, 3.0, sheet=0)]
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.svg")
            written = svg.write(target, parts, options)
            self.assertEqual(written, [target])

    def test_part_names_are_xml_safe(self):
        options = svg.SvgOptions()
        geometry = part.PartGeometry(name="P", thickness=3.0, contours=[(square(10.0), [])])
        parts = [part.PlacedPart('Odd <name> & "quotes"', geometry, 3.0, 3.0)]
        document = svg.render_sheet(parts, options, svg.build_layer_map(parts, options))
        ET.fromstring(document)  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
