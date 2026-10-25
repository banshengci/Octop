# 薄镜像：替换 octop_gateway 的 8 个文件，其余完全沿用官方基础镜像
#
# 密钥设计：**任何不确定都让构建失败**
#   - 每个目标文件先校验「基础哈希」，不符 => 说明镜像版本变了或上游改了代码
#   - 复制后再校验「新哈希」，不符 => 说明复制出错
#   - 最后跑冒烟：新行为在、其余通道还能 import
# 2026-10-15 的无限重启事故就是旧版本文件配上新版本镜像，且静默通过。
#
# 已验证的基础镜像（gateway 均为 v1.0.3，8 文件 base 哈希逐字节相同）：
#   - ghcr.io/tencentcloud/octop:1.0.2
#   - ghcr.io/tencentcloud/octop:1.0.3b1
# 见 tools/verify_103b1.py
#
# 构建（在 fork 目录下）：
#   sh build.sh                       # 基础 1.0.2，输出 octop-dedup:1.0.2
#   sh build.sh 1.0.3b1 1.0.3b1       # 基础 1.0.3b1，输出 octop-dedup:1.0.3b1

ARG OCTOP_BASE=ghcr.io/tencentcloud/octop:1.0.2
FROM ${OCTOP_BASE}

ARG BASE_DIR=/app/.venv/lib/python3.12/site-packages/octop_gateway

COPY files/ /tmp/in/
COPY apply.py /tmp/apply.py
COPY manifest.json /tmp/manifest.json

RUN /app/.venv/bin/python /tmp/apply.py \
        --base-dir "$BASE_DIR" \
        --src /tmp/in/octop_gateway \
        --manifest /tmp/manifest.json \
 && rm -rf /tmp/in /tmp/apply.py /tmp/manifest.json
