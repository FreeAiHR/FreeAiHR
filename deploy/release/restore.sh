#!/usr/bin/env bash
# Free-Hire 还原脚本(部署环境终端执行)。
#
# 用法:
#   ./restore.sh free-hire-backup-2026-05-04-120000.tar.gz
#
# 流程:
# 1. 校验 tarball 完整性 + manifest 存在
# 2. 提示用户停服务(避免热写覆盖)
# 3. 还原 pg_dump.sql.gz 到 postgres
# 4. 还原 objects.tar.gz 到对象卷
# 5. 提示用户启动服务
#
# .env 不还原(env.sanitized 是脱敏副本不可直接用),保留现有 .env 启动即可。

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "用法: $0 <free-hire-backup-...tar.gz>"
  exit 1
fi

TARBALL="$1"
if [ ! -f "${TARBALL}" ]; then
  echo "✗ tarball 不存在: ${TARBALL}"
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"

WORK="$(mktemp -d -t freehire-restore.XXXXXX)"
trap 'rm -rf "${WORK}"' EXIT

echo "Free-Hire 还原: ${TARBALL}"
echo

# ---- 1. 解压 + 校验 ----
echo "[1/5] 解压并校验"
tar xzf "${TARBALL}" -C "${WORK}"
if [ ! -f "${WORK}/manifest.json" ]; then
  echo "✗ tarball 内缺 manifest.json,可能不是 backup.sh 产物"
  exit 1
fi
echo "  ✓ manifest:"
cat "${WORK}/manifest.json" | sed 's/^/    /'

# ---- 2. 二次确认 ----
echo
read -r -p "继续还原会**覆盖**当前 PG 数据库与对象卷,确定继续?(yes/no) " ans
if [ "${ans}" != "yes" ]; then
  echo "用户取消"
  exit 0
fi

# ---- 3. 检查必要容器 ----
PG_CONTAINER="${FREEHIRE_PG_CONTAINER:-free-hire-postgres-1}"
if ! docker inspect "${PG_CONTAINER}" >/dev/null 2>&1; then
  echo "✗ 未找到 ${PG_CONTAINER},确保 docker compose up -d postgres redis 已起"
  exit 1
fi
PG_USER="${POSTGRES_USER:-freehire}"
PG_DB="${POSTGRES_DB:-freehire}"

# ---- 4. 停 backend / worker / frontend(避免热写)----
echo
echo "[2/5] 停 backend / worker / frontend(保留 postgres / redis 在线)"
for s in backend worker frontend; do
  cname="free-hire-${s}-1"
  if docker inspect "${cname}" >/dev/null 2>&1; then
    docker stop "${cname}" >/dev/null && echo "  - 停了 ${cname}" || true
  fi
done

# ---- 5. 还原 PG ----
echo
echo "[3/5] 还原 pg_dump"
# 重建数据库:DROP + CREATE,避免新旧数据混合
docker exec -i "${PG_CONTAINER}" psql -U "${PG_USER}" -d postgres <<SQL
-- 断开所有连接到目标 db
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${PG_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${PG_DB}";
CREATE DATABASE "${PG_DB}" OWNER "${PG_USER}";
SQL

gunzip -c "${WORK}/pg-dump.sql.gz" \
  | docker exec -i "${PG_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" >/dev/null
echo "  ✓ PG 还原完成"

# ---- 6. 还原对象卷 ----
echo
echo "[4/5] 还原对象卷"
VOL_NAME="${FREEHIRE_OBJECT_VOLUME:-free-hire_object-data}"
if [ -s "${WORK}/objects.tar.gz" ]; then
  # 清空旧卷再恢复 — 用临时容器
  docker run --rm \
    -v "${VOL_NAME}:/dst" \
    -v "${WORK}:/src:ro" \
    alpine:3 \
    sh -c "rm -rf /dst/* /dst/.[!.]* 2>/dev/null; cd /dst && tar xzf /src/objects.tar.gz"
  echo "  ✓ ${VOL_NAME} 已还原"
else
  echo "  · 跳过(备份里 objects.tar.gz 为空)"
fi

# ---- 7. 拉起服务 ----
echo
echo "[5/5] 启动 backend / worker / frontend"
( cd "${HERE}" && docker compose up -d ) >/dev/null

# 等 healthz
echo "  等 backend healthz(最多 60s)..."
for _ in $(seq 1 12); do
  if curl -sf "http://localhost:${FREEHIRE_HTTP_PORT:-80}/api/healthz" >/dev/null 2>&1; then
    echo "  ✓ backend ready"
    break
  fi
  sleep 5
done

echo
echo "✓ 还原完成"
echo "  访问:  http://localhost:${FREEHIRE_HTTP_PORT:-80}"
echo "  排错:  docker compose logs --since 5m backend"
