import { expect, test } from "@playwright/test";

declare const process: { env: Record<string, string | undefined> };

const baseURL = process.env.FIRE_INSPECT_BROWSER_BASE_URL!.replace(/\/$/, "");
const allowedOrigin = new URL(baseURL).origin;
const username = process.env.FIRE_INSPECT_BROWSER_USERNAME;
const password = process.env.FIRE_INSPECT_BROWSER_PASSWORD;

if (!username || !password) {
  throw new Error("browser acceptance credentials are required");
}

test("HTTPS login, material upload, and deep SPA refresh stay same-origin", async ({
  context,
  page,
}) => {
  const blockedRequests: string[] = [];
  await context.route("**/*", async (route) => {
    const requestURL = new URL(route.request().url());
    if (
      requestURL.origin === allowedOrigin ||
      requestURL.protocol === "data:" ||
      requestURL.protocol === "blob:" ||
      requestURL.protocol === "about:"
    ) {
      await route.continue();
      return;
    }
    blockedRequests.push(`${requestURL.protocol}//${requestURL.host}`);
    await route.abort("blockedbyclient");
  });

  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await expect(page.getByText("系统不提供默认账号或密码")).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(`${baseURL}/inspections`);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);

  await page.getByRole("link", { name: "新建检查", exact: true }).click();
  await page.getByLabel(/单位（场所）名称/).fill("浏览器验收消防安全示例单位");
  await page.getByLabel(/^地址/).fill("海州市内网验收路 18 号");
  await page.getByLabel(/法定代表人/).fill("验收负责人");
  await page.getByLabel(/^联系人/).fill("验收联系人");
  await page.getByLabel(/^联系电话/).fill("13800000000");
  await page.getByRole("button", { name: "创建并进入工作台" }).click();
  await expect(page).toHaveURL(/\/inspections\/[0-9a-f-]{36}\/materials$/);
  const match = page.url().match(/\/inspections\/([0-9a-f-]{36})\/materials$/);
  expect(match).not.toBeNull();
  const inspectionId = match![1];

  await page.getByRole("button", { name: /添加材料/ }).first().click();
  await page.getByLabel("添加文字说明").fill("浏览器验收材料：现场情况待人工核实。");
  await page.getByRole("button", { name: "提交材料" }).click();
  await expect(page.getByText("文字说明已安全保存")).toBeVisible();

  await page.goto(`/inspections/${inspectionId}/regulations`);
  await expect(page.getByRole("heading", { name: "法规检索" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "法规检索" })).toBeVisible();

  await page.goto(`/inspections/${inspectionId}/facts`);
  await expect(page.getByRole("heading", { name: "事实与证据" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "事实与证据" })).toBeVisible();

  const resourceOrigins = await page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => new URL(entry.name).origin)
      .filter((origin) => origin !== "null"),
  );
  expect(new Set(resourceOrigins)).toEqual(new Set([allowedOrigin]));
  expect(blockedRequests).toEqual([]);

  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(`${baseURL}/login`);
  await page.goto(`/inspections/${inspectionId}/materials`);
  await expect(page).toHaveURL(`${baseURL}/login`);
});
