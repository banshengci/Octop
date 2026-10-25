# octop-fork · 入站幂等（基类契约）

自己维护的 Octop 定制层。替换 `octop_gateway` 的 **8 个文件**，其余全部沿用官方镜像。

## 问题

IM 平台都是 at-least-once 投递。飞书对未确认的推送按 **20s / 5min / 1h / 6h** 阶梯重投
（累计 ≈7h05m15s），重投携带**同一个 message_id**。而 octop 的去重是**进程内存**的，
窗口内一重启就忘，整轮重跑一遍 —— 包括有副作用的工具调用。

2026-10-23 实测三条（id 完全相同）：

| message_id | 首投 | 重投 | 间隔 |
|---|---|---|---|
| `om_x100b629b98d20888b0b67ed2d3399e5` | 14:23:47 | 21:29:32 | 7h05m45s |
| `om_x100b629ba550c8a4cebb208ae745437` | 14:29:29 | 21:35:33 | 7h06m04s |
| `om_x100b629bb450e080cebfdc55ad6e07f` | 14:33:34 | 21:39:47 | 7h06m13s |

18 天日志全扫：577 个不同 id，**53 个被投过两次以上**（9.2%）。最早一次是 10-10 22:30 首投、
10-11 05:36 重投。

## 病灶

| 通道 | 原实现 | 现在 |
|---|---|---|
| `feishu.py` | 内存 OrderedDict 2000 | **基类** |
| `discord.py` | 内存 OrderedDict 1000 | **基类** |
| `telegram/channel.py` | 两份独立 FIFO | **基类** |
| `qq/channel.py` | 内存 OrderedDict 1000 | **基类** |
| `xiaoyi/channel.py` | 内存 OrderedDict 1000 | **基类** |
| `dingtalk.py` | **没有** | **基类**（`msg_id`） |
| `wecom.py` | **没有** | **基类**（`msgid`） |
| `yuanbao/channel.py` | monotonic + 5min TTL | 未改（见下） |
| `mqtt.py` | **没有** | 未改（无 message id） |

## 基类契约（`channel.py`）

```python
BaseChannel.is_duplicate(key, *, scope=None) -> bool   # 查 + 记账
BaseChannel.has_seen(key, *, scope=None) -> bool       # 只查不记账
```

| 决策 | 内容 | 理由 |
|---|---|---|
| 存储 | SQLite `~/.octop/inbound-dedup.sqlite`，WAL，表 `inbound_seen(scope, key, seen_at)` | 跨进程 |
| TTL | 8h（`OCTOP_INBOUND_DEDUP_TTL`） | 必须 > 7h05m 重投窗口 |
| **scope** | 默认 `channel_type`；**禁止用 `channel_id`** | **`channel_id` 是每次实例化生成的 UUID，用它等于每次重启换一把锁，持久化失效** |
| 快路径 | 每实例 OrderedDict(2000) | 不必每条消息读盘 |
| 失败姿态 | **fail-open**（两个方法一致） | 宁可重复，不可吞消息 |
| 清理 | 每 10 分钟最多一次 | 避免无限增长 |
| `has_seen` | 只读，telegram 用来判断「编辑消息对应的原消息是否处理过」 | 不能用 `is_duplicate` 代替：那会把未处理的原消息提前记账 |

## 两处不强制统一（有意为之）

- **`yuanbao`**：用 `time.monotonic()` + **5 分钟** TTL，且返回布尔语义相反（True = 新消息）。
  强行套 8 小时窗口会改变行为、可能误拦。需要单独评估。
- **`mqtt`**：MQTT payload 没有稳定 message id，用内容哈希会把「两条内容相同的不同消息」误判成重复。

## 另一个改动：WS 空闲拆连阈值

`feishu.py`：`_WS_ACTIVITY_STALE_SECONDS` **3600 → 21600**。

原判据是「1 小时没收到消息」，但**空闲不是故障**——2026-10-23 一个健康连接被白拆 39 次，
每次拆连都是 in-flight 事件丢 ACK 的窗口，反过来喂大上游重投队列。真正的死连接由
lark-oapi 的 ping loop + `websockets` ping_timeout 发现；`thread_dead` 与
`reconnect_stale=180s` 判据原样保留。

## 版本对应（构建时双向校验）

`manifest.json` 记录 8 个文件的「基础哈希 / 新哈希」，由 `make_manifest.py` 在容器内生成
（以容器里的 `octop_gateway` 为基准）。`apply.py` 在镜像里执行：

