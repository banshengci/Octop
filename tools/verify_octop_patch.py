#!/usr/bin/env python3
"""验证 octop 本体补丁（层 2）是否真的生效 —— 在**构建出的镜像内**跑。

用法（镜像内）:
    /app/.venv/bin/python3 /path/tools/verify_octop_patch.py [--manifest /path/manifest.octop.json]

检查项：
  1. /app/src/octop/<rel> 的 sha256 == manifest.octop.json 的 new（文件真的换了）
  2. 语法可编译
  3. 关键符号在（is_history_dump / history_tool_call_ids）
  4. 行为正确：单条消息不算历史 dump，多条才算
设计取向与 apply.py 一致：**任何不确定都让校验失败**，不允许"看起来装上了"。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import sys

DEFAULT_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "manifest.octop.json")
SRC_ROOT = "/app/src/octop"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--src", default=SRC_ROOT)
    a = ap.parse_args()

    man = json.load(open(a.manifest, encoding="utf-8"))
    checks: list[tuple[str, bool]] = []
    for e in man["files"]:
        target = os.path.join(a.src, e["rel"])
        if not os.path.isfile(target):
            checks.append(("存在 " + e["rel"], False))
            continue
        checks.append(("哈希 " + e["rel"], sha256(target) == e["new"]))
        try:
            py_compile.compile(target, doraise=True)
            checks.append(("编译 " + e["rel"], True))
        except Exception as exc:  # noqa: BLE001
            print("  编译失败 %s: %r" % (e["rel"], exc))
            checks.append(("编译 " + e["rel"], False))

    tm = os.path.join(a.src, "infra/gateway/media/tool_media.py")
    src = open(tm, encoding="utf-8").read()
    checks += [
        ("存在 is_history_dump", "def is_history_dump(" in src),
        ("存在 history_tool_call_ids", "def history_tool_call_ids(" in src),
        ("dashboard 投影遇 dump 提前返回", "if is_history_dump(chunk):" in src),
    ]

    # 行为验证：只加载 tool_media 模块，不初始化整个应用
    sys.path.insert(0, a.src.rsplit("/octop", 1)[0])
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_tm_verify", tm)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        single = {"messages": [{"tool_call_id": "a"}]}
        multi = {"messages": [{"tool_call_id": str(i)} for i in range(9)]}
        checks += [
            ("行为: 单条 → 不算 dump", mod.is_history_dump(single) is False),
            ("行为: 多条 → 算 dump", mod.is_history_dump(multi) is True),
        ]
    except Exception as exc:  # noqa: BLE001
        print("  行为校验无法执行: %r" % (exc,))
        checks.append(("行为校验可执行", False))

    bad = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(("  ✓ " if ok else "  ✗ ") + label)
    if bad:
        print("\nFAILED: " + ", ".join(bad), file=sys.stderr)
        return 1
    print("\nOctop 层补丁校验通过 (%d 项)" % len(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
