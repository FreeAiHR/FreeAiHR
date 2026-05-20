import { CheckCircle2 } from "lucide-react";
import { CandidateLayout } from "@/pages/CandidateInvite";

/**
 * 候选人侧面试完成页 (`/i/{token}/done`)。
 *
 * 极简:不展示分数 / 推荐结论 — 这些是 HR 内部数据。
 * 关掉浏览器即结束流程,无需"返回"按钮。
 */
export function CandidateDone() {
  return (
    <CandidateLayout>
      <div className="bg-white border border-[var(--color-border-subtle)] rounded-2xl p-8 flex flex-col items-center text-center gap-3">
        <div className="w-12 h-12 rounded-full bg-[var(--color-success-soft)] text-[var(--color-success)] flex items-center justify-center">
          <CheckCircle2 className="w-6 h-6" />
        </div>
        <h2 className="font-heading font-semibold text-lg text-[var(--color-text-primary)]">
          面试已提交
        </h2>
        <p className="text-[13px] text-[var(--color-text-secondary)] font-body leading-relaxed">
          感谢你完成本次面试。
          <br />
          我们会在审阅后尽快与你联系。
        </p>
      </div>
    </CandidateLayout>
  );
}
