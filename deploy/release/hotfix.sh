#!/usr/bin/env bash
# Free-Hire 测试环境热修脚本 —— 单镜像替换,不重打 tarball。
#
# 适用场景:
#   测试机已通过 build-tarball.sh + install.sh 部署完成,只想替换 backend
#   和/或 frontend 镜像快速验证一个 fix。完整发版仍走 build-tarball.sh。
#
# 用法:
#   cd deploy/release
#   ./hotfix.sh <ssh-target> <remote-deploy-dir> [backend|frontend|all]
#
# 例:
#   ./hotfix.sh root@test-vm /opt/free-hire-0.1.0-aliyun-test            # all
#   ./hotfix.sh root@test-vm /opt/free-hire-0.1.0-aliyun-test backend    # 只 backend(顺带重启 worker)
#   ./hotfix.sh root@test-vm /opt/free-hire-0.1.0-aliyun-test frontend   # 只 frontend
#
# Env (与 build-tarball.sh 一致):
#   FREEHIRE_TARGET_PLATFORM   默认 linux/amd64;Mac M 系列必须保持这个,否则 amd64 测试机起不来
#   FREEHIRE_APT_MIRROR        backend build 用(国内构建机访问 deb.debian.org 常 502)
#   FREEHIRE_PIP_INDEX         backend build 用
#   FREEHIRE_SKIP_BACKUP=1     跳过远端 ./backup.sh(默认会先备份一份到测试机 /tmp)
#   FREEHIRE_KEEP_TARBALL=1    完成后保留本地与远端 /tmp 的镜像 tarball(默认删除)
#
# 流程:
#   0) 远端读取 .env 中的 FREEHIRE_VERSION
#   1) [可选] 远端跑 ./backup.sh /tmp/(默认开)
#   2) 本地 docker buildx build,镜像 tag 直接打成现有 FREEHIRE_VERSION
#   3) docker save | gzip → 本地 /tmp/freehire-{role}-{version}.tar.gz
#   4) scp 到远端 /tmp/
#   5) 远端 docker load
#   6) 远端 docker compose up -d --force-recreate {service(s)}
#   7) 远端 tail 启动日志 + curl /api/healthz 验证

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"

# ---- 参数 ----
if [ $# -lt 2 ]; then
  sed -n '2,30p' "$0" >&2
  exit 1
fi

SSH_TARGET="$1"
REMOTE_DIR="$2"
ROLE="${3:-all}"

case "${ROLE}" in
  backend|frontend|all) ;;
  *)
    echo "✗ 第 3 个参数必须是 backend / frontend / all,得到: ${ROLE}" >&2
    exit 1
    ;;
esac

TARGET_PLATFORM="${FREEHIRE_TARGET_PLATFORM:-linux/amd64}"
APT_MIRROR="${FREEHIRE_APT_MIRROR:-}"
PIP_INDEX="${FREEHIRE_PIP_INDEX:-}"
SKIP_BACKUP="${FREEHIRE_SKIP_BACKUP:-0}"
KEEP_TARBALL="${FREEHIRE_KEEP_TARBALL:-0}"

# ---- SSH 连接复用 ----
# 用 ControlMaster 把整次执行复用同一条 ssh 连接 —— 即使是 password auth,
# 用户也只需输一次密码,后续 ssh / scp 全部走 master socket,免密。
# socket 路径要短(unix socket 上限 ~108 字节),放 /tmp 没问题。
SSH_MUX="/tmp/.hotfix-ssh-mux-$$-$(date +%s)"
SSH_OPTS=(-o "ControlMaster=auto" -o "ControlPath=${SSH_MUX}" -o "ControlPersist=10m")
ssh_run() { ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "$@"; }
scp_to()  { scp "${SSH_OPTS[@]}" "$@" "${SSH_TARGET}:/tmp/"; }

