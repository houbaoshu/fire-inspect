import { expect, test } from '@playwright/test'

test('创建检查、上传现场说明并确认候选事实', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('用户名').fill('admin')
  await page.getByLabel('密码').fill('admin-password')
  await page.getByRole('button', { name: /登\s*录/ }).click()

  await expect(page.getByRole('heading', { name: '检查管理' })).toBeVisible()
  await page.getByRole('button', { name: '创建检查' }).click()
  await page.getByLabel('检查名称').fill('端到端消防检查')
  await page.getByLabel('被检查单位').fill('示例商场')
  await page.getByRole('dialog').getByRole('button', { name: /创\s*建/ }).click()

  await expect(page.getByRole('heading', { name: '端到端消防检查' })).toBeVisible()
  await page.getByRole('tab', { name: '检查材料' }).click()
  const uploadFinished = page.waitForResponse(
    (response) => response.url().endsWith('/complete') && response.status() === 200,
  )
  await page.locator('input[type=file]').setInputFiles({
    name: '现场说明.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('消防控制室无人值班。现场未发现值班人员。'),
  })
  await uploadFinished
  await expect(page.getByText('现场说明.txt')).toBeVisible({ timeout: 15_000 })

  await page.getByRole('tab', { name: '检查事实' }).click()
  await expect(page.getByText('消防控制室无人值班', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '确认事实' }).first().click()
  await expect(page.getByText('已确认').first()).toBeVisible()

  await page.getByRole('button', { name: 'message AI 助手' }).click()
  const assistant = page.locator('.assistant-drawer')
  await assistant.getByPlaceholder('例如：这条事实的依据是什么？').fill('消防控制室无人值班在哪里能证明？')
  await assistant.getByRole('button', { name: /发\s*送/ }).click()
  await expect(assistant.getByText('检查事实：消防控制室无人值班')).toBeVisible()
})
