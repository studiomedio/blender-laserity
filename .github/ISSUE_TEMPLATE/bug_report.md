---
name: Bug report
about: Report a problem with scanning, nesting, or SVG export
title: '[Bug] '
labels: bug
---

## Describe the bug

What happened, and what did you expect to happen instead?

_e.g. A plate with holes exports without its holes, or the nesting editor window opens blank._

## Which stage?

Scanning / nesting editor / SVG export / LightBurn import.

## Steps to reproduce

1.
2.
3.

## Sample .blend

A small `.blend` containing just the affected objects helps enormously — most bugs in this add-on are about specific geometry.

_Please attach it here, or link to a download._

## What the scan reported

If an object was not detected, the sidebar's **Skipped Objects** panel gives a reason. Paste it here.

_e.g. `Lid — too chunky (smallest side vs thickness)`._

## Exported SVG

If the problem is in the output, attaching the exported `.svg` is usually enough to diagnose it.

## Blender version

Exact version from `Help > About Blender`.

_e.g. Blender 5.2.0, hash `abc123def`._

## Operating system

macOS / Linux / Windows, plus the version.

_e.g. macOS 14.5, Ubuntu 24.04, Windows 11 23H2._

## Console output

The console almost always contains the key error message.

- On Windows: enable via `Window > Toggle System Console` before reproducing.
- On macOS / Linux: launch Blender from a terminal so stdout/stderr are visible.

```
Paste console output here.
```

## Additional context

Anything else that might help — screenshots, unit scale, whether the objects came from a CAD import, etc.
