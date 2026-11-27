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


test('authenticated profile opens on iPhone from a touch pointer event', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page, true);
  await page.goto('/');
  await expect(page.locator('.market-app')).toHaveAttribute('data-auth-state', 'authenticated', { timeout: 15000 });
  const profileTab = page.locator('.mobile-nav button[data-nav-id="profile"]');
  await expect(profileTab).toBeVisible();
  await profileTab.dispatchEvent('pointerdown', { pointerType: 'touch', pointerId: 7, isPrimary: true, button: 0 });
  await expect(page.locator('.market-app')).toHaveAttribute('data-active-view', 'profile');
  await expect(page.locator('[data-profile-view]')).toBeVisible();
  await expect(page.locator('.profile-main')).toContainText('Test User');
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
  await profileTab.click();
  await expect(page.locator('.market-app')).toHaveAttribute('data-active-view', 'profile');
  await expect(profileTab).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-profile-view]')).toBeVisible();
  await expect(page.locator('.profile-main')).toContainText('Test User');
});

test('catalog shows real marketplace metrics and official Gram denomination', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page);
  await page.goto('/');
  await expect(page.locator('.market-live-stats')).toBeVisible();
  await expect(page.locator('.market-live-stats')).toContainText('Активные лоты');
  await expect(page.locator('.market-category-rail')).toBeVisible();
  await expect(page.locator('.market-search input')).toHaveCSS('font-size', '16px');
  await expect(page.locator('.market-currency-legend .gram-token-icon')).toBeVisible();
});

test('listing details button opens the real asset detail panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramAndApi(page);
  await page.route('https://hassle-nft-market.up.railway.app/api/marketplace/orders**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{
          id: 'listing-test-12345', asset_id: 'asset-test-12345', kind: 'listing',
          title: 'Jolly Chimp #93222', asset_type: 'gift', image_url: '',
          price: '12.5', currency: 'TON', status: 'active', createdAt: 1795737600,
          updatedAt: 1795737600, owner: '@seller_test', isMine: false,
          seller_profile_id: null, note: ''
        }],
        total: 1
      })
    });
  });
  await page.goto('/');
  const details = page.locator('.asset-details-button').first();
  await expect(details).toBeVisible();
  await details.click();
  await expect(page.locator('.asset-detail-modal')).toBeVisible();
  await expect(page.locator('#asset-detail-title')).toContainText('Jolly Chimp #93222');
});

test('mobile marketplace typography keeps seller handles readable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await stubTelegramAndApi(page);
  await page.route('https://hassle-nft-market.up.railway.app/api/marketplace/orders**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{
          id: 'listing-mobile-12345', asset_id: 'asset-mobile-12345', kind: 'listing',
          title: 'Eternal Rose #1204', asset_type: 'gift', image_url: '',
          price: '8.4', currency: 'TON', status: 'active', createdAt: 1795737600,
          updatedAt: 1795737600, owner: '@collector_name', isMine: false,
          seller_profile_id: null, note: ''
        }],
        total: 1
      })
    });
  });
  await page.goto('/');
  const seller = page.locator('.asset-owner').first();
  await expect(seller).toContainText('@collector_name');
  await expect(seller).toHaveCSS('font-size', '10px');
  const metrics = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    searchFont: getComputedStyle(document.querySelector('.market-search input')!).fontSize,
    sellerColor: getComputedStyle(document.querySelector('.asset-owner')!).color,
  }));
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.searchFont).toBe('16px');
  expect(metrics.sellerColor).not.toBe('rgb(0, 0, 0)');
});