1. 逐个校验基础哈希，**任何不符直接失败**（镜像版本变了或上游改了代码）
2. 复制后逐个校验新哈希
3. 冒烟：新方法在、其余 6 个通道仍能 import

2026-10-15 的无限重启就是「旧版本文件配上新版本镜像」且静默通过——这套校验就是针对它。

## 构建与部署

```sh
# ① 容器内生成 manifest（需要容器里的 octop_gateway 作基准）
/app/.venv/bin/python3 make_manifest.py

# ② NAS 宿主构建（几秒）
sh build.sh

# ③ 切换（重建容器，正在跑的回合会中断）
cp $OCTOP_DIR/docker-compose.yaml{,.bak-$(date +%Y%m%d-%H%M%S)}
# image: ghcr.io/tencentcloud/octop:1.0.2  →  image: octop-dedup:1.0.2
cd $OCTOP_DIR && docker compose up -d
```

起来后：

1. **禁用等价插件**（避免两套 patch 叠加）：`config.json` 里 `plugins["feishu-dedup"].enabled = false`，
   然后 `POST /api/plugins/reload`
2. **回填历史 id**（新表是空的）：`python3 tools/backfill_inbound_seen.py --from-plugin --apply`

## 验证

```sh
/app/.venv/bin/python3 tests/verify_base_dedup.py --src <fork>/src           # 8/8
/app/.venv/bin/python3 tests/verify_channel_integration.py --src <fork>/src  # 14/14
/app/.venv/bin/python3 apply.py --base-dir <一份 octop_gateway 副本> ...      # 模拟构建
```

覆盖：首次不判重 / 二次判重 / **重启后仍判重**（feishu、discord 真实类）/ 不同 key 不误判 /
TTL 过期放行 / scope 隔离 / fail-open / 空 key / 子类未覆盖基类 /
6 个已迁移通道的静态检查 / **8 文件哈希双向校验 + 12 项冒烟**。

## 可观测：让重投有数字

```sh
python3 tools/dedup_stats.py            # 最近 7 天，按天 × 通道
python3 tools/dedup_stats.py --json     # 给周检用
```

命中 > 50 会告警。这一项已接进每周环境盘点——**下次撤任何保护措施时，能当天看到它在涨，
而不是等 7 天后被用户撞见**（10-15 撤补丁后复发 13 次，就是没人看到）。

## 回滚

compose 的 image 改回 `ghcr.io/tencentcloud/octop:1.0.2` 后 `docker compose up -d`。
数据卷不受影响。

## 1.0.3b1 兼容性（2026-10-25 实测，结论：零改动可升）

上游发了 `octop 1.0.3b1`（GHCR `1.0.3b1` 含 arm64）。实测链条：

| 检查 | 结果 |
|---|---|
| `octop 1.0.3b1` 的依赖约束 | `octop-gateway>=1.0.3` |
| PyPI `octop-gateway` 最新版本 | **1.0.3**（没有 1.0.4） |
| 上游 `octop-gateway` v1.0.3 tag 的 8 个文件 sha256 | **与 `manifest.json` 的 `base` 逐字节相同** |
| 还原成 base 后跑生产同款 `apply.py` | 8/8 base 通过 → 8/8 替换 → 12 项冒烟 **全 PASS** |

⇒ 1.0.3b1 镜像里的 `octop_gateway` 与 1.0.2 镜像里是**同一份**，**manifest 无需重算**，
只需把 `Dockerfile` 的 `FROM` 改成 `1.0.3b1`。

可复跑验证：

```sh
# 先取上游基准（8 个文件，任选一种）
git -c http.proxy=$http_proxy clone --depth 1 -b v1.0.3 \
    https://github.com/tencentcloud/octop-gateway /tmp/og103
mkdir -p /tmp/up103 && cp -a /tmp/og103/src/octop_gateway/* /tmp/up103/
# 跑等价复现
/app/.venv/bin/python3 tools/verify_103b1.py --base-src /tmp/up103
```

**会变但不影响定制层**：`octop-harness` 1.0.4 → **1.0.5**（我们没定制 harness）；
`octop-memory` 1.0.2、`octop-browser` 1.0.0 不变。

## 上游更新时怎么跟

1. 改 `Dockerfile` 的 `FROM` tag
2. 把新镜像里的 8 个文件与 fork 版本 diff，重新应用改动
   （基类 +132 行；feishu/discord 是「删私有实现、改调用」；telegram/qq/xiaoyi 同；
   dingtalk/wecom 是「各加 5 行」）
3. 在容器内跑 `make_manifest.py` 重算哈希
4. `sh build.sh <新tag>`

漏了第 3 步，构建会直接失败。
