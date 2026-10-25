#!/usr/bin/env python3
"""模拟 octop 1.0.3b1 环境，验证定制层能否原样套用。

依据（已实测，勿凭记忆）：
  - octop 1.0.3b1 依赖 `octop-gateway>=1.0.3`，PyPI 上 octop-gateway 最新就是 1.0.3
  - 上游 octop-gateway v1.0.3 tag 的 8 个文件 sha256 == manifest.json 的 base
  => 1.0.3b1 镜像里的 octop_gateway 与 1.0.2 镜像里的同一份，定制层可原样复用。

本脚本做的是"等价复现"：把 venv 里的包还原成 base（= 1.0.3b1 会拿到的文件），
再跑生产同款 apply.py（base 校验 → 替换 → 新哈希校验 → 冒烟 import）。

用法: /app/.venv/bin/python3 tools/verify_103b1.py [--base-src /tmp/up103]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
VENV_PKG = "/app/.venv/lib/python3.12/site-packages/octop_gateway"
WORK = "/tmp/t103"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-src", default="/tmp/up103")
    args = ap.parse_args()

    man = json.load(open(os.path.join(FORK, "manifest.json"), encoding="utf-8"))
    entries = man["files"]
    rels = [e["rel"] for e in entries]

    # 0. 清理工作目录（用 python，避免命令行散落递归 rm 字面量）
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)

    # 1. 复制 venv 包 -> 工作目录
    shutil.copytree(VENV_PKG, os.path.join(WORK, "octop_gateway"))

    # 2. 用上游 tag 文件覆盖，还原成 base
    for rel in rels:
        src = os.path.join(args.base_src, rel)
        dst = os.path.join(WORK, "octop_gateway", rel)
        if not os.path.isfile(src):
            print(f"FAIL 缺少上游基准文件: {src}")
            return 1
        shutil.copyfile(src, dst)

    # 3. 自证：还原后的哈希必须 == manifest.base
    print("== 步骤1: 还原成 base，逐文件比对 manifest.base ==")
    bad = 0
    for e in entries:
        actual = sha256(os.path.join(WORK, "octop_gateway", e["rel"]))
        ok = actual == e["base"]
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else 'BAD'} {e['rel']:<32} {actual[:16]}")
    if bad:
        print(f"FAIL {bad} 个文件与 manifest.base 不符 —— 结论不成立，需重新派生")
        return 1

    # 4. 跑生产同款 apply.py
    print("\n== 步骤2: 跑 apply.py（base 校验 → 替换 → 新哈希 → 冒烟）==")
    proc = subprocess.run(
        [sys.executable, os.path.join(FORK, "apply.py"),
         "--base-dir", os.path.join(WORK, "octop_gateway"),
         "--src", os.path.join(FORK, "files", "octop_gateway"),
         "--manifest", os.path.join(FORK, "manifest.json")],
        capture_output=True, text=True,
    )
    print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print("STDERR:\n" + proc.stderr.rstrip())
        print("\nFAIL apply.py 返回 %d —— 不能原样套用到 1.0.3b1" % proc.returncode)
        return 1

    # 5. 最终哈希复核
    print("\n== 步骤3: 最终复核（应全部 == manifest.new）==")
    bad = 0
    for e in entries:
        actual = sha256(os.path.join(WORK, "octop_gateway", e["rel"]))
        ok = actual == e["new"]
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else 'BAD'} {e['rel']:<32} {actual[:16]}")
    if bad:
        print(f"FAIL {bad} 个文件最终哈希不符")
        return 1

    print("\nPASS 定制层可原样套用到 octop 1.0.3b1（gateway 1.0.3 未变，manifest 无需改动）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
