#!/usr/bin/env python3
"""给 xnu-deps/hostinc/hostcompat.h 追加 strlcpy/strlcat 的实现。

Ubuntu 22.04 的 glibc 是 2.35，strlcpy/strlcat 要到 2.38 才有；XNU 的 SETUP 主机工具
（replacecontents）在链接时要用它们，上游是在更新的 glibc 上编的。hostcompat.h 被
xnu-build.sh 用 -include 注入所有主机工具的编译，所以在它末尾定义即可。

    python3 ci/patch-hostcompat.py hakios

幂等：已经打过就什么都不做。
"""
import sys
from pathlib import Path

MARKER = "host_strlcat"

SNIPPET = r'''
/* glibc < 2.38 has no strlcpy/strlcat (added in 2.38); XNU's SETUP host tools use both. */
static inline size_t host_strlcpy(char *dst, const char *src, size_t size) {
	size_t len = strlen(src);
	if (size) {
		size_t n = (len >= size) ? (size - 1) : len;
		memcpy(dst, src, n);
		dst[n] = 0;
	}
	return len;
}
static inline size_t host_strlcat(char *dst, const char *src, size_t size) {
	size_t dlen = strnlen(dst, size);
	if (dlen == size) return size + strlen(src);
	return dlen + host_strlcpy(dst + dlen, src, size - dlen);
}
#define strlcpy host_strlcpy
#define strlcat host_strlcat
'''

def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "hakios")
    hdr = root / "xnu-deps" / "hostinc" / "hostcompat.h"
    if not hdr.is_file():
        print(f"patch-hostcompat: {hdr} not found", file=sys.stderr)
        return 1
    text = hdr.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"patch-hostcompat: already patched ({hdr})")
        return 0
    hdr.write_text(text + SNIPPET, encoding="utf-8")
    print(f"patch-hostcompat: appended strlcpy/strlcat to {hdr}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
