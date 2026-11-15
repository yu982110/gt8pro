#!/usr/bin/env python3
"""HAKIOS-LAFA-OPENRES: did the watched processes' open() succeed?

dyld opens the shared cache files but no library ever loads, so the next question is
whether those opens succeed at all.  open_nocancel() has a single return point, so the
probe is one line: for backboardd/SpringBoard, log ok/fail of every open.

Correlate with the HAKIOS-OPEN path lines (the probe logs the path before the call).

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-OPENRES */"
ANCHOR = (
    "open_nocancel(__unused proc_t p, struct open_nocancel_args *uap,\n"
    "    int32_t *retval)\n"
    "{\n"
    "\treturn openat_internal(vfs_context_current(), uap->path, uap->flags,\n"
    "\t           uap->mode, AT_FDCWD, UIO_USERSPACE, retval);\n"
    "}\n"
)
REPL = (
    "open_nocancel(proc_t p, struct open_nocancel_args *uap,\n"
    "    int32_t *retval)\n"
    "{\n"
    "\tint lafa_open_err = openat_internal(vfs_context_current(), uap->path, uap->flags,\n"
    "\t           uap->mode, AT_FDCWD, UIO_USERSPACE, retval);\n"
    "\t" + MARK + "\n"
    "\tif ((p->p_comm[0] == 'b' && strncmp(p->p_comm, \"backboardd\", 10) == 0) ||\n"
    "\t    (p->p_comm[0] == 'S' && strncmp(p->p_comm, \"SpringBoard\", 11) == 0)) {\n"
    "\t\tloader_log_line(lafa_open_err ? \"HAKIOS-OPENRES fail\\n\" : \"HAKIOS-OPENRES ok\\n\");\n"
    "\t}\n"
    "\treturn lafa_open_err;\n"
    "}\n"
)
DECL = "extern void loader_log_line(const char *line);\n"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-openres.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for p in root.rglob("vfs_syscalls.c"):
        text = p.read_text(errors="replace")
        if MARK in text:
            print("patch-openres: already applied in", p)
            hits += 1
            continue
        if ANCHOR not in text:
            print("patch-openres: MISS anchor in", p)
            continue
        text = text.replace(ANCHOR, REPL, 1)
        if DECL not in text:
            # put the extern near the top, after the first include line
            i = text.find("#include")
            j = text.find("\n", i)
            text = text[:j + 1] + DECL + text[j + 1:]
        p.write_text(text)
        print("patch-openres: open() result logged for backboardd/SpringBoard in", p)
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
