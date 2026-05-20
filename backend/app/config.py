"""集中读取环境变量的配置入口。

私有化客户的部署人员需要通过 .env 注入数据库、Redis、对象存储路径,
JWT 密钥、首次启动管理员账号、以及自配的 LLM Provider。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_version: str = "0.0.1"
    environment: str = Field(default="dev", description="dev / staging / prod")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost", "http://localhost:5173"],
    )
    # 公网可访问的前端域名根 — 邀请邮件 / 完成通知里的链接以此为前缀拼。
    # 留空则取 cors_origins 第一项。私有化部署的客户应在 .env 显式配置(例如
    # ``http://hr.company.local`` 或 ``https://hire.company.com``)。
    public_base_url: str | None = None

    # ---- 基础设施 ----
    database_url: str = "postgresql+psycopg://freehire:freehire@postgres:5432/freehire"
    redis_url: str = "redis://redis:6379/0"

    # ---- 运维诊断端点 ----
    # dev 默认公开 ``/api/healthz?detail=1`` 与 ``/api/metrics`` 方便本地调试。
    # prod/staging 默认隐藏,仅在内网网关/Prometheus 已做好访问控制时显式打开。
    expose_operational_diagnostics: bool = False

    # ---- 对象存储 ----
    storage_backend: str = Field(default="local", description="local / s3 / seaweedfs ...")
    storage_root: str = Field(default="/var/lib/free-hire/objects")

    # ---- 鉴权 ----
    # prod 必须设 JWT_SECRET >= 32 字符随机。dev 可以走默认占位但会被 main.py 警告。
    jwt_secret: str = "dev-only-please-change-in-prod-32+chars"
    jwt_expire_minutes: int = 480

    # ---- 首次启动管理员引导(都为空则跳过) ----
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # ---- License ----
    # 私有化容器部署时,容器内 /etc/machine-id 是 ephemeral 的;
    # 客户可在 .env 设 MACHINE_FINGERPRINT_OVERRIDE 固定指纹。
    machine_fingerprint_override: str | None = None

    # ---- 加密 Key(用于 DB 中存储 LLM API key 等敏感字段) ----
    # 生产建议显式设置(`openssl rand -hex 32`)。
    # 留空则从 jwt_secret 派生 — 注意:JWT_SECRET 改动后历史加密数据无法解密。
    llm_key_encryption_key: str | None = None

    # ---- LLM Provider 默认配置(.env 兜底,DB 中已配则忽略) ----
    # 留空 LLM_API_KEY 走 mock 模式,demo 不依赖外部 API。
    # ``llm_default_model`` 直接写 LiteLLM 标识符(如 ``openai/gpt-4o-mini``)。
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_default_model: str = "openai/gpt-4o-mini"

    # ---- STT / TTS 默认 backend(M6 语音面试) ----
    # 取值:
    # - ``mock``               出厂默认,无外部依赖,demo / CI 用
    # - ``openai_compatible``  OpenAI 兼容协议(/v1/audio/transcriptions /
    #                          /v1/audio/speech),客户可指向 OpenAI 公网、
    #                          阿里云 dashscope OpenAI 兼容入口、字节火山、
    #                          腾讯云、或自建 whisper.cpp / vLLM
    # 切换 backend 时也需要补充对应 API 配置(下面的 stt_*/tts_* 字段)。
    stt_backend: str = "mock"
    tts_backend: str = "mock"

    # ---- STT openai_compatible 配置 ----
    # 留空时业务侧抛 STTError,不会静默 fallback 到 mock — 客户配错能尽早发现。
    # base_url 必须包含 ``/v1`` 后缀(如 ``https://api.openai.com/v1``);
    # 阿里云走 ``https://dashscope.aliyuncs.com/compatible-mode/v1``。
    stt_api_base: str | None = None
    stt_api_key: str | None = None
    stt_model: str = "whisper-1"
    stt_language: str = "zh"  # ISO-639-1,中文岗默认 zh,英文岗改 en

    # ---- TTS openai_compatible 配置 ----
    # 同上。OpenAI tts-1 / tts-1-hd 都是合法 model;阿里云 dashscope 用
    # ``cosyvoice-v1`` 等。
    tts_api_base: str | None = None
    tts_api_key: str | None = None
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"  # OpenAI 音色;阿里云用 ``longxiaochun`` 等
    tts_format: str = "mp3"   # mp3 / opus / aac / flac / wav

    # ---- 邮箱后台同步 ----
    # 每隔多少秒拉一次所有启用的邮箱账户。<=0 表示禁用后台同步, 仅支持 UI 手动触发。
    email_sync_interval_seconds: int = 300

    # ---- 分布式锁 (Redis SET NX PX) ----
    # 单账户锁 TTL: 覆盖一次 sync_account 最坏耗时 (200 封邮件 + 30MB 附件解析)
    # 同时被后台 loop 与 UI 手动触发 POST /sync 共用, 让二者互斥
    email_sync_lock_ttl_seconds: int = 600
    # 全局 loop 锁 TTL: 防止 multi-worker 多个 _email_sync_loop 同时进入
    # 0 = 运行时按 email_sync_interval_seconds * 2 计算 (留一倍余量)
    email_sync_loop_lock_ttl_seconds: int = 0

    # ---- Celery (简历解析异步化) ----
    # broker / result backend 留空时,默认复用 redis_url(broker -> /1, backend -> /2),
    # 与邮箱锁的 redis_url(/0)隔离,避免 key 冲突。
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    # 单租户场景默认串行(并发=1),避免本地解析吃 CPU 拖垮 web。
    # multi-tenant 部署时 worker 容器单独提并发,见 deploy/docker-compose.yml。
    celery_worker_concurrency: int = 1
    # 任务硬超时(秒):pdfplumber 在大文件上偶发卡死,worker 用 SIGKILL 兜底。
    celery_task_time_limit: int = 300
    # 软超时(秒):提前给任务一次清理机会(SoftTimeLimitExceeded)
    celery_task_soft_time_limit: int = 240
    # 测试 / 单进程开发场景:eager=true 让 task 同步在调用方执行(不需要 worker 容器)
    celery_task_always_eager: bool = False

    @property
    def effective_celery_broker_url(self) -> str:
        if self.celery_broker_url:
            return self.celery_broker_url
        # 默认派生:redis://host:port/1
        return self._derive_redis_db(1)

    @property
    def effective_celery_result_backend(self) -> str:
        if self.celery_result_backend:
            return self.celery_result_backend
        return self._derive_redis_db(2)

    def _derive_redis_db(self, db: int) -> str:
        """从 redis_url 派生不同 db 的 URL,容错末尾 /N。"""
        base = self.redis_url
        # 简单替换最后一个 /N(N 为数字),否则追加
        import re

        m = re.match(r"^(redis://[^/]+)(?:/\d+)?/?$", base)
        if m:
            return f"{m.group(1)}/{db}"
        return base.rstrip("/") + f"/{db}"


@lru_cache
def _get_settings() -> Settings:
    return Settings()


settings = _get_settings()
