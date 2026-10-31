#!/bin/bash
# Pull the phone's own firmware out of its Android into the fw-*/ directories at the top of the
# repository (never committed: it is the vendor's). The phone is on adb, in Android.
#
# The file names below are the SM8850 family's, not redwood's (a660 / wpss): this script asks the phone
# what it has and pulls what matches, so a name that does not exist costs nothing. Whatever it reports as
# missing is a name to add from `adb shell ls /vendor/firmware` on this phone.
set -e
cd "$(dirname "$0")/../.."

adb get-state >/dev/null

pull() { # DIR FILE... (missing files are told, not fatal)
	local dir=$1; shift
	mkdir -p "$dir"
	for f in "$@"; do adb pull "$f" "$dir/" >/dev/null 2>&1 || echo "pull-firmware.sh: $f: not on the phone"; done
}

pullmatch() { # DIR PATTERN... (the patterns are expanded by the phone's own shell)
	local dir=$1; shift
	local n=0 f
	mkdir -p "$dir"
	for pat in "$@"; do
		while IFS= read -r f; do
			[ -n "$f" ] || continue
			case "$f" in *"No such file"*|*"Permission denied"*) continue ;; esac
			adb pull "$f" "$dir/" >/dev/null 2>&1 && n=$((n + 1))
		done <<EOF
$(adb shell "ls $pat 2>/dev/null" | tr -d '\r')
EOF
	done
	echo "pull-firmware.sh: $dir: $n files"
}

# GPU: Adreno 840. gmu + sqe + the signed zap shader.
pullmatch fw-gpu   '/vendor/firmware/a8*_gmu.bin' '/vendor/firmware/a8*_sqe.fw' '/vendor/firmware/a8*zap*' \
                   '/vendor/firmware/gen8*' '/vendor/firmware/*840*'
# Wi-Fi: the WPSS subsystem's firmware and the WLAN images.
pullmatch fw-wifi  '/vendor/firmware_mnt/image/wpss*' '/vendor/firmware/wlan*' '/vendor/firmware/*cnss*'
# Audio DSP: sound, microphone, and the battery's level come through it.
pullmatch fw-audio '/vendor/firmware_mnt/image/adsp*'
# Haptics.
pullmatch fw-haptic '/vendor/firmware/*haptic*' '/vendor/firmware/aw8*'
# Modem.
pullmatch fw-modem '/vendor/firmware_mnt/image/modem*' '/vendor/firmware_mnt/image/mba*'
mkdir -p fw-modem/image
adb pull /vendor/firmware_mnt/image fw-modem/ >/dev/null 2>&1 &&
	echo "pull-firmware.sh: fw-modem/image: the modem's own files (TFTP)" || true

echo "pull-firmware.sh: done; Wi-Fi also wants fw-wifi/mac (the radio's address, one line) and"
echo "                  fw-wifi/network (the network's name and passphrase, one line each)"
