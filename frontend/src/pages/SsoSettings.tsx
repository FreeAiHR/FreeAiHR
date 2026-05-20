import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ApiError, fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ROLE_HINT, ROLE_LABEL, type AppRole } from "@/lib/roles";

type SsoConfig = {
  tenant_id: string;
  enabled: boolean;
  provider_type: string;
  display_name: string;
  issuer_url: string | null;
  authorize_url: string | null;
  token_url: string | null;
  userinfo_url: string | null;
  client_id: string | null;
  client_secret_set: boolean;
  scopes: string;
  redirect_uri: string | null;
  auto_provision_enabled: boolean;
  default_role: AppRole;
  default_org_id: string | null;
  email_claim: string;
  name_claim: string;
  role_claim: string | null;
  org_claim: string | null;
  role_mapping_rules: Record<string, string>;
  org_mapping_rules: Record<string, string>;
  last_tested_at: string | null;
  last_status: string | null;
  last_error: string | null;
  updated_at: string;
};

type OrgNode = {
  id: string;
  name: string;
  kind: string;
  children: OrgNode[];
};

export function SsoSettings() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const { data: cfg, isLoading } = useQuery({
    queryKey: ["sso-config"],
    enabled: isAdmin,
    queryFn: () => fetchJSON<SsoConfig>("/api/sso/config"),
  });
  const { data: orgTree } = useQuery({
    queryKey: ["org-tree"],
    enabled: isAdmin,
    queryFn: () => fetchJSON<OrgNode[]>("/api/org/tree"),
  });

  if (!isAdmin) {
    return (
      <main className="p-8 max-w-[1200px] mx-auto w-full">
        <Empty
          icon={ShieldCheck}
          title="SSO 配置仅管理员可访问"
          description="SSO 接入信息含 client_secret 等敏感字段,仅管理员可见。"
        />
      </main>
    );
  }
  if (isLoading || !cfg) {
    return (
      <main className="p-8 max-w-[1200px] mx-auto w-full text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </main>
    );
  }
  return <SsoEditor cfg={cfg} orgTree={orgTree ?? []} qc={qc} />;
}

