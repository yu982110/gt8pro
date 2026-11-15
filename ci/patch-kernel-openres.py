#!/usr/bin/env python3
"""HAKIOS-LAFA-OPENLINE: one line per failing open: path + error.

The two-line version (HAKIOS-OPEN path before the call, HAKIOS-OPENRES ok/fail after it)
could not be correlated because open() is called from several threads at once.  This
version logs the path and the error together, for backboardd/SpringBoard only.

No snprintf: the string is assembled by hand, because adding a new kernel dependency here
broke the kext symbol-set check in an earlier build.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-OPENLINE */"
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
    "\tif (lafa_open_err != 0 &&\n"
    "\t    ((p->p_comm[0] == 'b' && strncmp(p->p_comm, \"backboardd\", 10) == 0) ||\n"
    "\t     (p->p_comm[0] == 'S' && strncmp(p->p_comm, \"SpringBoard\", 11) == 0))) {\n"
    "\t\tchar lafa_ob[96];\n"
    "\t\tchar lafa_pb[56];\n"
    "\t\tconst char *lafa_tag = \"HAKIOS-OPENFAIL \";\n"
    "\t\tint lafa_i = 0;\n"
    "\t\tint lafa_j;\n"
    "\t\twhile (lafa_tag[lafa_i] != '\\0' && lafa_i < 24) {\n"
    "\t\t\tlafa_ob[lafa_i] = lafa_tag[lafa_i];\n"
    "\t\t\tlafa_i++;\n"
    "\t\t}\n"
    "\t\tunsigned long lafa_len = 0;\n"
    "\t\tlafa_pb[0] = '\\0';\n"
    "\t\t(void) copyinstr(uap->path, lafa_pb, sizeof(lafa_pb), &lafa_len);\n"
    "\t\tfor (lafa_j = 0; lafa_pb[lafa_j] != '\\0' && lafa_i < 92; lafa_j++) {\n"
    "\t\t\tlafa_ob[lafa_i++] = lafa_pb[lafa_j];\n"
    "\t\t}\n"
    "\t\tlafa_ob[lafa_i++] = '\\n';\n"
    "\t\tlafa_ob[lafa_i] = '\\0';\n"
    "\t\tloader_log_line(lafa_ob);\n"
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
            print("patch-openline: already applied in", p)
            hits += 1
            continue
        if ANCHOR not in text:
            print("patch-openline: MISS anchor in", p)
            continue
        text = text.replace(ANCHOR, REPL, 1)
        if DECL not in text:
            i = text.find("#include")
            j = text.find("\n", i)
            text = text[:j + 1] + DECL + text[j + 1:]
        p.write_text(text)
        print("patch-openline: failing opens of backboardd/SpringBoard are logged with the path in", p)
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
