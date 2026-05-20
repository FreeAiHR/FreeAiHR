import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Mic, Trash2, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/**
 * 语音 Provider 配置(管理员专用,M6 V5)。
 *
 * 让 admin 在浏览器里配 STT/TTS,无需改 .env 重启容器。
 * 单表单(每租户最多一条),提交即 upsert。
 *
 * 与现有 .env 的关系:
 * - DB 中存在该租户行 + is_enabled=true → 用 DB 配置(走"DB 来源"badge)
 * - 否则 → 走 .env(走"环境变量来源"badge)
 *
 * Mock 模式不需要 api_base / api_key,适合 demo / CI。客户切到真实厂商时
 * 改 backend=openai_compatible 并填好 base_url + key。
 */

type VoiceAccount = {
  id: string;
  stt_backend: "mock" | "openai_compatible";
  stt_api_base: string | null;
  stt_api_key_masked: string | null;
  stt_model: string;
  stt_language: string;
  tts_backend: "mock" | "openai_compatible";
  tts_api_base: string | null;
  tts_api_key_masked: string | null;
  tts_model: string;
  tts_voice: string;
  tts_format: string;
  is_enabled: boolean;
  last_tested_at: string | null;
  last_status: string | null;
  last_error: string | null;
  effective_stt_source: "db" | "env";
  effective_tts_source: "db" | "env";
};

type TestResult = { ok: boolean; message: string };

// 厂商预设 — 让 admin 一键填上 base_url + 推荐 model,而不是 Google 半天
const PROVIDER_PRESETS: {
  label: string;
  stt_api_base: string;
  stt_model: string;
  tts_api_base: string;
  tts_model: string;
  tts_voice: string;
}[] = [
  {
    label: "OpenAI 公网",
    stt_api_base: "https://api.openai.com/v1",
    stt_model: "whisper-1",
    tts_api_base: "https://api.openai.com/v1",
    tts_model: "tts-1",
    tts_voice: "alloy",
  },
  {
    label: "阿里云 dashscope (推荐国内)",
    stt_api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    stt_model: "paraformer-v2",
    tts_api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    tts_model: "cosyvoice-v1",
    tts_voice: "longxiaochun",
  },
  {
    label: "字节火山引擎 ARK",
    stt_api_base: "https://ark.cn-beijing.volces.com/api/v3",
    stt_model: "whisper-1",
    tts_api_base: "https://ark.cn-beijing.volces.com/api/v3",
    tts_model: "tts-1",
    tts_voice: "alloy",
  },
  {
    label: "自建 vLLM / whisper.cpp",
    stt_api_base: "http://内网:8000/v1",
    stt_model: "whisper-large-v3",
    tts_api_base: "http://内网:8000/v1",
    tts_model: "tts-1",
    tts_voice: "alloy",
  },
];

