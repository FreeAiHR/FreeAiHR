import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  HelpCircle,
  Loader2,
  Plug,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Empty } from "@/components/ui/empty";
import { useConfirm } from "@/components/ui/confirm";
import { ApiError, fetchJSON } from "@/lib/api";
import { formatRelative } from "@/lib/format";

type Provider = {
  id: string;
  name: string;
  base_url: string | null;
  api_key_masked: string;
  model: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

// 常见厂商的快速填充模板;点了之后帮用户预填 base_url + model,纯 UI 助手,不入库。
// 用户也可以完全跳过,直接手填三个字段。
type Preset = {
  label: string;
  baseUrl: string;
  model: string;
};

const PRESETS: Preset[] = [
  { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "openai/gpt-4o-mini" },
  {
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek/deepseek-chat",
  },
  {
    label: "通义千问",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "openai/qwen-plus",
  },
  {
    label: "Azure OpenAI",
    baseUrl: "https://<your-resource>.openai.azure.com",
    model: "azure/<deployment-name>",
  },
  {
    label: "本地 vLLM",
    baseUrl: "http://localhost:8000/v1",
    model: "openai/Qwen2.5-7B",
  },
];

export function LLMSettings() {
  const [editing, setEditing] = useState<Provider | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => fetchJSON<Provider[]>("/api/llm/providers"),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            LLM 配置
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            管理云端大模型 endpoint · API key · 同时激活一个生效 · 数据是否出网由你决定
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="w-4 h-4" />
          添加 Provider
        </Button>
      </div>

      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center text-sm text-[var(--color-text-tertiary)] font-body">
            加载中…
          </div>
        ) : !data || data.length === 0 ? (
          <Empty
            icon={Plug}
            title="还没有 LLM Provider"
            description="添加一个 Provider 后, AI 文本面试将走真实模型。未配置时系统使用本地 mock。"
            action={
              <Button onClick={() => setShowCreate(true)}>
                添加 Provider
              </Button>
            }
          />
        ) : (
          data.map((p, i) => (
            <ProviderRow
              key={p.id}
              provider={p}
              isLast={i === data.length - 1}
              onEdit={() => setEditing(p)}
            />
          ))
        )}
      </div>

      <ProviderModal
        open={showCreate || !!editing}
        provider={editing}
        onClose={() => {
          setShowCreate(false);
          setEditing(null);
        }}
      />
    </main>
  );
}

function ProviderRow({
  provider,
  isLast,
  onEdit,
}: {
  provider: Provider;
  isLast: boolean;
  onEdit: () => void;
}) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [testing, setTesting] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  const test = useMutation({
    mutationFn: () =>
      fetchJSON<{ ok: boolean; message: string; sample: string | null }>(
        `/api/llm/providers/${provider.id}/test`,
        { method: "POST" },
      ),
    onSuccess: (res) => setTesting({ ok: res.ok, message: res.message }),
    onError: (e: unknown) =>
      setTesting({
        ok: false,
        message: e instanceof ApiError ? e.message : "测试失败",
      }),
  });
  const activate = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/llm/providers/${provider.id}/activate`, {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-providers"] }),
  });
  const del = useMutation({
    mutationFn: () =>
      fetchJSON(`/api/llm/providers/${provider.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-providers"] }),
    onError: (e: unknown) =>
      alert(e instanceof ApiError ? e.message : "删除失败"),
  });

  return (
    <div
      className={`flex items-center gap-4 px-6 py-4 ${
        isLast ? "" : "border-b border-[var(--color-border-row)]"
      }`}
    >
      <div className="w-9 h-9 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center shrink-0">
        <Zap
          className={`w-4 h-4 ${
            provider.is_active
              ? "text-[var(--color-success)]"
              : "text-[var(--color-text-tertiary)]"
          }`}
        />
      </div>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="font-medium text-sm text-[var(--color-text-primary)] font-body truncate">
            {provider.name}
          </span>
          {provider.is_active && (
            <span className="px-2 py-0.5 rounded-md bg-[var(--color-success-soft)] text-[var(--color-success)] text-[11px] font-medium font-body">
              已激活
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[11px] text-[var(--color-text-tertiary)] font-mono">
          <span>{provider.model}</span>
          <span>·</span>
          <span>{provider.api_key_masked}</span>
          {provider.base_url && (
            <>
              <span>·</span>
              <span className="truncate max-w-[260px]">
                {provider.base_url}
              </span>
            </>
          )}
        </div>
        {testing && (
          <div
            className={`text-[11px] font-body mt-1 ${
              testing.ok
                ? "text-[var(--color-success)]"
                : "text-[var(--color-danger)]"
            }`}
          >
            {testing.ok ? "✓ " : "✗ "}
            {testing.message}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => {
            setTesting(null);
            test.mutate();
          }}
          disabled={test.isPending}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] transition-colors disabled:opacity-50"
        >
          {test.isPending ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin inline" />
          ) : (
            "测试"
          )}
        </button>
        {!provider.is_active && (
          <button
            onClick={() => activate.mutate()}
            disabled={activate.isPending}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-primary)] bg-[var(--color-bg-subtle)] hover:bg-[var(--color-border-subtle)] transition-colors"
          >
            激活
          </button>
        )}
        <button
          onClick={onEdit}
          className="px-3 py-1.5 rounded-md text-[12px] font-medium font-body text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-muted)] transition-colors"
        >
          编辑
        </button>
        {!provider.is_active && (
          <button
            onClick={async () => {
              if (
                await confirm({
                  title: "删除 LLM Provider?",
                  description: `将永久删除「${provider.name}」配置,API key 一并清除。激活中的 provider 不能删,需先激活其他 provider。`,
                  tone: "danger",
                  confirmLabel: "删除",
                })
              )
                del.mutate();
            }}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] p-1 transition-colors"
            aria-label="删除"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
      <span className="text-xs text-[var(--color-text-tertiary)] font-mono shrink-0 w-20 text-right">
        {formatRelative(provider.updated_at)}
      </span>
    </div>
  );
}

