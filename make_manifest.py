#!/usr/bin/env python3
"""生成 manifest.json：记录每个被替换文件的「基础哈希 / 新哈希」。

在**容器内**跑（需要容器里真实的 octop_gateway 作基准）：

    /app/.venv/bin/python3 make_manifest.py

为什么要有这一步：镜像构建时用 manifest 做双向校验 ——
基础哈希不符（说明镜像版本变了）或新哈希不符（说明复制出错）都直接失败。
"""
from __future__ import annotations

import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.join(HERE, "files", "octop_gateway")
LIVE_ROOT = "/app/.venv/lib/python3.12/site-packages/octop_gateway"
OUT = os.path.join(HERE, "manifest.json")

REL_FILES = [
    "channel.py",
    "channels/feishu.py",
    "channels/discord.py",
    "channels/dingtalk.py",
    "channels/wecom.py",
    "channels/telegram/channel.py",
    "channels/qq/channel.py",
    "channels/xiaoyi/channel.py",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = []
    for rel in REL_FILES:
        src = os.path.join(SRC_ROOT, rel)
        live = os.path.join(LIVE_ROOT, rel)
        if not os.path.isfile(src):
            raise SystemExit("缺文件: " + src)
        if not os.path.isfile(live):
            raise SystemExit("容器内缺文件: " + live)
        files.append({"rel": rel, "base": sha256(live), "new": sha256(src)})

    payload = {
        "generated_by": "make_manifest.py",
        "base_image": "ghcr.io/tencentcloud/octop:1.0.2",
        "note": "base = 官方镜像内原文件；new = fork 内替换后文件",
        "files": files,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    for f in files:
        print("%-30s base=%s new=%s" % (f["rel"], f["base"][:12], f["new"][:12]))
    print("\n写出 %s（%d 个文件）" % (os.path.basename(OUT), len(files)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
