# FreeAiHR - AI时代下的人力资源系统-全量代码
<p align="left">
  <img src="https://img.shields.io/badge/Java-17-orange" alt="Java">
  <img src="https://img.shields.io/badge/Spring%20Boot-3.2.5-green" alt="Spring Boot">
  <img src="https://img.shields.io/badge/React-18-blue" alt="React">
  <img src="https://img.shields.io/badge/Ant%20Design-5.x-blue" alt="Ant Design">
  <img src="https://img.shields.io/badge/PostgreSQL-16-blue" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/License-AGPL-yellow" alt="License">
</p>
 
## 📖 简介

AI HR，AI辅助面试，AI面试系统，AI 简历，AI考评系统，AI带教系统，AI培训系统，AI 人力资源，人工智能人力资源系统！AI interview,AI assisted interview,AI resume,AI evaluation system,AI coaching system,AI training system,AI human resources.
FreeAiHR 是一款企业级、人工智能时代下的智能招聘解决方案，支持私有化部署，AI 驱动的全流程招聘管理系统！

全量代码，无缺失，如遇bug，尽快提交。

# Roadmap 路线图

（1）V1.0功能：企业自助发布简历，求职者可以直接投递；已完成，该模块完整代码请fork：https://github.com/FreeAiHR/FreeAiHR-PostJobs

（2）V2.0功能：AI辅助面试，AI一面；已完成，见本项目。

（3）AI评价，360度评价系统，已完成，该模块完整代码请fork：https://github.com/FreeAiHR/FreeAiHR-HR360 

（4）AI培训、AI考核；正在开发，欢迎加群，提出建议！！！

## 项目特性

- 简历上传、解析、候选人去重与原件管理
- 人才库沉淀，聚合简历版本、面试历史、标签、备注与时间线
- 岗位创建、岗位详情、岗位匹配与岗位治理
- 基于简历或岗位生成题集，支持题库复用与 AI 批量生成
- 候选人远程文本 / 语音面试，自动评分并生成报告
- KPI、漏斗、趋势、评分分布等分析能力
- 多角色权限、团队协作、SSO、审计与系统配置

## 🤝 体验地址
FreeAiHR 2.1 版本：
地址： http://47.100.73.57/ 
账户： admin@example.com
密码： vJUqlKw5b6sr4fcA68L7

## 社区共建：微信社群，一起迭代

<img width="595" height="501" alt="image" src="https://github.com/user-attachments/assets/c5949e7e-7557-4390-ab0f-62a42b85585a" />


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

## 🤝 部分截图

登录页面：
<img width="1600" height="864" alt="926db4452a465f515ac2a22a4786375a" src="https://github.com/user-attachments/assets/377ab974-7e3a-4c5e-a367-4a4f06b8aa51" />

工作岗位发布界面：
<img width="1183" height="754" alt="8843a450ab16a0616744eb7a5244beb2" src="https://github.com/user-attachments/assets/6519d671-d7a0-4ae1-98ee-b92d2995f747" />

HR发布岗位界面：
<img width="1753" height="829" alt="d7731d1f36aa1146c351acfe3bb4ffec" src="https://github.com/user-attachments/assets/13caa020-4917-4aac-87bf-f1716c05af1a" />

工作台界面：
<img width="1440" height="778" alt="31065121555f4874b08999925bdae5c8" src="https://github.com/user-attachments/assets/82702ca1-a04f-4f68-b463-2a815b247ec6" />

大模型配置界面：
<img width="2369" height="1280" alt="70a6f9f13f480f631d50564670700f89" src="https://github.com/user-attachments/assets/2dbfcc4a-88d3-49d7-ae8f-19664fe577b6" />

企业个性化界面：
<img width="1430" height="694" alt="d9ef22a0099c7e6239f62b957075db81" src="https://github.com/user-attachments/assets/eeb8ba0b-4149-4027-a5e0-7a2a145cd3cb" />

AI面试界面：
<img width="1616" height="921" alt="image" src="https://github.com/user-attachments/assets/8bcb5669-bb2a-444f-b3a6-a38a8e702dd1" />

报告页面：
<img width="1601" height="923" alt="image" src="https://github.com/user-attachments/assets/892fbbf1-84c5-4391-ad9d-890f370c5b33" />

SSO登录界面，方便使用：
<img width="1592" height="911" alt="image" src="https://github.com/user-attachments/assets/7f68213d-110f-400a-b8ca-8610940833ec" />

增加了审计功能：
<img width="1837" height="922" alt="image" src="https://github.com/user-attachments/assets/862ac14d-84ba-4053-b0f2-0fb8fe412f5a" />


## 🤝 联系客服小编

点击 # Star后，入群讨论：

<img width="177" height="297.6" alt="3578c36825ef86aff9005442191ccf8b" src="https://github.com/user-attachments/assets/c1c071b9-d658-4f52-bdb9-cd382c5a17fe" />

扫码添加客服小编微信

<img width="205.8" height="282.0" alt="3afe58413fda51e3833432a56742810f" src="https://github.com/user-attachments/assets/bae3f6d8-abfb-4739-8f67-9efd310d8b6a" />


如有问题或建议，欢迎提交 Issue 或 Pull Request。

## 开源不易，欢迎打赏

200-2k转账即可（可备注公司名称、个人姓名等标识信息，用于readme中的感谢墙）。
希望你加群，不断提出需求，在AI时代一起迭代！一起想需求，一起增代码，一起进步！


<img width="223.6" height="304.8" alt="66484146948f2c82ae88fd2b4001d8db" src="https://github.com/user-attachments/assets/023c9e16-078b-4810-b40e-ac65535a8c57" />

## 🤝 欢迎使用我们团队的大模型呼叫中心

可访问：

www.freeaicc.com 

www.freeipcc.com

有三个版本：
1. 外呼系统
2. 呼入系统
3. 大模型呼叫中心系统（呼入与呼出）

------------------------

<p align="center">Made with ❤️ by FreeAiHR Team</p>
