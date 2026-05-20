"""核心 ORM 模型。

实体边界:
- Tenant     一个客户/公司
- OrgUnit    租户下的组织节点(公司 / 部门 / 项目组)
- User       租户下的用户(admin / hr / interviewer / hiring_manager / viewer)
- License    当前激活的许可证(单实例:同一时刻只有一条记录)
- Job        岗位 / JD
- Candidate  候选人(邮箱+手机哈希去重)
- Resume     简历(关联 candidate,允许多版本)
- EmailAccount         租户级 IMAP 邮箱配置
- Interview / InterviewTurn / QuestionSet / ResumeJobMatch / VoiceProvider 等
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false as sql_false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_str() -> str:
    return str(uuid.uuid4())


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class OrgUnit(Base):
    """组织节点。

    采用简单邻接表(parent_id 指向上级)保存树结构:
    - ``kind``: company / department / project
    - ``parent_id`` 为空表示根节点
    """

    __tablename__ = "org_units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="department")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    org_unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("org_units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False, index=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="hr")
    # 团队管理 — admin 可禁用账户(disabled=不允许登录,数据保留)
    # active   — 正常使用
    # disabled — 禁用,登录时返 401 / token 已签发的请求返 403
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active", index=True
    )
    # EPIC-03 SSO:``local`` 走密码登录,``sso`` 由 IdP 首次登录自动建号。
    # SSO 用户的 ``password_hash`` 仍是 hash 占位串 — 用 secrets.token_urlsafe 派生,
    # 任何明文都不可能命中,从而禁用本地密码登录路径。
    auth_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="local"
    )
    # SSO 用户的 IdP subject(``sub`` claim),用于"邮箱被改" / 多 IdP 区分。
    # 本地账号此列为 NULL。
    external_subject: Mapped[str | None] = mapped_column(
        String(256), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class License(Base):
    """已激活的 license。同一时刻只保留一条最新记录。

    payload 与 signature 都是 base64 字符串,直接持久化以便审计与跨实例共享。
    校验逻辑在 ``app.infra.license.verifier``。
    """

    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lic_payload: Mapped[str] = mapped_column(Text, nullable=False)
    lic_signature: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    activated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Job(Base):
    """JD / 岗位。``skills`` 是字符串数组(JSON 列存)。

    EPIC-05 岗位治理字段:
    - ``competency_model`` JSON — 结构化能力模型(数组)
    - ``publish_status`` — 治理状态机: draft / pending_approval / published / closed
      与原 ``status`` (open/paused/closed) 正交:
        ``status`` 控制运行时可见度 / 匹配入队;
        ``publish_status`` 控制治理流程(只有 published 状态才让 HR 发起面试 / 自动匹配)。
      旧数据通过 migration server_default='published' 视为已发布,平滑过渡。
    - ``current_version`` — 当前版本号,内容每变更一次 +1
    - ``submitted_*`` / ``approved_*`` / ``approval_note`` — 审批留痕
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("org_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="intermediate")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # ---- EPIC-05 岗位治理 ----
    competency_model: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    publish_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="published", index=True
    )
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    submitted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Candidate(Base):
    """候选人。邮箱与手机哈希用于跨简历去重(隐私优先)。

    ``display_email`` / ``display_phone`` 为最近一次简历解析得到的明文,
    仅在 UI 展示时使用;按 PIPL 后期可加 mask 配置。

    EPIC-04 人才库运营字段:
    - ``tags`` JSON 字符串数组 — P0 直接挂在候选人上,简化模型;
      列表筛选用 PostgreSQL ``JSON 包含``即可
    - ``is_blacklisted`` / ``blacklist_reason`` / ``blacklisted_at`` / ``blacklisted_by``
      — 黑名单是高频筛选字段,冗余 bool 列方便索引
    - ``last_active_at`` — 任何相关动作发生时刷新,用于"最近活跃"排序
    - ``owner_user_id`` — 候选人主负责 HR;P1 可做"我的候选人"视图
    - ``status`` — active / archived,P1 可扩 hired / left
    """

    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_unit_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("org_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    display_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    display_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- EPIC-04 人才库运营字段 ----
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active", index=True
    )
    is_blacklisted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_false(), index=True
    )
    blacklist_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blacklisted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    blacklisted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Resume(Base):
    """简历(原始文件 + 解析产物)。

    ``storage_key`` 走 ``app.infra.storage.ObjectStore``,实际位置由后端配置决定。
    ``parsed_data`` 是结构化抽取结果(name/email/phone/experience/education/skills/...)。
    """

    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # `.docx` 等 Office 新格式 MIME 长度可达 71 字符
    # (application/vnd.openxmlformats-officedocument.wordprocessingml.document),
    # 加上历史/自定义 type 的余量,放宽到 255(RFC 6838 推荐上限)。
    file_mime: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 简历解析改为 Celery 异步任务后,UI 通过 parse_status 轮询当前进度。
    # pending  — 入库,任务待 worker 拉取
    # parsing  — worker 已开工(parse_started_at 同步写入)
    # done     — 解析完成,parsed_text/parsed_data 已写入
    # failed   — 解析失败,parse_error 含错误片段(任务不会自动重试,UI 提示用户重传)
    parse_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", index=True
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parse_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Interview(Base):
    """AI 面试会话。状态机:in_progress → done / abandoned。

    ``summary`` JSON 字段在面试结束时填充,包含:
    - dimension_scores: {accuracy, completeness, clarity, latency} 0-100
    - recommendation: "推荐进入下一轮" / "保留" / "不推荐"
    - evidence_quotes: 关键证据片段列表

    ``mode`` 字段:
    - ``self_test`` — HR 自测,在带登录态的页面自己答题(原 ``text`` 模式,
      用于校验 LLM 配置 / 演示 / 题目质量评估,无业务意义)
    - ``remote``    — 候选人远程异步答题。HR 发起后生成 ``invite_token_hash``
      + ``expires_at``,候选人通过免登录链接 ``/i/{token}`` 进入答题页。
      首题 lazy 生成 — 链接没人点不消耗 LLM。

    ``modality`` 字段(M6 语音面试):
    - ``text``  — 候选人在网页打字回答(历史默认,完全兼容)
    - ``voice`` — 候选人浏览器录音回答,后端 STT 转写后写回 ``InterviewTurn.answer``,
                  现有 LLM 评分链不变。"伪实时"产品规则(不可暂停 / 不可重录 /
                  题目间不留思考时间)由前端 ``VoiceSession`` 强制实现。

    向后兼容:历史 ``mode='text'`` 视同 ``self_test``,迁移会就地改名。
    """

    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="self_test")
    # M6 语音面试:答题载体。"text" / "voice"。默认 "text" 让历史数据零迁移成本。
    modality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="text", default="text"
    )
    # 单题录音上限秒数(仅 voice 模式生效)。前端倒计时 + 服务端 sanity check 都看它。
    single_turn_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="90", default=90
    )
    # 整场全程录音(可选,用于 HR 反作弊回放)。各 turn 的分段录音另存。
    full_audio_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", index=True
    )
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="intermediate")
    # 题数 / 题型 — 替代之前硬编码的 MAX_TURNS=5 与全题型混合。
    # remote 模式 HR 在发起表单选;self_test 默认 5 题、4 个题型全选。
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    kinds: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=lambda: ["tech", "project", "scenario", "soft"]
    )
    # remote 模式独有 — token + expires_at + 验证后 4 位手机号 + 候选人通知邮箱。
    # token 用 sha256(token_urlsafe(32)) 存,明文只发给 HR 一次。
    invite_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verify_phone_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    notify_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="link")
    # 候选人首次打开链接时间 — 区分"已邀请未答" / "已开始"
    candidate_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # _finish 后给 HR 发完成通知是否已发出(SMTP 模块上线后启用)
    hr_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class InterviewTurn(Base):
    """单轮问答。``scores`` 是 4 个维度的得分,``evidence`` 是 LLM 给出的证据片段。

    M6 语音面试:
    - ``audio_storage_key`` 指向候选人本题录音文件(走 ObjectStore)。
    - ``transcript`` 是 STT 转写文本;同时也写回 ``answer`` 字段,让现有 LLM 评分
      链路一行不动。两者保留有差异是为了 HR 在报告页能看到 STT 原文 + 任何后续编辑。
    - ``transcript_status`` idle/pending/transcribing/done/failed 控制评分流的入口:
      voice 面试要等 transcript_status='done' 才能进入 score_status 链。
    - ``voice_signals`` 存语音特有指标(语速 / 静默率 / 填充词 / 声纹 embedding /
      多人检测),由 STT 后处理填充,供 HR 反作弊参考。
    - 文本面试这些字段全为 NULL,完全兼容历史。
    """

    __tablename__ = "interview_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    interview_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 评分链异步化后,UI 通过 score_status 轮询本轮处理进度。
    # idle    — 还没作答(占位,等用户提交)
    # pending — 用户已提交答案,任务待 worker 拉取
    # scoring — worker 已开工(score_started_at 同步写入)
    # done    — 评分 + 下一题(若有)/ 结束 都完成
    # failed  — 任一 LLM 调用异常,score_error 含错误片段
    score_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="idle", index=True
    )
    score_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ---- M6 语音面试增量字段(text 面试一律 NULL,兼容历史) ----
    # 候选人单题录音的 ObjectStore key,例:interviews/{interview_id}/turns/{idx}.webm
    audio_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # STT 转写文本。落库后会同时写到 answer,但保留独立字段方便 HR 报告页看 "原始转写"。
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 转写状态机(语音面试专用,文本面试始终 'idle'):
    # idle          — 候选人还没录(text 模式恒在此态)
    # pending       — 录音已上传,等 STT worker
    # transcribing  — STT worker 正在转写
    # done          — 转写完成,answer 已填,可进入 score 链
    # failed        — STT 异常,transcript_error 含原因;HR 可重试
    transcript_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="idle", index=True
    )
    transcript_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 语音特有信号 JSON:{speech_rate_wpm, silence_ratio, filler_word_count,
    # voiceprint_embedding, background_voices_count}。供 HR 反作弊面板与 Interview
    # summary["voice_analysis"] 聚合用。
    voice_signals: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ---- 留痕字段 ----
    # LLM 评分原始返回(完整 JSON),便于事后审计/复核 AI 决策过程。
    llm_raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # HR 人工复核覆盖评分,格式同 scores:{accuracy,completeness,clarity,latency}
    hr_score_override: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    hr_score_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_scored_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    hr_scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LLMProvider(Base):
    """租户级 LLM Provider 配置。

    - ``api_key_encrypted`` 是 :func:`app.infra.crypto.encrypt` 输出
    - 同租户最多一条 ``is_active=true``(由 partial unique index 强制)
    - ``model`` 直接写 LiteLLM 标识符(如 ``openai/gpt-4o-mini``、``deepseek/deepseek-chat``、
      ``azure/<deployment>``),后端不再做前缀映射,LiteLLM 原生路由
    """

    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class EmailAccount(Base):
    """租户级邮箱配置。

    - 密码 Fernet 加密存储
    - 后台轮询任务从这里读所有 ``is_enabled=true`` 的账户,拉新邮件附件入简历库
    - ``last_synced_at`` / ``last_status`` / ``last_error`` 用于 UI 展示同步状态
    """

    __tablename__ = "email_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    imap_host: Mapped[str] = mapped_column(String(256), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    folder: Mapped[str] = mapped_column(String(64), nullable=False, default="INBOX")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class SMTPAccount(Base):
    """租户级 SMTP 发件配置(M4 远程面试邀请 / 完成通知用)。

    设计:
    - 每个租户最多一条记录(API 用 upsert,UI 是单表单不是列表),够用且简洁
    - 密码 Fernet 加密(同 :class:`EmailAccount`)
    - ``use_tls=True`` 走 STARTTLS(默认 587 端口);False 走 implicit SSL(465)
    - ``from_email`` 通常 = ``username``,但部分企业邮箱允许独立发件人地址
    - 失败诊断信息在 ``last_status`` / ``last_error``,UI 上展示给管理员

    与 :class:`EmailAccount` (IMAP 收件) 完全独立 — 不复用同一表 / 同一连接,
    职责清晰。
    """

    __tablename__ = "smtp_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,  # 一个租户最多一条
    )
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[str] = mapped_column(String(256), nullable=False)
    from_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class SSOConfig(Base):
    """租户级 SSO 配置(EPIC-03 P0:仅支持 OIDC / OAuth2)。

    设计:
    - 一个租户最多一条(``tenant_id`` unique),UI 是单表单 — 大多数客户
      只接一个 IdP,P0 不做多 IdP 并存
    - ``client_secret`` 走 Fernet 加密,DB 只存密文
    - 字段对齐 ``07-epic-03-sso-integration.md`` 建议:
      issuer / client_id/secret / authorize_url / token_url / userinfo_url /
      scopes / redirect_uri / 自动建号开关 / 默认角色 / 默认组织 /
      claim 名称 + 映射规则
    - 自动建号 + 默认角色 + 默认组织三件套保证"首次登录后权限不悬空"
    - role_mapping_rules / org_mapping_rules 用 JSON 存
      ``{"claim_value": "role_or_org_id"}``,P0 简单等值映射,P1 再扩条件表达式

    本地登录兜底:``enabled=False`` 或本表无记录时,登录页只显示密码登录入口。
    管理员把 SSO 配错后仍可用本地账号登录,不会被锁死。
    """

    __tablename__ = "sso_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # P0 固定 oidc;留字段是为了 P1 区分 saml / cas / casdoor
    provider_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="oidc"
    )
    display_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="企业统一登录"
    )

    # ---- OIDC 端点 ----
    issuer_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    authorize_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    userinfo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default="openid profile email"
    )
    redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ---- 自动建号策略 ----
    auto_provision_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    default_role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="hr"
    )
    default_org_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("org_units.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---- claim 解析 ----
    email_claim: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="email"
    )
    name_claim: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="name"
    )
    role_claim: Mapped[str | None] = mapped_column(String(64), nullable=True)
    org_claim: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- 映射规则 ----
    # role_mapping_rules: {"claim_value": "system_role"}
    # org_mapping_rules:  {"claim_value": "org_unit_id"}
    role_mapping_rules: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    org_mapping_rules: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ---- 状态 ----
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CandidateGroup(Base):
    """候选人静态分组(人才池)。EPIC-04 P0 — 仅手动加入 / 移出。

    设计:
    - 单租户内 ``name`` 唯一(管理员维护)
    - 与候选人多对多关系走 ``candidate_group_members`` 关联表
    - P0 不做规则化人才池(P1 再扩"自动入池规则")
    """

    __tablename__ = "candidate_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_candidate_groups_tenant_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CandidateGroupMember(Base):
    """候选人 ↔ 分组 关联。"""

    __tablename__ = "candidate_group_members"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "candidate_id", name="uq_candidate_group_members"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    group_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("candidate_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    added_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CandidateNote(Base):
    """候选人备注(人才库时间线主要来源之一)。

    ``author_email`` 冗余存储,避免用户被删除后旧备注显示空白。
    P0 不做编辑 / 删除 — append-only,简化模型 + 留痕一致。
    """

    __tablename__ = "candidate_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_email: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class QuestionSet(Base):
    """简历 → 面试题 题集。

    HR 在某份简历上点「生成面试题」即创建一条记录,worker 调 LLM 生成
    题目数组写入 ``questions`` 列。状态机:pending → generating → done / failed。

    questions 结构::

        [{
          "question":      "题干",
          "answer_points": ["要点1", "要点2", ...],
          "dimensions":    ["技术深度", "项目复盘"],
          "difficulty":    "初级|中级|高级|专家",
          "follow_up":     "追问题(可选)"
        }, ...]

    设计:
    - resume_id 必填(题目从简历内容衍生)
    - job_id 可选 — 不绑岗位时,LLM 完全按简历技能 / 经验出题
    - kinds 多选,LLM 在 prompt 里拼成 "题目类型应覆盖: ..."
    - 不存 LLM 原始 raw response,只保留结构化结果(节省存储)
    """

    __tablename__ = "question_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="intermediate")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", index=True
    )
    questions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ResumeJobMatch(Base):
    """简历 ↔ 岗位 AI 匹配评分。

    每对 (resume, job) 一条记录(unique 复合键),worker 异步调 LLM 给匹配度
    评分 + 短评。HR 在简历库 / 岗位"匹配候选人"页直接看分数排序,而不是
    人眼挑简历。

    状态机:pending → matching → done / failed
    autoretry=0,UI 显式"重新评估"才会重跑(同 :class:`QuestionSet` 模式)。

    LLM 输出 schema(写到 ``score`` / ``strengths`` / ``gaps`` / ``comment``):
        {
          "score":     0-100,
          "strengths": ["匹配亮点 1", ...],   # ≤5 条
          "gaps":      ["关键短板 1", ...],   # ≤5 条
          "comment":   "<=120 字总结"
        }

    触发来源:
    - 简历解析完成 → ``evaluate_matches_for_resume.delay`` 对所有 active 岗位
    - 岗位创建/置 open → ``evaluate_matches_for_job.delay`` 对最近简历
    - HR 手工 → ``POST /api/matches/.../evaluate-all`` 或 ``regen``

    设计:
    - 完全照抄 :class:`QuestionSet` 状态机风格
    - 一对 (resume, job) 不重跑(已 done 的不重新算,节约 LLM 调用)
    - ``created_by`` 区分自动(NULL)/手动(HR user_id)触发,便于审计
    """

    __tablename__ = "resume_job_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", index=True
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strengths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    gaps: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("resume_id", "job_id", name="uq_resume_job_match"),
    )


