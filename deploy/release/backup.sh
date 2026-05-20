#!/usr/bin/env bash
# Free-Hire 备份脚本(部署环境终端执行)。
#
# 产物: free-hire-backup-{YYYY-MM-DD-HHMMSS}.tar.gz, 内含
#   manifest.json     版本号 / 备份时间 / 含哪些组件
#   pg-dump.sql.gz    pg_dump (custom format 不行,要纯 SQL 方便跨版本)
#   objects.tar.gz    简历原文件 + 解析产物对象卷
#   env.sanitized     .env 去除密钥的脱敏副本(参考用,不可直接用)
#
# 用法:
#   ./backup.sh                       # 输出到 ./backups/
#   ./backup.sh /mnt/nas/freehire     # 指定目录
#   FREEHIRE_KEEP_LAST=7 ./backup.sh  # 自动清理只保留最近 7 份
#
# 假设:
# - docker compose project 名称为 "free-hire"(compose 里 name: free-hire)
# - 容器名:free-hire-postgres-1 / free-hire-backend-1(默认 docker compose v2)
# - .env 在脚本同目录(install.sh 生成的位置)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${HERE}"

# ---- 0. 参数 ----
DEST="${1:-./backups}"
mkdir -p "${DEST}"
DEST="$(cd "${DEST}" && pwd)"

TS="$(date +%Y-%m-%d-%H%M%S)"
NAME="free-hire-backup-${TS}"
WORK="$(mktemp -d -t freehire-backup.XXXXXX)"
trap 'rm -rf "${WORK}"' EXIT

OUT="${DEST}/${NAME}.tar.gz"

echo "Free-Hire 备份"
echo "  时间:  ${TS}"
echo "  目标:  ${OUT}"
echo

# ---- 1. 前置检查 ----
if ! command -v docker >/dev/null; then
  echo "✗ 未检测到 docker"
  exit 1
fi

PG_CONTAINER="${FREEHIRE_PG_CONTAINER:-free-hire-postgres-1}"
if ! docker inspect "${PG_CONTAINER}" >/dev/null 2>&1; then
  echo "✗ 未找到 ${PG_CONTAINER} 容器,先启动 docker compose"
  exit 1
fi

# ---- 2. PostgreSQL dump ----
echo "[1/4] pg_dump"
PG_USER="${POSTGRES_USER:-freehire}"
PG_DB="${POSTGRES_DB:-freehire}"
docker exec "${PG_CONTAINER}" \
  pg_dump -U "${PG_USER}" -d "${PG_DB}" --no-owner --no-acl \
  | gzip > "${WORK}/pg-dump.sql.gz"
DUMP_SIZE=$(du -h "${WORK}/pg-dump.sql.gz" | awk '{print $1}')
echo "  ✓ ${DUMP_SIZE}"

# ---- 3. 对象存储卷 tar ----
# named volume free-hire_object-data 内是简历原文件 + 面试报告等 ObjectStore 内容
echo "[2/4] tar object-data 命名卷"
VOL_NAME="${FREEHIRE_OBJECT_VOLUME:-free-hire_object-data}"
if docker volume inspect "${VOL_NAME}" >/dev/null 2>&1; then
  # 用一次性容器把 volume 挂进来 tar
  docker run --rm \
    -v "${VOL_NAME}:/src:ro" \
    -v "${WORK}:/dst" \
    alpine:3 \
    sh -c "cd /src && tar czf /dst/objects.tar.gz ."
  OBJ_SIZE=$(du -h "${WORK}/objects.tar.gz" | awk '{print $1}')
  echo "  ✓ ${OBJ_SIZE}"
else
  echo "  · 跳过(没找到 ${VOL_NAME})"
  : > "${WORK}/objects.tar.gz"
fi

# ---- 4. .env 脱敏副本 ----
echo "[3/4] 脱敏 .env"
if [ -f .env ]; then
  # 把所有 KEY=VALUE 形式的行,VALUE 改成 ***(注释行 / 空行保留)
  # 例外:VERSION 类元数据保留(便于跨版本恢复)
  awk -F= '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { print; next }
    NF >= 2 {
      key = $1
      gsub(/^[ \t]+|[ \t]+$/, "", key)
      if (key == "FREEHIRE_VERSION" || key == "POSTGRES_USER" || key == "POSTGRES_DB" || key == "FREEHIRE_HTTP_PORT" || key == "ENVIRONMENT") {
        print
      } else {
        print key "=***"
      }
      next
    }
    { print }
  ' .env > "${WORK}/env.sanitized"
  echo "  ✓ env.sanitized 已生成(密钥已脱敏)"
else
  echo "  · 没找到 .env, 跳过"
fi

# ---- 5. manifest ----
cat > "${WORK}/manifest.json" <<EOF
{
  "name": "${NAME}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host": "$(hostname)",
  "freehire_version": "$(grep '^FREEHIRE_VERSION=' .env 2>/dev/null | cut -d= -f2 || echo unknown)",
  "components": {
    "pg_dump": "pg-dump.sql.gz",
    "objects": "objects.tar.gz",
    "env_sanitized": "env.sanitized"
  },
  "restore_hint": "解压本 tarball, 跑 ./restore.sh 即可"
}
EOF

# ---- 6. tar 整包 ----
echo "[4/4] 打包"
( cd "${WORK}" && tar czf "${OUT}" manifest.json pg-dump.sql.gz objects.tar.gz env.sanitized 2>/dev/null )
SIZE=$(du -h "${OUT}" | awk '{print $1}')
echo "  ✓ ${OUT} (${SIZE})"

# ---- 7. 自动清理(可选) ----
if [ -n "${FREEHIRE_KEEP_LAST:-}" ]; then
  KEEP=${FREEHIRE_KEEP_LAST}
  echo
  echo "[清理] 保留最近 ${KEEP} 份, 删除更老的"
  ls -1t "${DEST}"/free-hire-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    echo "  - 删除 ${old}"
    rm -f "${old}"
  done
fi

echo
echo "✓ 备份完成"
echo "  恢复命令:  cd \$(dirname ${OUT}) && ./restore.sh ${NAME}.tar.gz"