function ProviderModal({
  open,
  provider,
  onClose,
}: {
  open: boolean;
  provider: Provider | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = provider !== null;

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showModelHelp, setShowModelHelp] = useState(false);

  useEffect(() => {
    if (open) {
      if (provider) {
        setName(provider.name);
        setBaseUrl(provider.base_url ?? "");
        setApiKey("");
        setModel(provider.model);
      } else {
        setName("");
        setBaseUrl("");
        setApiKey("");
        setModel("");
      }
      setError(null);
      setShowModelHelp(false);
    }
  }, [open, provider]);

  function applyPreset(p: Preset) {
    setBaseUrl(p.baseUrl);
    setModel(p.model);
  }

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        base_url: baseUrl.trim() || null,
        api_key: apiKey.trim() || null,
        model: model.trim(),
      };
      const url = isEdit
        ? `/api/llm/providers/${provider!.id}`
        : "/api/llm/providers";
      return fetchJSON<Provider>(url, {
        method: isEdit ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-providers"] });
      onClose();
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "保存失败"),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || !model.trim() || !baseUrl.trim()) {
      setError("名称、Base URL、模型均不能为空");
      return;
    }
    if (!isEdit && !apiKey.trim()) {
      setError("首次创建必须提供 API key");
      return;
    }
    setError(null);
    save.mutate();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? "编辑 Provider" : "添加 LLM Provider"}
      description={
        isEdit
          ? "API key 留空表示保留原值;非空则覆盖"
          : "配置任意 LiteLLM 支持的模型 endpoint"
      }
      width={580}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button onClick={onSubmit} disabled={save.isPending}>
            <CheckCircle2 className="w-4 h-4" />
            {save.isPending ? "保存中…" : "保存"}
          </Button>
        </>
      }
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4 py-2">
        {!isEdit && (
          <div className="flex flex-col gap-1.5">
            <span className="text-[12px] text-[var(--color-text-tertiary)] font-body">
              快速填充(可选,点了帮你预填 Base URL + 模型)
            </span>
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className="px-2.5 py-1 rounded-md text-[12px] font-body text-[var(--color-text-secondary)] bg-[var(--color-bg-subtle)] hover:bg-[var(--color-border-subtle)] transition-colors"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <Input
          label="显示名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例:DeepSeek 生产"
          required
        />
        <Input
          label="Base URL"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.deepseek.com"
          required
        />
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-medium text-[#374151] font-body">
              模型 (LiteLLM 标识符)
            </span>
            <button
              type="button"
              onClick={() => setShowModelHelp((v) => !v)}
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors"
              aria-label="模型字段说明"
              aria-expanded={showModelHelp}
            >
              <HelpCircle className="w-3.5 h-3.5" />
            </button>
          </div>
          <Input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="openai/gpt-4o-mini"
            required
          />
          {showModelHelp && <ModelHelp />}
        </div>
        <Input
          label={isEdit ? "API key (留空则不变)" : "API key"}
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={isEdit ? provider?.api_key_masked ?? "" : "sk-..."}
          autoComplete="off"
        />
        <div className="flex gap-2 items-start text-[12px] bg-[var(--color-warning-soft)] border border-[var(--color-warning-stroke)] text-[var(--color-warning-text)] rounded-md px-3 py-2 font-body">
          <span aria-hidden>⚠️</span>
          <span>
            Base URL 决定数据流向 — 指向公网服务时, 简历内容、面试问答会发送给该供应商。
            如需数据完全不出公司, 请填写贵公司内网部署的模型服务地址(自建网关 / vLLM / Ollama 等)。
          </span>
        </div>
        {error && (
          <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
            {error}
          </div>
        )}
      </form>
    </Modal>
  );
}

function ModelHelp() {
  return (
    <div className="text-[12px] bg-[var(--color-bg-subtle)] border border-[var(--color-border-subtle)] rounded-md px-3 py-2.5 font-body text-[var(--color-text-secondary)] flex flex-col gap-1.5">
      <div>
        模型字段直接写 <b>LiteLLM 模型标识符</b>,后端不做拼接,LiteLLM 按前缀路由到对应厂商。
      </div>
      <div className="font-mono text-[11.5px] text-[var(--color-text-primary)] flex flex-col gap-0.5">
        <div>
          OpenAI: <code>openai/gpt-4o-mini</code> (Base URL 留空)
        </div>
        <div>
          DeepSeek: <code>deepseek/deepseek-chat</code> (Base URL ={" "}
          <code>https://api.deepseek.com</code>)
        </div>
        <div>
          通义千问: <code>openai/qwen-plus</code> (Base URL = DashScope 兼容地址)
        </div>
        <div>
          Azure: <code>azure/&lt;deployment&gt;</code> (Base URL = Azure 实例地址)
        </div>
        <div>
          本地 vLLM / 任意 OpenAI 兼容: <code>openai/&lt;model&gt;</code>
        </div>
      </div>
      <div>
        完整列表:{" "}
        <a
          href="https://docs.litellm.ai/docs/providers"
          target="_blank"
          rel="noreferrer"
          className="underline text-[var(--color-text-primary)]"
        >
          LiteLLM Providers 文档
        </a>
      </div>
    </div>
  );
}
