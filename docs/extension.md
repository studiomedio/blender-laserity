Find the flat parts in a Blender scene, arrange them on sheets, and export them as **SVG ready for a laser cutter or engraver**. Model your project however you like — Laserity works out which objects are actually cuttable from sheet material, measures each one's thickness, flattens it to a 2D outline with its holes, and packs the results onto sheets you can tune by hand.

Output is tuned for **LightBurn**: each stock thickness lands on its own cut layer and engraving on another, so an import arrives already separated and you only have to set power and speed.

## The workflow

Everything lives in the 3D Viewport sidebar (`N`) under the **Laserity** tab.

1. **Scan Scene.** Every object that qualifies appears in a list with its measured thickness. Objects that do not qualify are listed with the reason, so nothing disappears silently.
2. **Choose what to cut.** Untick parts you do not want; set a quantity for parts you need more than one of.
3. **Set the sheet.** Pick a bed or stock size, and the margin kept clear at the edges and the spacing kept between parts.
4. **Arrange.** *Auto Nest* packs everything; *Open Nesting Editor* gives you a window to move things by hand.
5. **Export SVG.** One file per sheet, in millimetres.

## What counts as a flat part

A part is a *slab*: geometry whose vertices sit almost entirely on two parallel planes a short distance apart. That test catches the panels, ribs and gussets people actually laser-cut, while rejecting solids.

- **Orientation does not matter.** Measurement happens in world space, so an object tumbled to an arbitrary angle is measured exactly like an axis-aligned one.
- **Modifiers are applied first.** A plate with boolean holes exports with its holes.
- **Rejections are explained.** *Skipped Objects* lists each rejected object with its reason — too thick, not slab-like enough, too chunky to be sheet material, or too small — so you can adjust either the model or the thresholds.
- **Two tracing strategies.** *Top Faces* traces the boundary of the faces on the slab's upper plane, giving the exact profile of the material at full thickness. *Section* slices the solid at mid-thickness instead, which tolerates chamfered or otherwise non-planar top surfaces. *Auto* tries the first and falls back to the second.

Parts whose thicknesses fall within a configurable tolerance are treated as the same stock. **Stock thicknesses never share a sheet** — they are physically different pieces of material — so each gets its own run of sheets and its own colour.

## The nesting editor

*Open Nesting Editor* opens a separate window showing every sheet side by side, with one spare empty sheet at the end. Drag a part onto the spare to start a new sheet; empty sheets close themselves up again.

| | |
|---|---|
| `LMB` | Select, or drag to move. `Shift` extends the selection |
| `LMB` drag on empty space | Box select |
| `G` | Move the selection; click to confirm, `Esc` to cancel |
| `R` | Rotate the selection; `Ctrl` snaps to 15° |
| `Shift R` | Rotate 90° |
| `M` | Mirror — for parts that need cutting face down |
| `X` | Remove the selected copies |
| `A` / `Alt A` | Select all / none |
| `MMB` drag, wheel | Pan, zoom |
| `Home` / `F` | Frame everything / the selection |

Parts turn **red** when they overlap, sit closer than the spacing allows, hang off the sheet, or share a sheet with a different stock thickness. The sidebar reports material utilisation per sheet.

## Engraving

Two things become engraving rather than cutting:

- **Faces with an engrave material.** Any face whose material name starts with `LZ_ENGRAVE` (configurable) is exported as an engraved region.
- **Marked edges.** Freestyle marks by default; UV seams or sharp edges if you prefer. Restricted to the part's upper surface unless you say otherwise.

## Getting it into LightBurn

LightBurn picks a shape's cut layer by matching its **stroke** colour against its own palette, and ignores fill when a stroke is present. Laserity therefore writes every path unfilled and strokes it with an exact palette colour, assigning one layer per stock thickness and reserving another for engraving. Holes are written as extra subpaths of the same path, so they cut as holes rather than as separate outlines.

The **kerf** setting grows cut paths outwards by half the beam width so parts come out at their modelled size. Leave it at zero unless you have measured your kerf — it is usually better handled in LightBurn itself.

## Nesting

Parts are reduced to their minimum-area oriented bounding rectangle (rotating calipers) and packed with the MaxRects algorithm using best-short-side-fit, spilling onto further sheets as needed. Rectangle packing of the oriented bounds is a deliberate simplification over true irregular nesting: it is fast, deterministic and good enough for the plate-like parts this add-on targets — and anything it leaves on the table, you can recover by hand in the editor.

## Requirements

- **Blender 5.1** or newer.
- Pure Python — no bundled wheels, no internet access needed at install time, and a single universal build for every platform.

## Limitations

- **Nesting packs bounding rectangles, not true outlines.** Two L-shaped parts that could interlock will not be nested into each other automatically. Rearranging them by hand in the editor takes seconds.
- **Kerf compensation is a naive miter offset.** It does not resolve the self-intersections that appear when the offset exceeds a local concave radius, so it suits only the small distances kerf actually involves. It is off by default.
- **One outline per part.** A part is flattened from a single object; parts modelled as several joined objects need joining first.

## Source

Source code, issue tracker and development docs: [github.com/studiomedio/blender-laserity](https://github.com/studiomedio/blender-laserity)
