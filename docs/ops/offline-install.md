# 离线部署手册

> 适用对象:部署方 IT / 系统管理员。
> 前提:目标服务器已装 Docker Engine ≥ 24、Docker Compose V2,**不需要任何外网访问**。
> 交付物:`free-hire-{VERSION}.tar.gz`(大概 500MB-1GB,含所有镜像 + 配置 + 文档)。

## 一、获取交付包

获取发布包后,可通过以下任一方式分发到目标环境:

- 离线介质(U 盘 / 光盘)
- SFTP / SCP 推到目标跳板机
- 内部制品库下载

校验文件存在 + SHA256(供应商单独提供):

```bash
sha256sum free-hire-{VERSION}.tar.gz
# 与供应商提供的官方 checksum 比对
```

## 二、解压 + 一键安装

```bash
# 1. 解压
tar xzf free-hire-{VERSION}.tar.gz
cd free-hire-{VERSION}/

# 2. 跑安装脚本(第一次会停在 .env 编辑提示)
./install.sh

# 3. 编辑 .env 至少改三项:
#    - JWT_SECRET                 openssl rand -hex 32
#    - BOOTSTRAP_ADMIN_PASSWORD   强密码(BOOTSTRAP_ADMIN_EMAIL 可保留默认)
#    - MACHINE_FINGERPRINT_OVERRIDE   openssl rand -hex 16
vi .env

# 4. 再跑一次 install.sh 完成启动
./install.sh
```

跑完会看到:

```
✓ Free-Hire 已启动

访问: http://localhost
API 文档: http://localhost/api/docs
```

## 三、申请 License

容器化部署的"机器指纹"取自 `MACHINE_FINGERPRINT_OVERRIDE`(你刚刚生成的)。把它发给供应商,获取一份 `.lic`:

```bash
# 查机器指纹
grep MACHINE_FINGERPRINT_OVERRIDE .env
# 把这串发给 Free-Hire 供应商
```

供应商签发 `.lic` 后,导入路径:

1. 浏览器打开 `http://localhost`
2. 用 `BOOTSTRAP_ADMIN_*` 账号登录
3. 左侧 `系统 → License 设置` → 上传 `.lic`
4. 状态栏显示"已激活 · 至 yyyy-mm-dd"

License 不上传也能跑 30 天试用期(等同 Professional 全功能,过期降级 Community 4 项 + 配额受限)。

## 四、配置 LLM Provider

LLM 调用走部署方自己配置的 provider,可按你的网络边界要求运行。

1. 浏览器 → `系统 → LLM 配置`
2. 点 **添加 Provider**:
   - 数据留在内网:Base URL 指向你部署的 vLLM / Ollama / 任何 OpenAI 兼容服务,`model` 填 `openai/<内网模型名>`
   - 用 Azure OpenAI:点表单上方"Azure OpenAI"快速填充,Base URL 改成自家 tenant 地址,`model` 填 `azure/<deployment>`
3. 模型字段直接写 LiteLLM 标识符,UI 上有 `?` 图标弹常见示例
4. **不推荐**(会把候选人简历外发):OpenAI / DeepSeek / 通义千问 公网 API。前端会自动弹合规告知

## 五、日常运维

### 启停

```bash
# 停止(保留数据)
docker compose down

# 启动
docker compose --env-file .env up -d

# 查日志
docker compose logs -f backend
docker compose logs -f frontend

# 完全清理(含 PG / Redis / 对象存储数据)
docker compose down -v
```

### 升级

供应商发新版本 `free-hire-{NEW_VERSION}.tar.gz`:

```bash
# 1. 备份旧 .env
cp free-hire-{OLD_VERSION}/.env /tmp/free-hire.env.bak

# 2. 解压新包
tar xzf free-hire-{NEW_VERSION}.tar.gz
cd free-hire-{NEW_VERSION}/

# 3. 复用旧 .env(只换 FREEHIRE_VERSION)
cp /tmp/free-hire.env.bak .env

# 4. 安装(install.sh 会自动 sync 新版本号)
./install.sh
```

数据卷(`pg-data` / `object-data` / `redis-data`)默认是 docker named volume,跨版本保留。如果要做更稳的 backup,见下节。

### 备份

**PostgreSQL**:

```bash
# 备份
docker compose exec postgres pg_dump -U freehire freehire | gzip > backup-$(date +%Y%m%d).sql.gz

# 恢复
gunzip -c backup-20260503.sql.gz | docker compose exec -T postgres psql -U freehire freehire
```

**对象存储(简历原文件)**:

```bash
# named volume 路径
docker volume inspect free-hire_object-data --format '{{.Mountpoint}}'

# 备份
sudo tar czf objects-$(date +%Y%m%d).tar.gz -C $(docker volume inspect free-hire_object-data --format '{{.Mountpoint}}') .

# 恢复(目标 volume 必须先创建好)
sudo tar xzf objects-20260503.tar.gz -C $(docker volume inspect free-hire_object-data --format '{{.Mountpoint}}')
```

**.env**:必须备份(JWT_SECRET / 加密密钥变了的话,DB 里加密的 LLM API key、邮箱密码、license 都失效)。

### Multi-worker(高负载场景)

默认 backend 单进程。如果租户数 / 简历量大,改成 multi-worker:

编辑 `Dockerfile.backend`(在交付包里没有,如需调整请联系供应商定制),把 CMD 改成:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
```

Redis 分布式锁会自动接管去重(M2 Step B 已落地)。

## 六、故障排查

| 现象 | 排查 |
|---|---|
| `install.sh` 第 2 步报 docker compose 不存在 | 装 Docker Compose V2:`apt-get install docker-compose-plugin` |
| backend 启动后立刻退出 | `docker compose logs backend`,检查 `JWT_SECRET` 是否还是默认占位(prod 拒绝启动)|
| 前端打开是 502 | backend 没起来,先看 `docker compose ps` 状态 |
| `.lic` 上传时报"机器指纹不匹配" | 当前 `MACHINE_FINGERPRINT_OVERRIDE` 与签发时不一致,重新申请 |
| 邮箱拉取不工作 | `系统 → 邮箱拉取 → 测试连接`,常见是 IMAP 端口被防火墙挡 |
| LLM 调用超时 | 检查 `系统 → LLM 配置` 里 base_url 在当前网络环境是否可达 |

## 七、合规与责任

- 全部数据(简历 / 候选人 PII / 面试问答 / LLM 调用)默认留在你的部署环境中
- 但若主动配置外网 LLM provider(OpenAI / DeepSeek / Qwen),数据会发给对应 provider，前端 UI 已做明确提示
- License RSA-2048 离线验证,不向供应商回拨任何 telemetry

## 八、卸载

```bash
docker compose down -v          # 停服 + 删数据卷
docker rmi free-hire-backend:{VERSION} free-hire-frontend:{VERSION}
docker rmi postgres:16-alpine redis:7-alpine
cd .. && rm -rf free-hire-{VERSION}/
```

---

需要供应商支持时,把 `docker compose logs --since 1h backend frontend` 输出 + `.env`(脱去 JWT_SECRET / 密码部分)+ 当前 `.lic` 状态发过去。