class JobVersion(Base):
    """岗位版本快照(EPIC-05 T8)。

    每次岗位"内容字段"(title / level / description / skills / competency_model)
    实际发生变化时,落一条历史快照。``version_no`` 与 ``Job.current_version`` 对齐。

    ``change_kind`` 记录是 ``content_update`` / ``approve`` / ``reject`` /
    ``submit_approval`` / ``competency_generated`` / ``jd_optimized`` 等触发原因。
    审批 / 状态切换也会落一条版本(content 字段为空),帮助详情页统一时间线展示。
    """

    __tablename__ = "job_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 字段快照 — content_update 时填,纯状态事件可留 NULL
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    competency_model: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    publish_status: Mapped[str | None] = mapped_column(String(24), nullable=True)

    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_email: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class JobComment(Base):
    """岗位协作备注(EPIC-05 T10)。

    与 :class:`CandidateNote` 设计一致 — append-only,author_email 冗余存储。
    用人经理 / HR / 审批人都可留言,详情页时间线统一展示。
    """

    __tablename__ = "job_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_email: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class QuestionLibraryItem(Base):
    """租户级题库条目 — 可跨面试复用的问题池。

    ``source``: "manual"(HR 手工录入) / "ai_generated"(从题集导出或直接 AI 生成)。
    ``use_count`` / ``avg_score`` 在面试引用本题后异步更新,供区分度分析用。
    """

    __tablename__ = "question_library_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_points: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="tech")
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="intermediate")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    skill: Mapped[str | None] = mapped_column(String(128), nullable=True)
    follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_score: Mapped[float | None] = mapped_column(nullable=True)
    generated_from_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """操作审计日志。EPIC-02 审计合规中心的数据底座。

    ``actor_email`` 反规范化冗余存储,防止用户删除后日志无法显示操作人。
    ``detail`` 存操作附加信息,如评分覆盖前后值、邀请变更等。
    ``result`` 默认 ``success``;失败(异常 / 业务拒绝)与拒绝(403 / 越权)
    都需要单独记录,客户安全部门常用"failure / denied"做异常检测。
    ``ip`` / ``user_agent`` 来自请求头,用于事后排查异常访问来源;为空表示
    内部触发(如 worker)或请求头缺失。
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_email: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="success"
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )


class VoiceProvider(Base):
    """租户级语音(STT/TTS)Provider 配置(M6 V5 — Settings UI)。

    设计参考 :class:`SMTPAccount`:租户内最多一条(``tenant_id`` unique),
    UI 是单表单。STT 和 TTS 共享一份记录是因为大多数客户两者用同一家厂商
    (例:阿里云 dashscope 同一个 API key 同时支持 ASR 和 TTS)。

    与 .env 配置的关系(优先级):
    1. DB 中存在该 tenant 行 + ``is_enabled=True`` → 用 DB 配置
    2. 否则 → 走 ``settings.stt_backend`` / ``settings.tts_backend``(.env 兜底)
    3. 都没配 → 业务层报错(STT/TTS Error)

    API key 走 :func:`app.infra.crypto.encrypt`,DB 只存密文。

    ``last_tested_at`` / ``last_status`` / ``last_error``:UI 上展示连通状态,
    由 ``POST /api/voice/test`` 写入。

    与之前的 ``LLMProvider`` 不同点:
    - LLMProvider 允许同租户多条 +``is_active`` 单选(因为 LLM 可能切换试用)
    - VoiceProvider 是 unique tenant_id 单条:STT/TTS 切换频率低,简化即可
    """

    __tablename__ = "voice_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,  # 一个租户最多一条
    )

    # ---- STT ----
    stt_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="mock"
    )
    stt_api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stt_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    stt_model: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="whisper-1"
    )
    stt_language: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="zh"
    )

    # ---- TTS ----
    tts_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="mock"
    )
    tts_api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tts_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    tts_model: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="tts-1"
    )
    tts_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="alloy"
    )
    tts_format: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="mp3"
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
