#!/usr/bin/env python3
"""把历史入站 id 搬进新的持久去重表（inbound_seen）。

背景：镜像版的表是 `inbound_seen(scope, key, seen_at)`，而 2026-10-23 的插件版
用的是 `seen(message_id, seen_at)`。切换后新表是空的，如果上游在此刻重投一条
**切换前已经处理过**的消息，新表认不出来 -> 又会重复执行一次。

两种来源都支持：
  --from-log    从日志里提取所有 "Feishu received message: id=..."（默认）
  --from-plugin 从旧插件的 seen 表迁移（表的列名可能不同，自动识别）

用法：
    python3 backfill_inbound_seen.py                 # dry-run
    python3 backfill_inbound_seen.py --apply
    python3 backfill_inbound_seen.py --from-plugin --apply
"""
from __future__ import annotations

import glob
import gzip
import os
import re
import sqlite3
import sys
import time

DEFAULT_DB = os.environ.get(
    "OCTOP_INBOUND_DEDUP_DB", os.path.expanduser("~/.octop/inbound-dedup.sqlite")
)
LOG_DIR = "/data/.octop/logs"
RECV = re.compile(r"Feishu received message: id=(om_[A-Za-z0-9]+)")
DEFAULT_SCOPE = "feishu"


def open_any(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def ids_from_log() -> set[str]:
    seen: set[str] = set()
    for path in sorted(glob.glob(os.path.join(LOG_DIR, "octop*"))):
        try:
            with open_any(path) as fh:
                for line in fh:
                    m = RECV.search(line)
                    if m:
                        seen.add(m.group(1))
        except Exception:
            continue
    return seen


def ids_from_plugin(db: str) -> set[str]:
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(seen)")]
        if not cols:
            conn.close()
            return set()
        key_col = "message_id" if "message_id" in cols else cols[0]
        rows = {r[0] for r in conn.execute("SELECT %s FROM seen" % key_col)}
        conn.close()
        return rows
    except Exception as exc:  # noqa: BLE001
        print("!! 读旧表失败: %r" % (exc,))
        return set()


def main() -> int:
    apply = "--apply" in sys.argv
    from_plugin = "--from-plugin" in sys.argv
    db = DEFAULT_DB
    if "--db" in sys.argv:
        db = sys.argv[sys.argv.index("--db") + 1]

    ids = ids_from_plugin(db) if from_plugin else ids_from_log()
    print("来源: %s" % ("旧插件 seen 表" if from_plugin else "日志"))
    print("发现 id: %d 个" % len(ids))
    if not ids:
        print("没有可回填的 id")
        return 1

    conn = sqlite3.connect(db, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inbound_seen ("
        "scope TEXT NOT NULL, key TEXT NOT NULL, seen_at REAL NOT NULL, "
        "PRIMARY KEY (scope, key))"
    )
    existing = {
        r[0] for r in conn.execute("SELECT key FROM inbound_seen WHERE scope = ?", (DEFAULT_SCOPE,))
    }
    todo = ids - existing
    print("表内已有: %d 个 | 待写入: %d 个" % (len(existing), len(todo)))

    if not apply:
        print("\n[dry-run] 加 --apply 才真正写入")
        conn.close()
        return 0

    now = time.time()
    conn.executemany(
        "INSERT OR IGNORE INTO inbound_seen(scope, key, seen_at) VALUES(?, ?, ?)",
        [(DEFAULT_SCOPE, k, now) for k in todo],
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM inbound_seen").fetchone()[0]
    conn.close()
    print(
        "\n写入 %d 条；表内现有 %d 条（TTL 8h，覆盖到 %s）"
        % (len(todo), total, time.strftime("%m-%d %H:%M", time.localtime(now + 8 * 3600)))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
