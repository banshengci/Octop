#!/bin/sh
# 在 NAS 宿主上构建薄镜像（容器内没有 docker CLI，必须走宿主）
#
# 用法：sh build.sh [输出tag] [基础镜像tag]
#   sh build.sh                     → 基础 1.0.2，输出 octop-dedup:1.0.2
#   sh build.sh 1.0.2-r2            → 基础 1.0.2，输出 octop-dedup:1.0.2-r2
#   sh build.sh 1.0.3b1 1.0.3b1     → 基础 1.0.3b1，输出 octop-dedup:1.0.3b1
#
# 注意：manifest.json 里的 base 哈希必须与基础镜像里的 octop_gateway 一致。
#   1.0.2 与 1.0.3b1 的 gateway 都是 v1.0.3，manifest 通用（tools/verify_103b1.py 已验）。
#   换到其它 tag 时先在容器内跑 make_manifest.py 重算，否则构建会 fail-fast（这是好事）。

set -eu

OUT_TAG="${1:-1.0.2}"
BASE_TAG="${2:-1.0.2}"
IMAGE="octop-dedup:${OUT_TAG}"
BASE_IMAGE="ghcr.io/tencentcloud/octop:${BASE_TAG}"
HERE="$(cd "$(dirname "$0")" && pwd)"

cd "$HERE"

echo "=== 构建上下文 ==="
ls -la
echo "文件清单："
find files -name '*.py' | sort

if [ ! -f manifest.json ]; then
    echo
    echo "!! 缺 manifest.json"
    echo "   请先在容器内跑：/app/.venv/bin/python3 make_manifest.py"
    exit 1
fi

echo
echo "=== 基础镜像 $BASE_IMAGE ==="
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo "本地没有基础镜像，拉取中（约 400MB+，走代理）..."
    docker pull "$BASE_IMAGE"
fi

echo
echo "=== 构建 $IMAGE (from $BASE_IMAGE) ==="
docker build --network host --build-arg OCTOP_BASE="$BASE_IMAGE" -f Dockerfile -t "$IMAGE" .

echo
echo "=== 结果 ==="
docker image inspect "$IMAGE" --format '{{.Id}}  {{.Size}} bytes'
docker run --rm --entrypoint /app/.venv/bin/python "$IMAGE" -c "\
import octop_gateway.channel as c; \
import octop_gateway.channels.feishu as f; \
print('is_duplicate:', hasattr(c.BaseChannel, 'is_duplicate')); \
print('has_seen   :', hasattr(c.BaseChannel, 'has_seen')); \
print('ttl        :', c._DEDUP_TTL_SECONDS); \
print('ws stale   :', f._WS_ACTIVITY_STALE_SECONDS)"

echo
echo "下一步（会重建容器，正在跑的回合会中断）："
echo "  1) cp $OCTOP_DIR/docker-compose.yaml{,.bak-\$(date +%Y%m%d-%H%M%S)}"
echo "  2) image: <原镜像>  →  image: $IMAGE"
echo "  3) cd $OCTOP_DIR && docker compose up -d"
echo "  4) 起来后禁用等价插件：config.json 里 feishu-dedup.enabled=false + POST /api/plugins/reload"
echo "  5) 回填历史 id 到新表（如需）：tools/backfill_inbound_seen.py --apply"
