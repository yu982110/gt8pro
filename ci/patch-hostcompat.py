#!/usr/bin/env python3
"""在上游 hakios 树上打两个 CI 需要的补丁（都是 Ubuntu/上游 clang 才需要的，不改上游仓库）：

1. xnu-deps/hostinc/hostcompat.h：补 strlcpy/strlcat。
   Ubuntu 22.04 的 glibc 是 2.35，这两个函数 2.38 才进 glibc；XNU 的 SETUP 主机工具
   replacecontents 在链接时要用。hostcompat.h 被 -include 注入所有主机工具。

2. xnu-deps/host/bin/xnu-clang：不要再丢掉 -enable-trivial-auto-var-init-zero-knowing-...
   Apple 的 clang 不需要这个开关，L1ghtmann 的 LLVM 需要；上游 wrapper 把它过滤掉了，
   于是 `-ftrivial-auto-var-init=zero` 直接报错。

    python3 ci/patch-hostcompat.py hakios

幂等。
"""
import sys
from pathlib import Path

STRLIB_MARKER = "host_strlcat"
STRLIB_SNIPPET = r'''
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

CLANG_DROP = "    -enable-trivial-auto-var-init-zero-knowing-it-will-be-removed-from-clang) ;;\n"


def patch_hostcompat(root: Path) -> int:
    hdr = root / "xnu-deps" / "hostinc" / "hostcompat.h"
    if not hdr.is_file():
        print(f"patch: {hdr} not found", file=sys.stderr)
        return 1
    text = hdr.read_text(encoding="utf-8")
    if STRLIB_MARKER in text:
        print(f"patch: {hdr.name} already has strlcpy/strlcat")
        return 0
    hdr.write_text(text + STRLIB_SNIPPET, encoding="utf-8")
    print(f"patch: appended strlcpy/strlcat to {hdr}")
    return 0


def patch_xnu_clang(root: Path) -> int:
    wrapper = root / "xnu-deps" / "host" / "bin" / "xnu-clang"
    if not wrapper.is_file():
        print(f"patch: {wrapper} not found", file=sys.stderr)
        return 1
    text = wrapper.read_text(encoding="utf-8")
    if CLANG_DROP not in text:
        print("patch: xnu-clang already keeps the auto-var-init switch")
        return 0
    wrapper.write_text(text.replace(CLANG_DROP, ""), encoding="utf-8")
    print(f"patch: xnu-clang now passes -enable-trivial-auto-var-init-zero-... through")
    return 0


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "hakios")
    rc = patch_hostcompat(root)
    rc |= patch_xnu_clang(root)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
