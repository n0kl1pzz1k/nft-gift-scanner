import { expect, test } from '@playwright/test';

test('Mini App boots and renders the HASSLE NFT marketplace shell', async ({ page }) => {
  await page.route('https://hassle-nft-market.up.railway.app/**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], orders: [], offers: [], deals: [], total: 0 }),
    });
  });

  await page.goto('/');
  await expect(page).toHaveTitle(/HASSLE NFT/);
  await expect(page.locator('#app > *').first()).toBeVisible();
  await expect(page.locator('#app')).toContainText(/HASSLE NFT|Telegram Gifts|Твой рынок/i);
});

test('marketplace shell fits a narrow mobile viewport without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route('https://hassle-nft-market.up.railway.app/**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], orders: [], offers: [], deals: [], total: 0 }),
    });
  });

  await page.goto('/');
  await expect(page.locator('#app > *').first()).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasHorizontalOverflow).toBe(false);
});
