#!/usr/bin/env python3
"""镜像内执行：把 fork 的文件替换进 octop_gateway，全程哈希校验。

设计原则：**任何不确定都让构建失败**，不允许"看起来装上了但其实是旧文件"。
2026-10-15 的无限重启事故就是旧版本文件配上新版本镜像且静默通过。

用法（在 Dockerfile RUN 里）：
    python apply.py --base-dir <octop_gateway 目录> --src <复制进来的源码根> \
                    --manifest manifest.json
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fail(msg: str) -> None:
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    args = sys.argv[1:]
    opts = {}
    for i in range(0, len(args) - 1, 2):
        opts[args[i].lstrip("-")] = args[i + 1]
    base_dir = opts.get("base-dir")
    src = opts.get("src")
    manifest_path = opts.get("manifest")
    if not (base_dir and src and manifest_path):
        fail("需要 --base-dir / --src / --manifest")

    entries = json.load(open(manifest_path, encoding="utf-8"))["files"]
    print("目标目录: %s" % base_dir)
    print("待替换: %d 个文件" % len(entries))

    # 第一步：全部校验基础哈希，一个不符就整体放弃（不做部分替换）
    for e in entries:
        target = os.path.join(base_dir, e["rel"])
        if not os.path.isfile(target):
            fail("目标不存在: %s" % target)
        actual = sha256(target)
        if actual != e["base"]:
            fail(
                "基础哈希不符: %s\n  期望 %s\n  实际 %s\n"
                "  => 基础镜像版本变了或上游改了代码，需要重新同步 fork 并重算 manifest"
                % (target, e["base"], actual)
            )
    print("✓ 基础哈希全部匹配")

    # 第二步：复制并逐个校验新哈希
    for e in entries:
        source = os.path.join(src, e["rel"])
        target = os.path.join(base_dir, e["rel"])
        if not os.path.isfile(source):
            fail("源文件缺失: %s" % source)
        shutil.copyfile(source, target)
        actual = sha256(target)
        if actual != e["new"]:
            fail("替换后哈希不符: %s\n  期望 %s\n  实际 %s" % (target, e["new"], actual))
    print("✓ 替换完成，新哈希全部匹配")

    # 第三步：冒烟 —— 只验证"新行为真的在"，不验证业务逻辑
    # 注意：必须让 import 解析到**刚替换过的目录**，否则检查的是别处的旧代码。
    # 镜像里 base-dir 就在 site-packages 下，插入父目录是无害的；但如果不插，
    # 一旦安装路径与 base-dir 不一致，冒烟会对着旧代码跑出假阳性。
    import importlib

    real_base = os.path.realpath(base_dir)
    parent = os.path.dirname(real_base)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    for mod_name in list(sys.modules):
        if mod_name == "octop_gateway" or mod_name.startswith("octop_gateway."):
            del sys.modules[mod_name]

    chan = importlib.import_module("octop_gateway.channel")
    loaded_from = os.path.realpath(getattr(chan, "__file__", ""))
    if os.path.dirname(loaded_from) != real_base:
        fail(
            "import 解析到的 octop_gateway 不是替换目录\n"
            "  期望 %s\n  实际 %s" % (real_base, os.path.dirname(loaded_from))
        )
    checks = [
        ("BaseChannel.is_duplicate", hasattr(chan.BaseChannel, "is_duplicate")),
        ("BaseChannel.has_seen", hasattr(chan.BaseChannel, "has_seen")),
        ("dedup store path set", bool(getattr(chan, "_DEDUP_DB_PATH", ""))),
    ]
    feishu = importlib.import_module("octop_gateway.channels.feishu")
    checks.append(("feishu inherits is_duplicate", "is_duplicate" not in feishu.FeishuChannel.__dict__))
    checks.append(("feishu ws threshold 21600", getattr(feishu, "_WS_ACTIVITY_STALE_SECONDS", 0) == 21600.0))
    discord_mod = importlib.import_module("octop_gateway.channels.discord")
    checks.append(("discord inherits is_duplicate", "is_duplicate" not in discord_mod.DiscordChannel.__dict__))

    # 其余通道必须还能 import（回归）
    for name in (
        "octop_gateway.channels.telegram.channel",
        "octop_gateway.channels.qq.channel",
        "octop_gateway.channels.xiaoyi.channel",
        "octop_gateway.channels.dingtalk",
        "octop_gateway.channels.wecom",
        "octop_gateway.channels.yuanbao.channel",
    ):
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            checks.append(("import " + name, False))
            print("  import 失败 %s: %r" % (name, exc))
        else:
            checks.append(("import " + name, True))

    bad = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(("  ✓ " if ok else "  ✗ ") + label)
    if bad:
        fail("冒烟失败: %s" % ", ".join(bad))
    print("smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
