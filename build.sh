#!/bin/bash
# Package the add-on as a Blender extension zip in dist/.
set -euo pipefail

cd "$(dirname "$0")"

BLENDER="${BLENDER:-blender}"
if ! command -v "$BLENDER" >/dev/null 2>&1; then
  for candidate in /Applications/Blender.app/Contents/MacOS/Blender; do
    [ -x "$candidate" ] && BLENDER="$candidate" && break
  done
fi

echo "Validating manifest..."
"$BLENDER" --command extension validate laserity

echo "Building..."
mkdir -p dist
find laserity -name '__pycache__' -type d -exec rm -rf {} +
"$BLENDER" --command extension build --source-dir laserity --output-dir dist

ls -la dist/