export function VoiceSettings() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({
    queryKey: ["voice-account"],
    queryFn: () => fetchJSON<VoiceAccount | null>("/api/voice/account"),
  });

  const isEdit = data !== null && data !== undefined;

  // STT 表单
  const [sttBackend, setSttBackend] = useState<"mock" | "openai_compatible">(
    "mock",
  );
  const [sttApiBase, setSttApiBase] = useState("");
  const [sttApiKey, setSttApiKey] = useState("");
  const [sttModel, setSttModel] = useState("whisper-1");
  const [sttLanguage, setSttLanguage] = useState("zh");

  // TTS 表单
  const [ttsBackend, setTtsBackend] = useState<"mock" | "openai_compatible">(
    "mock",
  );
  const [ttsApiBase, setTtsApiBase] = useState("");
  const [ttsApiKey, setTtsApiKey] = useState("");
  const [ttsModel, setTtsModel] = useState("tts-1");
  const [ttsVoice, setTtsVoice] = useState("alloy");
  const [ttsFormat, setTtsFormat] = useState("mp3");

  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<TestResult | null>(null);

  useEffect(() => {
    if (data) {
      setSttBackend(data.stt_backend);
      setSttApiBase(data.stt_api_base || "");
      setSttApiKey("");
      setSttModel(data.stt_model);
      setSttLanguage(data.stt_language);
      setTtsBackend(data.tts_backend);
      setTtsApiBase(data.tts_api_base || "");
      setTtsApiKey("");
      setTtsModel(data.tts_model);
      setTtsVoice(data.tts_voice);
      setTtsFormat(data.tts_format);
      setEnabled(data.is_enabled);
    }
    // Intentionally hydrate only when the saved account identity changes;
    // same-id refetches must not wipe unsaved draft fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.id]);

  const save = useMutation({
    mutationFn: () =>
      fetchJSON<VoiceAccount>("/api/voice/account", {
        method: "PUT",
        body: JSON.stringify({
          stt_backend: sttBackend,
          stt_api_base: sttApiBase.trim() || null,
          stt_api_key: sttApiKey || undefined, // 留空保留旧密文
          stt_model: sttModel.trim(),
          stt_language: sttLanguage.trim(),
          tts_backend: ttsBackend,
          tts_api_base: ttsApiBase.trim() || null,
          tts_api_key: ttsApiKey || undefined,
          tts_model: ttsModel.trim(),
          tts_voice: ttsVoice.trim(),
          tts_format: ttsFormat,
          is_enabled: enabled,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voice-account"] });
      setError(null);
      setFeedback({ ok: true, message: "已保存" });
      setSttApiKey("");
      setTtsApiKey("");
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "保存失败"),
  });

  const testStt = useMutation({
    mutationFn: () =>
      fetchJSON<TestResult>("/api/voice/account/test-stt", { method: "POST" }),
    onSuccess: (res) => {
      setFeedback(res);
      qc.invalidateQueries({ queryKey: ["voice-account"] });
    },
    onError: (e: unknown) =>
      setFeedback({
        ok: false,
        message: e instanceof ApiError ? e.message : "测试失败",
      }),
  });

  const testTts = useMutation({
    mutationFn: () =>
      fetchJSON<TestResult>("/api/voice/account/test-tts", { method: "POST" }),
    onSuccess: (res) => {
      setFeedback(res);
      qc.invalidateQueries({ queryKey: ["voice-account"] });
    },
    onError: (e: unknown) =>
      setFeedback({
        ok: false,
        message: e instanceof ApiError ? e.message : "测试失败",
      }),
  });

  const remove = useMutation({
    mutationFn: () =>
      fetchJSON("/api/voice/account", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["voice-account"] });
      setFeedback({ ok: true, message: "已删除,语音功能将走 .env 默认" });
    },
  });

  function applyPreset(preset: (typeof PROVIDER_PRESETS)[number]) {
    setSttBackend("openai_compatible");
    setSttApiBase(preset.stt_api_base);
    setSttModel(preset.stt_model);
    setTtsBackend("openai_compatible");
    setTtsApiBase(preset.tts_api_base);
    setTtsModel(preset.tts_model);
    setTtsVoice(preset.tts_voice);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setFeedback(null);
    save.mutate();
  }

  async function onDelete() {
    if (
      !(await confirm({
        title: "删除语音 Provider 配置?",
        description: "删除后语音面试会回退到 .env 默认配置(通常是 mock)。",
        tone: "danger",
        confirmLabel: "删除",
      }))
    )
      return;
    remove.mutate();
  }

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <header className="flex flex-col gap-1.5">
        <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
          语音 Provider 配置
        </h2>
        <p className="text-sm text-[var(--color-text-secondary)] font-body">
          配置语音面试用的 STT(语音转文字)和 TTS(文字转语音)服务。
          支持 OpenAI 兼容协议:阿里云 dashscope、字节火山引擎、自建 vLLM 等。
        </p>
      </header>

      {/* 来源指示 */}
      {data && (
        <div className="flex items-center gap-2 text-[12px] font-body">
          <SourceBadge label="STT" source={data.effective_stt_source} />
          <SourceBadge label="TTS" source={data.effective_tts_source} />
          {data.last_tested_at && (
            <span className="text-[var(--color-text-tertiary)]">
              · 上次测试 {formatRelative(data.last_tested_at)}
              {data.last_status && (
                <span
                  className={`ml-1.5 ${
                    data.last_status === "ok"
                      ? "text-[var(--color-success)]"
                      : "text-[var(--color-danger)]"
                  }`}
                >
                  ({data.last_status})
                </span>
              )}
            </span>
          )}
        </div>
      )}

      {!data && !isLoading && (
        <div className="bg-[var(--color-info-soft)] text-[var(--color-info)] px-4 py-3 rounded-lg text-[13px] font-body">
          尚未配置。当前语音面试走 .env 默认(通常是 <code>mock</code>),
          仅用于 demo。要正式启用语音,请填下方表单。
        </div>
      )}

      {/* 厂商预设 */}
      <div className="flex flex-col gap-2">
        <span className="text-[13px] text-[var(--color-text-secondary)] font-body">
          快速填充(选一个厂商,会预填 base_url 和推荐 model)
        </span>
        <div className="flex flex-wrap gap-2">
          {PROVIDER_PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => applyPreset(p)}
              className="px-3 py-1.5 rounded-full text-[12px] font-body border border-[var(--color-border-subtle)] bg-white hover:bg-[var(--color-bg-subtle)] transition-colors"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-6 bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6"
      >
        {/* STT */}
        <Section icon={<Mic className="w-4 h-4" />} title="STT(语音转文字)">
          <Field label="后端">
            <Select
              value={sttBackend}
              onChange={(v) =>
                setSttBackend(v as "mock" | "openai_compatible")
              }
              options={[
                {
                  value: "mock",
                  label: "mock",
                  hint: "无外部依赖,demo 用",
                },
                {
                  value: "openai_compatible",
                  label: "openai_compatible",
                  hint: "对接 OpenAI / 阿里 / 字节 / 自建",
                },
              ]}
            />
          </Field>
          {sttBackend === "openai_compatible" && (
            <>
              <Field label="API Base URL">
                <Input
                  value={sttApiBase}
                  onChange={(e) => setSttApiBase(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                  required
                />
              </Field>
              <Field
                label={`API Key${
                  isEdit && data?.stt_api_key_masked
                    ? `(当前 ${data.stt_api_key_masked},留空保留)`
                    : ""
                }`}
              >
                <Input
                  type="password"
                  value={sttApiKey}
                  onChange={(e) => setSttApiKey(e.target.value)}
                  placeholder="sk-xxx 或 dashscope key"
                  autoComplete="new-password"
                />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Model">
                  <Input
                    value={sttModel}
                    onChange={(e) => setSttModel(e.target.value)}
                    placeholder="whisper-1 / paraformer-v2"
                  />
                </Field>
                <Field label="Language">
                  <Select
                    value={sttLanguage}
                    onChange={setSttLanguage}
                    options={[
                      { value: "zh", label: "中文" },
                      { value: "en", label: "英文" },
                    ]}
                  />
                </Field>
              </div>
            </>
          )}
        </Section>

        <hr className="border-[var(--color-border-row)]" />

        {/* TTS */}
        <Section
          icon={<Volume2 className="w-4 h-4" />}
          title="TTS(文字转语音)"
        >
          <Field label="后端">
            <Select
              value={ttsBackend}
              onChange={(v) =>
                setTtsBackend(v as "mock" | "openai_compatible")
              }
              options={[
                {
                  value: "mock",
                  label: "mock",
                  hint: "440Hz 正弦波,demo 用",
                },
                { value: "openai_compatible", label: "openai_compatible" },
              ]}
            />
          </Field>
          {ttsBackend === "openai_compatible" && (
            <>
              <Field label="API Base URL">
                <Input
                  value={ttsApiBase}
                  onChange={(e) => setTtsApiBase(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                  required
                />
              </Field>
              <Field
                label={`API Key${
                  isEdit && data?.tts_api_key_masked
                    ? `(当前 ${data.tts_api_key_masked},留空保留)`
                    : ""
                }`}
              >
                <Input
                  type="password"
                  value={ttsApiKey}
                  onChange={(e) => setTtsApiKey(e.target.value)}
                  placeholder="sk-xxx"
                  autoComplete="new-password"
                />
              </Field>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Model">
                  <Input
                    value={ttsModel}
                    onChange={(e) => setTtsModel(e.target.value)}
                    placeholder="tts-1 / cosyvoice-v1"
                  />
                </Field>
                <Field label="Voice">
                  <Input
                    value={ttsVoice}
                    onChange={(e) => setTtsVoice(e.target.value)}
                    placeholder="alloy / longxiaochun"
                  />
                </Field>
                <Field label="Format">
                  <Select
                    value={ttsFormat}
                    onChange={setTtsFormat}
                    options={[
                      { value: "mp3", label: "mp3" },
                      { value: "opus", label: "opus" },
                      { value: "aac", label: "aac" },
                      { value: "flac", label: "flac" },
                      { value: "wav", label: "wav" },
                    ]}
                  />
                </Field>
              </div>
            </>
          )}
        </Section>

        <hr className="border-[var(--color-border-row)]" />

        <label className="flex items-center gap-2 text-[13px] font-body text-[var(--color-text-primary)] cursor-pointer">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用本配置(取消勾选会回退到 .env 默认)
        </label>

        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}

        {feedback && (
          <div
            className={`text-[13px] px-3 py-2 rounded-md font-body ${
              feedback.ok
                ? "text-[var(--color-success)] bg-[var(--color-success-soft)]"
                : "text-[var(--color-danger)] bg-[var(--color-danger-soft)]"
            }`}
          >
            {feedback.ok ? (
              <CheckCircle2 className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
            ) : null}
            {feedback.message}
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            {isEdit ? "保存修改" : "创建"}
          </Button>
          {isEdit && (
            <>
              <Button
                type="button"
                variant="secondary"
                onClick={() => testStt.mutate()}
                disabled={testStt.isPending}
              >
                <Mic className="w-4 h-4" />
                {testStt.isPending ? "测试中…" : "测试 STT"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => testTts.mutate()}
                disabled={testTts.isPending}
              >
                <Volume2 className="w-4 h-4" />
                {testTts.isPending ? "测试中…" : "测试 TTS"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={onDelete}
                disabled={remove.isPending}
                className="!text-[var(--color-danger)]"
              >
                <Trash2 className="w-4 h-4" />
                删除
              </Button>
            </>
          )}
        </div>
      </form>
    </main>
  );
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className="font-heading font-semibold text-[15px] flex items-center gap-2 text-[var(--color-text-primary)]">
        {icon}
        {title}
      </h3>
      <div className="flex flex-col gap-3">{children}</div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-[#374151] font-body">
        {label}
      </span>
      {children}
    </div>
  );
}

function SourceBadge({
  label,
  source,
}: {
  label: string;
  source: "db" | "env";
}) {
  const cls =
    source === "db"
      ? "bg-[var(--color-success-soft)] text-[var(--color-success)]"
      : "bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]";
  return (
    <span className={`px-2 py-0.5 rounded-md ${cls}`}>
      {label} 来源:{source === "db" ? "数据库" : "环境变量(.env)"}
    </span>
  );
}
