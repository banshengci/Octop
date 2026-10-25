#!/usr/bin/env python3
"""盘点并同步 banshengci 的 5 个 octop fork 与上游。

用法:
  python3 tools/sync_forks.py            # 只盘点（dry-run，默认）
  python3 tools/sync_forks.py --apply    # 执行 merge-upstream

凭据: 从 /tmp/.ghtok 或环境变量 GITHUB_TOKEN 读取，**不打印**。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OWNER = "banshengci"
UPSTREAM_OWNER = "TencentCloud"
REPOS = ["Octop", "octop-gateway", "octop-harness", "octop-memory", "octop-browser"]
# 有我们自己的提交、绝不能 merge-upstream 的分支
PROTECTED_BRANCHES = {"fix/feishu-dedup-persistent-store"}


def token() -> str:
    p = "/tmp/.ghtok"
    if os.path.isfile(p):
        return open(p).read().strip()
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    if not t:
        raise SystemExit("缺少凭据：/tmp/.ghtok 或 GITHUB_TOKEN")
    return t


TOK = token()


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main() -> int:
    apply = "--apply" in sys.argv
    print(f"{'repo':<16} {'fork main':<10} {'upstream main':<14} {'behind':>6} {'ahead':>6}  状态")
    print("-" * 78)
    plan = []
    for repo in REPOS:
        st, fork = call("GET", f"/repos/{OWNER}/{repo}")
        if st != 200:
            print(f"{repo:<16} !! fork 读取失败 http={st} {fork.get('message','')}")
            continue
        st, up = call("GET", f"/repos/{UPSTREAM_OWNER}/{repo}")
        up_main = (up.get("default_branch") or "main") if st == 200 else "main"
        fork_branch = fork.get("default_branch") or "main"
        # base = 上游, head = fork：
        #   behind_by = fork 落后上游的提交数；ahead_by = fork 独有的提交数
        st, cmp = call(
            "GET",
            f"/repos/{OWNER}/{repo}/compare/{UPSTREAM_OWNER}:{up_main}...{fork_branch}",
        )
        behind = cmp.get("behind_by", "?")
        ahead = cmp.get("ahead_by", "?")
        fork_sha = (fork.get("pushed_at") or "")[:10]
        up_sha = ((up.get("pushed_at") or "")[:10]) if st == 200 else "?"
        state = "已同步" if behind == 0 and ahead == 0 else ("落后" if behind else "领先/分叉")
        print(f"{repo:<16} {str(fork_sha):<10} {str(up_sha):<14} {str(behind):>6} {str(ahead):>6}  {state}")
        if behind and behind != 0:
            plan.append((repo, fork_branch, behind))

    print()
    if not plan:
        print("无需同步。")
        return 0
    print("待同步:", ", ".join(f"{r}(落后{b})" for r, _, b in plan))
    if not apply:
        print("\n[dry-run] 加 --apply 执行 merge-upstream")
        return 0
    print()
    for repo, branch, _ in plan:
        if branch in PROTECTED_BRANCHES:
            print(f"SKIP {repo}:{branch} 受保护")
            continue
        st, res = call("POST", f"/repos/{OWNER}/{repo}/merge-upstream", {"branch": branch})
        msg = res.get("message", "")
        print(f"{repo}:{branch} -> http={st} {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
