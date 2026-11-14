#!/usr/bin/env python3
"""patch-launchd-task-optional.py -- make one of launchd's boot tasks optional instead of required.

Why
---
A boot task with RequireSuccess (or RequireRun) whose Program is missing, or which exits non-zero,
makes launchd panic with

    panic: boot task failure: <name> - required boot task executable not found

and the machine reboots -- which, from the console, looks like "it rebooted after <the task before
it>". On this port /usr/libexec/darwinos-boot-task does not exist and the task is required, so the
boot dies right after MSUEarlyBootTask (which is only AllowCrash, and prints "I have nothing to do").

How
---
The task's dict is in launchd's __TEXT,__config as XML. Both keys are renamed one character, in
place, to names launchd does not know ("RequireSuccesx", "RequireRux"): a plist reader ignores keys
it does not know, so the task keeps running but is no longer required -- the same state as the
keybag task, whose dict has neither key and is therefore optional. No byte of the section moves.

Usage
-----
    python3 ci/patch-launchd-task-optional.py <launchd.darwin> --task darwinos-boot-task [--check]
    python3 ci/patch-launchd-task-optional.py --payload-root <root dir> --task NAME [--check]
    python3 ci/patch-launchd-task-optional.py --list        # every task, and the keys it has

Idempotent: a second run reports "already applied".
"""
import argparse
import pathlib
import re
import sys

CANDIDATE_PATHS = ("sbin/launchd.darwin", "usr/sbin/launchd.darwin")
RENAMES = ((b"RequireSuccess", b"RequireSuccesx"), (b"RequireRun", b"RequireRux"))
TASK_KEY = re.compile(rb"<key>([A-Za-z0-9-]+)</key>\s*<dict>")


def find_target(args):
    if args.launchd:
        return pathlib.Path(args.launchd)
    base = pathlib.Path(args.payload_root) if args.payload_root else None
    if base is None:
        return None
    for name in CANDIDATE_PATHS:
        p = base / name
        if p.is_file():
            return p
    return None


def entry_of(data, task):
    """(start, end) of the task's dict: the first </dict> after its key."""
    at = data.find(b"<key>" + task.encode() + b"</key>")
    if at < 0:
        return -1, -1
    end = data.find(b"</dict>", at)
    return (at, end + len(b"</dict>")) if end > 0 else (-1, -1)


def list_tasks(data):
    print("boot tasks in the order they appear:")
    for m in TASK_KEY.finditer(data):
        off, name = m.start(), m.group(1).decode()
        end = data.find(b"</dict>", off)
        entry = data[off:end]
        keys = [k.decode() for k in (b"RequireSuccess", b"RequireRun") if b"<key>" + k + b"</key>" in entry]
        prog = re.search(rb"<key>Program</key>\s*<string>([^<]*)</string>", entry)
        print(f"  {name:26} {('required: ' + ','.join(keys)) if keys else 'optional':34} "
              f"{prog.group(1).decode() if prog else ''}")


def main(argv):
    ap = argparse.ArgumentParser(description="make a launchd boot task optional (in-place key rename)")
    ap.add_argument("launchd", nargs="?", help="path to launchd.darwin")
    ap.add_argument("--payload-root", help="payload APFS root (contains sbin/launchd.darwin)")
    ap.add_argument("--task", help="the boot task's name, as launchd's Boot dict has it")
    ap.add_argument("--list", action="store_true", help="print every boot task and its keys")
    ap.add_argument("--check", action="store_true", help="only look, do not write")
    args = ap.parse_args(argv)

    target = find_target(args)
    if target is None or not target.is_file():
        print("patch-launchd-task-optional: launchd.darwin not found (path or --payload-root)")
        return 2
    data = bytearray(target.read_bytes())

    if args.list:
        list_tasks(bytes(data))
        return 0
    if not args.task:
        print("patch-launchd-task-optional: give --task NAME (or --list)")
        return 2

    start, end = entry_of(data, args.task)
    if start < 0:
        print(f"patch-launchd-task-optional: MISS: no <key>{args.task}</key> in {target}")
        return 1
    print(f"patch-launchd-task-optional: {target}")
    print(f"  {args.task}: file {start:#x}..{end:#x} ({end - start} bytes)")

    todo = []
    for old, new in RENAMES:
        at = data.find(old, start, end)
        if at < 0:
            if data.find(new, start, end) >= 0:
                print(f"  {new.decode():14} already renamed")
                continue
            print(f"  {old.decode():14} not in this task (nothing to do for it)")
            continue
        byte_at = at + len(old) - 1
        if data[byte_at] != old[-1]:
            print(f"  MISS: byte at {byte_at:#x} is {data[byte_at]:#04x}, expected {old[-1]:#04x}")
            return 1
        todo.append((byte_at, old, new))
        print(f"  {old.decode():14} at {at:#x}, 1-byte rename at {byte_at:#x}: "
              f"{chr(old[-1])!r} -> {chr(new[-1])!r}")

    if not todo:
        print("  already applied -- the task is optional")
        return 0
    if args.check:
        print(f"  --check: patchable ({len(todo)} byte(s))")
        return 0

    for byte_at, _old, new in todo:
        data[byte_at] = new[-1]
    target.write_bytes(bytes(data))
    print(f"  wrote {len(data)} bytes; '{args.task}' now runs but is not required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
