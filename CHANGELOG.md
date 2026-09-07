# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] – 2026-09-07

Initial release.

### Added

- **Scene scanning** via `Laserity ▸ Flat Parts ▸ Scan Scene` in the 3D Viewport sidebar (`N`).
  - Finds *slabs* — geometry whose vertices sit almost entirely on two parallel planes a short distance apart. That test catches the panels, ribs and gussets people actually cut, and rejects solids.
  - Works in world space, so an object tumbled to an arbitrary orientation is measured exactly like an axis-aligned one.
  - Modifiers are applied before measuring, so a plate with boolean holes exports with its holes.
  - Scope selectable: visible objects, selected objects, or the whole scene.
  - Objects that do not qualify are listed under **Skipped Objects** with the reason — thickness, slab strictness, flatness ratio or minimum area — rather than disappearing silently.
- **Outline extraction** in two strategies, with `AUTO` picking between them.
  - `TOP_FACES` traces the boundary of the faces on the slab's upper plane. Interior holes fall out for free and the result is the exact profile of the material at full thickness.
  - `SECTION` slices the solid at mid-thickness, which tolerates chamfered or otherwise non-planar top surfaces.
  - Nested loops are classified by even-odd containment depth, so islands inside holes come out as their own contours instead of being lost.
  - Ramer–Douglas–Peucker simplification trims the point count that curve- and CAD-derived meshes carry, without visibly changing the cut path.
- **Stock grouping.** Thicknesses within a configurable tolerance are treated as the same material. Stock thicknesses never share a sheet — they are physically different pieces of material — so each gets its own run of sheets and its own cut layer.
- **Automatic nesting.** Parts are reduced to their minimum-area oriented bounding rectangle (rotating calipers) and packed with MaxRects / best-short-side-fit, across as many sheets as needed. A plain grid layout is available as an alternative.
  - **Quantity** per part, so a part needed four times is packed four times.
  - Sheet presets for common laser beds and stock sizes, plus margin and inter-part spacing.
- **Nesting editor** — a dedicated window (`Open Nesting Editor`) showing every sheet side by side in one continuous millimetre space, drawn with the `gpu` module.
  - Drag parts within a sheet or across to the next; one spare empty sheet always sits at the end to start a new one, and emptied sheets close themselves up again.
  - Select by click, shift-click or box; move (`G`), rotate freely (`R`, `Ctrl` snaps to 15°) or by a quarter turn (`Shift R`), mirror (`M`) for parts cut face down, and remove copies (`X`).
  - Optional snapping grid, part-name labels, and pan/zoom including trackpad gestures.
  - **Conflict highlighting** flags parts that overlap, sit closer than the spacing allows, hang outside the sheet's usable area, or share a sheet with a different stock thickness.
  - Sidebar showing selection actions, layout controls, per-sheet material utilisation, and export.
- **SVG export** via the sidebar or `File ▸ Export ▸ Laser Sheets (.svg)` — one file per sheet, in millimetres, with the Y axis flipped so parts appear the right way up.
  - **LightBurn-ready.** LightBurn assigns an imported shape to a cut layer by matching its *stroke* colour against its own palette, so every path is written unfilled and stroked with an exact palette colour. Each stock thickness gets its own layer and engraving gets another, so an import arrives with cutting and engraving already separated.
  - Holes are written as additional subpaths of the same path, so they cut as holes rather than as separate outlines.
  - Optional **kerf** compensation grows cut paths outwards by half the beam width.
- **Engraving.** Faces whose material name starts with a configurable prefix (`LZ_ENGRAVE`) become engraved regions; Freestyle-marked edges — or UV seams, or sharp edges — become engraved lines, optionally restricted to the part's upper surface.

### Notes

- Pure Python — no bundled wheels, and therefore a single universal build rather than per-platform ones.
- The geometry work lives in `laserity/core/` and imports no Blender modules beyond `bmesh` in the two files that read meshes, so it is covered by 51 tests that run under a plain interpreter. A further 29 tests run inside Blender and cover detection through export.

### Known limitations

- **Nesting packs oriented bounding rectangles, not true outlines.** It is fast, deterministic and good enough for plate-like parts, but two L-shaped parts that could interlock will not be nested into each other automatically. The editor is there to close that gap by hand.
- **Kerf compensation is a naive miter offset.** It does not resolve the self-intersections that appear when the offset exceeds a local concave radius, so it is only appropriate for the small distances kerf actually involves. It is off by default, and kerf is usually better handled in LightBurn.
- **One outline per part.** A part is flattened from a single object; parts modelled as several joined objects need joining first.
