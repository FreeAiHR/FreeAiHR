# Quickstart · 5-10 分钟体验

> 适合从 GitHub clone 仓库、想本地试玩 Free-Hire 的开发者或评估者。
>
> 如果你要做长期部署或运维，请看 [customer-guide.md](customer-guide.md)。
> 离线 tarball 安装请看 [offline-install.md](offline-install.md)。

## TL;DR

```bash
git clone https://github.com/yglyeluo-droid/free-hire.git
cd free-hire/deploy
docker compose up -d --build
```

首次 build 通常需要 5-10 分钟。启动完成后打开 <http://localhost>，使用默认账号登录：

```text
邮箱: admin@example.com
密码: admin123456
```

默认情况下：

- 不需要配置 `.env`
- 不需要外部 API key
- 自动进入 30 天试用期，等同 `professional`
- LLM 使用 mock 返回，完整业务链路可直接跑通

## 你会看到什么

启动成功后，可以直接体验这些模块：

| 模块 | 默认状态 |
|---|---|
| **岗位** | 已注入 3 条示例岗位 |
| **简历库** | 可直接上传 PDF / DOCX / TXT |
| **人才库** | 上传简历后自动沉淀候选人档案 |
| **面试题集 / 题库** | 可以基于简历或岗位直接生成 |
| **面试** | 走候选人远程答题流程，文本可直接体验，语音也能走通 mock 流程 |
| **报告 / 数据分析** | 面试完成后自动出现可用数据 |
| **系统设置** | 可查看 LLM、邮箱拉取、SMTP、语音、License、团队、SSO、审计等入口 |

## 推荐体验路径

如果你只想快速判断这个项目做到了什么，按下面顺序走最快。

### 1. 看岗位与岗位详情

登录后进入 **岗位**：

- 查看 3 条种子岗位
- 进入任一岗位详情页
- 看岗位状态、技能标签、岗位描述
- 打开岗位治理面板，查看能力模型、JD 优化、版本记录、协作备注与审批状态

### 2. 上传一份简历

进入 **简历库**：

1. 拖入一份 PDF / DOCX / TXT 简历
2. 等待状态从 `pending / parsing` 变成 `done`
3. 打开简历抽屉，查看：
   - 解析出的候选人信息
   - 技能标签
   - 原始文本
   - 该简历关联的题集与岗位匹配

如果 worker 没起来，状态会一直停在 `pending`，见后面的排障部分。

### 3. 看人才库

进入 **人才库**：

- 你会看到以候选人为中心的聚合视图
- 每个候选人可查看：
  - 简历版本
  - 面试历史
  - 岗位匹配
  - 标签
  - 分组
  - 黑名单状态
  - 备注与时间线

这一步能很快看出项目不是“单次简历解析 demo”，而是有长期运营数据结构。

### 4. 试一遍 AI 出题

有两条路：

- 在 **简历库** 里对某份简历点击“生成面试题”
- 或进入 **题库**，直接按岗位 / 分类 / 题型 / 难度批量 AI 生成

生成完成后可在：

- **题集** 查看基于简历生成的题集
- **题库** 管理可复用问题池

### 5. 发起一次远程面试

进入 **面试**：

1. 点击“发起面试”
2. 选择岗位与候选人
3. 选择题数、题型、答题形式
4. 生成邀请链接

这里的正式流程是：

- HR 发起邀请
- 候选人通过独立链接进入答题页
- 系统自动出题、收集回答、评分、生成报告

可选体验：

- **文字作答**：最简单，推荐先走通
- **语音作答**：会进入候选人录音 + 转写 + 打分链路

### 6. 打开候选人链接完成答题

复制邀请链接，用另一个浏览器窗口打开：

- 如候选人有手机号，系统会要求输入末 4 位验证
- 通过后进入候选人答题页
- 完成答题后跳到完成页

回到 HR 侧，面试报告会自动可见。

### 7. 看报告和数据分析

进入 **报告** 和 **数据分析**：

- **报告**：看 KPI、推荐率、来源分布、解析状态、岗位填充、热门技能
- **数据分析**：看面试趋势、候选人漏斗、评分分布、高频题目分析

如果你已经完成至少 1 场面试，这两页就能比较直观看出产品数据闭环。

## 配真 LLM

mock 模式适合跑通流程，但真实效果依赖你自己的模型或 API。

在仓库根目录写入 `backend/.env`：

```bash
cat > backend/.env <<'EOF'
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...你的 key
LLM_DEFAULT_MODEL=openai/gpt-4o-mini
EOF
```

然后重启服务：

```bash
cd deploy
docker compose down
docker compose up -d
```

支持：

- OpenAI
- 阿里通义
- 火山引擎
- Azure OpenAI
- 自建 vLLM / Ollama
- 任何 OpenAI 兼容网关

也可以登录后在 **系统 → LLM 配置** 页面直接改。

## 30 天试用期后会发生什么

代码中的 License 当前有 7 个功能位：

- `resume.upload`
- `resume.email`
- `interview.text`
- `interview.voice`
- `match.evaluate`
- `report.export`
- `team.multi`

版本与配额规则：

| | `community` | `professional` | `enterprise` |
|---|---|---|---|
| 功能位 | 4 项核心功能 | 7 项全开 | 7 项全开 |
| 近 30 天简历数 | 50 | 500 | 无限 |
| HR / 管理员账号数 | 1 | 10 | 无限 |
| 岗位数 | 5 | 无限 | 无限 |

试用期行为：

- **试用期内**：自动等同 `professional`
- **试用期到期后**：自动降级到 `community`
- **已有数据不删除**：只限制新增，不影响查看历史数据

## 常见问题

| 现象 | 处理方式 |
|---|---|
| `docker compose up` 很慢或拉取失败 | 国内网络可给 build 加镜像源参数，或先确保 Docker 能正常拉基础镜像 |
| 80 端口被占 | 改 `deploy/docker-compose.yml` 中前端映射端口，例如改为 `8080:80` |
| 简历一直是 `pending` | `cd deploy && docker compose logs -f worker`，通常是 worker 未启动或 broker 不通 |
| 前端打开 502 | 先看 `cd deploy && docker compose ps`，确认 backend 是否健康 |
| 候选人链接打不开或答题中断 | 邀请已过期、被重发、被取消，或 session 过期；重新生成链接即可 |
| 发起语音面试返回 402 | 当前 license 未开启 `interview.voice` |
| 想完全重置数据 | `cd deploy && docker compose down -v` |
| 看后端日志 | `cd deploy && docker compose logs -f backend` |
| 看面试 / 解析异步任务日志 | `cd deploy && docker compose logs -f worker` |

## 本地直跑开发服务器

如果你不想全程走容器，可以只用容器承载 PG / Redis：

```bash
cd deploy
docker compose up -d postgres redis
```

后端：

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

前端默认是 <http://localhost:5173>，`/api` 会自动反代到 8000。

## 下一步

你想继续深入时，建议按这个顺序看：

1. [customer-guide.md](customer-guide.md)：看完整业务流程与部署运维说明
2. [offline-install.md](offline-install.md)：看离线部署方式
3. [../../README.md](../../README.md)：看项目定位、模块与 License 规则
