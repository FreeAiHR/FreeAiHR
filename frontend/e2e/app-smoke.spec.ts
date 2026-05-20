import { expect, test, type Page } from "@playwright/test";

const adminEmail = process.env.E2E_ADMIN_EMAIL ?? "admin@example.com";
const adminPassword = process.env.E2E_ADMIN_PASSWORD ?? "admin123456";

async function login(page: Page) {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();

  await page.getByLabel("邮箱").fill(adminEmail);
  await page.getByLabel("密码").fill(adminPassword);
  await page.getByRole("button", { name: /^登录$/ }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText(adminEmail)).toBeVisible();
}

test.describe("app smoke", () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies();
  });

  test("admin can log in and open core pages", async ({ page }) => {
    await login(page);

    await expect(page.getByText("简历总数")).toBeVisible();
    await expect(page.getByText("开放岗位")).toBeVisible();

    await page.goto("/jobs");
    await expect(
      page.getByRole("heading", { level: 2, name: "岗位" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "新建岗位" })).toBeVisible();

    await page.goto("/settings/license");
    await expect(
      page.getByRole("heading", { level: 2, name: "License 设置" }),
    ).toBeVisible();
    await expect(page.getByText("授权计划")).toBeVisible();
    await expect(page.getByText("天剩余", { exact: true })).toBeVisible();
  });
});
