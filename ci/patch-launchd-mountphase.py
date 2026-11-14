#!/usr/bin/env python3
"""patch-launchd-mountphase.py -- make launchd's mount-phase-2 task optional instead of fatal.

Why
---
launchd's boot-task table (the XML plist in its __TEXT,__config section) has:

    <key>mount-phase-2</key>
    <dict>
        <key>RequireSuccess</key><true/>
        <key>RequireRun</key><true/>
        <key>ProgramArguments</key><array><string>mount</string><string>-P</string><string>2</string></array>
        <key>Program</key><string>/sbin/mount</string>
    </dict>

so at boot launchd runs **/sbin/mount -P 2** and, because the task is required, takes its exit status
as the boot's. What that program does on this port cannot succeed:

  * it looks the data volume up in the IORegistry as an **AppleAPFSVolume** service and picks the one
    with the Data role (strings: AppleAPFSVolume, APFSVolumeRoleFind, "failed to lookup data volume");
  * this port's /private/var is an **HFS+ volume** that init mounted (a synthetic darwin partition),
    not an APFS volume with the Data role, so the lookup finds nothing;
  * then either "mount: missing data volume" (fatal) or "mount: data volume missing, but not required
    in env: %u" (not fatal), where the env comes from IODeviceTree:/chosen -- os_env_type, a property
    our loader does not publish.

The boot does not need this task: init has already mounted the writable /private/var and launchd's own
data-volume bookkeeping is what the task is for. Making it optional lets the boot go on.

How
---
Both keys are renamed one character, in place, to names launchd does not know ("RequireSuccesx",
"RequireRux"). A plist reader ignores unknown keys, so the task keeps running but is no longer
required -- the same state as the "keybag" task, which the port already relies on being optional. No
byte of the section moves, so nothing else in the plist or the Mach-O is disturbed.

Offsets (launchd.darwin, 525,088 B; the port's copy has its own NOPs at 0x23EC0/0x41408 and the
keybag rename, so the whole-file SHA-256 is not the pristine one -- the keys are checked instead):

    <key>mount-phase-2</key>   file 0x611f9
    RequireSuccess 's' -> 'x'  file 0x61230
    RequireRun      'n' -> 'x'  file 0x61254

Usage
-----
    python3 ci/patch-launchd-mountphase.py <launchd.darwin> [--check]
    python3 ci/patch-launchd-mountphase.py --payload-root <root dir> [--check]
    python3 ci/patch-launchd-mountphase.py --sysroot <sysroot.slim dir> [--check]

Idempotent: a second run reports "already applied".
"""
import argparse
import pathlib
import sys

PHASE_KEY = b"<key>mount-phase-2</key>"
RENAMES = ((b"RequireSuccess", b"RequireSuccesx"), (b"RequireRun", b"RequireRux"))

CANDIDATE_PATHS = ("sbin/launchd.darwin", "usr/sbin/launchd.darwin")


def find_target(args):
    if args.launchd:
        return pathlib.Path(args.launchd)
    base = None
    if args.payload_root:
        base = pathlib.Path(args.payload_root)
    elif args.sysroot:
        base = pathlib.Path(args.sysroot)
    if base is None:
        return None
    for name in CANDIDATE_PATHS:
        p = base / name
        if p.is_file():
            return p
    return None


def entry_bounds(data):
    """(start, end) of the mount-phase-2 dict, or (-1, -1)."""
    at = data.find(PHASE_KEY)
    if at < 0:
        return -1, -1
    end = data.find(b"</dict>", at)
    return (at, end + len(b"</dict>")) if end > 0 else (-1, -1)


def main(argv):
    ap = argparse.ArgumentParser(description="make launchd's mount-phase-2 task optional (in-place key rename)")
    ap.add_argument("launchd", nargs="?", help="path to launchd.darwin")
    ap.add_argument("--payload-root", help="payload APFS root (contains sbin/launchd.darwin)")
    ap.add_argument("--sysroot", help="sysroot.slim directory")
    ap.add_argument("--check", action="store_true", help="only look, do not write")
    args = ap.parse_args(argv)

    target = find_target(args)
    if target is None or not target.is_file():
        print("patch-launchd-mountphase: launchd.darwin not found (give a path, --payload-root or --sysroot)")
        return 2

    data = bytearray(target.read_bytes())
    start, end = entry_bounds(data)
    if start < 0:
        print("patch-launchd-mountphase: MISS: no <key>mount-phase-2</key> in %s" % target)
        return 1
    print("patch-launchd-mountphase: %s" % target)
    print("  mount-phase-2 entry: file %#x..%#x (%d bytes)" % (start, end, end - start))

    todo = []
    for old, new in RENAMES:
        at = data.find(old, start, end)
        if at < 0:
            at_new = data.find(new, start, end)
            if at_new >= 0:
                print("  %-14s already renamed at %#x" % (new.decode(), at_new))
                continue
            print("  MISS: neither %r nor %r inside the entry" % (old, new))
            return 1
        byte_at = at + len(old) - 1
        if data[byte_at] != old[-1]:
            print("  MISS: byte at %#x is %#04x, expected %#04x" % (byte_at, data[byte_at], old[-1]))
            return 1
        todo.append((at, byte_at, old, new))
        print("  %-14s at %#x, 1-byte rename at %#x: %r -> %r"
              % (old.decode(), at, byte_at, chr(old[-1]), chr(new[-1])))

    if not todo:
        print("  already applied -- the task is optional")
        return 0
    if args.check:
        print("  --check: patchable (%d byte(s))" % len(todo))
        return 0

    for _at, byte_at, old, new in todo:
        data[byte_at] = new[-1]
    target.write_bytes(bytes(data))
    print("  wrote %d bytes; the mount-phase-2 task now runs but is not required" % len(data))
    print()
    print("Expect on the next boot, in place of a boot that stops there:")
    print("  com.apple.xpc.launchd (mount-phase-2) <Notice>: Doing boot task")
    print("  mount: ... data volume missing ...   (and the boot goes on)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
