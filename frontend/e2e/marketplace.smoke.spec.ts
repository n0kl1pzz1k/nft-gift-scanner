import { expect, test } from '@playwright/test';

async function stubTelegramAndApi(page: import('@playwright/test').Page, authenticated = false) {
  await page.route('https://telegram.org/js/telegram-web-app.js', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `window.Telegram = { WebApp: {
        initData: ${JSON.stringify(authenticated ? 'query_id=test&auth_date=1795737600&hash=test' : '')},
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
    const user = { first_name: 'Test User', username: 'test_user', photo_url: '', is_premium: false };
    if (url.pathname.endsWith('/api/auth/telegram') && route.request().method() === 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_token: 'grs_test_session', user, is_admin: false }) });
      return;
    }
    if (url.pathname.endsWith('/api/auth/me')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: authenticated, user: authenticated ? user : null, is_admin: false }) });
      return;
    }
    let body: Record<string, unknown> = { items: [], total: 0 };
    if (url.pathname.endsWith('/api/marketplace/stats')) {
      body = { active_listings: 0, active_buy_requests: 0, matched_orders: 0, volume_ton: 0, volume_stars: 0, floor_ton: null, floor_stars: null, total_orders: 0, volume_ton_24h: 0, volume_stars_24h: 0, completed_trades_24h: 0, collections: [] };
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
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page);
  await page.goto('/');
  const mark = page.locator('.topbar-brand-mobile img');
  await expect(mark).toBeVisible();
  await expect.poll(() => mark.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
});


test('authenticated profile opens from the mobile tab without waiting on navigation side effects', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page, true);
  await page.goto('/');
  await expect(page.locator('.market-app')).toHaveAttribute('data-auth-state', 'authenticated', { timeout: 15000 });
  const profileTab = page.locator('.mobile-nav button').filter({ hasText: 'Профиль' });
  await expect(profileTab).toBeVisible();
  const navHitTarget = await profileTab.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)?.outerHTML.slice(0, 500) || 'none';
  });
  console.log('AUTH PROFILE NAV HIT TARGET:', navHitTarget);
  await page.evaluate(() => {
    (window as any).__navEvents = [];
    document.addEventListener('pointerdown', event => (window as any).__navEvents.push('pointerdown:' + (event.target as Element).tagName), true);
    document.addEventListener('click', event => (window as any).__navEvents.push('click:' + (event.target as Element).tagName), true);
  });
  await profileTab.click();
  console.log('AUTH PROFILE NAV EVENTS:', await page.evaluate(() => (window as any).__navEvents));
  console.log('PROFILE FALLBACK:', await page.evaluate(() => ({ mounted: (window as any).__profileNavFallbackMounted, calls: (window as any).__profileNavFallbackCalls })));
  await expect(page.locator('.market-app')).toHaveAttribute('data-active-view', 'profile');
  await expect(profileTab).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-profile-view]')).toBeVisible();
  await expect(page.locator('.profile-main')).toContainText('Test User');
});
