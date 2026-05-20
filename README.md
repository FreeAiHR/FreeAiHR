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

AI 面试，AI 简历，AI 人力资源！FreeAiHR 是一款企业级、人工智能时代下的智能招聘解决方案，支持私有化部署，AI 驱动的全流程招聘管理系统！

全量代码，无缺失，如遇bug，尽快提交。

# Roadmap 路线图

（1）V1.0功能：企业自助发布简历，求职者可以直接投递；已完成。

（2）V2.0功能：AI辅助面试，AI一面；已完成。

（3）AI培训、AI考核、AI评价；正在开发，欢迎加群，提出建议！！！



### ✨ 核心特性

- 🤖 **AI智能解析** - 自动解析简历，提取结构化信息
- 🎯 **智能匹配** - AI精准匹配人才与职位，自动评分
- 📊 **全流程管理** - 从投递到入职，一站式招聘流程管理
- 🔐 **私有化部署** - 一键部署，数据完全私有
- 🔑 **BYOK模式** - 支持自带AI密钥，灵活配置

## 🏗️ 技术栈

### 后端
- Java 17
- Spring Boot 3.2.5
- Sa-Token (权限认证)
- MyBatis-Plus (ORM)
- PostgreSQL (数据库)
- Redis (缓存)

### 前端
- React 18
- TypeScript
- Ant Design 5
- Vite
- TailwindCSS
- Zustand (状态管理)

## 📁 项目结构

```
FreeHR/
├── freehire-server/          # 后端项目
│   ├── src/main/java/com/freehire/
│   │   ├── common/           # 公共模块
│   │   ├── modules/          # 业务模块
│   │   │   ├── auth/         # 认证
│   │   │   ├── system/       # 系统管理
│   │   │   ├── job/          # 职位管理
│   │   │   ├── resume/       # 简历管理
│   │   │   ├── candidate/    # 候选人
│   │   │   └── ai/           # AI服务
│   │   └── FreeHireApplication.java
│   └── pom.xml
│
├── freehire-web/             # 前端项目
│   ├── src/
│   │   ├── pages/            # 页面
│   │   ├── components/       # 组件
│   │   ├── services/         # API服务
│   │   ├── stores/           # 状态管理
│   │   └── router/           # 路由
│   └── package.json
│
├── deploy/                   # 部署配置
│   ├── docker-compose.yml
│   ├── nginx.conf
│   └── install.sh
│
└── README.md
```

## 🚀 快速开始

### 环境要求

- Docker 20.10+
- Docker Compose 2.0+
- Node.js 18+ (本地开发)
- JDK 17+ (本地开发)
- Maven 3.8+ (本地开发)

### 一键部署 (推荐)

```bash
# 克隆项目
git clone https://github.com/your-repo/freehire.git
cd freehire

# 运行安装脚本
cd deploy
chmod +x install.sh
./install.sh
```

安装完成后访问：
- 系统地址: http://localhost
- 默认账号: admin / admin123

### 本地开发

#### 后端启动

```bash
cd freehire-server

# 安装依赖
mvn install

# 启动服务（需要先启动PostgreSQL和Redis）
mvn spring-boot:run
```

#### 前端启动

```bash
cd freehire-web

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

访问 http://localhost:3000

## 📊 功能模块

| 模块 | 功能 | 状态 |
|------|------|------|
| 工作台 | 数据概览、待办事项、招聘漏斗 | ✅ |
| 职位管理 | 发布/编辑/关闭职位、AI生成JD | ✅ |
| 简历管理 | 上传/解析简历、AI智能解析 | ✅ |
| 候选人管理 | 候选人状态流转、沟通记录 | ✅ |
| 面试管理 | 面试安排、反馈录入 | ✅ |
| 人才库 | 人才搜索、标签管理 | ✅ |
| 系统管理 | 用户/角色/部门/AI配置 | ✅ |
| 招聘官网 | 对外招聘页面、在线投递 | ✅ |
| 社区建设 | 企业人力资源社区建设 | 🚧 |

## 🤖 AI功能

系统支持多种AI提供商（BYOK模式）：

| 提供商 | 模型 | 说明 |
|--------|------|------|
| OpenAI | GPT-4/GPT-4o | 推荐，效果最好 |
| 通义千问 | qwen-max | 阿里云，国内首选 |
| DeepSeek | deepseek-chat | 性价比高 |
| 智谱清言 | GLM-4 | 国产大模型 |

AI功能包括：
- 📄 简历智能解析
- 🎯 JD-简历匹配评分
- ✍️ 职位描述生成
- 💬 面试问题建议

## 📦 部署配置

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| DB_HOST | 数据库地址 | localhost |
| DB_PORT | 数据库端口 | 5432 |
| DB_NAME | 数据库名 | freehire |
| DB_USERNAME | 数据库用户名 | postgres |
| DB_PASSWORD | 数据库密码 | postgres |
| REDIS_HOST | Redis地址 | localhost |
| REDIS_PORT | Redis端口 | 6379 |
| MINIO_ENDPOINT | MinIO地址 | http://localhost:9000 |

### 硬件要求

| 规模 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| 小型 (<100用户) | 2核 | 4GB | 50GB |
| 中型 (<500用户) | 4核 | 8GB | 100GB |
| 大型 (>500用户) | 8核 | 16GB | 200GB+ |

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

