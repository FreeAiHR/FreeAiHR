import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  FileText,
  Briefcase,
  Video,
  ChartBar,
  KeyRound,
  Mail,
  Mic,
  Plug,
  Send,
  ShieldCheck,
  Shuffle,
  Sparkles,
  BookOpen,
  TrendingUp,
  Users,
  Ellipsis,
  LogOut,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { PERM, formatRoleLabel, type AppPermission } from "@/lib/roles";

type NavDef = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 仅 admin 可见(快捷:相当于 require_role('admin'))。 */
  adminOnly?: boolean;
  /** 需要至少一个该权限位才显示。优先级低于 adminOnly。 */
  permission?: AppPermission;
};

const mainNav: NavDef[] = [
  { to: "/", label: "概览", icon: LayoutDashboard },
  { to: "/resumes", label: "简历库", icon: FileText },
  { to: "/talents", label: "人才库", icon: Users },
  { to: "/jobs", label: "岗位", icon: Briefcase },
  { to: "/question-sets", label: "面试题集", icon: Sparkles },
  { to: "/question-library", label: "题库", icon: BookOpen },
  { to: "/interviews", label: "面试", icon: Video, permission: PERM.VIEW_REPORTS },
  { to: "/reports", label: "报告", icon: ChartBar, permission: PERM.VIEW_REPORTS },
  { to: "/analytics", label: "数据分析", icon: TrendingUp, permission: PERM.VIEW_REPORTS },
];

// 系统设置类菜单 —— 默认需要 manage_settings(只有 admin 持有)。
// adminOnly 单独标的项是更严格的"硬限 admin"(SSO / 审计)。
const sysNav: NavDef[] = [
  { to: "/settings/llm", label: "LLM 配置", icon: Plug, permission: PERM.MANAGE_SETTINGS },
  { to: "/settings/email", label: "邮箱拉取", icon: Mail, permission: PERM.MANAGE_SETTINGS },
  { to: "/settings/smtp", label: "SMTP 发件", icon: Send, permission: PERM.MANAGE_SETTINGS },
  { to: "/settings/voice", label: "语音 STT/TTS", icon: Mic, permission: PERM.MANAGE_SETTINGS },
  { to: "/settings/license", label: "License 设置", icon: KeyRound, permission: PERM.MANAGE_SETTINGS },
  { to: "/settings/team", label: "团队", icon: Users, permission: PERM.MANAGE_TEAM },
  { to: "/settings/sso", label: "SSO 接入", icon: Shuffle, adminOnly: true },
  { to: "/settings/audit", label: "审计中心", icon: ShieldCheck, adminOnly: true },
];

function NavRow({ to, label, icon: Icon }: NavDef) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors font-body",
          isActive
            ? "bg-[var(--color-bg-subtle)] text-[var(--color-text-primary)] font-semibold"
            : "text-[var(--color-text-secondary)] font-medium hover:bg-[var(--color-bg-muted)]",
        )
      }
    >
      <Icon className="w-4 h-4" />
      <span>{label}</span>
    </NavLink>
  );
}

export function Sidebar() {
  const { user, logout, hasPermission } = useAuth();
  const navigate = useNavigate();
  const [openMenu, setOpenMenu] = useState(false);
  const initials = user?.email.slice(0, 2).toUpperCase() ?? "—";
  const isAdmin = user?.role === "admin";

  const filterNav = (items: NavDef[]) =>
    items.filter((it) => {
      if (it.adminOnly) return isAdmin;
      if (it.permission) return hasPermission(it.permission);
      return true;
    });

  const visibleMainNav = filterNav(mainNav);
  const visibleSysNav = filterNav(sysNav);

  return (
    <aside className="w-[240px] h-full bg-white border-r border-[var(--color-border-subtle)] flex flex-col px-4 py-5 shrink-0">
      <div className="flex items-center gap-2.5 px-2 py-2 shrink-0">
        <div className="w-7 h-7 rounded-md bg-[var(--color-accent)] text-white font-heading font-bold text-sm flex items-center justify-center">
          F
        </div>
        <span className="font-heading font-semibold text-base">Free-Hire</span>
      </div>
      <nav className="flex-1 overflow-y-auto flex flex-col gap-1 mt-4 -mx-1 px-1">
        <div className="px-3 py-1 text-[11px] font-medium text-[var(--color-text-tertiary)] tracking-wider font-body">
          工作台
        </div>
        {visibleMainNav.map((it) => (
          <NavRow key={it.to} {...it} />
        ))}
        <div className="h-4" />
        <div className="px-3 py-1 text-[11px] font-medium text-[var(--color-text-tertiary)] tracking-wider font-body">
          系统
        </div>
        {visibleSysNav.map((it) => (
          <NavRow key={it.to} {...it} />
        ))}
      </nav>

      {/* 用户区 — 点击展开"登出"菜单 */}
      <div className="relative shrink-0 mt-1">
        <button
          type="button"
          onClick={() => setOpenMenu((v) => !v)}
          className="flex items-center gap-2.5 px-2 py-2 rounded-lg w-full hover:bg-[var(--color-bg-muted)] transition-colors text-left"
        >
          <div className="w-8 h-8 rounded-full bg-[var(--color-bg-subtle)] flex items-center justify-center text-xs font-heading font-semibold">
            {initials}
          </div>
          <div className="flex flex-col flex-1 min-w-0">
            <span className="text-[13px] font-medium truncate font-body">
              {user?.email ?? "未登录"}
            </span>
            <span className="text-[11px] text-[var(--color-text-tertiary)] font-body">
              {formatRoleLabel(user?.role)}
            </span>
          </div>
          <Ellipsis className="w-4 h-4 text-[var(--color-text-tertiary)] shrink-0" />
        </button>
        {openMenu && (
          <div
            className="absolute bottom-[calc(100%+6px)] left-0 right-0 z-40 bg-white rounded-xl border border-[var(--color-border-subtle)] shadow-[0_8px_24px_rgba(15,17,21,0.08)] overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => {
                setOpenMenu(false);
                logout();
                navigate("/login", { replace: true });
              }}
              className="flex items-center gap-2 w-full px-4 py-2.5 text-[13px] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-muted)] font-body transition-colors"
            >
              <LogOut className="w-4 h-4" />
              登出
            </button>
          </div>
        )}
      </div>

      {openMenu && (
        <div
          className="fixed inset-0 z-30"
          onClick={() => setOpenMenu(false)}
          aria-hidden
        />
      )}
    </aside>
  );
}
