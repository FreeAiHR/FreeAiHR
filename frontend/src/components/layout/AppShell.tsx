import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useAuth } from "@/lib/auth";

const TITLE_BY_PATH: Record<string, string> = {
  "/": "概览",
  "/resumes": "简历库",
  "/talents": "人才库",
  "/jobs": "岗位",
  "/interviews": "面试",
  "/question-sets": "面试题集",
  "/reports": "报告",
  "/settings/llm": "LLM 配置",
  "/settings/email": "邮箱拉取",
  "/settings/smtp": "SMTP 发件",
  "/settings/license": "License 设置",
  "/settings/team": "团队与组织",
  "/settings/sso": "SSO 接入",
  "/settings/audit": "审计中心",
};

/**
 * 受保护的应用骨架。
 * - 未登录 → 跳 /login(并记录原路径以便登录后回跳)
 * - 加载中 → 全屏占位提示
 * - 已登录 → sidebar + topbar + <Outlet />
 */
export function AppShell() {
  const { user, loading } = useAuth();
  const loc = useLocation();

  if (loading) {
    return (
      <div className="h-full grid place-items-center text-sm text-[var(--color-text-tertiary)] font-body">
        加载中…
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  }

  // 面试详情/报告也归到"面试"标签下,不为每个 id 写一行
  let title = TITLE_BY_PATH[loc.pathname] ?? "Free-Hire";
  if (loc.pathname.startsWith("/interviews/")) {
    title = loc.pathname.endsWith("/report") ? "面试报告" : "面试进行中";
  } else if (loc.pathname.startsWith("/jobs/")) {
    // /jobs/:id → 岗位详情;/jobs/:id/matches → 岗位匹配候选人
    title = loc.pathname.endsWith("/matches") ? "岗位匹配" : "岗位详情";
  } else if (loc.pathname.startsWith("/talents/")) {
    title = "候选人档案";
  } else if (loc.pathname.startsWith("/question-sets/")) {
    title = "题集详情";
  }
  return (
    <div className="flex h-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar title={title} />
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
