import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth";

/**
 * SSO 回跳落地页。
 *
 * 后端 /api/sso/oidc/callback 把结果放在 URL fragment 而不是 query string —
 * 这样 token 不会被中间反向代理 / nginx access_log 记录。fragment 永远
 * 不会发到服务端。
 *
 * 期望的 URL 格式:
 *   /login/sso-callback#token=<jwt>
 *   /login/sso-callback#error=<code>
 */
const ERROR_MESSAGES: Record<string, string> = {
  sso_disabled: "管理员尚未启用 SSO 登录",
  sso_misconfigured: "SSO 配置不完整,请联系管理员",
  state_invalid: "登录状态校验失败,请重新尝试",
  missing_params: "回调参数缺失,请重新尝试登录",
  token_exchange_failed: "向身份系统换取凭据失败,请稍后重试",
  userinfo_failed: "无法获取身份系统返回的用户信息",
  email_missing: "身份系统未返回邮箱,无法登录",
  user_not_provisioned: "账号尚未在系统中注册,请联系管理员开通",
  user_disabled: "账号已禁用,请联系管理员",
  no_tenant: "系统尚未初始化租户,无法登录",
  multi_tenant_unsupported: "当前部署不支持多租户 SSO 直接登录",
  internal_error: "登录过程出现错误,请重试或联系管理员",
  idp_error: "身份系统返回错误,请稍后重试",
};

export function SsoCallback() {
  const { user, consumeToken } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    const params = new URLSearchParams(hash);
    const token = params.get("token");
    const errCode = params.get("error");

    if (errCode) {
      setError(errCode);
      setLoading(false);
      return;
    }
    if (!token) {
      setError("missing_params");
      setLoading(false);
      return;
    }

    consumeToken(token)
      .then(() => {
        // 清掉 fragment 再跳首页,避免地址栏里残留 token
        window.history.replaceState({}, "", "/");
        navigate("/", { replace: true });
      })
      .catch(() => {
        setError("internal_error");
        setLoading(false);
      });
  }, [consumeToken, navigate]);

  // 已经登录(比如刷新此页) → 直接回首页
  if (user && !error) return <Navigate to="/" replace />;

  return (
    <main className="h-full grid place-items-center bg-[var(--color-bg-canvas)] p-4">
      <div className="w-[440px] max-w-full bg-white rounded-2xl border border-[var(--color-border-subtle)] shadow-[0_4px_24px_rgba(15,17,21,0.06)] p-10 flex flex-col gap-5">
        {loading ? (
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-8 h-8 text-[var(--color-text-tertiary)] animate-spin" />
            <span className="text-sm text-[var(--color-text-secondary)] font-body">
              正在完成 SSO 登录…
            </span>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 text-[var(--color-danger)]">
              <AlertTriangle className="w-5 h-5" />
              <span className="font-heading font-semibold text-base">SSO 登录失败</span>
            </div>
            <div className="text-[13px] text-[var(--color-text-secondary)] font-body">
              {(error && ERROR_MESSAGES[error]) || `未知错误:${error ?? "internal_error"}`}
            </div>
            <button
              type="button"
              onClick={() => navigate("/login", { replace: true })}
              className="self-start text-sm text-[var(--color-text-primary)] font-medium hover:underline font-body"
            >
              ← 返回登录页
            </button>
          </>
        )}
      </div>
    </main>
  );
}
