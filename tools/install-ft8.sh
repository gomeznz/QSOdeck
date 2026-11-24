#!/bin/sh
# Installs the FT8 engine QSODesk uses for real decoding and transmitting:
# the open-source ft8_lib by Karlis Goba (MIT licence), built from source.
# Run this yourself on the Pi:   sh tools/install-ft8.sh
# NOTE: not yet tested by the QSODesk author - please report how it goes.
set -e
cd "$(dirname "$0")/.."
command -v git >/dev/null && command -v make >/dev/null && command -v cc >/dev/null || {
  echo "First run:  sudo apt install git build-essential"; exit 1; }
TMP="$(mktemp -d)"
git clone --depth 1 https://github.com/kgoba/ft8_lib "$TMP/ft8_lib"
make -C "$TMP/ft8_lib"
mkdir -p qsodesk/bin
find "$TMP/ft8_lib" -type f \( -name decode_ft8 -o -name gen_ft8 \) -exec cp {} qsodesk/bin/ \;
rm -rf "$TMP"
if [ -x qsodesk/bin/decode_ft8 ] && [ -x qsodesk/bin/gen_ft8 ]; then
  echo "FT8 engine installed in qsodesk/bin. Restart QSODesk."
else
  echo "Build finished but decode_ft8 / gen_ft8 were not found - please report this."; exit 1
fi
