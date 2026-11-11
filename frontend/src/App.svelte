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
  type DemoOffer = { id:string; order_id:string; sender_name:string; amount:string; currency:'TON'|'XTR'; note:string; status:'pending'|'accepted'|'declined'|'withdrawn'; created_at:number; is_mine:boolean; order_title:string; order_owner_id:string; order_owner_name:string; order_status:string };
  type DemoDeal = { id:string; order_id:string; offer_id:string; title:string; listing_owner:string; offer_sender:string; amount:string; currency:'TON'|'XTR'; created_at:number; status:string; simulation_only:boolean };
  type AccountNotification = { id:string; type:'offer_received'|'offer_accepted'|'offer_declined'|'offer_withdrawn'; offer_id:string; order_id:string; order_title:string; actor_name:string; created_at:number; read_at:number|null };
  type AccountStats = { active_orders:number; completed_orders:number; pending_offers_sent:number; pending_offers_received:number; simulated_deals:number; unread_notifications:number };
  type AccountActivity = { id:string; type:string; order_id:string; title:string; actor_name:string; created_at:number; amount:string; currency:'TON'|'XTR'; simulation_only:boolean };
  type MarketplaceStats = {
    active_listings: number; active_buy_requests: number; matched_orders: number;
    volume_ton: number; volume_stars: number; total_orders: number;
  };
  type ApiStatus = {
    sources?: Record<string, string>;
    data_pipeline?: { refresh_interval_seconds?: number };
    note?: string;
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
  let publicOrdersTotal = $state(0);
  let publicOrdersOffset = $state(0);
  let ordersLoadingMore = $state(false);
  let offers = $state<DemoOffer[]>([]);
  let offersTotal = $state(0);
  let offersLoadingMore = $state(false);
  let deals = $state<DemoDeal[]>([]);
  let dealsTotal = $state(0);
  let dealsLoadingMore = $state(false);
  let notifications = $state<AccountNotification[]>([]);
  let notificationTotal = $state(0);
  let notificationsLoadingMore = $state(false);
  let accountActivity = $state<AccountActivity[]>([]);
  let activityTotal = $state(0);
  let activityBusy = $state(false);
  let accountStats = $state<AccountStats>({active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,simulated_deals:0,unread_notifications:0});
  let offerAmount = $state('');
  let offerNote = $state('');
  let offersBusy = $state(false);
  let offerActionBusy = $state('');
  let offerStatusFilter = $state<'all'|'pending'|'accepted'|'declined'|'withdrawn'>('all');
  let marketplaceStats = $state<MarketplaceStats>({active_listings:0,active_buy_requests:0,matched_orders:0,volume_ton:0,volume_stars:0,total_orders:0});
  let apiStatus = $state<ApiStatus | null>(null);
  let statusCheckedAt = $state<number | null>(null);
  let statusBusy = $state(false);
  let statusError = $state(false);
  let search = $state('');
  let minPrice = $state('');
  let maxPrice = $state('');
  let busy = $state(true);
  let loadingMore = $state(false);
  let error = $state('');
  let total = $state(0);
  let offset = $state(0);
  let selected = $state<Gift | null>(null);
  let selectedHistory = $state<{item: Gift; observed_at: number}[]>([]);
  let historyLoading = $state(false);
  let telegramUser = $state('');
  let telegramPhoto = $state('');
  let telegramUsername = $state('');
  let telegramPremium = $state(false);
  let signedIn = $state(false);
  let isAdmin = $state(false);
  let adminMarketplaceEnabled = $state(true);
  let adminBusy = $state(false);
  let notice = $state('');
  let createKind = $state<'listing' | 'buy-order'>('listing');
  let createTitle = $state('');
  let createPrice = $state('');
  let createCurrency = $state<'TON' | 'XTR'>('TON');
  let createNote = $state('');
  let editingOrderId = $state('');
  let editTitle = $state('');
  let editPrice = $state('');
  let editNote = $state('');
  let orderEditBusy = $state(false);
  let collectionFilter = $state('all');
  let statusFilter = $state('listed');
  let sortBy = $state('listed_at desc');
  let catalogueRequestId = 0;
  let orderStatusFilter = $state<'all' | 'active' | 'matched' | 'cancelled'>('active');
  let orderKindFilter = $state<'all' | 'listing' | 'buy-order'>('all');

  const ru = {
    market: 'Маркет', orders: 'Активность', create: 'Разместить', favorites: 'Избранное', profile: 'Профиль', readonly: 'ТОЛЬКО ПРОСМОТР', catalogOnly: 'Актуальные объявления Portals. Просмотр и поиск доступны; управление сделками в GIFT RADAR пока не включено.',
    headline: 'Подарки, которые находят своих владельцев', subtitle: 'Единое пространство Telegram-подарков на базе каталога Portals.',
    brandEyebrow: 'ОТКРЫВАЙ РЕДКОЕ · GIFT RADAR', heroLine1: 'Telegram Gifts.', heroLine2: 'Открой редкие подарки.', heroDescription: 'Исследуй коллекции Telegram-подарков, объявления и историю характеристик в одном месте.', openCatalogue: 'Открыть каталог', connectedLabel: 'КАТАЛОГ PORTALS · ТОЛЬКО ПРОСМОТР', exploreMarket: 'ИССЛЕДУЙ МАРКЕТ', collectionsTitle: 'Коллекции', collectionsCount: 'коллекций в каталоге', discoverLabel: 'ОТКРЫВАЙ', portals: 'Единственный источник каталога · Portals', search: 'Название или номер подарка', min: 'Цена от', max: 'Цена до',
    listings: 'Предложения Portals', results: 'найдено', loading: 'Загружаем каталог…', loadMore: 'Показать ещё',
    empty: 'По этому запросу ничего не найдено.', failed: 'Не удалось загрузить каталог.',
    details: 'О подарке', floor: 'Флор', price: 'Цена', traits: 'Характеристики',
    createTitle: 'Создать объявление', sell: 'Предложение', buy: 'Заявка на поиск', titleLabel: 'Название подарка',
    priceLabel: 'Цена (демо)', noteLabel: 'Комментарий', publish: 'Создать демо-объявление',
    createHint: 'Общие тестовые заявки видны всем пользователям GIFT RADAR. Реальные сделки и платежи не выполняются.',
    ordersTitle: 'Ордера маркетплейса', noOrders: 'Пока нет активных предложений или заявок.',
    offersTitle: 'Офферы', dealsTitle: 'Демо-сделки', sendOffer: 'Отправить оффер', offerAmount: 'Сумма оффера', acceptOffer: 'Принять', declineOffer: 'Отклонить', withdrawOffer: 'Отозвать', noOffers: 'Офферов пока нет.', noDeals: 'Демо-сделок пока нет.', incoming: 'Входящий', outgoing: 'Исходящий', offerStatusFilter: 'Статус оффера', offerPending: 'Ожидает ответа', offerAccepted: 'Принят', offerDeclined: 'Отклонён', offerWithdrawn: 'Отозван',
    accountStatsTitle: 'Статистика аккаунта', activeOrdersStat: 'Активные заявки', completedOrdersStat: 'Завершённые', sentOffersStat: 'Мои офферы', receivedOffersStat: 'Входящие офферы', notificationsTitle: 'Уведомления', markAllRead: 'Прочитать все', noNotifications: 'Новых уведомлений нет.', offerReceivedNotice: 'отправил(а) оффер', offerAcceptedNotice: 'принят оффер по', offerDeclinedNotice: 'отклонил(а) оффер по', offerWithdrawnNotice: 'отозвал(а) оффер по', activityTitle: 'История активности', noActivity: 'Активность пока не появилась.', loadMoreActivity: 'Показать ещё', orderCreatedActivity: 'Создана заявка', orderFilledActivity: 'Заявка отмечена как исполненная (демо)', orderCancelledActivity: 'Заявка снята', offerReceivedActivity: 'Получен оффер', offerAcceptedActivity: 'Оффер принят', offerDeclinedActivity: 'Оффер отклонён', offerWithdrawnActivity: 'Оффер отозван', dealCreatedActivity: 'Создана демо-сделка',
    active: 'Активен', matched: 'Смоделирован', cancelled: 'Отменён', cancel: 'Снять', match: 'Симулировать исполнение', editOrder: 'Изменить', saveOrder: 'Сохранить', cancelEdit: 'Отмена', editTitle: 'Название', editPrice: 'Демо-цена', editNote: 'Комментарий', orderUpdated: 'Объявление обновлено.',
    demo: 'PORTALS LIVE · READ ONLY', demoWallet: 'Кошелёк не подключён', demoWalletText: 'Подключение кошельков и операции с активами пока не поддерживаются.',
    signIn: 'Подтвердить Telegram', guest: 'Гость', authHint: 'Открой Mini App через Telegram, чтобы подтвердить профиль.',
    signed: 'Telegram подтверждён', profileText: 'Профиль Telegram и общая история демонстрационных заявок.',
    favoritesTitle: 'Избранные подарки', noFavorites: 'Добавьте подарки в избранное из каталога.',
    save: 'В избранное', saved: 'В избранном', currency: 'TON', success: 'Демо-запись добавлена.', statsListings: 'Активные предложения', statsBuys: 'Заявки на покупку', statsVolume: 'Демо-оборот', statsVolumeHint: 'Только смоделированные записи', statsCatalog: 'Подарков в каталоге', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'Каталог Portals', refresh: 'Обновить', all: 'Все', orderStatus: 'Статус', orderKind: 'Тип заявки', activeOrders: 'Активные', matchedOrders: 'Смоделированные', cancelledOrders: 'Снятые', listingsOnly: 'Предложения', buyRequestsOnly: 'Заявки на поиск', invalidPrice: 'Проверь диапазон цены: значения должны быть неотрицательными, а минимальная цена — не выше максимальной.',
    sourceStatusTitle: 'СОСТОЯНИЕ ИСТОЧНИКОВ', sourceStatusSubtitle: 'Что подключено к приложению', sourcePortals: 'Каталог объявлений', sourceChanges: 'Изменения подарков', sourceDailyFloors: 'История floor-цен', sourceStatusChecked: 'Проверено', sourceStatusChecking: 'Проверяем статус…', sourceStatusUnavailable: 'Не удалось получить статус API.', sourceRefreshInterval: 'Интервал фонового обновления', sourceConfigured: 'Настроен · только чтение', sourceNeedsKey: 'Требуется API-ключ', sourcePublic: 'Публичный источник', sourceDaily: 'Публичная ежедневная история', sourceUnknown: 'Нет данных',
  };
  const en = {
    market: 'Market', orders: 'Activity', create: 'Create', favorites: 'Favorites', profile: 'Profile', readonly: 'READ ONLY', catalogOnly: 'Live Portals listings. Browse and search are available; trade execution is not enabled in GIFT RADAR.',
    headline: 'Discover gifts worth keeping', subtitle: 'A unified Telegram gifts experience powered by the Portals catalogue.',
    brandEyebrow: 'DISCOVER THE RARE · GIFT RADAR', heroLine1: 'Telegram Gifts.', heroLine2: 'Discover rare finds.', heroDescription: 'Explore Telegram gift collections, marketplace listings and item history in one place.', openCatalogue: 'Explore catalogue', connectedLabel: 'PORTALS CATALOGUE · READ ONLY', exploreMarket: 'EXPLORE THE MARKET', collectionsTitle: 'Collections', collectionsCount: 'collections in catalogue', discoverLabel: 'DISCOVER', portals: 'Single catalogue source · Portals', search: 'Gift name or number', min: 'Min price', max: 'Max price',
    listings: 'Portals listings', results: 'found', loading: 'Loading catalogue…', loadMore: 'Load more',
    empty: 'No gifts match this search.', failed: 'Could not load the catalogue.',
    details: 'Gift details', floor: 'Floor', price: 'Price', traits: 'Attributes',
    createTitle: 'Create an order', sell: 'Sell listing', buy: 'Buy request', titleLabel: 'Gift name',
    priceLabel: 'Demo price', noteLabel: 'Note', publish: 'Create demo order',
    createHint: 'Shared demo orders are visible to GIFT RADAR users. No real trades or payments take place.',
    ordersTitle: 'Marketplace orders', noOrders: 'No active listings or requests yet.',
    offersTitle: 'Offers', dealsTitle: 'Demo deals', sendOffer: 'Send offer', offerAmount: 'Offer amount', acceptOffer: 'Accept', declineOffer: 'Decline', withdrawOffer: 'Withdraw', noOffers: 'No offers yet.', noDeals: 'No demo deals yet.', incoming: 'Incoming', outgoing: 'Outgoing', offerStatusFilter: 'Offer status', offerPending: 'Awaiting response', offerAccepted: 'Accepted', offerDeclined: 'Declined', offerWithdrawn: 'Withdrawn',
    accountStatsTitle: 'Account stats', activeOrdersStat: 'Active orders', completedOrdersStat: 'Completed', sentOffersStat: 'Offers sent', receivedOffersStat: 'Offers received', notificationsTitle: 'Notifications', markAllRead: 'Mark all read', noNotifications: 'No notifications yet.', offerReceivedNotice: 'sent you an offer for', offerAcceptedNotice: 'accepted your offer for', offerDeclinedNotice: 'declined your offer for', offerWithdrawnNotice: 'withdrew an offer for', activityTitle: 'Activity history', noActivity: 'No activity yet.', loadMoreActivity: 'Load more', orderCreatedActivity: 'Order created', orderFilledActivity: 'Order marked as filled (demo)', orderCancelledActivity: 'Order cancelled', offerReceivedActivity: 'Offer received', offerAcceptedActivity: 'Offer accepted', offerDeclinedActivity: 'Offer declined', offerWithdrawnActivity: 'Offer withdrawn', dealCreatedActivity: 'Demo deal created',
    active: 'Active', matched: 'Simulated', cancelled: 'Cancelled', cancel: 'Cancel', match: 'Simulate fill', editOrder: 'Edit', saveOrder: 'Save changes', cancelEdit: 'Discard', editTitle: 'Title', editPrice: 'Demo price', editNote: 'Note', orderUpdated: 'Order updated.',
    demo: 'PORTALS LIVE · READ ONLY', demoWallet: 'Wallet not connected', demoWalletText: 'Wallet connections and asset operations are not supported yet.',
    signIn: 'Verify Telegram', guest: 'Guest', authHint: 'Open the Mini App in Telegram to verify your profile.',
    signed: 'Telegram verified', profileText: 'Telegram profile and shared demo order history.',
    favoritesTitle: 'Saved gifts', noFavorites: 'Save gifts from the catalogue to see them here.',
    save: 'Save', saved: 'Saved', currency: 'TON', success: 'Demo entry created.', statsListings: 'Active listings', statsBuys: 'Buy requests', statsVolume: 'Demo volume', statsVolumeHint: 'Simulated entries only', statsCatalog: 'Catalogue gifts', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'Portals catalogue', refresh: 'Refresh', all: 'All', orderStatus: 'Status', orderKind: 'Order type', activeOrders: 'Active', matchedOrders: 'Simulated', cancelledOrders: 'Cancelled', listingsOnly: 'Listings', buyRequestsOnly: 'Buy requests', invalidPrice: 'Check the price range: values must be non-negative and the minimum cannot exceed the maximum.',
    sourceStatusTitle: 'SOURCE STATUS', sourceStatusSubtitle: 'Connected data sources', sourcePortals: 'Marketplace catalogue', sourceChanges: 'Gift metadata changes', sourceDailyFloors: 'Floor price history', sourceStatusChecked: 'Checked', sourceStatusChecking: 'Checking status…', sourceStatusUnavailable: 'Could not load API status.', sourceRefreshInterval: 'Background refresh interval', sourceConfigured: 'Configured · read-only', sourceNeedsKey: 'API key required', sourcePublic: 'Public source', sourceDaily: 'Public daily history', sourceUnknown: 'No data',
  };
  const t = $derived(lang === 'ru' ? ru : en);
  const keyOf = (g: Gift) => String(g.id ?? g.number ?? g.name ?? 'gift');
  const shownGifts = $derived(view === 'favorites' ? favoriteItems : gifts);
  const visibleOrders = $derived(orders.filter(order =>
    (orderStatusFilter === 'all' || order.status === orderStatusFilter) &&
    (orderKindFilter === 'all' || order.kind === orderKindFilter)
  ));
  const visibleOffers = $derived(offers.filter(offer => offerStatusFilter === 'all' || offer.status === offerStatusFilter));
  const demoListings = $derived(marketplaceStats.active_listings);
  const demoBuyRequests = $derived(marketplaceStats.active_buy_requests);
  const demoVolumeTon = $derived(marketplaceStats.volume_ton);
  const demoVolumeStars = $derived(marketplaceStats.volume_stars);
  const fmt = (value: unknown, currency = '') => value === null || value === undefined || value === '' ? '—' : String(value) + (currency ? ' ' + currency : '');

  function offerStatusLabel(status: DemoOffer['status']) {
    return status === 'pending' ? t.offerPending : status === 'accepted' ? t.offerAccepted : status === 'declined' ? t.offerDeclined : t.offerWithdrawn;
  }

  function sourceStatusText(value?: string) {
    if (value === 'configured_read_only') return t.sourceConfigured;
    if (value === 'needs_api_key') return t.sourceNeedsKey;
    if (value === 'public_read_only') return t.sourcePublic;
    if (value === 'public_daily_read_only') return t.sourceDaily;
    return t.sourceUnknown;
  }

  function sourceStatusClass(value?: string) {
    return value && value !== 'needs_api_key' ? 'is-available' : 'is-pending';
  }

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

  function orderFilterQuery() {
    const params = new URLSearchParams({ status: orderStatusFilter, limit: '200', offset: '0' });
    if (orderKindFilter !== 'all') params.set('kind', orderKindFilter);
    return params.toString();
  }

  async function loadMarketplace() {
    try {
      const [publicData, mineData, statsData] = await Promise.all([
        request('/api/marketplace/orders?' + orderFilterQuery()),
        window.Telegram?.WebApp?.initData ? request('/api/marketplace/mine') : Promise.resolve({items:[]}),
        request('/api/marketplace/stats'),
      ]);
      const publicItems = Array.isArray(publicData.items) ? publicData.items as DemoOrder[] : [];
      const mineItems = Array.isArray(mineData.items) ? mineData.items as DemoOrder[] : [];
      const combined = new Map<string, DemoOrder>();
      for (const item of [...publicItems, ...mineItems]) combined.set(item.id, item);
      orders = [...combined.values()].sort((a,b) => b.createdAt - a.createdAt);
      publicOrdersTotal = Number(publicData.total) || publicItems.length;
      publicOrdersOffset = publicItems.length;
      marketplaceStats = { ...marketplaceStats, ...statsData };
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function changeOrderFilters() {
    await loadMarketplace();
  }

  async function loadMoreOrders() {
    if (ordersLoadingMore || publicOrdersOffset >= publicOrdersTotal) return;
    ordersLoadingMore = true;
    try {
      const params = new URLSearchParams({ status: orderStatusFilter, limit: '200', offset: String(publicOrdersOffset) });
      if (orderKindFilter !== 'all') params.set('kind', orderKindFilter);
      const data = await request('/api/marketplace/orders?' + params.toString());
      const next = Array.isArray(data.items) ? data.items as DemoOrder[] : [];
      const combined = new Map<string, DemoOrder>(orders.map(order => [order.id, order] as const));
      for (const item of next) combined.set(item.id, item);
      orders = [...combined.values()].sort((a,b) => b.createdAt - a.createdAt);
      publicOrdersOffset += next.length;
      publicOrdersTotal = Number(data.total) || publicOrdersTotal;
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      ordersLoadingMore = false;
    }
  }

  async function loadAccountOverview() {
    if (!window.Telegram?.WebApp?.initData) {
      notifications = [];
      notificationTotal = 0;
      accountStats = {active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,simulated_deals:0,unread_notifications:0};
      return;
    }
    const [stats, feed, activity] = await Promise.all([
      request('/api/marketplace/account/stats'),
      request('/api/marketplace/notifications?limit=40&offset=0'),
      request('/api/marketplace/activity?limit=20&offset=0'),
    ]);
    accountStats = stats;
    notifications = Array.isArray(feed.items) ? feed.items : [];
    notificationTotal = Number(feed.total) || notifications.length;
    accountActivity = Array.isArray(activity.items) ? activity.items : [];
    activityTotal = Number(activity.total) || 0;
  }

  async function loadMoreNotifications() {
    if (notificationsLoadingMore || notifications.length >= notificationTotal) return;
    notificationsLoadingMore = true;
    try {
      const data = await request('/api/marketplace/notifications?limit=40&offset=' + notifications.length);
      const next = Array.isArray(data.items) ? data.items as AccountNotification[] : [];
      const combined = new Map<string, AccountNotification>(notifications.map(item => [item.id, item] as const));
      for (const item of next) combined.set(item.id, item);
      notifications = [...combined.values()];
      notificationTotal = Number(data.total) || notificationTotal;
      accountStats = {...accountStats, unread_notifications: Number(data.unread_count) || 0};
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { notificationsLoadingMore = false; }
  }

  function notificationMessage(item: AccountNotification) {
    const labels = lang === 'ru'
      ? {offer_received:t.offerReceivedNotice,offer_accepted:t.offerAcceptedNotice,offer_declined:t.offerDeclinedNotice,offer_withdrawn:t.offerWithdrawnNotice}
      : {offer_received:t.offerReceivedNotice,offer_accepted:t.offerAcceptedNotice,offer_declined:t.offerDeclinedNotice,offer_withdrawn:t.offerWithdrawnNotice};
    return item.type === 'offer_received'
      ? item.actor_name + ' ' + labels[item.type] + ' «' + item.order_title + '»'
      : item.type === 'offer_accepted'
        ? labels[item.type] + ' «' + item.order_title + '»'
        : item.actor_name + ' ' + labels[item.type] + ' «' + item.order_title + '»';
  }

  async function loadOffersAndDeals() {
    if (!window.Telegram?.WebApp?.initData) { offers = []; deals = []; await loadAccountOverview(); return; }
    offersBusy = true;
    try {
      const offerStatusQuery = offerStatusFilter === 'all' ? '' : '&status=' + encodeURIComponent(offerStatusFilter);
      const results = await Promise.all([request('/api/marketplace/offers?limit=100&offset=0' + offerStatusQuery), request('/api/marketplace/deals?limit=100&offset=0'), loadAccountOverview()]);
      offers = Array.isArray(results[0].items) ? results[0].items : [];
      offersTotal = Number(results[0].total) || offers.length;
      deals = Array.isArray(results[1].items) ? results[1].items : [];
      dealsTotal = Number(results[1].total) || deals.length;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offersBusy = false; }
  }

  async function changeOfferStatus(event: Event) {
    const value = (event.currentTarget as HTMLSelectElement).value;
    offerStatusFilter = value as typeof offerStatusFilter;
    await loadOffersAndDeals();
  }

  async function loadMoreOffers() {
    if (offersLoadingMore || offers.length >= offersTotal) return;
    offersLoadingMore = true;
    try {
      const offerStatusQuery = offerStatusFilter === 'all' ? '' : '&status=' + encodeURIComponent(offerStatusFilter);
      const data = await request('/api/marketplace/offers?limit=100&offset=' + offers.length + offerStatusQuery);
      const next = Array.isArray(data.items) ? data.items as DemoOffer[] : [];
      const combined = new Map<string, DemoOffer>(offers.map(offer => [offer.id, offer] as const));
      for (const offer of next) combined.set(offer.id, offer);
      offers = [...combined.values()];
      offersTotal = Number(data.total) || offersTotal;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offersLoadingMore = false; }
  }

  async function loadMoreDeals() {
    if (dealsLoadingMore || deals.length >= dealsTotal) return;
    dealsLoadingMore = true;
    try {
      const data = await request('/api/marketplace/deals?limit=100&offset=' + deals.length);
      const next = Array.isArray(data.items) ? data.items as DemoDeal[] : [];
      const combined = new Map<string, DemoDeal>(deals.map(deal => [deal.id, deal] as const));
      for (const deal of next) combined.set(deal.id, deal);
      deals = [...combined.values()];
      dealsTotal = Number(data.total) || dealsTotal;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { dealsLoadingMore = false; }
  }

  async function loadMoreActivity() {
    if (activityBusy || accountActivity.length >= activityTotal) return;
    activityBusy = true;
    try {
      const data = await request('/api/marketplace/activity?limit=20&offset=' + accountActivity.length);
      const next = Array.isArray(data.items) ? data.items as AccountActivity[] : [];
      accountActivity = [...accountActivity, ...next];
      activityTotal = Number(data.total) || activityTotal;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { activityBusy = false; }
  }

  function activityLabel(item: AccountActivity) {
    const labels: Record<string,string> = {
      order_created:t.orderCreatedActivity, order_simulated_fill:t.orderFilledActivity,
      order_cancelled:t.orderCancelledActivity, offer_received:t.offerReceivedActivity,
      offer_accepted:t.offerAcceptedActivity, offer_declined:t.offerDeclinedActivity,
      offer_withdrawn:t.offerWithdrawnActivity, demo_deal_created:t.dealCreatedActivity,
    };
    return labels[item.type] || item.type;
  }

  async function markNotificationRead(id: string) {
    try {
      await request('/api/marketplace/notifications/' + encodeURIComponent(id) + '/read', undefined, {method:'PATCH'});
      notifications = notifications.map(item => item.id === id ? {...item, read_at: Math.floor(Date.now()/1000)} : item);
      accountStats = {...accountStats, unread_notifications: Math.max(0, accountStats.unread_notifications - 1)};
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function markAllNotificationsRead() {
    try {
      await request('/api/marketplace/notifications/read-all', undefined, {method:'POST'});
      const now = Math.floor(Date.now()/1000);
      notifications = notifications.map(item => ({...item, read_at:item.read_at || now}));
      accountStats = {...accountStats, unread_notifications:0};
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function createOffer(orderId: string) {
    const amount = Number(offerAmount);
    if (!Number.isFinite(amount) || amount <= 0) { notice = lang === 'ru' ? 'Укажи положительную сумму оффера.' : 'Enter a positive offer amount.'; return; }
    try {
      await request('/api/marketplace/offers', undefined, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:orderId,amount,note:offerNote.trim()}) });
      offerAmount = ''; offerNote = '';
      notice = lang === 'ru' ? 'Оффер отправлен владельцу заявки.' : 'Offer sent to the order owner.';
      await loadOffersAndDeals();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function respondToOffer(offerId: string, action: 'accept'|'decline'|'withdraw') {
    if (offerActionBusy) return;
    offerActionBusy = offerId;
    try {
      await request('/api/marketplace/offers/' + encodeURIComponent(offerId), undefined, { method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}) });
      notice = lang === 'ru' ? 'Статус оффера обновлён.' : 'Offer status updated.';
      await Promise.all([loadOffersAndDeals(), loadMarketplace()]);
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offerActionBusy = ''; }
  }
  async function loadApiStatus() {
    if (statusBusy) return;
    statusBusy = true;
    try {
      apiStatus = await request('/api/status');
      statusError = false;
      statusCheckedAt = Date.now();
    } catch {
      statusError = true;
    } finally {
      statusBusy = false;
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

  async function openGift(gift: Gift) {
    selected = gift;
    selectedHistory = [];
    historyLoading = true;
    const giftId = keyOf(gift);
    try {
      const data = await request('/api/catalog/items/' + encodeURIComponent(giftId) + '/history?limit=5');
      if (selected && keyOf(selected) === giftId) {
        selectedHistory = Array.isArray(data.history) ? data.history : [];
      }
    } catch {
      // Ignore stale responses when the user has opened a different gift.
      if (selected && keyOf(selected) === giftId) selectedHistory = [];
    } finally {
      if (selected && keyOf(selected) === giftId) historyLoading = false;
    }
  }

  async function loadCatalogue(append = false) {
    if (append && (loadingMore || busy)) return;
    const requestId = ++catalogueRequestId;
    if (append) loadingMore = true; else { busy = true; error = ''; offset = 0; }
    const minValue = minPrice.trim() === '' ? null : Number(minPrice);
    const maxValue = maxPrice.trim() === '' ? null : Number(maxPrice);
    if ((minValue !== null && (!Number.isFinite(minValue) || minValue < 0)) ||
        (maxValue !== null && (!Number.isFinite(maxValue) || maxValue < 0)) ||
        (minValue !== null && maxValue !== null && minValue > maxValue)) {
      error = t.invalidPrice;
      busy = false;
      loadingMore = false;
      return;
    }
    try {
      const nextOffset = append ? offset : 0;
      const params = new URLSearchParams({ limit: '40', offset: String(nextOffset), search: search.trim() });
      if (minValue !== null) params.set('min_price', String(minValue));
      if (maxValue !== null) params.set('max_price', String(maxValue));
      if (collectionFilter !== 'all') params.set('collection_id', collectionFilter);
      if (statusFilter !== 'all') params.set('status', statusFilter);
      if (sortBy) params.set('sort_by', sortBy);
      const data = await request('/api/gifts?' + params.toString());
      if (requestId !== catalogueRequestId) return;
      const items = Array.isArray(data.items) ? data.items : [];
      gifts = append ? [...gifts, ...items] : items;
      total = Math.max(Number(data.total_count) || 0, gifts.length);
      offset = nextOffset + items.length;
    } catch (e) {
      if (requestId === catalogueRequestId) error = e instanceof Error ? e.message : String(e);
    } finally {
      if (requestId === catalogueRequestId) {
        busy = false;
        loadingMore = false;
      }
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
      await loadOffersAndDeals();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function completeOrder(id: string) {
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id) + '/simulate-fill', undefined, {method:'POST'});
      notice = lang === 'ru' ? 'Демо-ордер отмечен как исполненный.' : 'Demo order marked as filled.';
      await loadMarketplace();
      await loadOffersAndDeals();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  function startEditOrder(order: DemoOrder) {
    editingOrderId = order.id;
    editTitle = order.title;
    editPrice = String(order.price);
    editNote = order.note || '';
  }

  async function saveOrderEdit(id: string) {
    const price = Number(editPrice);
    if (!editTitle.trim() || !Number.isFinite(price) || price <= 0) {
      notice = lang === 'ru' ? 'Проверь название и положительную демо-цену.' : 'Enter a title and a positive demo price.';
      return;
    }
    if (orderEditBusy) return;
    orderEditBusy = true;
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id), undefined, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: editTitle.trim(), price, note: editNote.trim() }),
      });
      editingOrderId = '';
      notice = t.orderUpdated;
      await loadMarketplace();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      orderEditBusy = false;
    }
  }

  async function cancelOrder(id: string) {
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id), undefined, {method:'DELETE'});
      notice = lang === 'ru' ? 'Заявка снята.' : 'Order cancelled.';
      await loadMarketplace();
      await loadOffersAndDeals();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function loadAdminOverview() {
    if (!isAdmin) return;
    try {
      const data = await request('/api/admin/overview');
      adminMarketplaceEnabled = data.marketplace_visibility?.portals !== false;
    } catch (e) {
      // Do not render privileged controls if the protected overview cannot be loaded.
      isAdmin = false;
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function toggleAdminMarketplace() {
    if (!isAdmin || adminBusy) return;
    adminBusy = true;
    try {
      const data = await request('/api/admin/marketplaces/portals', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !adminMarketplaceEnabled }),
      });
      adminMarketplaceEnabled = data.enabled === true;
      notice = lang === 'ru'
        ? (adminMarketplaceEnabled ? 'Каталог Portals включён.' : 'Каталог Portals выключен.')
        : (adminMarketplaceEnabled ? 'Portals catalogue enabled.' : 'Portals catalogue disabled.');
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      adminBusy = false;
    }
  }

  async function telegramLogin(showNotice = true) {
    const initData = window.Telegram?.WebApp?.initData || '';
    if (!initData) {
      isAdmin = false;
      signedIn = false;
      if (showNotice) notice = t.authHint;
      return;
    }
    // Fail closed while refreshing the signed Telegram session.
    isAdmin = false;
    signedIn = false;
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
      isAdmin = data.is_admin === true;
      signedIn = true;
      if (isAdmin) await loadAdminOverview();
      await loadAccountFavorites();
      await loadOffersAndDeals();
      if (showNotice) notice = t.signed;
    } catch (e) {
      isAdmin = false;
      signedIn = false;
      telegramUser = '';
      telegramPhoto = '';
      telegramUsername = '';
      telegramPremium = false;
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
    void loadApiStatus();
    if (window.Telegram?.WebApp?.initData) void telegramLogin(false);
  });
</script>

<svelte:head>
  <title>GIFT RADAR — Marketplace</title>
  <meta name="theme-color" content="#090B18" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
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
      <div class="top-actions"><span class="demo-pill">{t.readonly}</span><button class="language" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang.toUpperCase()}</button><button class="avatar-button" onclick={()=>view='profile'}>{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser ? telegramUser.slice(0,1).toUpperCase() : 'G'}</span>{/if}</button></div>
    </header>

    {#if view==='market'}
      <section class="hero hero-market">
        <div class="hero-copy">
          <div class="eyebrow"><span class="spark"></span> {t.brandEyebrow}</div>
          <h1>{t.heroLine1}<br/><span>{t.heroLine2}</span></h1>
          <p>{t.heroDescription}</p>
          <div class="hero-actions"><button class="search-button" onclick={()=>document.getElementById('catalogue')?.scrollIntoView({behavior:'smooth',block:'start'})}>{t.openCatalogue} <span>↘</span></button><span class="hero-live"><i></i> {t.connectedLabel}</span></div>
        </div>
        <div class="hero-art" aria-hidden="true">
          {#each collections.slice(0,3) as collection, index}
            <div class={'collection-orb orb-'+index}>{#if collection.image}<img src={collection.image} alt=""/>{:else}<span>✳</span>{/if}</div>
          {/each}
          <span class="float-chip chip-a">TELEGRAM GIFTS</span><span class="float-chip chip-b">PORTALS DATA</span><span class="float-chip chip-c">COLLECT · DISCOVER</span>
        </div>
      </section>
      {#if collections.length}
        <section class="collection-shelf">
          <div class="shelf-heading"><div><div class="eyebrow">{t.exploreMarket}</div><h2>{t.collectionsTitle}</h2></div><span>{collections.length} {t.collectionsCount}</span></div>
          <div class="collection-rail">
            {#each collections.slice(0,8) as collection (String(collection.id ?? collection.short_name ?? collection.name))}
              <button class:chosen={collectionFilter===String(collection.id ?? collection.short_name ?? '')} class="collection-tile" onclick={()=>{collectionFilter=String(collection.id ?? collection.short_name ?? 'all');loadCatalogue();}}>
                <span class="collection-image">{#if collection.image}<img src={collection.image} alt="" loading="lazy"/>{:else}<span>✳</span>{/if}</span>
                <span class="collection-info"><strong>{collection.name || collection.short_name || 'Telegram Gifts'}</strong><small>{collection.short_name || 'PORTALS COLLECTION'}</small></span>
                <span class="collection-arrow">↗</span>
              </button>
            {/each}
          </div>
        </section>
      {/if}
      <section class="market-stats" aria-label="Catalogue overview"><article><span>▦</span><div><strong>{total.toLocaleString()}</strong><small>{t.statsCatalog}</small></div></article><p>{t.catalogOnly}</p></section>
      <section class="source-status-panel" aria-label={t.sourceStatusTitle}>
        <div class="source-status-heading">
          <div><div class="eyebrow">{t.sourceStatusTitle}</div><h2>{t.sourceStatusSubtitle}</h2></div>
          <div class="source-status-actions">
            {#if statusCheckedAt}<span>{t.sourceStatusChecked} {new Date(statusCheckedAt).toLocaleTimeString(lang==='ru'?'ru-RU':'en-US')}</span>{/if}
            <button class="refresh-button" onclick={loadApiStatus} disabled={statusBusy}>{statusBusy?'…':t.refresh}</button>
          </div>
        </div>
        {#if apiStatus?.sources}
          <div class="source-status-grid">
            <article class="source-status-item"><div><strong>{t.sourcePortals}</strong><span class={'source-status-dot '+sourceStatusClass(apiStatus.sources.portals)}></span></div><small>Portals</small><b>{sourceStatusText(apiStatus.sources.portals)}</b></article>
            <article class="source-status-item"><div><strong>{t.sourceChanges}</strong><span class={'source-status-dot '+sourceStatusClass(apiStatus.sources.gift_changes)}></span></div><small>Gift Changes API</small><b>{sourceStatusText(apiStatus.sources.gift_changes)}</b></article>
            <article class="source-status-item"><div><strong>{t.sourceDailyFloors}</strong><span class={'source-status-dot '+sourceStatusClass(apiStatus.sources.tg_gifts_daily)}></span></div><small>TG Gifts</small><b>{sourceStatusText(apiStatus.sources.tg_gifts_daily)}</b></article>
          </div>
          {#if apiStatus.data_pipeline?.refresh_interval_seconds}<p class="source-status-footnote">{t.sourceRefreshInterval}: {apiStatus.data_pipeline.refresh_interval_seconds}s</p>{/if}
        {:else if statusError}
          <p class="source-status-message">{t.sourceStatusUnavailable}</p>
        {:else}
          <p class="source-status-message">{t.sourceStatusChecking}</p>
        {/if}
      </section>
      <section class="market-section" id="catalogue">
        <div class="section-title"><div><div class="eyebrow">{t.discoverLabel}</div><h2>{t.listings}</h2><p>{total.toLocaleString()} {t.results}</p></div><button class="refresh-button" onclick={()=>loadCatalogue()} disabled={busy}>↻ {t.refresh}</button></div>
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
        {#if busy}<div class="state-panel"><div class="loader"></div>{t.loading}</div>
        {:else if error}<div class="state-panel error-state">{t.failed}<small>{error}</small><button class="search-button" onclick={()=>loadCatalogue()}>{t.refresh}</button></div>
        {:else if gifts.length===0}<div class="state-panel">{t.empty}</div>
        {:else}
          <div class="gift-grid">
            {#each gifts as gift (keyOf(gift))}
              <article class="gift-card">
                <button class="gift-visual" onclick={()=>void openGift(gift)} aria-label={t.details}>
                  <GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-media"/>
                  <span class="source-tag">PORTALS</span>
                </button>
                <div class="gift-card-body"><div class="gift-card-title">{gift.name||'Telegram Gift'} <span>{gift.number?'#'+gift.number:''}</span></div><div class="gift-card-meta">Telegram collectible · {gift.status||'Listed'}</div><div class="gift-card-bottom"><div><small>{t.price}</small><strong>{fmt(gift.price,gift.currency||'TON')}</strong></div><button class:hearted={favorites.includes(keyOf(gift))} class="heart-button" onclick={()=>toggleFavorite(gift)}>{favorites.includes(keyOf(gift))?'♥':'♡'}</button></div><button class="card-details" onclick={()=>void openGift(gift)}>{t.details} <span>↗</span></button></div>
              </article>
            {/each}
          </div>
          {#if gifts.length<total}<div class="load-more-wrap"><button class="load-more" onclick={()=>loadCatalogue(true)} disabled={loadingMore}>{loadingMore?'…':t.loadMore}</button></div>{/if}
        {/if}
      </section>
    {:else if view==='orders'}
      <section class="page-heading"><div class="eyebrow">YOUR ACTIVITY</div><h1>{t.ordersTitle}</h1><p>{t.createHint}</p></section>
      <div class="order-filter-bar"><label>{t.orderStatus}<select class="filter-input" bind:value={orderStatusFilter} onchange={changeOrderFilters}><option value="all">{t.all}</option><option value="active">{t.activeOrders}</option><option value="matched">{t.matchedOrders}</option><option value="cancelled">{t.cancelledOrders}</option></select></label><label>{t.orderKind}<select class="filter-input" bind:value={orderKindFilter} onchange={changeOrderFilters}><option value="all">{t.all}</option><option value="listing">{t.listingsOnly}</option><option value="buy-order">{t.buyRequestsOnly}</option></select></label></div>{#if visibleOrders.length===0}<div class="state-panel">{t.noOrders}</div>{:else}<div class="orders-list">{#each visibleOrders as order (order.id)}<article class="order-card"><div class="order-icon">{order.kind==='listing'?'↗':'⌕'}</div><div class="order-main"><div class="order-kind">{order.kind==='listing'?t.sell:t.buy}</div><h3>{order.title}</h3><p>{new Date(order.createdAt * 1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')} {'· '+order.owner} {order.note?'· '+order.note:''} {order.events?.length?'· '+order.events.map(event=>event.type==='created'?(lang==='ru'?'создан':'created'):event.type==='simulated_fill'?(lang==='ru'?'смоделировано исполнение':'simulated fill'):(lang==='ru'?'снят':'cancelled')).join(' → '):''}</p></div><div class="order-price"><strong>{order.price} {order.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class={'status-'+order.status}>{order.status==='active'?t.active:order.status==='matched'?t.matched:t.cancelled}</span></div>{#if order.status==='active' && order.isMine}
                  <div class="order-actions"><button class="match-button" onclick={()=>startEditOrder(order)}>{t.editOrder}</button><button class="cancel-button" onclick={()=>cancelOrder(order.id)}>{t.cancel}</button></div>
                  {#if editingOrderId===order.id}
                    <div class="order-edit-form">
                      <label>{t.editTitle}<input class="form-input" bind:value={editTitle} maxlength="120"/></label>
                      <label>{t.editPrice}<input class="form-input" type="number" min="0.01" step="0.01" bind:value={editPrice}/></label>
                      <label>{t.editNote}<textarea class="form-input note-input" bind:value={editNote} maxlength="300"></textarea></label>
                      <div class="order-actions"><button class="match-button" onclick={()=>saveOrderEdit(order.id)} disabled={orderEditBusy}>{orderEditBusy?'…':t.saveOrder}</button><button class="cancel-button" onclick={()=>editingOrderId=''} disabled={orderEditBusy}>{t.cancelEdit}</button></div>
                    </div>
                  {/if}{:else if order.status==='active' && signedIn}<div class="order-actions"><input class="form-input" type="number" min="0.01" step="0.01" bind:value={offerAmount} placeholder={t.offerAmount}/><button class="match-button" onclick={()=>createOffer(order.id)}>{t.sendOffer}</button></div>{/if}</article>{/each}</div>{#if publicOrdersOffset < publicOrdersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOrders} disabled={ordersLoadingMore}>{ordersLoadingMore?'…':t.loadMore}</button></div>{/if}{/if}
      <section class="market-section account-offers">
        <div class="section-title"><div><div class="eyebrow">ACCOUNT ACTIVITY</div><h2>{t.offersTitle}</h2></div><button class="refresh-button" onclick={()=>loadOffersAndDeals()} disabled={offersBusy}>↻ {t.refresh}</button></div>
        <label class="offer-filter-label">{t.offerStatusFilter}<select class="filter-input" value={offerStatusFilter} onchange={changeOfferStatus}><option value="all">{t.all}</option><option value="pending">{t.offerPending}</option><option value="accepted">{t.offerAccepted}</option><option value="declined">{t.offerDeclined}</option><option value="withdrawn">{t.offerWithdrawn}</option></select></label>
        {#if offersBusy}<div class="state-panel">{t.loading}</div>{:else if offers.length===0}<div class="state-panel">{t.noOffers}</div>{:else if visibleOffers.length===0}<div class="state-panel">{lang==='ru'?'Нет офферов с таким статусом.':'No offers with this status.'}</div>{:else}
          <div class="orders-list">{#each visibleOffers as offer (offer.id)}
            <article class="order-card"><div class="order-icon">↗</div><div class="order-main"><div class="order-kind">{offer.is_mine?t.outgoing:t.incoming} · {offer.order_title}</div><h3>{offer.amount} {offer.currency==='XTR'?'⭐ Stars':'TON'}</h3><p>{offer.note || offer.sender_name} · {new Date(offer.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><span class={'status-'+(offer.status==='pending'?'active':offer.status==='accepted'?'matched':'cancelled')}>{offerStatusLabel(offer.status)}</span></div>
            {#if offer.status==='pending' && offer.is_mine}<div class="order-actions"><button class="cancel-button" onclick={()=>respondToOffer(offer.id,'withdraw')} disabled={offerActionBusy===offer.id}>{offerActionBusy===offer.id?'…':t.withdrawOffer}</button></div>{:else if offer.status==='pending' && !offer.is_mine}<div class="order-actions"><button class="match-button" onclick={()=>respondToOffer(offer.id,'accept')} disabled={offerActionBusy===offer.id}>{offerActionBusy===offer.id?'…':t.acceptOffer}</button><button class="cancel-button" onclick={()=>respondToOffer(offer.id,'decline')} disabled={offerActionBusy===offer.id}>{t.declineOffer}</button></div>{/if}</article>
          {/each}</div>{/if}
        {#if offers.length < offersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOffers} disabled={offersLoadingMore}>{offersLoadingMore?'…':t.loadMore}</button></div>{/if}
         <div class="section-title"><div><div class="eyebrow">HISTORY</div><h2>{t.dealsTitle}</h2></div></div>
        {#if deals.length===0}<div class="state-panel">{t.noDeals}</div>{:else}<div class="orders-list">{#each deals as deal (deal.id)}<article class="order-card"><div class="order-icon">✓</div><div class="order-main"><div class="order-kind">{deal.title}</div><h3>{deal.amount} {deal.currency==='XTR'?'⭐ Stars':'TON'}</h3><p>{deal.listing_owner} · {deal.offer_sender} · {new Date(deal.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><span class="status-matched">{lang==='ru'?'Смоделирована':'Simulated'}</span></div></article>{/each}</div>{/if}
               {#if deals.length < dealsTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreDeals} disabled={dealsLoadingMore}>{dealsLoadingMore?'…':t.loadMore}</button></div>{/if}
       </section>
    
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
      {#if shownGifts.length}<div class="gift-grid">{#each shownGifts as gift (keyOf(gift))}<article class="gift-card"><button class="gift-visual" onclick={()=>void openGift(gift)}><GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-media"/></button><div class="gift-card-body"><div class="gift-card-title">{gift.name||'Telegram Gift'}</div><div class="gift-card-bottom"><strong>{fmt(gift.price,gift.currency||'TON')}</strong><button class="heart-button hearted" onclick={()=>toggleFavorite(gift)}>♥</button></div></div></article>{/each}</div>{:else}<div class="state-panel">{t.noFavorites}<button class="search-button" onclick={()=>view='market'}>{t.market}</button></div>{/if}
    {:else}
      <section class="page-heading"><div class="eyebrow">ACCOUNT CENTER</div><h1>{t.profile}</h1><p>{t.profileText}</p></section>
      <section class="profile-layout"><article class="profile-card profile-main"><div class="profile-avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser?telegramUser.slice(0,1).toUpperCase():'G'}</span>{/if}</div><div><h2>{telegramUser||t.guest}</h2><p>{signedIn?t.signed:t.authHint}</p>{#if telegramUsername}<small class="profile-handle">{telegramUsername}</small>{/if}{#if telegramPremium}<span class="profile-premium">TELEGRAM PREMIUM</span>{/if}</div><button class="publish-button" onclick={()=>telegramLogin()}>{t.signIn} →</button></article>
      <article class="profile-card account-stats-card">
        <div class="eyebrow">{t.accountStatsTitle}</div>
        <div class="account-stat-grid">
          <div><strong>{accountStats.active_orders}</strong><span>{t.activeOrdersStat}</span></div>
          <div><strong>{accountStats.completed_orders}</strong><span>{t.completedOrdersStat}</span></div>
          <div><strong>{accountStats.pending_offers_sent}</strong><span>{t.sentOffersStat}</span></div>
          <div><strong>{accountStats.pending_offers_received}</strong><span>{t.receivedOffersStat}</span></div>
          <div><strong>{accountStats.simulated_deals}</strong><span>{t.dealsTitle}</span></div>
        </div>
      </article>
      <article class="profile-card activity-card">
        <div class="notification-heading"><div><div class="eyebrow">{t.activityTitle}</div><strong>{accountActivity.length} / {activityTotal}</strong></div><span class="activity-demo-tag">DEMO LOG</span></div>
        {#if accountActivity.length}
          <div class="activity-timeline">
            {#each accountActivity as item (item.id)}
              <div class="activity-row">
                <span class="activity-marker"></span>
                <div class="activity-row-main"><strong>{activityLabel(item)}</strong><span>{item.title}</span><small>{item.actor_name} · {new Date(item.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</small></div>
                <div class="activity-row-value"><strong>{item.amount} {item.currency==='XTR'?'⭐':'TON'}</strong><small>{item.order_id}</small></div>
              </div>
            {/each}
          </div>
          {#if accountActivity.length < activityTotal}<button class="refresh-button activity-more" onclick={loadMoreActivity} disabled={activityBusy}>{activityBusy?'…':t.loadMoreActivity}</button>{/if}
        {:else}<div class="state-panel">{t.noActivity}</div>{/if}
      </article>
      <article class="profile-card notification-card">
        <div class="notification-heading"><div><div class="eyebrow">{t.notificationsTitle}</div><strong>{accountStats.unread_notifications} {lang==='ru'?'непрочитано':'unread'}</strong></div>
          {#if accountStats.unread_notifications}<button class="refresh-button" onclick={markAllNotificationsRead}>{t.markAllRead}</button>{/if}
        </div>
        {#if notifications.length}
          <div class="notification-list">
            {#each notifications as item (item.id)}
              <button class:unread={!item.read_at} class="notification-item" onclick={()=>markNotificationRead(item.id)}>
                <span class="notification-dot"></span>
                <span class="notification-copy"><strong>{notificationMessage(item)}</strong><small>{new Date(item.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</small></span>
                {#if !item.read_at}<span class="notification-new">{lang==='ru'?'Новое':'New'}</span>{/if}
              </button>
            {/each}
          </div>
          {#if notifications.length < notificationTotal}<button class="refresh-button activity-more" onclick={loadMoreNotifications} disabled={notificationsLoadingMore}>{notificationsLoadingMore?'…':t.loadMoreActivity}</button>{/if}
        {:else}<div class="state-panel">{t.noNotifications}</div>{/if}
      </article>
      <article class="profile-card"><div class="eyebrow">WALLET AREA</div><h2>{t.demoWallet}</h2><p>{t.demoWalletText}</p><div class="wallet-placeholder"><span>◈</span><div><strong>Wallet connection disabled</strong><small>Demo profile · no assets stored</small></div><b>DEMO</b></div></article><article class="profile-card"><div class="eyebrow">PREFERENCES</div><h2>GIFT RADAR</h2><p>Portals catalogue · TON / Stars demo labels · RU / EN</p><button class="language wide" onclick={()=>lang=lang==='ru'?'en':'ru'}>Language: {lang.toUpperCase()}</button></article>{#if isAdmin}<article class="profile-card"><div class="eyebrow">ADMINISTRATION</div><h2>{lang==='ru'?'Управление каталогом':'Catalogue controls'}</h2><p>{lang==='ru'?'Доступ подтверждён сервером Telegram. Здесь можно только включать или отключать отображение каталога Portals.':'Access verified by the server through Telegram. You can only enable or disable the Portals catalogue visibility here.'}</p><div class="wallet-placeholder"><span>◈</span><div><strong>{lang==='ru'?'Каталог Portals':'Portals catalogue'}</strong><small>{adminMarketplaceEnabled?(lang==='ru'?'Включён':'Enabled'):(lang==='ru'?'Отключён':'Disabled')}</small></div><b>ADMIN</b></div><button class="publish-button" onclick={toggleAdminMarketplace} disabled={adminBusy}>{adminBusy?'…':(adminMarketplaceEnabled?(lang==='ru'?'Отключить каталог':'Disable catalogue'):(lang==='ru'?'Включить каталог':'Enable catalogue'))}</button></article>{/if}</section>
    {/if}

    {#if notice}<div class="toast" role="status">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
    <footer class="footer"><span>✳ GIFT RADAR</span><span>PORTALS LIVE · READ ONLY</span><span>© 2026</span></footer>
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
      <div class="modal-content"><div class="eyebrow">PORTALS COLLECTIBLE</div><h2>{selected.name||'Telegram Gift'} {selected.number?'#'+selected.number:''}</h2><div class="modal-price"><span>{t.price}</span><strong>{fmt(selected.price,selected.currency||'TON')}</strong></div><div class="modal-price"><span>{t.floor}</span><strong>{fmt(selected.floor_price,selected.currency||'TON')}</strong></div><h3>{t.traits}</h3>{#each selected.attributes||[] as trait}<div class="trait-row"><span>{trait.type||trait.name}</span><strong>{trait.value||'—'}</strong></div>{/each}<h3>{lang==='ru'?'История данных':'Metadata history'}</h3>{#if historyLoading}<div class="trait-row"><span>{lang==='ru'?'Загружаем снимки…':'Loading snapshots…'}</span></div>{:else if selectedHistory.length}{#each selectedHistory as snapshot}<div class="trait-row"><span>{new Date(snapshot.observed_at * 1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</span><strong>{fmt(snapshot.item.price, String(snapshot.item.currency||'GRAM'))}</strong></div>{/each}{:else}<div class="trait-row"><span>{lang==='ru'?'История пока не накоплена':'No history snapshots yet'}</span></div>{/if}<button class="publish-button" onclick={()=>toggleFavorite(selected!)}>{favorites.includes(keyOf(selected))?t.saved:t.save} <span>♡</span></button></div>
    </div>
  </div>
{/if}