cleanup_local=()
cleanup_remote=()
cleanup() {
  if [ "${KEEP_TARBALL}" != "1" ]; then
    for f in "${cleanup_local[@]:-}"; do [ -n "${f}" ] && rm -f "${f}" 2>/dev/null || true; done
    if [ "${#cleanup_remote[@]:-0}" -gt 0 ]; then
      ssh_run "rm -f ${cleanup_remote[*]}" 2>/dev/null || true
    fi
  fi
  # 关掉 ssh master(ControlPersist 也会自动过期,但显式关更干净)
  ssh -O exit "${SSH_OPTS[@]}" "${SSH_TARGET}" 2>/dev/null || true
  rm -f "${SSH_MUX}" 2>/dev/null || true
}
trap cleanup EXIT

# ---- 0) 读 FREEHIRE_VERSION ----
echo "==> [0/6] 读取测试机 ${SSH_TARGET}:${REMOTE_DIR}/.env"
VERSION="$(ssh_run "grep -E '^FREEHIRE_VERSION=' '${REMOTE_DIR}/.env' | cut -d= -f2- | tr -d '[:space:]'")"
if [ -z "${VERSION}" ]; then
  echo "✗ 未在 ${REMOTE_DIR}/.env 找到 FREEHIRE_VERSION" >&2
  exit 1
fi
echo "    当前 FREEHIRE_VERSION=${VERSION}"
echo "    目标 platform=${TARGET_PLATFORM}, role=${ROLE}"

# ---- 1) 远端备份(可选) ----
if [ "${SKIP_BACKUP}" = "1" ]; then
  echo "==> [1/6] 跳过远端备份 (FREEHIRE_SKIP_BACKUP=1)"
else
  echo "==> [1/6] 远端备份 ./backup.sh /tmp/"
  ssh_run "cd '${REMOTE_DIR}' && ./backup.sh /tmp/"
fi

# ---- 2) build & save ----
BACKEND_BUILD_ARGS=()
[ -n "${APT_MIRROR}" ] && BACKEND_BUILD_ARGS+=(--build-arg "APT_MIRROR=${APT_MIRROR}")
[ -n "${PIP_INDEX}"  ] && BACKEND_BUILD_ARGS+=(--build-arg "PIP_INDEX=${PIP_INDEX}")

build_and_save_backend() {
  echo "==> [2a/6] 构建 backend 镜像 free-hire-backend:${VERSION}"
  docker buildx build \
    --platform "${TARGET_PLATFORM}" \
    --load \
    ${BACKEND_BUILD_ARGS[@]+"${BACKEND_BUILD_ARGS[@]}"} \
    -t "free-hire-backend:${VERSION}" \
    -f "${REPO}/deploy/Dockerfile.backend" \
    "${REPO}/backend"

  local out="/tmp/freehire-backend-${VERSION}.tar.gz"
  echo "==> [3a/6] docker save backend → ${out}"
  docker save "free-hire-backend:${VERSION}" | gzip > "${out}"
  cleanup_local+=("${out}")
  echo "${out}"
}

build_and_save_frontend() {
  echo "==> [2b/6] 构建 frontend 镜像 free-hire-frontend:${VERSION}"
  docker buildx build \
    --platform "${TARGET_PLATFORM}" \
    --load \
    -t "free-hire-frontend:${VERSION}" \
    -f "${REPO}/deploy/Dockerfile.frontend" \
    "${REPO}/frontend"

  local out="/tmp/freehire-frontend-${VERSION}.tar.gz"
  echo "==> [3b/6] docker save frontend → ${out}"
  docker save "free-hire-frontend:${VERSION}" | gzip > "${out}"
  cleanup_local+=("${out}")
  echo "${out}"
}

LOCAL_BACKEND_TGZ=""
LOCAL_FRONTEND_TGZ=""
if [ "${ROLE}" = "backend" ] || [ "${ROLE}" = "all" ]; then
  LOCAL_BACKEND_TGZ="$(build_and_save_backend | tail -n1)"
fi
if [ "${ROLE}" = "frontend" ] || [ "${ROLE}" = "all" ]; then
  LOCAL_FRONTEND_TGZ="$(build_and_save_frontend | tail -n1)"
fi

