#!/usr/bin/env python3
"""BaseChannel.is_duplicate 的单元测试 —— 用最小子类实例化真实基类。

先定答案再验（不是让脚本自证）：
  预期 ① 首次不认识 → False
  预期 ② 同实例再来 → True
  预期 ③ 新实例（模拟进程重启）→ True   ← 这是整个改动的核心
  预期 ④ 不同 key → False
  预期 ⑤ 超过 TTL → False
  预期 ⑥ 不同 scope 互不影响 → False
  预期 ⑦ 存储不可用 → fail-open 返回 False，且不抛异常

跑法：/app/.venv/bin/python3 verify_base_dedup.py [--src <path>]
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import time
import types

DEFAULT_SRC = "/tmp/gw2-095643/src"
src = DEFAULT_SRC
if "--src" in sys.argv:
    src = sys.argv[sys.argv.index("--src") + 1]

TMP = tempfile.mkdtemp(prefix="base-dedup-verify-")
DB = os.path.join(TMP, "dedup.sqlite")

# 让被导入的 octop_gateway 指向 fork 源码
sys.path.insert(0, src)

import octop_gateway.channel as chan_mod  # noqa: E402


class _Minimal(chan_mod.BaseChannel):
    """最小实现，只为能实例化 ABC。"""

    channel_type = "testtype"

    async def start(self) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    async def _send_text(self, subject, text):  # pragma: no cover
        return None

    async def _send_content(self, subject, parts):  # pragma: no cover
        return None

    async def _send_media(self, subject, media):  # pragma: no cover
        return None

    def parse_inbound(self, raw_payload):  # pragma: no cover
        raise NotImplementedError


class _Other(_Minimal):
    channel_type = "othertype"


def fresh(store_path: str = DB) -> None:
    """重置模块级状态，模拟一次全新的进程启动。"""
    chan_mod._DEDUP_DB_PATH = store_path
    if chan_mod._dedup_conn is not None:
        try:
            chan_mod._dedup_conn.close()
        except Exception:
            pass
    chan_mod._dedup_conn = None
    chan_mod._dedup_last_sweep = 0.0


def main() -> int:
    fresh()
    out: list[tuple[str, bool]] = []
    key = "om_test_0001"

    ch = _Minimal(processor=None)
    out.append(("① 首次不认识 => False", ch.is_duplicate(key) is False))
    out.append(("② 同实例再来 => True", ch.is_duplicate(key) is True))

    # 模拟进程重启：新实例 + 清空模块级连接
    fresh()
    ch2 = _Minimal(processor=None)
    out.append(("③ **重启后同 key => True（核心）**", ch2.is_duplicate(key) is True))

    out.append(("④ 不同 key => False", ch2.is_duplicate("om_test_0002") is False))

    # TTL：把记录改老
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE inbound_seen SET seen_at = ? WHERE key = ?", (time.time() - 9 * 3600, key))
    conn.commit()
    conn.close()
    fresh()
    ch3 = _Minimal(processor=None)
    out.append(("⑤ 超过 TTL(8h) 后放过 => False", ch3.is_duplicate(key) is False))

    # scope 隔离
    fresh()
    a = _Minimal(processor=None)
    b = _Other(processor=None)
    a.is_duplicate("same_key")
    out.append(("⑥ 不同 scope 互不影响 => False", b.is_duplicate("same_key") is False))

    # fail-open：指向一个不可写的路径
    fresh("/proc/nonexistent-dir/dedup.sqlite")
    ch4 = _Minimal(processor=None)
    try:
        res = ch4.is_duplicate("om_any")
        ok = res is False
    except Exception:
        ok = False
    out.append(("⑦ 存储不可用 => fail-open 返回 False 且不抛异常", ok))

    # 空 key 不参与判重
    fresh()
    ch5 = _Minimal(processor=None)
    out.append(("⑧ 空 key 永远 False", ch5.is_duplicate("") is False))

    width = max(len(n) for n, _ in out)
    failed = 0
    for name, ok in out:
        print(("PASS  " if ok else "FAIL  ") + name.ljust(width))
        failed += 0 if ok else 1
    print("\n%d/%d PASS" % (len(out) - failed, len(out)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
