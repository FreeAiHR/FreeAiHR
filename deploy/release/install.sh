#!/usr/bin/env bash
# Free-Hire 离线安装脚本(目标环境终端执行)。
#
# 用法:
#   tar xzf free-hire-{VERSION}.tar.gz
#   cd free-hire-{VERSION}
#   ./install.sh
#
# 第一次跑会停在"请编辑 .env"那一步, 改完 .env 后再跑一次完成安装。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"

if [ ! -f VERSION ]; then
  echo "✗ 未找到 VERSION 文件,确认你在解压后的 free-hire-{VERSION}/ 目录里"
  exit 1
fi
VERSION="$(cat VERSION)"

echo "Free-Hire 离线安装 v${VERSION}"
echo

# ---- 0. 前置检查 ----
if ! command -v docker >/dev/null; then
  echo "✗ 未检测到 docker,请先安装 Docker Engine ≥ 24"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "✗ docker compose plugin 未安装(需要 Compose V2)"
  exit 1
fi

# ---- 1. checksum 校验 ----
echo "[1/5] 校验 tarball 完整性"
if command -v sha256sum >/dev/null; then
  sha256sum -c SHA256SUMS
elif command -v shasum >/dev/null; then
  # macOS fallback
  while IFS= read -r line; do
    expected="${line%% *}"
    file="${line##* }"
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    if [ "$expected" != "$actual" ]; then
      echo "✗ checksum 不匹配: $file"
      exit 1
    fi
  done < SHA256SUMS
  echo "  ✓ 全部校验通过"
fi

# ---- 2. docker load 镜像 ----
echo
echo "[2/5] 加载 Docker 镜像"
for t in \
  "postgres-16-alpine.tar.gz" \
  "redis-7-alpine.tar.gz" \
  "free-hire-backend.tar.gz" \
  "free-hire-frontend.tar.gz"; do
  if [ -f "$t" ]; then
    echo "  - 加载 $t"
    docker load -i "$t" >/dev/null
  fi
done
echo "  ✓ 已加载所有镜像"

# ---- 3. 准备 .env ----
echo
echo "[3/5] 检查 .env"
if [ ! -f ".env" ]; then
  cp .env.example .env
  # 把 FREEHIRE_VERSION 写进去, 让 docker-compose.yml 的 ${FREEHIRE_VERSION} 生效
  if ! grep -q "^FREEHIRE_VERSION=" .env; then
    echo "" >> .env
    echo "# 由 install.sh 注入,对应 docker-compose.yml 镜像 tag" >> .env
    echo "FREEHIRE_VERSION=${VERSION}" >> .env
  fi
  echo "  ✓ 已生成 .env (${HERE}/.env)"
  echo
  echo "下一步: 编辑 .env 至少修改以下三项, 然后再次运行 ./install.sh:"
  echo "  - JWT_SECRET           openssl rand -hex 32 生成 32+ 字符随机串"
  echo "  - BOOTSTRAP_ADMIN_*    首次启动管理员账号 (建议改为强密码)"
  echo "  - MACHINE_FINGERPRINT_OVERRIDE   openssl rand -hex 16 (固定容器指纹)"
  exit 0
fi

# 已有 .env, 把版本号 sync 进去(防止用户保留了老版本的 .env)
if grep -q "^FREEHIRE_VERSION=" .env; then
  # 替换已有行
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/^FREEHIRE_VERSION=.*/FREEHIRE_VERSION=${VERSION}/" .env
  else
    sed -i "s/^FREEHIRE_VERSION=.*/FREEHIRE_VERSION=${VERSION}/" .env
  fi
else
  echo "FREEHIRE_VERSION=${VERSION}" >> .env
fi
echo "  ✓ .env 已存在, 已同步 FREEHIRE_VERSION=${VERSION}"

# ---- 4. 启动 ----
echo
echo "[4/5] 启动服务"
docker compose --env-file .env up -d

# ---- 5. healthcheck ----
echo
echo "[5/5] 等待 backend healthy (最多 120s)"
for _ in $(seq 1 24); do
  if curl -sf "http://localhost:${FREEHIRE_HTTP_PORT:-80}/api/healthz" >/dev/null 2>&1; then
    echo "  ✓ Free-Hire 已启动"
    echo
    echo "访问: http://localhost:${FREEHIRE_HTTP_PORT:-80}"
    echo "API 文档: http://localhost:${FREEHIRE_HTTP_PORT:-80}/api/docs"
    echo "查看日志: docker compose logs -f backend"
    echo "停止: docker compose down"
    echo "完全清理 (含数据): docker compose down -v"
    exit 0
  fi
  sleep 5
done

echo "  ✗ backend 未在 120 秒内 ready"
echo "  排查:"
echo "    docker compose ps"
echo "    docker compose logs backend"
exit 1