# ---- 4) scp ----
echo "==> [4/6] scp tarball 到 ${SSH_TARGET}:/tmp/"
SCP_FILES=()
[ -n "${LOCAL_BACKEND_TGZ}"  ] && SCP_FILES+=("${LOCAL_BACKEND_TGZ}")
[ -n "${LOCAL_FRONTEND_TGZ}" ] && SCP_FILES+=("${LOCAL_FRONTEND_TGZ}")
scp_to "${SCP_FILES[@]}"

REMOTE_BACKEND_TGZ=""
REMOTE_FRONTEND_TGZ=""
[ -n "${LOCAL_BACKEND_TGZ}"  ] && REMOTE_BACKEND_TGZ="/tmp/$(basename "${LOCAL_BACKEND_TGZ}")"  && cleanup_remote+=("${REMOTE_BACKEND_TGZ}")
[ -n "${LOCAL_FRONTEND_TGZ}" ] && REMOTE_FRONTEND_TGZ="/tmp/$(basename "${LOCAL_FRONTEND_TGZ}")" && cleanup_remote+=("${REMOTE_FRONTEND_TGZ}")

# ---- 5) load + restart ----
echo "==> [5/6] 远端 docker load + compose up -d --force-recreate"

RESTART_SVCS=()
[ -n "${REMOTE_BACKEND_TGZ}"  ] && RESTART_SVCS+=(backend worker)   # backend 镜像同时驱动 worker
[ -n "${REMOTE_FRONTEND_TGZ}" ] && RESTART_SVCS+=(frontend)

REMOTE_SCRIPT="set -euo pipefail
cd '${REMOTE_DIR}'
"
[ -n "${REMOTE_BACKEND_TGZ}"  ] && REMOTE_SCRIPT+="echo '  - load ${REMOTE_BACKEND_TGZ}';  docker load -i '${REMOTE_BACKEND_TGZ}'  >/dev/null
"
[ -n "${REMOTE_FRONTEND_TGZ}" ] && REMOTE_SCRIPT+="echo '  - load ${REMOTE_FRONTEND_TGZ}'; docker load -i '${REMOTE_FRONTEND_TGZ}' >/dev/null
"
REMOTE_SCRIPT+="docker compose --env-file .env up -d --force-recreate ${RESTART_SVCS[*]}"

ssh_run "${REMOTE_SCRIPT}"

# ---- 6) 验证 ----
echo "==> [6/6] 等待服务 ready"
if [ -n "${REMOTE_BACKEND_TGZ}" ]; then
  echo "    backend 启动日志(关注 alembic / 0015 / file_mime / error):"
  ssh_run "cd '${REMOTE_DIR}' && docker compose logs --tail=60 backend" \
    | grep -iE 'alembic|migrat|0015|file_mime|error|listening|complete' \
    || ssh_run "cd '${REMOTE_DIR}' && docker compose logs --tail=20 backend"
fi

ssh_run "cd '${REMOTE_DIR}' && \
  for i in \$(seq 1 24); do \
    if curl -sf 'http://localhost:'\"\${FREEHIRE_HTTP_PORT:-80}\"'/api/healthz' >/dev/null 2>&1; then \
      echo '    ✓ /api/healthz OK'; exit 0; \
    fi; sleep 5; \
  done; \
  echo '    ✗ /api/healthz 120s 内未通过,请人工排查' >&2; \
  exit 1"

echo
echo "✓ 热修完成 (version=${VERSION}, role=${ROLE})"
[ "${KEEP_TARBALL}" = "1" ] && echo "  tarball 已保留(本地 + 远端 /tmp)"
[ "${SKIP_BACKUP}"  = "0" ] && echo "  备份位置: ${SSH_TARGET}:/tmp/free-hire-backup-*.tar.gz"
echo
echo "回滚:"
echo "  - 镜像层面: 把上一次 tarball 的镜像再 docker load 一次,然后 compose up -d --force-recreate ${RESTART_SVCS[*]}"
[ -n "${REMOTE_BACKEND_TGZ}" ] && \
echo "  - 数据/迁移: ssh ${SSH_TARGET} \"cd ${REMOTE_DIR} && docker compose exec backend alembic downgrade -1\""
