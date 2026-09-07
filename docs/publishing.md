# Publishing to extensions.blender.org

The steps for getting a version of Laserity onto the Blender Extensions
platform, and the things that have bitten this project's sibling
([Point Cloud I/O](https://extensions.blender.org/add-ons/point-cloud-io/))
before.

## Before every release

1. **Bump the version** in `laserity/blender_manifest.toml`. Semantic
   versioning — the platform refuses an upload whose version already exists.
2. **Update `CHANGELOG.md`** with a dated section for that version.
3. **Run everything.**

   ```bash
   python3 tests/test_core.py                            # 51 geometry tests
   blender --background --python tests/test_blender.py   # 29 end-to-end tests
   ruff format --check . && ruff check .
   ```

4. **Build and validate.**

   ```bash
   ./build.sh          # validates the manifest, then writes dist/laserity-<version>.zip
   ```

5. **Install the built ZIP into a clean Blender** and actually use it —
   `Edit ▸ Preferences ▸ Get Extensions ▸ ▾ ▸ Install from Disk`. The test suite
   runs against the source tree, not the package, so this is the only step that
   proves the ZIP itself is sound.
6. **Tag and push**, then attach the ZIP to a GitHub release.

## Manifest rules that have caused rejections

- **`tagline` must be 64 characters or fewer**, and must not end with a full
  stop. `blender --command extension validate` catches this; the platform
  rejects it on upload if you skip validation. Point Cloud I/O 0.5.0 was
  rejected for a 71-character tagline and needed a 0.5.1 purely to fix it.
- **`platforms` must only name platforms the site hosts** —
  `windows-x64`, `windows-arm64`, `macos-x64`, `macos-arm64`, `linux-x64`.
  `linux-arm64` is not recognised and a manifest naming it is rejected.
  Laserity is pure Python, so it declares no `platforms` at all and ships one
  universal build. Do not add the key unless wheels are ever introduced.
- **`copyright` must credit every copyright holder** if any third-party code or
  assets are ever bundled.
- **`blender_version_min` must be a version you have actually tested.** Laserity
  declares `5.1.0`; it is developed and tested against 5.2 LTS. Lowering it means
  testing against that older build first.

## First submission

1. Create the GitHub repository at the URL the manifest's `website` field points
   to — currently `https://github.com/studiomedio/blender-laserity`. Reviewers
   follow that link, and a 404 is a bad first impression.
2. Log in to [extensions.blender.org](https://extensions.blender.org) with your
   Blender ID and upload `dist/laserity-<version>.zip`.
3. Fill in the listing:
   - **Description** — paste [`extension.md`](extension.md).
   - **Preview images** — `images/featured.jpg` (1920 × 1080) as the cover, plus
     `images/viewport.png` and `images/nesting-editor.png`.
   - **Support / source links** — the GitHub repository and its issue tracker.
4. Submit for review and watch the
   [approval queue](https://extensions.blender.org/approval-queue/).

## What reviewers check

- The manifest carries complete metadata, and the licence is a real SPDX
  identifier matching a `LICENSE` file in the package.
- The extension installs and its basic functionality works.
- The code is reviewable — no obfuscation, no bytecode-only modules.
- It does not harm Blender's stability or performance.
- Python dependencies, if any, are bundled as wheels rather than pip-installed
  at runtime. Laserity has none.
- Declared permissions match what the code does. Laserity declares only `files`,
  for writing the exported SVG sheets.

## Regenerating the screenshots

The images in `images/` are captured from a scripted Blender session rather than
by hand, so they can be refreshed after a UI change. See the note in
`CHANGELOG.md` for the version they were taken at; the capture scripts are not
part of the repository because they hard-code local paths.
