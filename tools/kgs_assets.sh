#!/bin/bash
# Usage: tools/kgs_assets.sh [<game disk.adf> [<intro disk.adf>]]
# Makes this project's inputs. With disk images: first extracts original/ from them (tools/kgs_extract.py).
# Then folds every tune in original/ into channels 0-2 (tools/mod3.py) as assets/music/<name>3.mod, and keeps
# what each fold lost in assets/music/fold.txt. The effects are converted by tools/check.sh (EFFECTS in
# musicfx.conf). Only Python 3 is needed.
set -e
cd "$(dirname "$0")/.."
if [ -n "$1" ]; then python3 tools/kgs_extract.py "$@" original; fi
mkdir -p assets/music
: > assets/music/fold.txt
for m in original/*.mod; do
  n=$(basename "$m" .mod)
  python3 tools/mod3.py "$m" "assets/music/${n}3.mod" | tee -a assets/music/fold.txt
done
