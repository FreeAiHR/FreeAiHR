import { createBrowserRouter, Navigate } from "react-router-dom";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { LicenseSettings } from "@/pages/LicenseSettings";
import { LLMSettings } from "@/pages/LLMSettings";
import { EmailSettings } from "@/pages/EmailSettings";
import { Jobs } from "@/pages/Jobs";
import { JobDetail } from "@/pages/JobDetail";
import { JobMatches } from "@/pages/JobMatches";
import { ResumeLibrary } from "@/pages/ResumeLibrary";
import { Interviews } from "@/pages/Interviews";
import { InterviewReport } from "@/pages/InterviewReport";
import { Reports } from "@/pages/Reports";
import { TeamSettings } from "@/pages/TeamSettings";
import { AuditCenter } from "@/pages/AuditCenter";
import { SsoCallback } from "@/pages/SsoCallback";
import { SsoSettings } from "@/pages/SsoSettings";
import { TalentPool } from "@/pages/TalentPool";
import { TalentDetail } from "@/pages/TalentDetail";
import { QuestionSets } from "@/pages/QuestionSets";
import { QuestionSetDetail } from "@/pages/QuestionSetDetail";
import { QuestionLibrary } from "@/pages/QuestionLibrary";
import { Analytics } from "@/pages/Analytics";
import { CandidateInvite } from "@/pages/CandidateInvite";
import { CandidateSession } from "@/pages/CandidateSession";
import { CandidateDone } from "@/pages/CandidateDone";
import { SmtpSettings } from "@/pages/SmtpSettings";
import { VoiceSettings } from "@/pages/VoiceSettings";
import { AppShell } from "@/components/layout/AppShell";

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/login/sso-callback", element: <SsoCallback /> },
  // 候选人侧公开路由 — 无登录态,极简 layout,不挂 AppShell
  { path: "/i/:token", element: <CandidateInvite /> },
  { path: "/i/:token/session", element: <CandidateSession /> },
  { path: "/i/:token/done", element: <CandidateDone /> },
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "resumes", element: <ResumeLibrary /> },
      { path: "talents", element: <TalentPool /> },
      { path: "talents/:id", element: <TalentDetail /> },
      { path: "jobs", element: <Jobs /> },
      { path: "jobs/:id", element: <JobDetail /> },
      { path: "jobs/:id/matches", element: <JobMatches /> },
      { path: "interviews", element: <Interviews /> },
      { path: "interviews/:id/report", element: <InterviewReport /> },
      { path: "question-sets", element: <QuestionSets /> },
      { path: "question-sets/:id", element: <QuestionSetDetail /> },
      { path: "question-library", element: <QuestionLibrary /> },
      { path: "reports", element: <Reports /> },
      { path: "analytics", element: <Analytics /> },
      { path: "settings/llm", element: <LLMSettings /> },
      { path: "settings/email", element: <EmailSettings /> },
      { path: "settings/smtp", element: <SmtpSettings /> },
      { path: "settings/voice", element: <VoiceSettings /> },
      { path: "settings/license", element: <LicenseSettings /> },
      { path: "settings/team", element: <TeamSettings /> },
      { path: "settings/sso", element: <SsoSettings /> },
      { path: "settings/audit", element: <AuditCenter /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
