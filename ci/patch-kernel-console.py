#!/usr/bin/env python3
"""让用户态写 /dev/console 的内容进入内核日志（loader 会实时打印内核日志，所以它就能被看见）。

不碰 framebuffer：vcputc 在本移植上会在 pmap 切换后崩内核，这条路不走。

    python3 ci/patch-kernel-console.py <xnu 源码树>
"""
import sys
from pathlib import Path

CW_ANCHOR = """\tdev = cndev();
\treturn (*cdevsw[major(dev)].d_write)(dev, uio, ioflag);
"""

CW_NEW = """\t/* HAKIOS-LAFA: no console device on this port yet: put what user space writes to
\t * /dev/console into the kernel log, which the loader shows while the kernel runs */
\t{
\t\tchar buf[256];
\t\tint n = (int)uio_resid(uio);

\t\twhile (n > 0) {
\t\t\tint want = n > (int)sizeof(buf) ? (int)sizeof(buf) : n;
\t\t\tif (uiomove(buf, want, uio) != 0) {
\t\t\t\tbreak;
\t\t\t}
\t\t\tfor (int i = 0; i < want; i++) {
\t\t\t\tprintf("%c", buf[i]);
\t\t\t}
\t\t\tn -= want;
\t\t}
\t}
\treturn 0;
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    cn = root / "bsd" / "dev" / "arm" / "cons.c"
    text = cn.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: no console device" in text:
        print("patch-console: cons.c already patched")
        return 0
    if CW_ANCHOR not in text:
        print("patch-console: cons.c anchor not found", file=sys.stderr)
        return 1
    text = text.replace(CW_ANCHOR, CW_NEW, 1)
    cn.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-console: /dev/console -> kernel log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
