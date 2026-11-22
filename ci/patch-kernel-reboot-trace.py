#!/usr/bin/env python3
"""HAKIOS-REBOOT-TRACE: name whoever calls reboot(2), and do not actually reset.

The loader alone survives 663 s (2026-11-22 16:16), so the platform has no timer that kills the
machine: every 56-89 s death comes from the guest.  The kernel is idle (launchd in kevent) when
it happens, the reset is PS_HOLD and the ABL records "kernel crash Fulldump", and the per-syscall
log only watches launchd/backboardd/logd -- a reboot(2) from any other daemon leaves no trace.

So: print the caller (pid + comm + howto) through the loader's log hook, and return success
without resetting.  If a userspace daemon is the killer the run survives and the line names it;
if the machine still resets, the path is not reboot(2) and the next step is the SMC audit.

Idempotent; exits 0 when already applied, 1 when no anchor is found.

    python3 ci/patch-kernel-reboot-trace.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-REBOOT-TRACE"

ANCHORS = [
    "int\nreboot(proc_t p, struct reboot_args *uap, int32_t *retval)\n{\n",
    "int\nreboot(proc_t p, struct reboot_args *uap, __unused int32_t *retval)\n{\n",
]

BLOCK = (
    "\t/* " + MARK + ": who is rebooting the machine?  Print it and do not reset. */\n"
    "\t{\n"
    "\t\tchar lafa_rb[160];\n"
    "\t\tint lafa_rbp = 0;\n"
    "\n"
    "\t\tif (p != NULL) {\n"
    "\t\t\tlafa_rbp = proc_pid(p);\n"
    "\t\t}\n"
    "\t\tsnprintf(lafa_rb, sizeof(lafa_rb), "
    "\"HAKIOS-REBOOT pid=%d comm=%s howto=%x\\n\",\n"
    "\t\t    lafa_rbp, (p != NULL) ? p->p_comm : \"?\", (unsigned)uap->opt);\n"
    "\t\tloader_log_line(lafa_rb);\n"
    "\t\treturn 0;\n"
    "\t}\n"
)

DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("bsd/kern/kern_shutdown.c"), None)
    if f is None:
        print("patch-reboot-trace: MISS bsd/kern/kern_shutdown.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-reboot-trace: already applied")
        return 0
    for a in ANCHORS:
        if a in text:
            text = text.replace(a, a + BLOCK, 1)
            if DECL not in text:
                # put the declaration after the last #include near the top
                idx = text.find("#include")
                if idx >= 0:
                    text = DECL + text
            f.write_text(text, encoding="utf-8")
            print("patch-reboot-trace: installed (returns 0 instead of resetting)")
            return 0
    print("patch-reboot-trace: MISS reboot() definition")
    return 1


if __name__ == "__main__":
    sys.exit(main())
