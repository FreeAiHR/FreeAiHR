# Free-Hire License 工具

> ⚠️ 此工具仅由产品供应商使用,**绝对不能**进入客户环境。
> 私钥泄漏 = 任何人都能伪造 license,产品商业模式直接崩塌。

## 用途

1. **首次部署前**:生成 RSA-2048 keypair,公钥同步到 backend 仓库
2. **签发客户 License**:用私钥为客户机器签发 `.lic` 文件
3. **续期 / 升级**:为同一客户机器签发新 `.lic` 替换旧的

## 安装

```bash
cd license-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 用法

### 1. 一次性生成密钥对

```bash
python generator.py keygen
# 默认输出到 ./keys/{private,public}.pem
# 同时自动复制 public.pem 到 ../backend/app/infra/license/keys/
```

输出:

```
✓ 私钥: keys/private.pem (永远不要 commit)
✓ 公钥: keys/public.pem
✓ 已同步公钥到 backend: ../backend/app/infra/license/keys/public.pem
```

**接下来必做**:
- 把 `keys/private.pem` 备份到密码管理器或 HSM
- **从工作机删除或加密保护私钥**
- 把 backend 公钥的变更纳入版本控制(`git add backend/app/infra/license/keys/public.pem`)

### 2. 查询客户机器指纹

让客户在他的部署里跑:

```bash
curl http://localhost/api/license/status | jq -r .machine_fingerprint
# 例:FH-2A8B-7C19-EF4D
```

### 3. 签发一份 trial license(30 天)

> 注:trial 由 backend 自动判定首次启动后 30 天,**不需要**也**不允许**手工签发 plan=trial 的 lic。
> 真正给客户发版本时按下面三档选一档。

### 3a. 三档版本签发(社区 / 专业 / 企业)

工具按 `--plan` 自动展开 features,**不再需要** `--features`:

```bash
# 开源社区版(免费,4 项核心功能 + 配额受限)
python generator.py issue \
  --machine FH-2A8B-7C19-EF4D \
  --plan community \
  --days 365 \
  --customer ACME-2026-001 \
  --out acme-com.lic

# 专业版(8 项功能 + 宽松配额)
python generator.py issue \
  --machine FH-2A8B-7C19-EF4D \
  --plan professional \
  --days 365 \
  --customer ACME-2026-001 \
  --out acme-pro.lic

# 企业版(8 项功能 + 配额无限)
python generator.py issue \
  --machine FH-2A8B-7C19-EF4D \
  --plan enterprise \
  --days 365 \
  --customer ACME-2026-001 \
  --out acme-ent.lic
```

特批场景(给某客户单独开/关一项)— 显式覆盖 `--features` 整列表:

```bash
python generator.py issue --plan professional \
  --features resume.upload,interview.text \
  --machine ... --customer ... --days 365 --out custom.lic
```

### 4. 老 lic 兼容(plan=standard)

历史签发的 `--plan standard` lic 继续工作 — backend 自动 alias 为 professional,
features 不变,**老客户零影响**。续期时仍可用 `--plan standard` 或显式改为
`--plan professional`,行为等价。

## 安全检查清单

- [ ] `keys/private.pem` 已加入 `.gitignore`(本目录默认已配)
- [ ] 私钥已备份到 HSM / 密码管理器
- [ ] 从签发机器删除私钥,只在签发时临时还原
- [ ] 公钥已 commit 到 backend 仓库 `app/infra/license/keys/public.pem`
- [ ] 签发记录纸质或独立审计存档(payload 里的 `customer_id` / `expires_at`)

## License 格式

`.lic` 是单行文本,格式:

```
<urlsafe_b64(payload_json)>.<urlsafe_b64(signature)>
```

payload JSON 字段:`version, machine_fingerprint, plan, issued_at, expires_at, features[], customer_id`。
签名:RSA-2048 + PSS + SHA-256(由 backend `app/infra/license/verifier.py` 校验)。

`plan` 取值(2026-05 三档改造后):
- `community`    开源免费版,4 项核心功能 + 50 简历/月 / 1 HR / 5 岗位
- `professional` 专业版,8 项功能全开 + 500 简历/月 / 10 HR / 岗位无限
- `enterprise`   企业版,8 项功能 + 配额全部无限
- `standard`     **遗留**值,等价 professional(老客户兼容,可继续签发)
- `trial`        系统自动判定,**不允许手工签发**

配额(quotas)**不**进 payload — 由 backend 代码端的 `EDITIONS` 字典维护,
改配额只需要发新版客户端,不必重签所有 lic。
