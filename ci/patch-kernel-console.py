#!/usr/bin/env python3
"""把内核日志和 /dev/console 的输出画到屏幕上（本移植没有显示驱动，XNU 自带的视频控制台
已经在按启动参数初始化，只差把字符送进去）。

    python3 ci/patch-kernel-console.py <xnu 源码树>
"""
import sys
from pathlib import Path

# --- 1) 内核的每个字符：loader_return.c 的 loader_log_put_locked -------------------------------

LR_ANCHOR = """static void
loader_log_put_locked(char c)
{
"""

LR_HELPER = """/* HAKIOS-LAFA: draw kernel output on the panel (the port has no display driver of its own) */
#include <console/video_console.h>
extern void vcputc(char c);
extern struct vc_info vinfo;
static inline int
lafa_vc_ready(void)
{
\treturn vinfo.v_baseaddr != 0;
}

static void
loader_log_put_locked(char c)
{
\tif (lafa_vc_ready()) {
\t\tvcputc(c);
\t}
"""

# --- 2) 用户态写 /dev/console：bsd/dev/arm/cons.c 的 cnwrite ---------------------------------

CW_ANCHOR = """\tdev = cndev();
\treturn (*cdevsw[major(dev)].d_write)(dev, uio, ioflag);
"""

CW_NEW = """\t/* HAKIOS-LAFA: what user space writes to /dev/console goes to the panel */
\t{
\t\textern void vcputc(char);
\t\textern struct vc_info vinfo;
\t\tchar buf[256];
\t\tint n = (int)uio_resid(uio);

\t\tif (vinfo.v_baseaddr == 0) {
\t\t\treturn 0;
\t\t}
\t\twhile (n > 0) {
\t\t\tint want = n > (int)sizeof(buf) ? (int)sizeof(buf) : n;
\t\t\tif (uiomove(buf, want, uio) != 0) {
\t\t\t\tbreak;
\t\t\t}
\t\t\tfor (int i = 0; i < want; i++) {
\t\t\t\tvcputc(buf[i]);
\t\t\t}
\t\t\tn -= want;
\t\t}
\t}
\treturn 0;
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    lr = root / "osfmk" / "arm64" / "loader_return.c"
    cn = root / "bsd" / "dev" / "arm" / "cons.c"

    text = lr.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: draw kernel output" not in text:
        if LR_ANCHOR not in text:
            print("patch-console: loader_return.c anchor not found", file=sys.stderr)
            return 1
        text = text.replace(LR_ANCHOR, LR_HELPER, 1)
        lr.write_text(text, encoding="utf-8", errors="surrogateescape")
        print("patch-console: kernel log -> vcputc")
    else:
        print("patch-console: loader_return.c already patched")

    text = cn.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: what user space writes" not in text:
        if CW_ANCHOR not in text:
            print("patch-console: cons.c anchor not found", file=sys.stderr)
            return 1
        text = text.replace(CW_ANCHOR, CW_NEW, 1)
        cn.write_text(text, encoding="utf-8", errors="surrogateescape")
        print("patch-console: /dev/console -> vcputc")
    else:
        print("patch-console: cons.c already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
