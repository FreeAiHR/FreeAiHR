# Free-Hire

[![CI](https://github.com/yglyeluo-droid/free-hire/actions/workflows/ci.yml/badge.svg)](https://github.com/yglyeluo-droid/free-hire/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

面向招聘团队的 AI 招聘工作台，覆盖岗位管理、简历解析、候选人沉淀、AI 面试、报告分析与后台治理。

> **快速体验** → [`docs/ops/quickstart.md`](docs/ops/quickstart.md)
> **部署与运维** → [`docs/ops/customer-guide.md`](docs/ops/customer-guide.md)
> **离线安装** → [`docs/ops/offline-install.md`](docs/ops/offline-install.md)

## 项目特性

- 简历上传、解析、候选人去重与原件管理
- 人才库沉淀，聚合简历版本、面试历史、标签、备注与时间线
- 岗位创建、岗位详情、岗位匹配与岗位治理
- 基于简历或岗位生成题集，支持题库复用与 AI 批量生成
- 候选人远程文本 / 语音面试，自动评分并生成报告
- KPI、漏斗、趋势、评分分布等分析能力
- 多角色权限、团队协作、SSO、审计与系统配置

## 角色模型

系统默认包含以下角色：

- `admin`：系统管理、团队、审计、License 等全权限操作
- `hr`：岗位、简历、面试、报告等日常招聘操作
- `interviewer`：参与面试和查看结果
- `hiring_manager`：查看职责范围内的岗位与报告，并可编辑岗位
- `viewer`：只读查看

接口会结合角色权限和组织范围做访问控制。

## 快速启动

前置要求：

- Docker Desktop 或 Docker Engine 24+
- 已启用 BuildKit

启动开发体验环境：

```bash
cd deploy
docker compose up -d --build
```

首次构建通常需要 5-10 分钟。服务启动后访问 <http://localhost>，默认账号如下：

```text
邮箱: admin@example.com
密码: admin123456
```

默认体验包含：

- 自动 bootstrap 管理员账号
- 自动注入示例岗位
- 默认进入 30 天试用期，能力等同 `professional`
- LLM 默认使用 mock，无需外部 API key 也可跑通主要流程

完整体验步骤见 [`docs/ops/quickstart.md`](docs/ops/quickstart.md)。

## 配置真实模型

若需要接入真实 LLM，可在 `backend/.env` 中配置兼容 OpenAI 的接口：

```bash
cat > backend/.env <<'EOF'
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_DEFAULT_MODEL=openai/gpt-4o-mini
EOF

cd deploy
docker compose down
docker compose up -d
```

支持 OpenAI 兼容 API，包括 OpenAI、阿里通义、自建 vLLM / Ollama 或其他网关。

## License 机制

仓库内包含社区版、专业版和企业版相关的 License 机制。当前代码中的能力控制主要由功能位和配额驱动。

功能位示例：

- `resume.upload`
- `resume.email`
- `interview.text`
- `interview.voice`
- `match.evaluate`
- `report.export`
- `team.multi`

默认规则以当前代码实现为准：

- 首次部署后默认进入 30 天试用期
- 激活 `.lic` 后按 `plan` 生效
- 过期或未激活时回落到 `community`
- 已有数据不会因版本回落被删除

如果你只是评估或二次开发，可以直接使用仓库默认体验配置。

## 技术栈

- Backend: FastAPI, SQLAlchemy, Alembic, Celery
- Frontend: React, Vite, TypeScript
- Infra: Docker Compose, Nginx, PostgreSQL, Redis

## 仓库结构

```text
backend/        FastAPI 服务、领域模型、任务与测试
frontend/       React 前端与 E2E 用例
deploy/         Docker Compose、Dockerfile、Nginx 配置
docs/           公开运维与部署文档
license-tool/   License 生成与续期辅助工具
```

## 文档

- 本地体验：[`docs/ops/quickstart.md`](docs/ops/quickstart.md)
- 部署说明：[`docs/ops/customer-guide.md`](docs/ops/customer-guide.md)
- 离线安装：[`docs/ops/offline-install.md`](docs/ops/offline-install.md)
- 安全披露：[`SECURITY.md`](SECURITY.md)

## 开源与商业授权

本项目以 **[GNU AGPL-3.0](LICENSE)** 协议开源。

AGPL 适合以下场景：

- 学习、评估、内部研究
- 愿意将修改后的源码继续以 AGPL 方式公开

以下场景通常需要单独的商业授权或企业协议：

- 希望在商用场景中闭源修改
- 以 SaaS 或托管服务形式对外提供
- 集成到闭源产品或商业发行版中

> 根据 AGPL-3.0 第 13 条，如果你通过网络向他人提供基于本软件的服务，通常需要公开相关修改源码。

**商业授权咨询**：请通过仓库主页、维护者资料或后续补充的官网入口联系。
