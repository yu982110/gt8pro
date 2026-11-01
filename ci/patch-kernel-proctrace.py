#!/usr/bin/env python3
"""在 exec 成功后打印一行内核日志（本移植还没有控制台，这是唯一能看见用户态的办法）。

定位方式：找到 load_machfile 调用，再找它后面第一个 "\t}" 结束的错误分支，插在它后面。

    python3 ci/patch-kernel-proctrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

CALL = "lret = load_machfile(imgp, mach_header, thread, &map, &load_result);"

INSERT = """
	/* HAKIOS-LAFA: the image is loaded: say what is about to run (the port has no console yet) */
	{
		const char *lafa_path = (imgp->ip_ndp != NULL) ? imgp->ip_ndp->ni_cnd.cn_nameptr : "?";
		printf("proctrace: pid %d exec ok: %s entry %llx\\n",
		    proc_getpid(p), lafa_path, (unsigned long long)load_result.entry_point);
	}
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "kern_exec.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: the image is loaded" in text:
        print("patch-proctrace: already patched")
        return 0
    idx = text.find(CALL)
    if idx < 0:
        print("patch-proctrace: load_machfile call not found", file=sys.stderr)
        return 1
    end = text.find("\n\t}\n", idx)
    if end < 0:
        print("patch-proctrace: end of the error branch not found", file=sys.stderr)
        return 1
    end += len("\n\t}\n")
    text = text[:end] + INSERT + text[end:]
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-proctrace: exec trace inserted after the load_machfile error branch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
