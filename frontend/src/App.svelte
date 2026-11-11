<script lang="ts">
  import { onMount } from 'svelte';
  import GiftMedia from './GiftMedia.svelte';

  type Gift = {
    id?: string | number; name?: string; number?: string | number;
    image?: string; animation_url?: string; has_animation?: boolean;
    price?: string | number; floor_price?: string | number | null;
    currency?: string; source?: string; status?: string;
    attributes?: { type?: string; name?: string; value?: string }[];
  };
  type Collection = { id?: string | number; name?: string; short_name?: string; image?: string };
  type DemoOrder = {
    id: string; kind: 'listing' | 'buy-order'; title: string;
    price: string | number; currency: 'TON' | 'XTR'; status: 'active' | 'matched' | 'cancelled';
    createdAt: number; updatedAt: number; owner: string; isMine: boolean; note?: string; events?: {type:string;createdAt:number}[];
  };
  type MarketplaceStats = {
    active_listings: number; active_buy_requests: number; matched_orders: number;
    volume_ton: number; volume_stars: number; total_orders: number;
  };
  type View = 'market' | 'orders' | 'create' | 'favorites' | 'profile';

  const API = (import.meta.env.VITE_API_BASE_URL || window.API_BASE || 'https://nft-gift-scanner-production.up.railway.app').replace(/\/$/, '');
  let lang = $state<'ru' | 'en'>('ru');
  let view = $state<View>('market');
  let gifts = $state<Gift[]>([]);
  let collections = $state<Collection[]>([]);
  let favorites = $state<string[]>([]);
  let favoriteItems = $state<Gift[]>([]);
  let orders = $state<DemoOrder[]>([]);
  let marketplaceStats = $state<MarketplaceStats>({active_listings:0,active_buy_requests:0,matched_orders:0,volume_ton:0,volume_stars:0,total_orders:0});
  let search = $state('');
  let minPrice = $state('');
  let maxPrice = $state('');
  let busy = $state(true);
  let loadingMore = $state(false);
  let error = $state('');
  let total = $state(0);
  let offset = $state(0);
  let selected = $state<Gift | null>(null);
  let telegramUser = $state('');
  let telegramPhoto = $state('');
  let telegramUsername = $state('');
  let telegramPremium = $state(false);
  let signedIn = $state(false);
  let notice = $state('');
  let createKind = $state<'listing' | 'buy-order'>('listing');
  let createTitle = $state('');
  let createPrice = $state('');
  let createCurrency = $state<'TON' | 'XTR'>('TON');
  let createNote = $state('');
  let collectionFilter = $state('all');
  let statusFilter = $state('listed');
  let sortBy = $state('listed_at desc');

  const ru = {
    market: 'Маркет', orders: 'Активность', create: 'Разместить', favorites: 'Избранное', profile: 'Профиль', readonly: 'ТОЛЬКО ПРОСМОТР', catalogOnly: 'Актуальные объявления Portals. Просмотр и поиск доступны; управление сделками в GIFT RADAR пока не включено.',
    headline: 'Подарки, которые находят своих владельцев', subtitle: 'Единое пространство Telegram-подарков на базе каталога Portals.',
    portals: 'Единственный источник каталога · Portals', search: 'Название или номер подарка', min: 'Цена от', max: 'Цена до',
    listings: 'Предложения Portals', results: 'найдено', loading: 'Загружаем каталог…', loadMore: 'Показать ещё',
    empty: 'По этому запросу ничего не найдено.', failed: 'Не удалось загрузить каталог.',
    details: 'О подарке', floor: 'Флор', price: 'Цена', traits: 'Характеристики',
    createTitle: 'Создать объявление', sell: 'Предложение', buy: 'Заявка на поиск', titleLabel: 'Название подарка',
    priceLabel: 'Цена (демо)', noteLabel: 'Комментарий', publish: 'Создать демо-объявление',
    createHint: 'Общие тестовые заявки видны всем пользователям GIFT RADAR. Реальные сделки и платежи не выполняются.',
    ordersTitle: 'Ордера маркетплейса', noOrders: 'Пока нет активных предложений или заявок.',
    active: 'Активен', matched: 'Смоделирован', cancelled: 'Отменён', cancel: 'Снять', match: 'Симулировать исполнение',
    demo: 'ДЕМО-РЕЖИМ', demoWallet: 'Демо-профиль', demoWalletText: 'Подключение кошельков и реальные платежи отключены.',
    signIn: 'Подтвердить Telegram', guest: 'Гость', authHint: 'Открой Mini App через Telegram, чтобы подтвердить профиль.',
    signed: 'Telegram подтверждён', profileText: 'Профиль Telegram и общая история демонстрационных заявок.',
    favoritesTitle: 'Избранные подарки', noFavorites: 'Добавьте подарки в избранное из каталога.',
    save: 'В избранное', saved: 'В избранном', currency: 'TON', success: 'Демо-запись добавлена.', statsListings: 'Активные предложения', statsBuys: 'Заявки на покупку', statsVolume: 'Демо-оборот', statsVolumeHint: 'Только смоделированные записи', statsCatalog: 'Подарков в каталоге', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'Каталог Portals', refresh: 'Обновить', all: 'Все',
  };
  const en = {
    market: 'Market', orders: 'Activity', create: 'Create', favorites: 'Favorites', profile: 'Profile', readonly: 'READ ONLY', catalogOnly: 'Live Portals listings. Browse and search are available; trade execution is not enabled in GIFT RADAR.',
    headline: 'Discover gifts worth keeping', subtitle: 'A unified Telegram gifts experience powered by the Portals catalogue.',
    portals: 'Single catalogue source · Portals', search: 'Gift name or number', min: 'Min price', max: 'Max price',
    listings: 'Portals listings', results: 'found', loading: 'Loading catalogue…', loadMore: 'Load more',
    empty: 'No gifts match this search.', failed: 'Could not load the catalogue.',
    details: 'Gift details', floor: 'Floor', price: 'Price', traits: 'Attributes',
    createTitle: 'Create an order', sell: 'Sell listing', buy: 'Buy request', titleLabel: 'Gift name',
    priceLabel: 'Demo price', noteLabel: 'Note', publish: 'Create demo order',
    createHint: 'Shared demo orders are visible to GIFT RADAR users. No real trades or payments take place.',
    ordersTitle: 'Marketplace orders', noOrders: 'No active listings or requests yet.',
    active: 'Active', matched: 'Simulated', cancelled: 'Cancelled', cancel: 'Cancel', match: 'Simulate fill',
    demo: 'DEMO MODE', demoWallet: 'Demo profile', demoWalletText: 'Wallet connections and real payments are disabled.',
    signIn: 'Verify Telegram', guest: 'Guest', authHint: 'Open the Mini App in Telegram to verify your profile.',
    signed: 'Telegram verified', profileText: 'Telegram profile and shared demo order history.',
    favoritesTitle: 'Saved gifts', noFavorites: 'Save gifts from the catalogue to see them here.',
    save: 'Save', saved: 'Saved', currency: 'TON', success: 'Demo entry created.', statsListings: 'Active listings', statsBuys: 'Buy requests', statsVolume: 'Demo volume', statsVolumeHint: 'Simulated entries only', statsCatalog: 'Catalogue gifts', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'Portals catalogue', refresh: 'Refresh', all: 'All',
  };
  const t = $derived(lang === 'ru' ? ru : en);
  const keyOf = (g: Gift) => String(g.id ?? g.number ?? g.name ?? 'gift');
  const shownGifts = $derived(view === 'favorites' ? favoriteItems : gifts);
  const demoListings = $derived(marketplaceStats.active_listings);
  const demoBuyRequests = $derived(marketplaceStats.active_buy_requests);
  const demoVolumeTon = $derived(marketplaceStats.volume_ton);
  const demoVolumeStars = $derived(marketplaceStats.volume_stars);
  const fmt = (value: unknown, currency = '') => value === null || value === undefined || value === '' ? '—' : String(value) + (currency ? ' ' + currency : '');

  const authHeaders = (): Record<string, string> => {
    const initData = window.Telegram?.WebApp?.initData || '';
    const headers: Record<string, string> = {};
    if (initData) headers['X-Telegram-Init-Data'] = initData;
    return headers;
  };

  async function request(path: string, signal?: AbortSignal, init: RequestInit = {}) {
    const headers = new Headers(authHeaders());
    new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
    const response = await fetch(API + path, { ...init, signal, headers });
    const data = await response.json().catch(() => ({ detail: 'Invalid server response' }));
    if (!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }

  async function loadMarketplace() {
    try {
      const [publicData, mineData, statsData] = await Promise.all([
        request('/api/marketplace/orders?status=active&limit=100'),
        window.Telegram?.WebApp?.initData ? request('/api/marketplace/history') : Promise.resolve({items:[]}),
        request('/api/marketplace/stats'),
      ]);
      const combined = new Map<string, DemoOrder>();
      for (const item of [...(publicData.items || []), ...(mineData.items || [])]) combined.set(item.id, item);
      orders = [...combined.values()].sort((a,b) => b.createdAt - a.createdAt);
      marketplaceStats = { ...marketplaceStats, ...statsData };
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function loadCollections() {
    try {
      const data = await request('/api/collections');
      collections = Array.isArray(data.items) ? data.items : [];
    } catch {
      collections = [];
    }
  }

  async function loadCatalogue(append = false) {
    if (append) loadingMore = true; else { busy = true; error = ''; offset = 0; }
    try {
      const nextOffset = append ? offset : 0;
      const params = new URLSearchParams({ limit: '40', offset: String(nextOffset), search });
      if (minPrice) params.set('min_price', minPrice);
      if (maxPrice) params.set('max_price', maxPrice);
      if (collectionFilter !== 'all') params.set('collection_id', collectionFilter);
      if (statusFilter !== 'all') params.set('status', statusFilter);
      if (sortBy) params.set('sort_by', sortBy);
      const data = await request('/api/gifts?' + params.toString());
      const items = Array.isArray(data.items) ? data.items : [];
      gifts = append ? [...gifts, ...items] : items;
      total = Math.max(Number(data.total_count) || 0, gifts.length);
      offset = nextOffset + items.length;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
      loadingMore = false;
    }
  }

  function toggleFavorite(gift: Gift) {
    const id = keyOf(gift);
    const exists = favorites.includes(id);
    favorites = exists ? favorites.filter(x => x !== id) : [...favorites, id];
    favoriteItems = exists
      ? favoriteItems.filter(item => keyOf(item) !== id)
      : [gift, ...favoriteItems.filter(item => keyOf(item) !== id)];
    try { localStorage.setItem('gift-radar-favorites-v2', JSON.stringify(favoriteItems)); } catch {}
    if (signedIn) void persistFavorite(gift, !exists);
  }

  async function persistFavorite(gift: Gift, enabled: boolean) {
    const id = keyOf(gift);
    try {
      if (enabled) {
        await request('/api/marketplace/favorites/' + encodeURIComponent(id), undefined, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ gift }),
        });
      } else {
        await request('/api/marketplace/favorites/' + encodeURIComponent(id), undefined, { method: 'DELETE' });
      }
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function loadAccountFavorites() {
    const data = await request('/api/marketplace/favorites');
    const items = Array.isArray(data.items) ? data.items as Gift[] : [];
    favoriteItems = items;
    favorites = items.map(keyOf);
    try { localStorage.setItem('gift-radar-favorites-v2', JSON.stringify(items)); } catch {}
  }

  async function createOrder() {
    const price = Number(createPrice);
    if (!createTitle.trim() || !Number.isFinite(price) || price <= 0) {
      notice = lang === 'ru' ? 'Укажи название подарка и положительную цену.' : 'Enter a gift name and a positive price.';
      return;
    }
    if (!window.Telegram?.WebApp?.initData) {
      notice = lang === 'ru' ? 'Открой приложение через Telegram, чтобы публиковать заявки.' : 'Open the app in Telegram to publish demo orders.';
      return;
    }
    try {
      await request('/api/marketplace/orders', undefined, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({kind:createKind,title:createTitle.trim(),price,currency:createCurrency,note:createNote.trim()}),
      });
      createTitle = ''; createPrice = ''; createNote = ''; createCurrency = 'TON';
      notice = t.success;
      view = 'orders';
      await loadMarketplace();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function completeOrder(id: string) {
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id) + '/simulate-fill', undefined, {method:'POST'});
      notice = lang === 'ru' ? 'Демо-ордер отмечен как исполненный.' : 'Demo order marked as filled.';
      await loadMarketplace();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function cancelOrder(id: string) {
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id), undefined, {method:'DELETE'});
      notice = lang === 'ru' ? 'Заявка снята.' : 'Order cancelled.';
      await loadMarketplace();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function telegramLogin(showNotice = true) {
    const initData = window.Telegram?.WebApp?.initData || '';
    if (!initData) { if (showNotice) notice = t.authHint; return; }
    try {
      const response = await fetch(API + '/api/auth/telegram', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: initData }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Authentication failed');
      telegramUser = data.user?.first_name || 'Telegram user';
      telegramPhoto = data.user?.photo_url || '';
      telegramUsername = data.user?.username ? '@' + data.user.username : '';
      telegramPremium = data.user?.is_premium === true;
      signedIn = true;
      await loadAccountFavorites();
      if (showNotice) notice = t.signed;
    } catch (e) {
      if (showNotice) notice = e instanceof Error ? e.message : String(e);
    }
  }

  onMount(() => {
    window.Telegram?.WebApp?.ready?.();
    window.Telegram?.WebApp?.expand?.();
    try {
      const fav = JSON.parse(localStorage.getItem('gift-radar-favorites-v2') || '[]');
      if (Array.isArray(fav)) {
        if (fav.length && typeof fav[0] === 'object') {
          favoriteItems = fav as Gift[];
          favorites = favoriteItems.map(keyOf);
        } else {
          favorites = fav as string[];
        }
      }
    } catch {}
    void loadCatalogue();
    void loadCollections();
    void loadMarketplace();
    if (window.Telegram?.WebApp?.initData) void telegramLogin(false);
  });
</script>

<svelte:head>
  <title>GIFT RADAR — Marketplace</title>
  <meta name="theme-color" content="#090B18" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
</svelte:head>

<div class="market-app">
  <aside class="sidebar">
    <a class="brand" href="#market"><span class="brand-symbol">✳</span><span>GIFT RADAR<small>THE GIFT MARKET</small></span></a>
    <div class="nav-caption">WORKSPACE</div>
    <nav class="side-nav">
      {#each [{id:'market',label:t.market,icon:'▦'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
        <button class:active={view===item.id} class="nav-item" onclick={()=>{view=item.id as View;notice='';}}><span>{item.icon}</span>{item.label}</button>
      {/each}
    </nav>
    <div class="sidebar-bottom"><div class="demo-card"><span class="demo-dot"></span>{t.demo}<p>{t.demoWalletText}</p></div><div class="sidebar-version">PORTALS DATA · GIFT RADAR</div></div>
  </aside>

  <main class="main-content">
    <header class="topbar">
      <div class="crumb"><span>GIFT RADAR</span><b>/</b><span>{view.toUpperCase()}</span></div>
      <div class="top-actions"><span class="demo-pill">{t.readonly}</span><button class="language" onclick={()=>createCurrency=createCurrency==='TON'?'XTR':'TON'}>{createCurrency==='TON'?'TON':'⭐ Stars'}</button><button class="language" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang.toUpperCase()}</button><button class="avatar-button" onclick={()=>view='profile'}>{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser ? telegramUser.slice(0,1).toUpperCase() : 'G'}</span>{/if}</button></div>
    </header>

    {#if view==='market'}
      <section class="hero">
        <div class="hero-copy"><div class="eyebrow"><span class="spark"></span> {t.portals}</div><h1>{t.headline}</h1><p>{t.subtitle}</p><div class="hero-tags"><span>✦ Animated gifts</span><span>◈ Portals catalogue</span><span>◎ Live listings · read only</span></div></div>
        <div class="hero-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="hero-gem">✳</div><span class="float-chip chip-a">GIFT #042</span><span class="float-chip chip-b">PORTALS</span><span class="float-chip chip-c">✦ RARE FIND</span></div>
      </section>
      <section class="market-stats" aria-label="Catalogue overview"><article><span>▦</span><div><strong>{total.toLocaleString()}</strong><small>{t.statsCatalog}</small></div></article><p>{t.catalogOnly}</p></section>
      <section class="market-section">
        <div class="section-title"><div><div class="eyebrow">DISCOVER</div><h2>{t.listings}</h2><p>{total.toLocaleString()} {t.results}</p></div><button class="refresh-button" onclick={()=>loadCatalogue()} disabled={busy}>↻ {t.refresh}</button></div>
        <div class="filter-bar">
          <div class="search-field"><span>⌕</span><input bind:value={search} placeholder={t.search} onkeydown={e=>e.key==="Enter"&&loadCatalogue()}/></div>
          <select class="filter-input" bind:value={collectionFilter} aria-label="Collection">
            <option value="all">Все коллекции</option>
            {#each collections as collection}
              <option value={String(collection.id ?? collection.short_name ?? "")}>{collection.name || collection.short_name || "Collection"}</option>
            {/each}
          </select>
          <select class="filter-input" bind:value={statusFilter} aria-label="Listing status">
            <option value="listed">В продаже</option><option value="unlisted">Не выставлены</option><option value="all">Все статусы</option>
          </select>
          <input class="filter-input" type="number" min="0" bind:value={minPrice} placeholder={t.min}/>
          <input class="filter-input" type="number" min="0" bind:value={maxPrice} placeholder={t.max}/>
          <select class="filter-input" bind:value={sortBy} aria-label="Sort listings">
            <option value="listed_at desc">Сначала новые</option><option value="price asc">Сначала дешевле</option><option value="price desc">Сначала дороже</option>
            <option value="model_rarity asc">Редкость модели ↑</option><option value="model_rarity desc">Редкость модели ↓</option>
          </select>
          <button class="search-button" onclick={()=>loadCatalogue()}>Применить</button>
        </div>
        <div class="filter-bar"><div class="search-field"><span>⌕</span><input bind:value={search} placeholder={t.search} onkeydown={e=>e.key==='Enter'&&loadCatalogue()}/></div><input class="filter-input" type="number" min="0" bind:value={minPrice} placeholder={t.min}/><input class="filter-input" type="number" min="0" bind:value={maxPrice} placeholder={t.max}/><button class="search-button" onclick={()=>loadCatalogue()}>{t.market}</button></div>
        {#if busy}<div class="state-panel"><div class="loader"></div>{t.loading}</div>
        {:else if error}<div class="state-panel error-state">{t.failed}<small>{error}</small><button class="search-button" onclick={()=>loadCatalogue()}>{t.refresh}</button></div>
        {:else if gifts.length===0}<div class="state-panel">{t.empty}</div>
        {:else}
          <div class="gift-grid">
            {#each gifts as gift (keyOf(gift))}
              <article class="gift-card">
                <button class="gift-visual" onclick={()=>selected=gift} aria-label={t.details}>
                  <GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-media"/>
                  <span class="source-tag">PORTALS</span>
                </button>
                <div class="gift-card-body"><div class="gift-card-title">{gift.name||'Telegram Gift'} <span>{gift.number?'#'+gift.number:''}</span></div><div class="gift-card-meta">Telegram collectible · {gift.status||'Listed'}</div><div class="gift-card-bottom"><div><small>{t.price}</small><strong>{fmt(gift.price,gift.currency||'TON')}</strong></div><button class:hearted={favorites.includes(keyOf(gift))} class="heart-button" onclick={()=>toggleFavorite(gift)}>{favorites.includes(keyOf(gift))?'♥':'♡'}</button></div><button class="card-details" onclick={()=>selected=gift}>{t.details} <span>↗</span></button></div>
              </article>
            {/each}
          </div>
          {#if gifts.length<total}<div class="load-more-wrap"><button class="load-more" onclick={()=>loadCatalogue(true)} disabled={loadingMore}>{loadingMore?'…':t.loadMore}</button></div>{/if}
        {/if}
      </section>
    {:else if view==='orders'}
      <section class="page-heading"><div class="eyebrow">YOUR ACTIVITY</div><h1>{t.ordersTitle}</h1><p>{t.createHint}</p></section>
      {#if orders.length===0}<div class="state-panel">{t.noOrders}<button class="search-button" onclick={()=>view='create'}>{t.create}</button></div>
      {:else}<div class="orders-list">{#each orders as order (order.id)}<article class="order-card"><div class="order-icon">{order.kind==='listing'?'↗':'⌕'}</div><div class="order-main"><div class="order-kind">{order.kind==='listing'?t.sell:t.buy}</div><h3>{order.title}</h3><p>{new Date(order.createdAt * 1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')} {'· '+order.owner} {order.note?'· '+order.note:''} {order.events?.length?'· '+order.events.map(event=>event.type==='created'?(lang==='ru'?'создан':'created'):event.type==='simulated_fill'?(lang==='ru'?'смоделировано исполнение':'simulated fill'):(lang==='ru'?'снят':'cancelled')).join(' → '):''}</p></div><div class="order-price"><strong>{order.price} {order.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class={'status-'+order.status}>{order.status==='active'?t.active:order.status==='matched'?t.matched:t.cancelled}</span></div>{#if order.status==='active' && order.isMine}<div class="order-actions"><button class="cancel-button" onclick={()=>cancelOrder(order.id)}>{t.cancel}</button></div>{:else if order.status==='active'}<div class="order-actions"><button class="match-button" onclick={()=>completeOrder(order.id)}>{t.match}</button></div>{/if}</article>{/each}</div>{/if}
    {:else if view==='create'}
      <section class="page-heading"><div class="eyebrow">MARKETPLACE STUDIO</div><h1>{t.createTitle}</h1><p>{t.createHint}</p></section>
      <section class="create-layout"><div class="create-form">
        <div class="segmented"><button class:chosen={createKind==='listing'} onclick={()=>createKind='listing'}>{t.sell}</button><button class:chosen={createKind==='buy-order'} onclick={()=>createKind='buy-order'}>{t.buy}</button></div>
        <label>{t.titleLabel}<input class="form-input" bind:value={createTitle} placeholder="Plush Pepe #042"/></label>
        <label>{t.priceLabel}<div class="currency-choice"><button class:chosen={createCurrency==='TON'} onclick={()=>createCurrency='TON'}>TON</button><button class:chosen={createCurrency==='XTR'} onclick={()=>createCurrency='XTR'}>⭐ Telegram Stars</button></div><input class="form-input" type="number" min="0" step="0.01" bind:value={createPrice} placeholder={createCurrency==='TON'?'1.25':'100'}/></label>
        <label>{t.noteLabel}<textarea class="form-input note-input" bind:value={createNote} placeholder="..."></textarea></label>
        <button class="publish-button" onclick={createOrder}>{t.publish} <span>→</span></button>
        <p class="safety-note">ⓘ {t.createHint}</p>
      </div><div class="create-aside"><div class="aside-icon">✳</div><div class="eyebrow">GIFT RADAR</div><h2>Make your next move visible.</h2><p>Заявки хранятся на сервере и видны другим пользователям. TON и Stars — только обозначения тестовых цен.</p><div class="aside-stats"><div><strong>{orders.length}</strong><span>DEMO ORDERS</span></div><div><strong>{favorites.length}</strong><span>SAVED GIFTS</span></div></div></div></section>
    {:else if view==='favorites'}
      <section class="page-heading"><div class="eyebrow">YOUR COLLECTION</div><h1>{t.favoritesTitle}</h1><p>{favorites.length} saved items</p></section>
      {#if shownGifts.length}<div class="gift-grid">{#each shownGifts as gift (keyOf(gift))}<article class="gift-card"><button class="gift-visual" onclick={()=>selected=gift}><GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-media"/></button><div class="gift-card-body"><div class="gift-card-title">{gift.name||'Telegram Gift'}</div><div class="gift-card-bottom"><strong>{fmt(gift.price,gift.currency||'TON')}</strong><button class="heart-button hearted" onclick={()=>toggleFavorite(gift)}>♥</button></div></div></article>{/each}</div>{:else}<div class="state-panel">{t.noFavorites}<button class="search-button" onclick={()=>view='market'}>{t.market}</button></div>{/if}
    {:else}
      <section class="page-heading"><div class="eyebrow">ACCOUNT CENTER</div><h1>{t.profile}</h1><p>{t.profileText}</p></section>
      <section class="profile-layout"><article class="profile-card profile-main"><div class="profile-avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser?telegramUser.slice(0,1).toUpperCase():'G'}</span>{/if}</div><div><h2>{telegramUser||t.guest}</h2><p>{signedIn?t.signed:t.authHint}</p>{#if telegramUsername}<small class="profile-handle">{telegramUsername}</small>{/if}{#if telegramPremium}<span class="profile-premium">TELEGRAM PREMIUM</span>{/if}</div><button class="publish-button" onclick={()=>telegramLogin()}>{t.signIn} →</button></article><article class="profile-card"><div class="eyebrow">WALLET AREA</div><h2>{t.demoWallet}</h2><p>{t.demoWalletText}</p><div class="wallet-placeholder"><span>◈</span><div><strong>Wallet connection disabled</strong><small>Demo profile · no assets stored</small></div><b>DEMO</b></div></article><article class="profile-card"><div class="eyebrow">PREFERENCES</div><h2>GIFT RADAR</h2><p>Portals catalogue · TON / Stars demo labels · RU / EN</p><button class="language wide" onclick={()=>lang=lang==='ru'?'en':'ru'}>Language: {lang.toUpperCase()}</button></article></section>
    {/if}

    {#if notice}<div class="toast" role="status">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
    <footer class="footer"><span>✳ GIFT RADAR</span><span>PORTALS CATALOGUE · DEMO MARKETPLACE</span><span>© 2026</span></footer>
  </main>
</div>

<nav class="mobile-nav">
  {#each [{id:'market',label:t.market,icon:'▦'},{id:'orders',label:t.orders,icon:'⇄'},{id:'create',label:t.create,icon:'＋'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
    <button class:active={view===item.id} onclick={()=>{view=item.id as View;notice='';}}><span>{item.icon}</span><small>{item.label}</small></button>
  {/each}
</nav>

{#if selected}
  <div class="modal-backdrop" role="presentation" onclick={()=>selected=null}>
    <div class="gift-modal" role="dialog" tabindex="-1" aria-modal="true" aria-label={selected.name||'Gift details'} onclick={e=>e.stopPropagation()} onkeydown={e=>e.stopPropagation()}>
      <button class="modal-close" onclick={()=>selected=null}>×</button>
      <div class="modal-media"><GiftMedia image={selected.image} animationUrl={selected.animation_url} hasAnimation={selected.has_animation} alt={selected.name||'Gift'} className="gift-media"/></div>
      <div class="modal-content"><div class="eyebrow">PORTALS COLLECTIBLE</div><h2>{selected.name||'Telegram Gift'} {selected.number?'#'+selected.number:''}</h2><div class="modal-price"><span>{t.price}</span><strong>{fmt(selected.price,selected.currency||'TON')}</strong></div><div class="modal-price"><span>{t.floor}</span><strong>{fmt(selected.floor_price,selected.currency||'TON')}</strong></div><h3>{t.traits}</h3>{#each selected.attributes||[] as trait}<div class="trait-row"><span>{trait.type||trait.name}</span><strong>{trait.value||'—'}</strong></div>{/each}<button class="publish-button" onclick={()=>toggleFavorite(selected!)}>{favorites.includes(keyOf(selected))?t.saved:t.save} <span>♡</span></button></div>
    </div>
  </div>
{/if}