function SsoEditor({
  cfg,
  orgTree,
  qc,
}: {
  cfg: SsoConfig;
  orgTree: OrgNode[];
  qc: any;
}) {
  // 用本地 state 编辑,Save 时整批 PUT。client_secret 单独维护,空串=不变
  const [form, setForm] = useState({
    enabled: cfg.enabled,
    display_name: cfg.display_name,
    issuer_url: cfg.issuer_url ?? "",
    authorize_url: cfg.authorize_url ?? "",
    token_url: cfg.token_url ?? "",
    userinfo_url: cfg.userinfo_url ?? "",
    client_id: cfg.client_id ?? "",
    client_secret: "",
    scopes: cfg.scopes,
    redirect_uri: cfg.redirect_uri ?? "",
    auto_provision_enabled: cfg.auto_provision_enabled,
    default_role: cfg.default_role,
    default_org_id: cfg.default_org_id ?? "",
    email_claim: cfg.email_claim,
    name_claim: cfg.name_claim,
    role_claim: cfg.role_claim ?? "",
    org_claim: cfg.org_claim ?? "",
  });
  const [roleMappingText, setRoleMappingText] = useState(
    JSON.stringify(cfg.role_mapping_rules ?? {}, null, 2),
  );
  const [orgMappingText, setOrgMappingText] = useState(
    JSON.stringify(cfg.org_mapping_rules ?? {}, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // cfg 变化(比如保存后刷新)同步回 form
  useEffect(() => {
    setForm((prev) => ({
      ...prev,
      enabled: cfg.enabled,
      display_name: cfg.display_name,
      issuer_url: cfg.issuer_url ?? "",
      authorize_url: cfg.authorize_url ?? "",
      token_url: cfg.token_url ?? "",
      userinfo_url: cfg.userinfo_url ?? "",
      client_id: cfg.client_id ?? "",
      // 不覆盖 client_secret — 用户可能正在输入新值
      scopes: cfg.scopes,
      redirect_uri: cfg.redirect_uri ?? "",
      auto_provision_enabled: cfg.auto_provision_enabled,
      default_role: cfg.default_role,
      default_org_id: cfg.default_org_id ?? "",
      email_claim: cfg.email_claim,
      name_claim: cfg.name_claim,
      role_claim: cfg.role_claim ?? "",
      org_claim: cfg.org_claim ?? "",
    }));
    setRoleMappingText(JSON.stringify(cfg.role_mapping_rules ?? {}, null, 2));
    setOrgMappingText(JSON.stringify(cfg.org_mapping_rules ?? {}, null, 2));
  }, [cfg]);

  const orgOptions = useMemo(() => buildOrgOptions(orgTree), [orgTree]);

  const save = useMutation({
    mutationFn: async () => {
      let role_mapping_rules: Record<string, string> | null = null;
      let org_mapping_rules: Record<string, string> | null = null;
      try {
        role_mapping_rules = roleMappingText.trim()
          ? JSON.parse(roleMappingText)
          : null;
        org_mapping_rules = orgMappingText.trim()
          ? JSON.parse(orgMappingText)
          : null;
      } catch (e) {
        throw new Error(
          "角色 / 组织映射规则必须是 JSON 对象, " +
            (e instanceof Error ? e.message : "未知错误"),
        );
      }
      const body: Record<string, unknown> = {
        enabled: form.enabled,
        display_name: form.display_name,
        issuer_url: form.issuer_url || null,
        authorize_url: form.authorize_url || null,
        token_url: form.token_url || null,
        userinfo_url: form.userinfo_url || null,
        client_id: form.client_id || null,
        scopes: form.scopes,
        redirect_uri: form.redirect_uri || null,
        auto_provision_enabled: form.auto_provision_enabled,
        default_role: form.default_role,
        default_org_id: form.default_org_id || null,
        email_claim: form.email_claim,
        name_claim: form.name_claim,
        role_claim: form.role_claim || null,
        org_claim: form.org_claim || null,
        role_mapping_rules,
        org_mapping_rules,
      };
      if (form.client_secret) {
        body.client_secret = form.client_secret;
      }
      return fetchJSON<SsoConfig>("/api/sso/config", {
        method: "PUT",
        body: JSON.stringify(body),
      });
    },
    onSuccess: () => {
      setError(null);
      setSaved(true);
      setForm((f) => ({ ...f, client_secret: "" }));
      setTimeout(() => setSaved(false), 2500);
      qc.invalidateQueries({ queryKey: ["sso-config"] });
    },
    onError: (e: unknown) =>
      setError(
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "保存失败",
      ),
  });

  return (
    <main className="p-8 flex flex-col gap-6 max-w-[1200px] mx-auto w-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="font-heading font-semibold text-2xl text-[var(--color-text-primary)]">
            SSO 接入
          </h2>
          <p className="text-sm text-[var(--color-text-secondary)] font-body">
            配置企业身份系统(OIDC / OAuth2),启用后登录页会出现"企业统一登录"入口。本地账号密码登录始终保留作为兜底。
          </p>
        </div>
      </div>

      {error && (
        <div className="text-[13px] text-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 rounded-md font-body">
          {error}
        </div>
      )}
      {saved && (
        <div className="text-[13px] text-[var(--color-success)] bg-[var(--color-success-soft)] px-3 py-2 rounded-md font-body">
          已保存 SSO 配置
        </div>
      )}

      <Section title="基础" icon={ShieldCheck}>
        <ToggleRow
          label="启用 SSO 登录"
          description="开关关闭后,登录页只展示账号密码登录入口。"
          value={form.enabled}
          onChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
        />
        <Field label="登录入口显示名" hint="将展示在登录页按钮上,例如:公司 SSO">
          <Input
            value={form.display_name}
            onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            placeholder="企业统一登录"
          />
        </Field>
      </Section>

      <Section title="OIDC 端点" icon={Sparkles}>
        <Field label="Issuer URL" hint="可选;有些 IdP 提供 .well-known 自动发现">
          <Input
            value={form.issuer_url}
            onChange={(e) => setForm((f) => ({ ...f, issuer_url: e.target.value }))}
            placeholder="https://idp.company.com"
          />
        </Field>
        <Field label="Authorize URL" hint="授权端点,必填">
          <Input
            value={form.authorize_url}
            onChange={(e) => setForm((f) => ({ ...f, authorize_url: e.target.value }))}
            placeholder="https://idp.company.com/oauth2/authorize"
          />
        </Field>
        <Field label="Token URL" hint="换 token 端点,必填">
          <Input
            value={form.token_url}
            onChange={(e) => setForm((f) => ({ ...f, token_url: e.target.value }))}
            placeholder="https://idp.company.com/oauth2/token"
          />
        </Field>
        <Field label="UserInfo URL" hint="拉用户信息端点,必填">
          <Input
            value={form.userinfo_url}
            onChange={(e) => setForm((f) => ({ ...f, userinfo_url: e.target.value }))}
            placeholder="https://idp.company.com/userinfo"
          />
        </Field>
        <Field label="Client ID" hint="IdP 控制台给的 client_id">
          <Input
            value={form.client_id}
            onChange={(e) => setForm((f) => ({ ...f, client_id: e.target.value }))}
            placeholder="freehire-client"
          />
        </Field>
        <Field
          label="Client Secret"
          hint={
            cfg.client_secret_set
              ? "已保存(留空表示不变);如需更新请直接覆盖输入"
              : "首次配置必填"
          }
        >
          <Input
            type="password"
            value={form.client_secret}
            onChange={(e) => setForm((f) => ({ ...f, client_secret: e.target.value }))}
            placeholder={cfg.client_secret_set ? "••••••" : "请输入 client_secret"}
            autoComplete="new-password"
          />
        </Field>
        <Field label="Scope" hint="空格分隔,P0 默认 openid profile email">
          <Input
            value={form.scopes}
            onChange={(e) => setForm((f) => ({ ...f, scopes: e.target.value }))}
            placeholder="openid profile email"
          />
        </Field>
        <Field
          label="Redirect URI"
          hint="必填。需要把这个值填到 IdP 客户端配置里的回调白名单。"
        >
          <Input
            value={form.redirect_uri}
            onChange={(e) => setForm((f) => ({ ...f, redirect_uri: e.target.value }))}
            placeholder="https://hr.company.com/api/sso/oidc/callback"
          />
        </Field>
      </Section>

      <Section title="自动建号策略" icon={Building2}>
        <ToggleRow
          label="允许首次登录自动建号"
          description="关闭后,SSO 登录命中未注册账号时直接拒绝,需要管理员先在团队页创建。"
          value={form.auto_provision_enabled}
          onChange={(v) => setForm((f) => ({ ...f, auto_provision_enabled: v }))}
        />
        <Field label="默认角色" hint="未在 role_mapping_rules 命中的账号将分配此角色">
          <Select
            value={form.default_role}
            onChange={(v) => setForm((f) => ({ ...f, default_role: v as AppRole }))}
            options={(Object.keys(ROLE_LABEL) as AppRole[]).map((r) => ({
              value: r,
              label: ROLE_LABEL[r],
              hint: ROLE_HINT[r],
            }))}
          />
        </Field>
        <Field label="默认组织" hint="可选;未命中 org_mapping_rules 时使用">
          <Select
            value={form.default_org_id}
            onChange={(v) => setForm((f) => ({ ...f, default_org_id: v }))}
            options={[{ value: "", label: "不分配组织" }, ...orgOptions]}
          />
        </Field>
      </Section>

      <Section title="Claim 映射" icon={Sparkles}>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="邮箱 claim" hint="OIDC 默认 email">
            <Input
              value={form.email_claim}
              onChange={(e) => setForm((f) => ({ ...f, email_claim: e.target.value }))}
            />
          </Field>
          <Field label="姓名 claim" hint="OIDC 默认 name">
            <Input
              value={form.name_claim}
              onChange={(e) => setForm((f) => ({ ...f, name_claim: e.target.value }))}
            />
          </Field>
          <Field label="角色 claim 名称" hint="留空则不读取角色,统一用默认角色">
            <Input
              value={form.role_claim}
              onChange={(e) => setForm((f) => ({ ...f, role_claim: e.target.value }))}
              placeholder="role"
            />
          </Field>
          <Field label="组织 claim 名称" hint="留空则不读取组织,统一用默认组织">
            <Input
              value={form.org_claim}
              onChange={(e) => setForm((f) => ({ ...f, org_claim: e.target.value }))}
              placeholder="department"
            />
          </Field>
        </div>
        <Field
          label="角色映射规则"
          hint='JSON 对象,key = claim 值,value = 系统角色(admin / hr / interviewer / hiring_manager / viewer)'
        >
          <textarea
            value={roleMappingText}
            onChange={(e) => setRoleMappingText(e.target.value)}
            rows={5}
            spellCheck={false}
            className="w-full px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-[12px] font-mono focus:outline-none focus:border-[var(--color-text-primary)]"
            placeholder='{"hr_admin": "admin", "interviewer_role": "interviewer"}'
          />
        </Field>
        <Field
          label="组织映射规则"
          hint="JSON 对象,key = claim 值,value = 组织节点 id(可从团队与组织页复制)"
        >
          <textarea
            value={orgMappingText}
            onChange={(e) => setOrgMappingText(e.target.value)}
            rows={5}
            spellCheck={false}
            className="w-full px-3 py-2 rounded-md bg-white border border-[var(--color-border-subtle)] text-[12px] font-mono focus:outline-none focus:border-[var(--color-text-primary)]"
            placeholder='{"R&D": "<org_unit_id>"}'
          />
        </Field>
      </Section>

      <div className="flex justify-end gap-2">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "保存中…" : "保存配置"}
        </Button>
      </div>
    </main>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: any;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-6 flex flex-col gap-4">
      <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
        <Icon className="w-4 h-4" />
        <h3 className="font-heading font-semibold text-base">{title}</h3>
      </div>
      <div className="flex flex-col gap-4">{children}</div>
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[12px] font-body text-[var(--color-text-secondary)]">
        {label}
      </label>
      {children}
      {hint && (
        <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
          {hint}
        </span>
      )}
    </div>
  );
}

function ToggleRow({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 w-4 h-4 rounded border-[var(--color-border-subtle)]"
      />
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-[var(--color-text-primary)] font-body">
          {label}
        </span>
        <span className="text-[12px] text-[var(--color-text-secondary)] font-body">
          {description}
        </span>
      </div>
    </label>
  );
}

function buildOrgOptions(nodes: OrgNode[], depth = 0) {
  const out: { value: string; label: string; hint?: string }[] = [];
  for (const n of nodes) {
    out.push({
      value: n.id,
      label: `${"— ".repeat(depth)}${n.name}`,
      hint: n.kind,
    });
    out.push(...buildOrgOptions(n.children, depth + 1));
  }
  return out;
}
