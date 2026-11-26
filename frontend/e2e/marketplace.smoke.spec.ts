import { expect, test } from '@playwright/test';

async function stubTelegramAndApi(page: import('@playwright/test').Page) {
  await page.route('https://telegram.org/js/telegram-web-app.js', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `window.Telegram = { WebApp: {
        initData: '',
        themeParams: { bg_color: '#0e1116', text_color: '#f5f7ff' },
        ready: function () { window.__telegramReadyCalled = true; },
        expand: function () { window.__telegramExpandCalled = true; },
        onEvent: function () {},
        offEvent: function () {}
      } };`,
    });
  });

  await page.route('https://hassle-nft-market.up.railway.app/**', async route => {
    const url = new URL(route.request().url());
    let body: Record<string, unknown> = { items: [], total: 0 };
    if (url.pathname.endsWith('/api/marketplace/stats')) {
      body = { active_listings: 0, active_buy_requests: 0, matched_orders: 0, volume_ton: 0, volume_stars: 0, floor_ton: null, floor_stars: null, total_orders: 0 };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('Mini App boots, signals Telegram ready, and renders the real empty catalogue state', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await stubTelegramAndApi(page);

  await page.goto('/');
  await expect(page).toHaveTitle(/HASSLE NFT/);
  await expect(page.locator('.market-heading-row')).toBeVisible();
  await expect(page.locator('.market-empty')).toBeVisible();
  await expect(page.locator('#app')).toHaveAttribute('data-boot-state', 'ready');
  await expect.poll(() => page.evaluate(() => (window as any).__telegramReadyCalled)).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as any).__telegramExpandCalled)).toBe(true);
  await expect(page.locator('.asset-grid-skeleton')).toHaveCount(0);
  expect(pageErrors).toEqual([]);
});

test('Telegram Mini App shell fits a narrow iPhone viewport and remains visible', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page);

  await page.goto('/');
  await expect(page.locator('.market-heading-row')).toBeVisible();
  await expect(page.locator('.market-empty')).toBeVisible();
  await expect(page.locator('#app')).toHaveAttribute('data-boot-state', 'ready');
  const metrics = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    appHeight: document.querySelector('#app')?.getBoundingClientRect().height || 0,
  }));
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.appHeight).toBeGreaterThan(500);
  expect(pageErrors).toEqual([]);
});


test('profile tab is clickable on iPhone and opens the account screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page);
  await page.goto('/');
  const profileTab = page.locator('.mobile-nav button').filter({ hasText: 'Профиль' });
  await expect(profileTab).toBeVisible();
  await profileTab.click();
  await expect(profileTab).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.profile-auth-panel')).toBeVisible();
  await expect(page.locator('.profile-auth-panel')).toContainText('Гость');
});

test('HASSLE brand artwork is loaded in the Mini App', async ({ page }) => {
  await stubTelegramAndApi(page);
  await page.goto('/');
  const mark = page.locator('.topbar-brand-mobile img');
  await expect(mark).toBeVisible();
  await expect.poll(() => mark.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
});
