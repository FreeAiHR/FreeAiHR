import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from "@playwright/test";

const adminEmail = process.env.E2E_ADMIN_EMAIL ?? "admin@example.com";
const adminPassword = process.env.E2E_ADMIN_PASSWORD ?? "admin123456";

type LoginResponse = {
  access_token: string;
};

type JobOut = {
  id: string;
  title: string;
};

type ResumeOut = {
  id: string;
  candidate: {
    id: string;
    name: string;
    display_email: string | null;
    display_phone: string | null;
  };
};

type StartInterviewResponse = {
  interview_id: string;
  invite: {
    token: string;
    invite_url: string;
  };
};

async function loginViaApi(request: APIRequestContext): Promise<string> {
  const response = await request.post("/api/auth/login", {
    data: { email: adminEmail, password: adminPassword },
  });
  const body = await parseJson<LoginResponse>(response);
  return body.access_token;
}

async function loginViaUi(page: Page) {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();

  await page.getByLabel("邮箱").fill(adminEmail);
  await page.getByLabel("密码").fill(adminPassword);
  await page.getByRole("button", { name: /^登录$/ }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText(adminEmail)).toBeVisible();
}

async function parseJson<T>(response: APIResponse): Promise<T> {
  const text = await response.text();
  expect(
    response.ok(),
    `${response.url()} -> ${response.status()}: ${text}`,
  ).toBeTruthy();
  return JSON.parse(text) as T;
}

function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

test.describe("business flow", () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies();
  });

  test("admin starts a remote interview and sees the completed candidate result", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);

    const token = await loginViaApi(request);
    const headers = authHeaders(token);
    const runId = Date.now().toString(36);
    const jobTitle = `E2E 后端工程师 ${runId}`;
    const candidateName = `E2E 候选人 ${runId}`;
    const candidateEmail = `e2e.${runId}@example.com`;
    const candidatePhone = `139${String(Date.now() % 100_000_000).padStart(8, "0")}`;

    let jobId: string | null = null;
    let resumeId: string | null = null;
    let interviewId: string | null = null;

    try {
      const job = await parseJson<JobOut>(
        await request.post("/api/jobs/", {
          headers,
          data: {
            title: jobTitle,
            level: "intermediate",
            description: "负责 FastAPI 服务、异步任务和招聘业务闭环。",
            skills: ["Python", "FastAPI", "PostgreSQL"],
          },
        }),
      );
      jobId = job.id;

      await loginViaUi(page);
      await page.goto(`/jobs?q=${encodeURIComponent(jobTitle)}`);
      await expect(
        page.getByRole("heading", { level: 2, name: "岗位" }),
      ).toBeVisible();
      await expect(page.getByText(jobTitle)).toBeVisible();

      const resumeText = [
        `姓名：${candidateName}`,
        `邮箱：${candidateEmail}`,
        `电话：${candidatePhone}`,
        "技能：Python FastAPI PostgreSQL Celery Redis",
        "项目：负责招聘系统候选人面试流程、评分任务和后台报表。",
      ].join("\n");
      const resume = await parseJson<ResumeOut>(
        await request.post("/api/resumes/upload", {
          headers,
          multipart: {
            file: {
              name: `${candidateName}.txt`,
              mimeType: "text/plain",
              buffer: Buffer.from(resumeText, "utf8"),
            },
          },
        }),
      );
      resumeId = resume.id;

      const interview = await parseJson<StartInterviewResponse>(
        await request.post("/api/interviews/start", {
          headers,
          data: {
            job_id: jobId,
            candidate_id: resume.candidate.id,
            question_count: 3,
            kinds: ["tech", "project"],
            expires_in_hours: 24,
            delivery: "link",
            modality: "text",
          },
        }),
      );
      interviewId = interview.interview_id;

      await page.goto(interview.invite.invite_url);
      await expect(page.getByRole("heading", { name: jobTitle })).toBeVisible();
      await expect(page.getByText(`你好 ${resume.candidate.name}`)).toBeVisible();
      await expect(page.getByText("AI 面试邀请")).toBeVisible();
      await expect(page.getByText("身份核对")).toBeVisible();

      await page.getByPlaceholder("0000").fill(candidatePhone.slice(-4));
      await page.getByRole("button", { name: "开始面试" }).click();
      await expect(page).toHaveURL(/\/session$/);

      for (let i = 1; i <= 3; i += 1) {
        await expect(page.getByText(`第 ${i} 题`, { exact: true })).toBeVisible({
          timeout: 45_000,
        });
        await page
          .getByPlaceholder("在此输入你的回答…(Ctrl/⌘ + Enter 提交)")
          .fill(
            `这是第 ${i} 题的 E2E 回答。我会结合 Python、FastAPI 和项目交付经验说明问题定位、方案选择和结果复盘。`,
          );
        await page.getByRole("button", { name: /^提交$/ }).click();
      }

      await expect(page).toHaveURL(/\/done$/, { timeout: 45_000 });
      await expect(page.getByRole("heading", { name: "面试已提交" })).toBeVisible();

      await page.goto(`/interviews?q=${encodeURIComponent(candidateName)}`);
      await expect(
        page.getByRole("heading", { level: 2, name: "面试" }),
      ).toBeVisible();
      await expect(page.getByText(candidateName).first()).toBeVisible();
      await expect(page.getByText(jobTitle).first()).toBeVisible();
      await expect(page.getByText("已完成").first()).toBeVisible();
    } finally {
      if (interviewId) {
        await request.post(`/api/interviews/${interviewId}/cancel-invite`, {
          headers,
        });
      }
      if (jobId) {
        await request.delete(`/api/jobs/${jobId}`, { headers });
      }
      if (resumeId) {
        await request.delete(`/api/resumes/${resumeId}`, { headers });
      }
    }
  });
});
