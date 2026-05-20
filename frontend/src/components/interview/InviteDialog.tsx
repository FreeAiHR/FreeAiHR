import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Copy, Check, RefreshCw, XCircle } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { fetchJSON } from "@/lib/api";

/**
 * 发起远程面试成功后的链接 / 邀请展示对话框。
 *
 * 重要:明文 token 只在生成响应里出现一次(DB 只存 sha256),所以这里把
 * 完整 URL 高亮 + 提供复制按钮 + 提示 HR "关闭后不再展示,请立即复制"。
 *
 * 进阶能力(重生 token / 撤销邀请)在调用方传入 onResend / onCancel 时启用,
 * 调用方负责重新弹这个对话框展示新 token,或者关掉弹窗。
 */
export function InviteDialog({
  open,
  invite,
  onClose,
  onResend,
  onCancel,
}: {
  open: boolean;
  invite: {
    token: string;
    invite_url: string;
    expires_at: string;
    delivery: string;
    notify_email: string | null;
  } | null;
  onClose: () => void;
  onResend?: () => void;
  onCancel?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  // 拉 SMTP 配置判断"自动邮件"是否真的能用 — 不能用时给 HR 显眼引导。
  // 仅当对话框打开 + delivery 包含 email 时才拉,避免无意义请求。
  const wantsEmail =
    !!invite && (invite.delivery === "email" || invite.delivery === "both");
  const { data: smtp } = useQuery({
    queryKey: ["smtp-account"],
    queryFn: () =>
      fetchJSON<{ is_enabled: boolean } | null>("/api/smtp/account"),
    enabled: open && wantsEmail,
  });

  if (!invite) return null;

  const fullUrl = new URL(invite.invite_url, window.location.origin).toString();
  const expiresLocal = new Date(invite.expires_at).toLocaleString("zh-CN");

  function copyUrl() {
    // navigator.clipboard 仅在 secure context (HTTPS / localhost) 可用,
    // 私有化部署常走 http://内网 IP,这里走 execCommand 兜底。
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    };
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(fullUrl).then(done).catch(legacyCopy);
      return;
    }
    legacyCopy();

    function legacyCopy() {
      const ta = document.createElement("textarea");
      ta.value = fullUrl;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        if (document.execCommand("copy")) done();
      } catch {
        /* 静默失败 — 用户可以手动选中输入框文本复制 */
      } finally {
        document.body.removeChild(ta);
      }
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="邀请已生成"
      description="将以下链接发给候选人。链接关闭后不再展示,请立即复制保存。"
      width={620}
      footer={
        <>
          {onCancel && (
            <Button
              variant="secondary"
              onClick={onCancel}
              className="!text-[var(--color-danger)] !border-[var(--color-danger-soft)]"
            >
              <XCircle className="w-4 h-4" />
              撤销邀请
            </Button>
          )}
          {onResend && (
            <Button variant="secondary" onClick={onResend}>
              <RefreshCw className="w-4 h-4" />
              重新生成
            </Button>
          )}
          <Button onClick={onClose}>完成</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 py-2">
        <div className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-[#374151] font-body">
            候选人链接
          </span>
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={fullUrl}
              onFocus={(e) => e.currentTarget.select()}
              className="flex-1 px-3 py-2 rounded-lg bg-[var(--color-bg-muted)] border border-[var(--color-border-subtle)] text-[13px] font-mono"
            />
            <Button
              variant="secondary"
              onClick={copyUrl}
              className="!px-3 !py-2"
              aria-label="复制链接"
            >
              {copied ? (
                <Check className="w-4 h-4 text-[var(--color-success)]" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 text-[12px] font-body">
          <div className="flex flex-col gap-1">
            <span className="text-[var(--color-text-tertiary)]">截止时间</span>
            <span className="text-[var(--color-text-primary)]">
              {expiresLocal}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-[var(--color-text-tertiary)]">送达方式</span>
            <span className="text-[var(--color-text-primary)]">
              {deliveryLabel(invite.delivery)}
              {invite.notify_email ? ` → ${invite.notify_email}` : ""}
            </span>
          </div>
        </div>

        {/* 自动邮件状态提示 */}
        {wantsEmail &&
          (smtp && smtp.is_enabled ? (
            <div className="text-[12px] bg-[var(--color-info-soft)] text-[var(--color-info)] p-3 rounded-lg font-body">
              邀请邮件已交由后台发送至 {invite.notify_email}。如候选人未收到,
              请复制链接手动发送(也可能落入垃圾邮件)。
            </div>
          ) : (
            <div className="text-[12px] bg-[var(--color-warning-soft,#FFF7E6)] text-[var(--color-warning,#B45309)] p-3 rounded-lg font-body border border-[#FCD34D33]">
              当前未配置 SMTP 发件,系统不会自动发邮件。
              请前往{" "}
              <Link
                to="/settings/smtp"
                onClick={onClose}
                className="underline font-medium"
              >
                SMTP 发件
              </Link>{" "}
              页面配置,或直接复制链接手动发送给候选人。
            </div>
          ))}
      </div>
    </Modal>
  );
}

function deliveryLabel(d: string): string {
  if (d === "email") return "自动邮件";
  if (d === "both") return "邮件 + 链接";
  return "仅链接";
}
