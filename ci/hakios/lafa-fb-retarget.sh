#!/bin/bash
# Point the reference's early framebuffer console at the lafa panel's framebuffer.
#
# The reference (0010-sweet-early-framebuffer-debug.patch, 0012-sweet-keep-debug-console-after-ttbr0.patch)
# brings up an early console before pe_init() has one.  Its framebuffer address is 0x9c000000, which
# is the sweet/fuxi splash address; lafa's panel framebuffer is at 0xfc800000 (measured: our UEFI
# loader draws on it, and pandora's mkadt_pandora.py uses 0xfc800000 for the splash region).
#
# This script runs from darwin-xnu-build's patch step BEFORE the *.patch files are applied, so it
# edits the patch files themselves.  Only the address is rewritten: the 0x9c000000 occurrences are
# added ('+') or removed ('-') lines in both patches, so rewriting both keeps the chain consistent.
# The width/height/stride were deliberately left at 1080x2400 -- those occur as context lines in
# 0012, and changing them makes that patch fail to apply; the console still renders in the
# top-left of the 1440x3136 panel and scrolls inside its own 1080x2400 window.
set +e
PATCHES="$(dirname "$(pwd)")/patches"
if [ ! -d "$PATCHES" ]; then
	echo "LAFA-FB: no patches dir at $PATCHES (skipping)"
	exit 0
fi
before=$(grep -l '0x9c000000' "$PATCHES"/*.patch 2>/dev/null | wc -l | tr -d ' ')
sed -i '' 's/0x9c000000/0xfc800000/g' "$PATCHES"/*.patch
after=$(grep -l '0xfc800000' "$PATCHES"/*.patch 2>/dev/null | wc -l | tr -d ' ')
echo "LAFA-FB: fb address 0x9c000000 -> 0xfc800000 in $before patch files ($after now carry 0xfc800000)"
exit 0
