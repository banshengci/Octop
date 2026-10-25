#!/usr/bin/env python3
"""统计「入站重复被拦下」的次数 —— 让重投从"看不见"变成"有数字"。

存在意义：2026-10-23 之前，这类问题只有用户偶然撞见才会被发现。
10-15 撤掉 v6 补丁后问题立刻复发（13 次），但没人知道；直到 10-23 用户抱怨。

用法：
    python3 dedup_stats.py              # 最近 7 天，按天 × 通道
    python3 dedup_stats.py --days 30
    python3 dedup_stats.py --json       # 机器可读，给周检用

判据：
    - 有命中 => 上游确实在重投，去重层在生效（正常，但值得知道是哪个通道）
    - 命中突然放量（比如单日 > 20）=> 该通道的 ACK 在丢，去看 WS 日志
    - 长期为 0 且该通道有流量 => 要么没重投，要么去重层没生效，需核实
"""
from __future__ import annotations

import collections
import datetime
import glob
import gzip
import json
import os
import re
import sys

LOG_DIR = "/data/.octop/logs"
# 镜像版（基类契约）的日志格式
HIT = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}.*Duplicate inbound ignored \(scope=([^ ]+) key=([^)]+)\)"
)
# 插件版（2026-10-23）的两种旧格式，同样计入，避免切换期数据断档
HIT_OLD_FEISHU = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}.*Feishu duplicate message ignored \(persistent store\): (\S+)"
)
HIT_OLD_PLUGIN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}.*DUPLICATE \(persistent\) id=(\S+)"
)


def open_any(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def collect(days: int) -> tuple[dict, dict]:
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    per_day: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    keys: dict[str, list[str]] = collections.defaultdict(list)
    patterns = ["octop*", "feishu-dedup.log*"]
    paths: list[str] = []
    for pat in patterns:
        paths.extend(glob.glob(os.path.join(LOG_DIR, pat)))
    for path in sorted(set(paths)):
        try:
            with open_any(path) as fh:
                for line in fh:
                    m = HIT.match(line)
                    if m:
                        day, scope, key = m.group(1), m.group(2), m.group(3)
                    else:
                        m2 = HIT_OLD_FEISHU.match(line)
                        m3 = HIT_OLD_PLUGIN.match(line)
                        if m2:
                            day, scope, key = m2.group(1), "feishu", m2.group(2)
                        elif m3:
                            day, scope, key = m3.group(1), "feishu", m3.group(2)
                        else:
                            continue
                    try:
                        if datetime.date.fromisoformat(day) < cutoff:
                            continue
                    except ValueError:
                        continue
                    per_day[day][scope] += 1
                    keys[day].append(scope + ":" + key)
        except Exception:
            continue
    return per_day, keys


def main() -> int:
    as_json = "--json" in sys.argv
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    per_day, keys = collect(days)
    total = sum(sum(c.values()) for c in per_day.values())
    by_scope: collections.Counter = collections.Counter()
    for c in per_day.values():
        by_scope.update(c)

    if as_json:
        print(json.dumps({
            "window_days": days,
            "total_hits": total,
            "by_scope": dict(by_scope),
            "by_day": {d: dict(c) for d, c in sorted(per_day.items())},
            "alert": total > 50,
        }, ensure_ascii=False, indent=1))
        return 0

    print("=== 最近 %d 天：入站重复被拦下 %d 次 ===" % (days, total))
    if not per_day:
        print("（无命中）")
    else:
        scopes = sorted(by_scope)
        print("%-12s %s" % ("日期", "  ".join("%-10s" % s for s in scopes)))
        for d in sorted(per_day):
            row = per_day[d]
            print("%-12s %s" % (d, "  ".join("%-10d" % row.get(s, 0) for s in scopes)))
        print("\n按通道合计：")
        for s, n in by_scope.most_common():
            print("  %-12s %d" % (s, n))

    if total > 50:
        print("\n⚠️ 窗口内命中 > 50，说明上游在大量重投，建议看对应通道的 WS 拆连日志")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
