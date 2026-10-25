#!/usr/bin/env python3
"""第 2 步的回归测试：基类幂等组件 + feishu/discord 接入。

先定答案（不是让脚本自证）：
  ① BaseChannel.is_duplicate 存在且被 feishu/discord 继承（子类没有覆盖）
  ② feishu 真实实例：首次 False，二次 True
  ③ feishu **新实例**（模拟重启）同 key => True        ← 核心
  ④ discord 真实实例同 ②③
  ⑤ feishu / discord 的私有实现已删除（_is_duplicate / _seen / _seen_message_ids 不再存在）
  ⑥ 其余通道（xiaoyi/telegram/qq）仍能 import，且仍用自己的私有实现（兼容不改）
  ⑦ 全通道模块 import 不报错

跑法：/app/.venv/bin/python3 verify_channel_integration.py --src <fork>/src
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

SRC = "/tmp/gw2-095643/src"
if "--src" in sys.argv:
    SRC = sys.argv[sys.argv.index("--src") + 1]
sys.path.insert(0, SRC)

TMP = tempfile.mkdtemp(prefix="chan-integration-")
os.environ["OCTOP_INBOUND_DEDUP_DB"] = os.path.join(TMP, "dedup.sqlite")

import octop_gateway.channel as chan_mod  # noqa: E402
from octop_gateway.channels.feishu import FeishuChannel, FeishuConfig  # noqa: E402


def reset_store() -> None:
    if chan_mod._dedup_conn is not None:
        try:
            chan_mod._dedup_conn.close()
        except Exception:
            pass
    chan_mod._dedup_conn = None
    chan_mod._dedup_last_sweep = 0.0


def new_feishu():
    return FeishuChannel(processor=None, config=FeishuConfig(app_id="cli_test", app_secret="secret_test"))


def main() -> int:
    out: list[tuple[str, bool]] = []

    # ① 继承关系：子类不能覆盖基类方法
    out.append((
        "① feishu 未覆盖 is_duplicate（继承基类）",
        "is_duplicate" not in FeishuChannel.__dict__
        and FeishuChannel.is_duplicate is chan_mod.BaseChannel.is_duplicate,
    ))

    reset_store()
    ch = new_feishu()
    key = "om_integration_0001"
    out.append(("② feishu 首次 => False", ch.is_duplicate(key) is False))
    out.append(("② feishu 二次 => True", ch.is_duplicate(key) is True))

    reset_store()  # 模拟进程重启
    ch2 = new_feishu()
    out.append(("③ **feishu 重启后同 key => True**", ch2.is_duplicate(key) is True))

    # ④ discord
    try:
        dmod = importlib.import_module("octop_gateway.channels.discord")
        out.append((
            "④ discord 未覆盖 is_duplicate（继承基类）",
            "is_duplicate" not in dmod.DiscordChannel.__dict__,
        ))
        reset_store()
        d = dmod.DiscordChannel(processor=None, config=dmod.DiscordConfig(bot_token="t"))
        out.append(("④ discord 首次 => False", d.is_duplicate("123456789") is False))
        out.append(("④ discord 二次 => True", d.is_duplicate("123456789") is True))
        reset_store()
        d2 = dmod.DiscordChannel(processor=None, config=dmod.DiscordConfig(bot_token="t"))
        out.append(("④ **discord 重启后同 key => True**", d2.is_duplicate("123456789") is True))
    except Exception as exc:  # noqa: BLE001
        print("!! discord 段失败: %r" % (exc,))
        out.append(("④ discord 段完成", False))

    # ⑤ 私有实现已删除
    fsrc = open(os.path.join(SRC, "octop_gateway/channels/feishu.py"), encoding="utf-8").read()
    dsrc = open(os.path.join(SRC, "octop_gateway/channels/discord.py"), encoding="utf-8").read()
    out.append((
        "⑤ feishu 私有去重已删除",
        "def _is_duplicate" not in fsrc and "_seen_message_ids" not in fsrc,
    ))
    out.append((
        "⑤ discord 私有去重已删除",
        "def _is_duplicate" not in dsrc and "self._seen" not in dsrc,
    ))

    # ⑥ 其余通道：已迁移的三个 (telegram/qq/xiaoyi) 不再有私有实现，
    #    dingtalk/wecom 补上了调用，yuanbao/mqtt 按设计不动
    unchanged = True
    for name in ("xiaoyi.channel", "telegram.channel", "qq.channel"):
        try:
            importlib.import_module("octop_gateway.channels." + name)
        except Exception as exc:  # noqa: BLE001
            print("!! import %s 失败: %r" % (name, exc))
            unchanged = False
    out.append(("⑥ xiaoyi/telegram/qq 仍可 import", unchanged))

    migrated = {
        "telegram/channel.py": ("is_duplicate", "_is_duplicate"),
        "qq/channel.py": ("is_duplicate", "_is_duplicate"),
        "xiaoyi/channel.py": ("is_duplicate", "_is_duplicate"),
    }
    ok = True
    for rel, (must_have, must_not) in migrated.items():
        src = open(os.path.join(SRC, "octop_gateway/channels", rel), encoding="utf-8").read()
        if "self." + must_have not in src or "def " + must_not in src:
            print("!! 迁移检查失败: %s" % rel)
            ok = False
    out.append(("⑥ telegram/qq/xiaoyi 已迁移（调用基类、删私有实现）", ok))

    added = {}
    for rel in ("dingtalk.py", "wecom.py"):
        src = open(os.path.join(SRC, "octop_gateway/channels", rel), encoding="utf-8").read()
        added[rel] = "self.is_duplicate(" in src
    out.append(("⑥ dingtalk/wecom 补上了去重调用（原本裸奔）", all(added.values())))

    yb = open(os.path.join(SRC, "octop_gateway/channels/yuanbao/channel.py"), encoding="utf-8").read()
    out.append(("⑥ yuanbao 按设计保留自己的实现（语义不同：monotonic + 5min TTL）",
                "_remember_message_id" in yb))

    width = max(len(n) for n, _ in out)
    failed = 0
    for name, ok in out:
        print(("PASS  " if ok else "FAIL  ") + name.ljust(width))
        failed += 0 if ok else 1
    print("\n%d/%d PASS" % (len(out) - failed, len(out)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
