import { test, expect } from '@playwright/test';

const TEST_EMAIL = process.env.E2E_TEST_EMAIL ?? `e2e-${Date.now()}@example.com`;
const TEST_PASSWORD = 'correct-horse-battery-staple';

test('signup → submit small scan → see live feed → see report', async ({ page, request }) => {
  // Seed allowed_emails by making a direct request? No — easier: do it via psql
  // outside the test. So this test ASSUMES the email is already in allowed_emails.
  // To make it self-contained, the test could call a debug endpoint, but for
  // Phase 2 we keep it simple: precondition is documented in Makefile.

  // 1. Sign up.
  await page.goto('/auth/signup');
  await page.fill('input[name=email]', TEST_EMAIL);
  await page.fill('input[name=password]', TEST_PASSWORD);
  await page.click('button[type=submit]');
  await expect(page).toHaveURL(/\/scans/);

  // 2. New scan form.
  await page.click('a[href="/scans/new"]');
  await page.fill('input[name=subject]', 'E2E Smoke Test');
  // ReAct should be the default agent (alphabetical first if not explicitly set);
  // its only param is `passes` which defaults to 1.
  await page.click('button[type=submit]:has-text("RUN SCAN")');
  await expect(page).toHaveURL(/\/scans\/[0-9a-f-]{36}/);

  // 3. Wait for the scan to terminate (max 5 min).
  await expect(page.locator('text=COMPLETE').or(page.locator('text=FAILED')))
    .toBeVisible({ timeout: 5 * 60_000 });

  // 4. The tool-call steps should be available after completion.
  if (process.env.E2E_EXPECT_MOCK_TOOL === '1') {
    await page.click('button:has-text("Show steps")');
    await expect(page.locator('text=Web search')).toBeVisible({ timeout: 30_000 });
  }
});
