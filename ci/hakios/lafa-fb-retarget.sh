#!/bin/bash
# Retarget the reference's early framebuffer console at the lafa panel.
#
# The reference brings up an early framebuffer console long before pe_init() can set up a
# real one (0010-sweet-early-framebuffer-debug.patch, 0012-sweet-keep-debug-console-after-ttbr0.patch).
# Its constants are the sweet/fuxi panel (1080x2400, stride 4320, fb at 0x9c000000).  On lafa the
# panel is 1440x3136, stride 5760, and the framebuffer sits at 0xfc800000 -- the same address our
# UEFI loader draws its own log on, and the address pandora's mkadt_pandora.py uses for the
# splash region.  This script runs from darwin-xnu-build's patch step BEFORE the *.patch files
# are applied, so it edits the patch files themselves.
#
# Runs as: (cd ${WORK_DIR}/xnu && KDCROOT=... bash <this>)
set +e
PATCHES="$(dirname "$(pwd)")/patches"
[ -d "$PATCHES" ] || { echo "LAFA-FB: no patches dir at $PATCHES (skipping)"; exit 0; }

echo "LAFA-FB: retargeting the early console in $PATCHES"
before=$(grep -l '0x9c000000' "$PATCHES"/*.patch 2>/dev/null | wc -l | tr -d ' ')
before=${before:-0}
sed -i '' 's/0x9c000000/0xfc800000/g' "$PATCHES"/*.patch
sed -i '' 's/EARLY_WIDTH 1080u/EARLY_WIDTH 1440u/g' "$PATCHES"/*.patch
sed -i '' 's/EARLY_HEIGHT 2400u/EARLY_HEIGHT 3136u/g' "$PATCHES"/*.patch
sed -i '' 's/(4320u \/ 4)/(5760u \/ 4)/g' "$PATCHES"/*.patch
# start.s stage-0 band starts at the last 32 rows of the panel
sed -i '' 's/2368 \* 4320/2880 * 5760/g' "$PATCHES"/*.patch
after=$(grep -l '0xfc800000' "$PATCHES"/*.patch 2>/dev/null | wc -l | tr -d ' ')
after=${after:-0}
echo "LAFA-FB: patches mentioning 0x9c000000 before: $before, 0xfc800000 after: $after"
grep -h 'EARLY_WIDTH\|EARLY_HEIGHT\|EARLY_STRIDE\|0xfc800000' "$PATCHES"/*.patch 2>/dev/null | sort -u | head -8 || true

exit 0
