#!/bin/bash
# Usage: tools/kgs_assets.sh [<game disk.adf> [<intro disk.adf>]]
# Makes this project's inputs. With disk images: first extracts original/ from them (tools/kgs_extract.py).
# Then folds the in-game tune into channels 0-2 (tools/mod3.py) as assets/music/ingame3.mod, and keeps what the
# fold lost in assets/music/fold.txt. Only the in-game tune is folded: it is the only one the game plays effects
# over. The credits and intro tunes stay exactly as the game has them (original/credits.mod, original/intro.mod).
# The effects are converted by tools/check.sh (EFFECTS in musicfx.conf). Only Python 3 is needed.
set -e
cd "$(dirname "$0")/.."
if [ -n "$1" ]; then python3 tools/kgs_extract.py "$@" original; fi
mkdir -p assets/music
rm -f assets/music/credits3.mod assets/music/intro3.mod      # made by earlier versions of this script
python3 tools/mod3.py original/ingame.mod assets/music/ingame3.mod | tee assets/music/fold.txt
