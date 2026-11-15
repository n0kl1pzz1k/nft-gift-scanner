<script lang="ts">
  import { onMount } from 'svelte';
  import GiftMedia from './GiftMedia.svelte';
  import { TonConnectUI } from '@tonconnect/ui';
  import { Address, beginCell, toNano } from '@ton/ton';

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
    createdAt: number; updatedAt: number; owner: string; isMine: boolean; note?: string; seller_profile_id?: string | null; events?: {type:string;createdAt:number}[];
  };
  type DemoOffer = { id:string; order_id:string; sender_name:string; amount:string; currency:'TON'|'XTR'; note:string; status:'pending'|'accepted'|'declined'|'withdrawn'; created_at:number; is_mine:boolean; order_title:string; order_owner_id:string; order_owner_name:string; order_status:string };
  type DemoDeal = { id:string; order_id:string; offer_id:string; title:string; listing_owner:string; offer_sender:string; amount:string; currency:'TON'|'XTR'; created_at:number; status:string; simulation_only:boolean };
  type AccountNotification = { id:string; type:'offer_received'|'offer_accepted'|'offer_declined'|'offer_withdrawn'; offer_id:string; order_id:string; order_title:string; actor_name:string; created_at:number; read_at:number|null };
  type AccountStats = { active_orders:number; completed_orders:number; pending_offers_sent:number; pending_offers_received:number; simulated_deals:number; unread_notifications:number };
  type AccountActivity = { id:string; type:string; order_id:string; title:string; actor_name:string; created_at:number; amount:string; currency:'TON'|'XTR'; simulation_only:boolean };
  type OrderReport = { id:string; order_id:string; reporter_name:string; reason:'scam'|'misleading'|'inappropriate'|'spam'|'other'; details:string; status:'open'|'dismissed'|'removed'; created_at:number; order_title:string; order_status:string; order_owner:string };
  type SellerProfile = { profile_id:string; display_name:string; bio:string; created_at:number; updated_at:number; username:string };
  type PublicSellerProfile = SellerProfile & { active_listings:number; completed_trades:number; rating:number|null; listings:{id:string;title:string;price:string;currency:'TON'|'XTR';createdAt:number;note:string}[] };
  type MarketplaceStats = {
    active_listings: number; active_buy_requests: number; matched_orders: number;
    volume_ton: number; volume_stars: number; total_orders: number;
  };
  type NativeListing = { listing_id:string; price_raw:number; currency:'XTR'|'GRAM'|'USDT'|'tUSDT'; created_at:number; asset_type:string; network:'ton-mainnet'|'ton-testnet'; collection_address:string; token_id:string; item_address:string };
  type ApiStatus = {
    sources?: Record<string, string>;
    data_pipeline?: { refresh_interval_seconds?: number };
    note?: string;
  };
  type View = 'market' | 'orders' | 'create' | 'favorites' | 'profile' | 'seller' | 'inventory' | 'payments';

  const API = (import.meta.env.VITE_API_BASE_URL || window.API_BASE || 'https://nft-gift-scanner-production.up.railway.app').replace(/\/$/, '');
  let tonConnectUI = $state<TonConnectUI | null>(null);
  let connectedWallet = $state('');
  let walletNetwork = $state('');
  let walletAppName = $state('');
  let walletBusy = $state(false);
  let walletVerified = $state(false);
  let inventoryItems = $state<any[]>([]);
  let inventoryBusy = $state(false);
  let nftImportBusy = $state(false);
  let inventoryListingBusy = $state('');
  let inventoryCancelBusy = $state('');
  let nftAssetType = $state<'ton_nft'|'collectible_username'|'collectible_phone_number'|'testnet_mock_nft'>('ton_nft');
  let nftCollectionAddress = $state('');
  let nftTokenId = $state('');
  let nftItemAddress = $state('');
  let inventoryPrices = $state<Record<string,string>>({});
  let inventoryCurrencies = $state<Record<string,'GRAM'|'USDT'|'XTR'>>({});
  let testnetListings = $state<any[]>([]);
  let testnetListingsBusy = $state(false);
  let testnetDeal = $state<any | null>(null);
  let testnetIntent = $state<any | null>(null);
  let testnetPaymentBusy = $state(false);
  let testnetVerifyBusy = $state(false);
  let lang = $state<'ru' | 'en'>('ru');
  let view = $state<View>('market');
  let nativeListings = $state<NativeListing[]>([]);
  let nativeListingsTotal = $state(0);
  let nativeListingsBusy = $state(false);
  let nativeListingSearch = $state('');
  let nativeListingCurrency = $state('all');
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
  let sellerProfile = $state<SellerProfile | null>(null);
  let sellerDisplayName = $state('');
  let sellerBio = $state('');
  let sellerProfileBusy = $state(false);
  let publicSellerProfile = $state<PublicSellerProfile | null>(null);
  let publicSellerBusy = $state(false);
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
  let authToken = $state('');
  let isAdmin = $state(false);
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
  let reportingOrderId = $state('');
  let reportReason = $state<OrderReport['reason']>('other');
  let reportDetails = $state('');
  let reportBusy = $state(false);
  let adminReports = $state<OrderReport[]>([]);
  let adminReportsBusy = $state(false);
  let adminReportActionBusy = $state('');
  let collectionFilter = $state('all');
  let statusFilter = $state('listed');
  let sortBy = $state('listed_at desc');
  let orderStatusFilter = $state<'all' | 'active' | 'matched' | 'cancelled'>('active');
  let orderKindFilter = $state<'all' | 'listing' | 'buy-order'>('all');

  const ru = {
    market: 'Маркет', orders: 'Активность', create: 'Разместить', favorites: 'Избранное', profile: 'Профиль', readonly: 'ТОЛЬКО ПРОСМОТР', catalogOnly: 'Самостоятельный маркетплейс GIFT RADAR. Здесь публикуются собственные объявления и оформляются сделки.',
    headline: 'Подарки, которые находят своих владельцев', subtitle: 'Самостоятельный рынок Telegram-подарков. Объявления, ордера и сделки живут внутри GIFT RADAR.',
    brandEyebrow: 'ОТКРЫВАЙ РЕДКОЕ · GIFT RADAR', heroLine1: 'Telegram Gifts.', heroLine2: 'Открой редкие подарки.', heroDescription: 'Покупай и продавай NFT напрямую у пользователей GIFT RADAR. Собственные объявления, ордера и сделки без внешних маркетплейсов.', openCatalogue: 'Открыть каталог', connectedLabel: 'СОБСТВЕННЫЙ МАРКЕТПЛЕЙС · GIFT RADAR', exploreMarket: 'ИССЛЕДУЙ МАРКЕТ', collectionsTitle: 'Коллекции', collectionsCount: 'коллекций в каталоге', discoverLabel: 'ОТКРЫВАЙ', nativeCatalog: 'Собственный каталог GIFT RADAR', search: 'Название или номер подарка', min: 'Цена от', max: 'Цена до',
    listings: 'Объявления GIFT RADAR', results: 'найдено', loading: 'Загружаем каталог…', loadMore: 'Показать ещё',
    empty: 'Активных объявлений пока нет.', failed: 'Не удалось загрузить каталог.',
    details: 'О подарке', floor: 'Флор', price: 'Цена', traits: 'Характеристики',
    createTitle: 'Создать объявление', sell: 'Предложение', buy: 'Заявка на поиск', titleLabel: 'Название подарка',
    priceLabel: 'Цена (демо)', noteLabel: 'Комментарий', publish: 'Создать демо-объявление',
    createHint: 'Общие тестовые заявки видны всем пользователям GIFT RADAR. Реальные сделки и платежи не выполняются.',
    reportOrder: 'Пожаловаться', reportReason: 'Причина жалобы', reportDetails: 'Подробности', sendReport: 'Отправить жалобу', cancelReport: 'Отмена', reportSent: 'Жалоба отправлена на проверку.', reportScam: 'Мошенничество', reportMisleading: 'Вводящее в заблуждение объявление', reportInappropriate: 'Недопустимый контент', reportSpam: 'Спам', reportOther: 'Другое', adminReports: 'Жалобы на объявления', noAdminReports: 'Открытых жалоб нет.', dismissReport: 'Отклонить', removeReportedOrder: 'Снять объявление', reportResolved: 'Решение по жалобе сохранено.',
    ordersTitle: 'Ордера маркетплейса', noOrders: 'Пока нет активных предложений или заявок.',
    offersTitle: 'Офферы', dealsTitle: 'Демо-сделки', sendOffer: 'Отправить оффер', offerAmount: 'Сумма оффера', acceptOffer: 'Принять', declineOffer: 'Отклонить', withdrawOffer: 'Отозвать', noOffers: 'Офферов пока нет.', noDeals: 'Демо-сделок пока нет.', incoming: 'Входящий', outgoing: 'Исходящий', offerStatusFilter: 'Статус оффера', offerPending: 'Ожидает ответа', offerAccepted: 'Принят', offerDeclined: 'Отклонён', offerWithdrawn: 'Отозван',
    accountStatsTitle: 'Статистика аккаунта', activeOrdersStat: 'Активные заявки', completedOrdersStat: 'Завершённые', sentOffersStat: 'Мои офферы', receivedOffersStat: 'Входящие офферы', notificationsTitle: 'Уведомления', markAllRead: 'Прочитать все', noNotifications: 'Новых уведомлений нет.', offerReceivedNotice: 'отправил(а) оффер', offerAcceptedNotice: 'принят оффер по', offerDeclinedNotice: 'отклонил(а) оффер по', offerWithdrawnNotice: 'отозвал(а) оффер по', activityTitle: 'История активности', noActivity: 'Активность пока не появилась.', loadMoreActivity: 'Показать ещё', orderCreatedActivity: 'Создана заявка', orderUpdatedActivity: 'Заявка изменена', orderFilledActivity: 'Заявка отмечена как исполненная (демо)', orderCancelledActivity: 'Заявка снята', offerReceivedActivity: 'Получен оффер', offerAcceptedActivity: 'Оффер принят', offerDeclinedActivity: 'Оффер отклонён', offerWithdrawnActivity: 'Оффер отозван', dealCreatedActivity: 'Создана демо-сделка',
    active: 'Активен', matched: 'Смоделирован', cancelled: 'Отменён', cancel: 'Снять', match: 'Симулировать исполнение', editOrder: 'Изменить', saveOrder: 'Сохранить', cancelEdit: 'Отмена', editTitle: 'Название', editPrice: 'Демо-цена', editNote: 'Комментарий', orderUpdated: 'Объявление обновлено.',
    demo: 'GIFT RADAR · NATIVE MARKET', demoWallet: 'Кошелёк не подключён', demoWalletText: 'TON Connect и проверка владения доступны. Переводы NFT и реальные платежи включаются только после безопасного запуска эскроу.',
    signIn: 'Подтвердить Telegram', guest: 'Гость', authHint: 'Открой Mini App через Telegram, чтобы подтвердить профиль.',
    signed: 'Telegram подтверждён', profileText: 'Профиль Telegram и управление публичной карточкой продавца.', sellerCardTitle: 'Публичная карточка продавца', sellerDisplayName: 'Отображаемое имя', sellerBio: 'О себе', sellerSave: 'Сохранить профиль', sellerSaved: 'Профиль продавца обновлён.', sellerProfileId: 'Публичный ID',
    favoritesTitle: 'Избранные подарки', noFavorites: 'Добавьте подарки в избранное из каталога.',
    save: 'В избранное', saved: 'В избранном', currency: 'TON', success: 'Демо-запись добавлена.', statsListings: 'Активные предложения', statsBuys: 'Заявки на покупку', statsVolume: 'Демо-оборот', statsVolumeHint: 'Только смоделированные записи', statsCatalog: 'Активных лотов', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'Каталог GIFT RADAR', refresh: 'Обновить', all: 'Все', orderStatus: 'Статус', orderKind: 'Тип заявки', activeOrders: 'Активные', matchedOrders: 'Смоделированные', cancelledOrders: 'Снятые', listingsOnly: 'Предложения', buyRequestsOnly: 'Заявки на поиск', invalidPrice: 'Проверь диапазон цены: значения должны быть неотрицательными, а минимальная цена — не выше максимальной.',
    sourceStatusTitle: 'СТАТУС ПЛАТФОРМЫ', sourceStatusSubtitle: 'Собственная инфраструктура GIFT RADAR', sourceNative: 'Внутренний каталог', sourceChanges: 'Индексация подарков', sourceDailyFloors: 'История платформы', sourceStatusChecked: 'Проверено', sourceStatusChecking: 'Проверяем статус…', sourceStatusUnavailable: 'Не удалось получить статус API.', sourceRefreshInterval: 'Интервал обновления', sourceConfigured: 'Активен', sourceNeedsKey: 'Ожидает настройки', sourcePublic: 'Внутренний источник', sourceDaily: 'Внутренняя история', sourceUnknown: 'Нет данных',
  };
  const en = {
    market: 'Market', orders: 'Activity', create: 'Create', favorites: 'Favorites', profile: 'Profile', readonly: 'READ ONLY', catalogOnly: 'Live GIFT RADAR listings. Browse and search are available; trade execution is not enabled in GIFT RADAR.',
    headline: 'Discover gifts worth keeping', subtitle: 'A unified Telegram gifts experience powered by the GIFT RADAR catalogue.',
    brandEyebrow: 'DISCOVER THE RARE · GIFT RADAR', heroLine1: 'Telegram Gifts.', heroLine2: 'Discover rare finds.', heroDescription: 'Buy and sell NFTs directly with GIFT RADAR users. Native listings, orders and deals without external marketplaces.', openCatalogue: 'Explore catalogue', connectedLabel: 'GIFT RADAR · INDEPENDENT MARKET', exploreMarket: 'EXPLORE THE MARKET', collectionsTitle: 'Collections', collectionsCount: 'collections in catalogue', discoverLabel: 'DISCOVER', nativeCatalog: 'GIFT RADAR native catalogue', search: 'Gift name or number', min: 'Min price', max: 'Max price',
    listings: 'GIFT RADAR listings', results: 'found', loading: 'Loading catalogue…', loadMore: 'Load more',
    empty: 'No active native listings yet.', failed: 'Could not load the catalogue.',
    details: 'Gift details', floor: 'Floor', price: 'Price', traits: 'Attributes',
    createTitle: 'Create an order', sell: 'Sell listing', buy: 'Buy request', titleLabel: 'Gift name',
    priceLabel: 'Demo price', noteLabel: 'Note', publish: 'Create demo order',
    createHint: 'Shared demo orders are visible to GIFT RADAR users. No real trades or payments take place.',
    reportOrder: 'Report', reportReason: 'Report reason', reportDetails: 'Details', sendReport: 'Submit report', cancelReport: 'Cancel', reportSent: 'Report submitted for review.', reportScam: 'Scam', reportMisleading: 'Misleading listing', reportInappropriate: 'Inappropriate content', reportSpam: 'Spam', reportOther: 'Other', adminReports: 'Listing reports', noAdminReports: 'No open reports.', dismissReport: 'Dismiss', removeReportedOrder: 'Remove listing', reportResolved: 'Report action saved.',
    ordersTitle: 'Marketplace orders', noOrders: 'No active listings or requests yet.',
    offersTitle: 'Offers', dealsTitle: 'Demo deals', sendOffer: 'Send offer', offerAmount: 'Offer amount', acceptOffer: 'Accept', declineOffer: 'Decline', withdrawOffer: 'Withdraw', noOffers: 'No offers yet.', noDeals: 'No demo deals yet.', incoming: 'Incoming', outgoing: 'Outgoing', offerStatusFilter: 'Offer status', offerPending: 'Awaiting response', offerAccepted: 'Accepted', offerDeclined: 'Declined', offerWithdrawn: 'Withdrawn',
    accountStatsTitle: 'Account stats', activeOrdersStat: 'Active orders', completedOrdersStat: 'Completed', sentOffersStat: 'Offers sent', receivedOffersStat: 'Offers received', notificationsTitle: 'Notifications', markAllRead: 'Mark all read', noNotifications: 'No notifications yet.', offerReceivedNotice: 'sent you an offer for', offerAcceptedNotice: 'accepted your offer for', offerDeclinedNotice: 'declined your offer for', offerWithdrawnNotice: 'withdrew an offer for', activityTitle: 'Activity history', noActivity: 'No activity yet.', loadMoreActivity: 'Load more', orderCreatedActivity: 'Order created', orderUpdatedActivity: 'Order updated', orderFilledActivity: 'Order marked as filled (demo)', orderCancelledActivity: 'Order cancelled', offerReceivedActivity: 'Offer received', offerAcceptedActivity: 'Offer accepted', offerDeclinedActivity: 'Offer declined', offerWithdrawnActivity: 'Offer withdrawn', dealCreatedActivity: 'Demo deal created',
    active: 'Active', matched: 'Simulated', cancelled: 'Cancelled', cancel: 'Cancel', match: 'Simulate fill', editOrder: 'Edit', saveOrder: 'Save changes', cancelEdit: 'Discard', editTitle: 'Title', editPrice: 'Demo price', editNote: 'Note', orderUpdated: 'Order updated.',
    demo: 'GIFT RADAR · NATIVE MARKET', demoWallet: 'Wallet not connected', demoWalletText: 'TON Connect and ownership verification are available. NFT transfers and real payments stay disabled until escrow is safely released.',
    signIn: 'Verify Telegram', guest: 'Guest', authHint: 'Open the Mini App in Telegram to verify your profile.',
    signed: 'Telegram verified', profileText: 'Your Telegram account and public seller card.', sellerCardTitle: 'Public seller card', sellerDisplayName: 'Display name', sellerBio: 'About', sellerSave: 'Save profile', sellerSaved: 'Seller profile updated.', sellerProfileId: 'Public ID',
    favoritesTitle: 'Saved gifts', noFavorites: 'Save gifts from the catalogue to see them here.',
    save: 'Save', saved: 'Saved', currency: 'TON', success: 'Demo entry created.', statsListings: 'Active listings', statsBuys: 'Buy requests', statsVolume: 'Demo volume', statsVolumeHint: 'Simulated entries only', statsCatalog: 'Active listings', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'GIFT RADAR catalogue', refresh: 'Refresh', all: 'All', orderStatus: 'Status', orderKind: 'Order type', activeOrders: 'Active', matchedOrders: 'Simulated', cancelledOrders: 'Cancelled', listingsOnly: 'Listings', buyRequestsOnly: 'Buy requests', invalidPrice: 'Check the price range: values must be non-negative and the minimum cannot exceed the maximum.',
    sourceStatusTitle: 'PLATFORM STATUS', sourceStatusSubtitle: 'GIFT RADAR native infrastructure', sourceNative: 'Native catalogue', sourceChanges: 'Gift indexing', sourceDailyFloors: 'Platform history', sourceStatusChecked: 'Checked', sourceStatusChecking: 'Checking status…', sourceStatusUnavailable: 'Could not load API status.', sourceRefreshInterval: 'Refresh interval', sourceConfigured: 'Active', sourceNeedsKey: 'Setup pending', sourcePublic: 'Native source', sourceDaily: 'Internal history', sourceUnknown: 'No data',
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

  function reportReasonLabel(reason: OrderReport['reason']) {
    const labels = {scam:t.reportScam,misleading:t.reportMisleading,inappropriate:t.reportInappropriate,spam:t.reportSpam,other:t.reportOther};
    return labels[reason] || reason;
  }

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
    const headers: Record<string, string> = {};
    if (authToken) headers['X-Telegram-Init-Data'] = authToken;
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

  async function openSellerProfile(profileId: string) {
    if (!profileId || !profileId.startsWith('usr_')) return;
    publicSellerBusy = true;
    publicSellerProfile = null;
    view = 'seller';
    try {
      const data = await request('/api/marketplace/profiles/' + encodeURIComponent(profileId));
      publicSellerProfile = data.profile as PublicSellerProfile;
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
      view = 'orders';
    } finally {
      publicSellerBusy = false;
    }
  }

  async function loadSellerProfile() {
    if (!signedIn) { sellerProfile = null; return; }
    const data = await request('/api/marketplace/profiles/me');
    sellerProfile = data.profile as SellerProfile;
    sellerDisplayName = sellerProfile.display_name || '';
    sellerBio = sellerProfile.bio || '';
  }

  async function saveSellerProfile() {
    if (!signedIn || sellerProfileBusy) return;
    sellerProfileBusy = true;
    try {
      const data = await request('/api/marketplace/profiles/me', undefined, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: sellerDisplayName, bio: sellerBio }),
      });
      sellerProfile = data.profile as SellerProfile;
      sellerDisplayName = sellerProfile.display_name;
      sellerBio = sellerProfile.bio;
      notice = t.sellerSaved;
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      sellerProfileBusy = false;
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
      order_created:t.orderCreatedActivity, order_updated:t.orderUpdatedActivity, order_simulated_fill:t.orderFilledActivity,
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

  function openGift(gift: Gift) {
    selected = gift;
    selectedHistory = [];
    historyLoading = false;
  }

  async function loadNativeListings() {
    if (nativeListingsBusy) return;
    nativeListingsBusy = true;
    try {
      const params = new URLSearchParams({limit:'60',offset:'0'});
      if (nativeListingSearch.trim()) params.set('search', nativeListingSearch.trim());
      if (nativeListingCurrency !== 'all') params.set('currency', nativeListingCurrency);
      const data = await request('/api/inventory/listings?' + params.toString());
      nativeListings = Array.isArray(data.items) ? data.items : [];
      nativeListingsTotal = Number(data.total) || nativeListings.length;
    } catch (e) {
      nativeListings = [];
      nativeListingsTotal = 0;
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      nativeListingsBusy = false;
    }
  }

  function formatNativeListingPrice(listing: NativeListing) {
    const decimals = listing.currency === 'XTR' ? 0 : listing.currency === 'USDT' || listing.currency === 'tUSDT' ? 6 : 9;
    const amount = Number(listing.price_raw || 0) / (10 ** decimals);
    return new Intl.NumberFormat(lang === 'ru' ? 'ru-RU' : 'en-US', {maximumFractionDigits:decimals > 2 ? 4 : 2}).format(amount) + ' ' + listing.currency;
  }

  function shortAddress(value:string) {
    return value && value.length > 18 ? value.slice(0,8) + '…' + value.slice(-6) : value || '—';
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
      await loadAdminReports();
    } catch (e) {
      // Do not render privileged controls if the protected overview cannot be loaded.
      isAdmin = false;
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function loadAdminReports() {
    if (!isAdmin || adminReportsBusy) return;
    adminReportsBusy = true;
    try {
      const data = await request('/api/marketplace/admin/reports?status=open&limit=50');
      adminReports = Array.isArray(data.items) ? data.items as OrderReport[] : [];
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      adminReportsBusy = false;
    }
  }

  async function moderateOrderReport(id: string, action: 'dismiss'|'remove_order') {
    if (!isAdmin || adminReportActionBusy) return;
    adminReportActionBusy = id;
    try {
      await request('/api/marketplace/admin/reports/' + encodeURIComponent(id), undefined, {
        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action}),
      });
      adminReports = adminReports.filter(report => report.id !== id);
      notice = t.reportResolved;
      if (action === 'remove_order') await loadMarketplace();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      adminReportActionBusy = '';
    }
  }

  function startOrderReport(orderId: string) {
    reportingOrderId = orderId;
    reportReason = 'other';
    reportDetails = '';
  }

  async function submitOrderReport(orderId: string) {
    if (reportBusy) return;
    reportBusy = true;
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(orderId) + '/reports', undefined, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason:reportReason,details:reportDetails.trim()}),
      });
      reportingOrderId = '';
      reportDetails = '';
      notice = t.reportSent;
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      reportBusy = false;
    }
  }

  async function loadTestnetListings() {
    if (!signedIn) { testnetListings = []; return; }
    testnetListingsBusy = true;
    try {
      const data = await request('/api/inventory/listings?currency=USDT&limit=50&offset=0');
      testnetListings = Array.isArray(data.items) ? data.items : [];
    } catch (e) {
      testnetListings = [];
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      testnetListingsBusy = false;
    }
  }

  async function startTestnetPayment(listingId: string) {
    if (!signedIn || !walletVerified || walletNetwork !== '-3' || testnetPaymentBusy) {
      notice = lang === 'ru'
        ? (walletVerified && walletNetwork !== '-3' ? 'Для тестовой сделки подключи TON testnet-кошелёк и подтверди его через TON Proof.' : 'Сначала войди через Telegram и подтверди TON testnet-кошелёк в профиле.')
        : (walletVerified && walletNetwork !== '-3' ? 'Connect and verify a TON testnet wallet for this test deal.' : 'Sign in through Telegram and verify your TON testnet wallet first.');
      return;
    }
    testnetPaymentBusy = true;
    testnetIntent = null;
    try {
      const reservation = await request('/api/inventory/listings/' + encodeURIComponent(listingId) + '/reserve', undefined, { method: 'POST' });
      if (typeof reservation.reservation_id !== 'string') throw new Error('Reservation response is incomplete');
      const deal = await request('/api/inventory/reservations/' + encodeURIComponent(reservation.reservation_id) + '/deal', undefined, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      });
      if (typeof deal.deal_id !== 'string') throw new Error('Deal response is incomplete');
      testnetDeal = deal;
      const intent = await request('/api/inventory/deals/' + encodeURIComponent(deal.deal_id) + '/testnet-payment-intent', undefined, { method: 'POST' });
      if (intent.network !== 'testnet' || intent.currency !== 'tUSDT' || intent.route_id !== 'ton_usdt_testnet') {
        throw new Error('Backend returned an unexpected payment route');
      }
      testnetIntent = intent;
      notice = lang === 'ru'
        ? 'Тестовая сделка создана. Используй только tUSDT в TON testnet и точный комментарий платежа.'
        : 'Testnet deal created. Use only tUSDT on TON testnet with the exact payment comment.';
      await loadTestnetListings();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      testnetPaymentBusy = false;
    }
  }

  async function sendTestnetPayment() {
    if (!tonConnectUI || !walletVerified || walletNetwork !== '-3' || !testnetIntent || testnetPaymentBusy) {
      notice = lang === 'ru' ? 'Подключи и подтверди TON testnet-кошелёк перед оплатой.' : 'Connect and verify a TON testnet wallet before paying.';
      return;
    }
    testnetPaymentBusy = true;
    try {
      if (!connectedWallet || Address.parse(connectedWallet).toRawString() !== Address.parse(testnetIntent.payer_address).toRawString()) {
        throw new Error('Connected wallet does not match the payment intent');
      }
      if (!testnetIntent.payer_jetton_wallet_address || !testnetIntent.escrow_jetton_wallet_address) {
        throw new Error('Verified buyer or escrow Jetton wallet is missing');
      }
      const amount = BigInt(testnetIntent.amount_raw);
      const queryId = BigInt(testnetIntent.query_id);
      if (amount <= 0n || queryId <= 0n || testnetIntent.currency !== 'tUSDT' || testnetIntent.network !== 'testnet') {
        throw new Error('Payment intent contains invalid testnet payment data');
      }
      if (testnetIntent.reference !== 'grpay:' + testnetDeal?.deal_id) {
        throw new Error('Payment reference does not match the active deal');
      }
      const forwardPayload = beginCell()
        .storeUint(0, 32)
        .storeStringTail(testnetIntent.reference)
        .endCell();
      const transferBody = beginCell()
        .storeUint(0x0f8a7ea5, 32)
        .storeUint(queryId, 64)
        .storeCoins(amount)
        .storeAddress(Address.parse(testnetIntent.escrow_address))
        .storeAddress(Address.parse(connectedWallet))
        .storeBit(0)
        .storeCoins(toNano('0.05'))
        .storeBit(1)
        .storeRef(forwardPayload)
        .endCell();
      await tonConnectUI.sendTransaction({
        validUntil: Math.floor(Date.now() / 1000) + 600,
        network: '-3',
        messages: [{
          address: testnetIntent.payer_jetton_wallet_address,
          amount: toNano('0.2').toString(),
          payload: transferBody.toBoc().toString('base64'),
        }],
      });
      notice = lang === 'ru'
        ? 'Транзакция отправлена кошельком. Ожидаем финализацию и сверку TON Center + TonAPI. Сделка пока не считается оплаченной.'
        : 'Wallet submitted the transaction. Waiting for finalization and TON Center + TonAPI reconciliation. The deal is not marked paid yet.';
      await refreshTestnetDeal();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      testnetPaymentBusy = false;
    }
  }

  async function refreshTestnetDeal() {
    if (!testnetDeal?.deal_id) return;
    try {
      const latest = await request('/api/inventory/deals/' + encodeURIComponent(testnetDeal.deal_id));
      testnetDeal = latest;
      notice = lang === 'ru' ? 'Статус сделки обновлён.' : 'Deal status refreshed.';
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function verifyTestnetPayment() {
    if (!isAdmin || !testnetDeal?.deal_id || testnetVerifyBusy) return;
    testnetVerifyBusy = true;
    try {
      const result = await request('/api/inventory/deals/' + encodeURIComponent(testnetDeal.deal_id) + '/testnet-payment-confirmation', undefined, { method: 'POST' });
      if (result.confirmed) {
        testnetDeal = { ...testnetDeal, status: result.status || 'payment_confirmed' };
        notice = lang === 'ru' ? 'Платёж подтверждён независимыми TON-провайдерами.' : 'Payment confirmed by independent TON providers.';
      } else {
        notice = lang === 'ru' ? 'Платёж пока не финализирован или не найден. Сделка не отмечена оплаченной.' : 'Payment is not finalized or was not found. The deal remains unpaid.';
      }
      await refreshTestnetDeal();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      testnetVerifyBusy = false;
    }
  }

  async function copyTestnetValue(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      notice = lang === 'ru' ? 'Скопировано.' : 'Copied.';
    } catch {
      notice = lang === 'ru' ? 'Не удалось скопировать значение.' : 'Could not copy value.';
    }
  }

  async function loadMyInventory() {
    if (!signedIn) return;
    inventoryBusy = true;
    try {
      const data = await request('/api/inventory/mine');
      inventoryItems = Array.isArray(data.items) ? data.items : [];
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      inventoryBusy = false;
    }
  }

  async function importOwnedNft() {
    if (!signedIn || !walletVerified || nftImportBusy) {
      notice = lang==='ru' ? 'Сначала подключи и подтверди TON-кошелёк.' : 'Connect and verify your TON wallet first.';
      return;
    }
    nftImportBusy = true;
    try {
      await request('/api/inventory/nft/import', undefined, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          asset_type:walletNetwork === '-3' ? 'testnet_mock_nft' : nftAssetType,
          network:walletNetwork === '-3' ? 'ton-testnet' : 'ton-mainnet',
          collection_address:nftCollectionAddress.trim(),
          token_id:nftTokenId.trim(), item_address:nftItemAddress.trim(),
        }),
      });
      notice = lang==='ru'
        ? 'NFT подтверждён двумя источниками и добавлен в твой инвентарь. Теперь создай лот.'
        : 'NFT verified by two providers and added to your inventory. Create a listing next.';
      nftCollectionAddress = '';
      nftTokenId = '';
      nftItemAddress = '';
      await loadMyInventory();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      nftImportBusy = false;
    }
  }

  function toRawListingPrice(value: string, currency: 'GRAM'|'USDT'|'XTR') {
    const decimals = currency === 'GRAM' ? 9 : currency === 'USDT' ? 6 : 0;
    const normalized = value.trim().replace(',', '.');
    if (!/^\d+(\.\d+)?$/.test(normalized)) throw new Error('Invalid listing price');
    const [whole, fraction=''] = normalized.split('.');
    if (fraction.length > decimals) throw new Error('Too many decimal places for selected currency');
    const raw = BigInt(whole) * (10n ** BigInt(decimals)) + BigInt((fraction + '0'.repeat(decimals)).slice(0, decimals) || '0');
    if (raw <= 0n || raw > 9000000000000000n) throw new Error('Listing price is outside the allowed range');
    return Number(raw);
  }

  async function createInventoryListing(assetId: string) {
    if (inventoryListingBusy) return;
    if (!signedIn || !walletVerified || !connectedWallet) {
      notice = lang==='ru'
        ? 'Сначала подключи TON-кошелёк и подтверди владение через TON Proof.'
        : 'Connect a TON wallet and verify ownership with TON Proof first.';
      return;
    }
    inventoryListingBusy = assetId;
    try {
      const currency = inventoryCurrencies[assetId] || 'GRAM';
      const priceRaw = toRawListingPrice(inventoryPrices[assetId] || '', currency);
      await request('/api/inventory/listings', undefined, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          asset_id:assetId, price_raw:priceRaw, currency,
          idempotency_key:crypto.randomUUID(),
        }),
      });
      notice = lang==='ru'
        ? 'Черновик лота создан. NFT всё ещё находится в твоём кошельке: перевод в эскроу пока не выполнялся, лот не опубликован.'
        : 'Listing draft created. The NFT remains in your wallet: no escrow transfer was sent and the listing is not public.';
      await loadMyInventory();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      inventoryListingBusy = '';
    }
  }


  async function cancelInventoryListing(listingId: string) {
    if (!signedIn || inventoryCancelBusy || !listingId) return;
    const item = inventoryItems.find((entry:any) => entry.listing_id === listingId);
    const isDraft = item?.status === 'draft';
    const confirmed = window.confirm(lang==='ru'
      ? (isDraft
          ? 'Удалить черновик? NFT останется в твоём TON-кошельке, блокчейн-перевод не выполняется.'
          : 'Снять объявление? NFT останется в эскроу. Для возврата потребуется отдельная безопасная транзакция.')
      : (isDraft
          ? 'Delete this draft? The NFT stays in your TON wallet; no blockchain transfer is made.'
          : 'Cancel this listing? The NFT remains in escrow and requires a separate safe return transaction.'));
    if (!confirmed) return;
    inventoryCancelBusy = listingId;
    try {
      await request('/api/inventory/listings/' + encodeURIComponent(listingId) + '/cancel', undefined, {method:'POST'});
      notice = lang==='ru'
        ? (isDraft
            ? 'Черновик удалён. NFT остался в твоём кошельке.'
            : 'Объявление снято. NFT остаётся в эскроу до отдельной безопасной процедуры возврата.')
        : (isDraft
            ? 'Draft deleted. The NFT remains in your wallet.'
            : 'Listing cancelled. The NFT remains in escrow pending a separate safe return process.');
      await loadMyInventory();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      inventoryCancelBusy = '';
    }
  }

  async function connectTonWallet() {
    if (!tonConnectUI || walletBusy) return;
    if (!signedIn) {
      notice = lang === 'ru' ? 'Сначала открой GIFT RADAR через Telegram.' : 'Sign in through Telegram first.';
      return;
    }
    walletBusy = true;
    walletVerified = false;
    try {
      const challenge = await request('/api/wallet/ton-proof/payload', undefined, { method: 'POST' });
      if (typeof challenge.payload !== 'string' || !challenge.payload) throw new Error('TON Proof challenge was not issued');
      tonConnectUI.setConnectRequestParameters({
        state: 'ready',
        value: { tonProof: challenge.payload },
      });
      // A restored session has no fresh ton_proof item. Reconnect after the user
      // explicitly asks to verify so the wallet signs this new challenge.
      if (tonConnectUI.wallet) await tonConnectUI.disconnect();
      const wallet = await tonConnectUI.connectWallet();
      if (!wallet) throw new Error('Wallet connection was cancelled');
      const proofItem = wallet.connectItems?.tonProof;
      if (!proofItem || !('proof' in proofItem)) {
        throw new Error('This wallet does not support TON Proof. Try a compatible TON wallet.');
      }
      const verified = await request('/api/wallet/ton-proof/verify', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          address: wallet.account.address,
          network: wallet.account.chain,
          public_key: wallet.account.publicKey,
          state_init: wallet.account.walletStateInit,
          proof: proofItem.proof,
        }),
      });
      if (verified.verified !== true) throw new Error('Server did not verify wallet ownership');
      connectedWallet = verified.address || wallet.account.address;
      walletAppName = wallet.device.appName || 'TON Connect';
      walletNetwork = String(verified.network || wallet.account.chain);
      if (!['-239', '-3'].includes(walletNetwork)) throw new Error('Unsupported TON network');
      walletVerified = verified.verified === true;
      if (!walletVerified) throw new Error('Server did not verify wallet ownership');
      if (walletNetwork === '-3') nftAssetType = 'testnet_mock_nft';
      else if (nftAssetType === 'testnet_mock_nft') nftAssetType = 'ton_nft';
      notice = lang === 'ru'
        ? (walletNetwork === '-3'
            ? 'TON testnet-кошелёк подтверждён. Можно импортировать только тестовые NFT, а платежи остаются тестовыми.'
            : 'TON mainnet-кошелёк подтверждён. Mainnet-активы проверяются отдельно от тестовой оплаты tUSDT.')
        : (walletNetwork === '-3'
            ? 'TON testnet wallet verified. Only testnet NFT assets can be imported; payments remain test-only.'
            : 'TON mainnet wallet verified. Mainnet assets are verified separately from testnet tUSDT payments.');
    } catch (e) {
      walletVerified = false;
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      tonConnectUI.setConnectRequestParameters(null);
      walletBusy = false;
    }
  }

  async function disconnectTonWallet() {
    if (!tonConnectUI || walletBusy) return;
    walletBusy = true;
    try {
      if (signedIn && connectedWallet && walletNetwork) {
        await request('/api/wallet/ton-proof/revoke?network=' + (walletNetwork === '-3' ? 'ton-testnet' : 'ton-mainnet'), undefined, { method:'POST' });
      }
      await tonConnectUI.disconnect();
      connectedWallet = '';
      walletAppName = '';
      walletNetwork = '';
      walletVerified = false;
      notice = lang==='ru' ? 'Кошелёк отключён, подтверждённая привязка отозвана.' : 'Wallet disconnected and verified binding revoked.';
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      walletBusy = false;
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
    authToken = '';
    try {
      const response = await fetch(API + '/api/auth/telegram', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: initData }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Authentication failed');
      if (typeof data.session_token !== 'string' || !data.session_token.startsWith('grs_')) throw new Error('Server did not issue a valid session');
      authToken = data.session_token;
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
      authToken = '';
      telegramUser = '';
      telegramPhoto = '';
      telegramUsername = '';
      telegramPremium = false;
      if (showNotice) notice = e instanceof Error ? e.message : String(e);
    }
  }

  onMount(() => {
    try {
      tonConnectUI = new TonConnectUI({
        manifestUrl: `${window.location.origin}/tonconnect-manifest.json`,
      });
      tonConnectUI.onStatusChange((wallet) => {
        const nextAddress = wallet?.account.address || '';
        const nextNetwork = wallet?.account.chain || '';
        if (nextAddress !== connectedWallet || nextNetwork !== walletNetwork) walletVerified = false;
        connectedWallet = nextAddress;
        walletNetwork = nextNetwork;
        walletAppName = wallet?.device.appName || '';
      });
    } catch (e) {
      notice = e instanceof Error ? e.message : 'TON Connect unavailable';
    }
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
    void loadNativeListings();
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
      {#each [{id:'market',label:t.market,icon:'▦'},{id:'inventory',label:lang==='ru'?'Мои NFT':'My NFTs',icon:'◈'},{id:'payments',label:lang==='ru'?'Платежи':'Payments',icon:'₮'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
        <button class:active={view===item.id} class="nav-item" onclick={()=>{view=item.id as View;notice='';if(item.id==='inventory')void loadMyInventory();if(item.id==='payments')void loadTestnetListings();}}><span>{item.icon}</span>{item.label}</button>
      {/each}
    </nav>
    <div class="sidebar-bottom"><div class="demo-card"><span class="demo-dot"></span>{t.demo}<p>{t.demoWalletText}</p></div><div class="sidebar-version">INDEPENDENT MARKET · GIFT RADAR</div></div>
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
          <span class="float-chip chip-a">TELEGRAM GIFTS</span><span class="float-chip chip-b">NATIVE MARKET</span><span class="float-chip chip-c">COLLECT · DISCOVER</span>
        </div>
      </section>

      <section class="market-stats" aria-label="Catalogue overview"><article><span>▦</span><div><strong>{nativeListingsTotal.toLocaleString()}</strong><small>{t.statsCatalog}</small></div></article><p>{t.catalogOnly}</p></section>
      <section class="source-status-panel native-market-panel" aria-label="GIFT RADAR platform status">
        <div class="source-status-heading">
          <div><div class="eyebrow">{lang==='ru'?'СВОЯ ИНФРАСТРУКТУРА':'OUR INFRASTRUCTURE'}</div><h2>{lang==='ru'?'Независимый маркетплейс':'Independent marketplace'}</h2></div>
          <span class="native-status-pill"><i></i>{lang==='ru'?'ВНУТРЕННЯЯ ПЛАТФОРМА':'NATIVE PLATFORM'}</span>
        </div>
        <div class="native-principles">
          <div><b>01</b><strong>{lang==='ru'?'Свои объявления':'Native listings'}</strong><span>{lang==='ru'?'Публикации создаются пользователями GIFT RADAR':'Listings are created by GIFT RADAR users'}</span></div>
          <div><b>02</b><strong>{lang==='ru'?'Свои ордера':'Native orders'}</strong><span>{lang==='ru'?'Офферы и сделки обрабатываются внутри платформы':'Offers and deals are handled inside the platform'}</span></div>
          <div><b>03</b><strong>{lang==='ru'?'Без чужих маркетов':'No external markets'}</strong><span>{lang==='ru'?'Чужие каталоги и агрегаторы отключены':'Third-party catalogues and aggregators are disabled'}</span></div>
        </div>
      </section>

      <section class="market-section" id="catalogue">
        <div class="section-title">
          <div><div class="eyebrow">{lang==='ru'?'СОБСТВЕННЫЕ ЛОТЫ':'NATIVE LISTINGS'}</div><h2>{lang==='ru'?'Объявления GIFT RADAR':'GIFT RADAR listings'}</h2><p>{nativeListingsTotal.toLocaleString()} {lang==='ru'?'активных лотов':'active listings'}</p></div>
          <button class="refresh-button" onclick={()=>void loadNativeListings()} disabled={nativeListingsBusy}>↻ {t.refresh}</button>
        </div>
        <div class="filter-bar">
          <div class="search-field"><span>⌕</span><input bind:value={nativeListingSearch} placeholder={lang==='ru'?'Адрес NFT или token ID':'NFT address or token ID'} onkeydown={e=>e.key==='Enter'&&loadNativeListings()}/></div>
          <select class="filter-input" bind:value={nativeListingCurrency} aria-label="Currency">
            <option value="all">{lang==='ru'?'Все валюты':'All currencies'}</option><option value="GRAM">GRAM</option><option value="USDT">USDT</option><option value="XTR">Stars</option><option value="tUSDT">tUSDT testnet</option>
          </select>
          <button class="search-button" onclick={()=>void loadNativeListings()}>{lang==='ru'?'Найти':'Search'}</button>
        </div>
        {#if nativeListingsBusy}
          <div class="state-panel"><div class="loader"></div>{lang==='ru'?'Загружаем собственные объявления…':'Loading native listings…'}</div>
        {:else if nativeListings.length===0}
          <div class="state-panel">
            <strong>{lang==='ru'?'Пока нет активных объявлений':'No active listings yet'}</strong>
            <p>{lang==='ru'?'Здесь будут только NFT, которые пользователи разместили непосредственно на GIFT RADAR и передали в проверяемый эскроу. Чужие каталоги и API маркетплейсов не используются.':'Only NFTs listed directly on GIFT RADAR and deposited into verified escrow appear here. No external marketplace catalogues or APIs are used.'}</p>
            <button class="search-button" onclick={()=>{view='inventory';if(signedIn)void loadMyInventory();}}>{lang==='ru'?'Добавить свой NFT':'List your NFT'}</button>
          </div>
        {:else}
          <div class="gift-grid">
            {#each nativeListings as listing (listing.listing_id)}
              <article class="gift-card">
                <div class="gift-visual">
                  <div class="native-nft-placeholder"><span>✳</span><small>{listing.asset_type.replaceAll('_',' ').toUpperCase()}</small></div>
                  <span class="source-tag">GIFT RADAR ESCROW</span>
                </div>
                <div class="gift-card-body">
                  <div class="gift-card-title">NFT #{listing.token_id}</div>
                  <div class="gift-card-meta">{listing.network==='ton-testnet'?'TON TESTNET':'TON MAINNET'} · {shortAddress(listing.collection_address)}</div>
                  <div class="gift-card-bottom"><div><small>{lang==='ru'?'Цена продавца':'Seller price'}</small><strong>{formatNativeListingPrice(listing)}</strong></div></div>
                  <div class="gift-card-meta">{lang==='ru'?'NFT в проверенном эскроу':'NFT held in verified escrow'} · {new Date(listing.created_at*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</div>
                  <button class="card-details" onclick={()=>void copyTestnetValue(listing.item_address)}>{lang==='ru'?'Скопировать адрес NFT':'Copy NFT address'} <span>↗</span></button>
                </div>
              </article>
            {/each}
          </div>
        {/if}
      </section>
    {:else if view==='seller'}
      <section class="page-heading"><div class="eyebrow">GIFT RADAR · SELLER</div><h1>{lang==='ru'?'Профиль продавца':'Seller profile'}</h1><p>{lang==='ru'?'Публичная информация и активные объявления':'Public information and active listings'}</p></section>
      {#if publicSellerBusy}<div class="state-panel">{t.loading}</div>
      {:else if publicSellerProfile}
        <section class="public-seller-layout">
          <article class="profile-card public-seller-card">
            <div class="profile-avatar"><span>{publicSellerProfile.display_name.slice(0,1).toUpperCase()}</span></div>
            <div class="public-seller-identity"><h2>{publicSellerProfile.display_name}</h2>{#if publicSellerProfile.username}<p>@{publicSellerProfile.username}</p>{/if}<p>{publicSellerProfile.bio || (lang==='ru'?'Продавец GIFT RADAR':'GIFT RADAR seller')}</p><small>{lang==='ru'?'На платформе с':'Member since'} {new Date(publicSellerProfile.created_at*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</small></div>
            <div class="public-seller-stats"><div><strong>{publicSellerProfile.active_listings}</strong><span>{lang==='ru'?'Активные объявления':'Active listings'}</span></div><div><strong>{publicSellerProfile.completed_trades}</strong><span>{lang==='ru'?'Завершённые сделки':'Completed trades'}</span></div></div>
            <small class="seller-reputation-note">{lang==='ru'?'Рейтинг появится после запуска проверяемых завершённых сделок.':'Ratings will appear after verified completed trades are supported.'}</small>
          </article>
          <div class="section-title"><div><div class="eyebrow">MARKETPLACE</div><h2>{lang==='ru'?'Объявления продавца':'Seller listings'}</h2></div><button class="refresh-button" onclick={()=>view='orders'}>{lang==='ru'?'Назад':'Back'}</button></div>
          {#if publicSellerProfile.listings.length}
            <div class="orders-list">{#each publicSellerProfile.listings as listing (listing.id)}
              <article class="order-card"><div class="order-icon">↗</div><div class="order-main"><div class="order-kind">{lang==='ru'?'Предложение':'Listing'}</div><h3>{listing.title}</h3><p>{listing.note || (lang==='ru'?'Без комментария':'No note')} · {new Date(listing.createdAt*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><strong>{listing.price} {listing.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class="status-active">{t.active}</span></div></article>
            {/each}</div>
          {:else}<div class="state-panel">{lang==='ru'?'У продавца пока нет активных объявлений.':'This seller has no active listings yet.'}</div>{/if}
        </section>
      {:else}<div class="state-panel">{lang==='ru'?'Профиль не найден.':'Seller profile not found.'}</div>{/if}
    {:else if view==='orders'}
      <section class="page-heading"><div class="eyebrow">YOUR ACTIVITY</div><h1>{t.ordersTitle}</h1><p>{t.createHint}</p></section>
      <div class="order-filter-bar"><label>{t.orderStatus}<select class="filter-input" bind:value={orderStatusFilter} onchange={changeOrderFilters}><option value="all">{t.all}</option><option value="active">{t.activeOrders}</option><option value="matched">{t.matchedOrders}</option><option value="cancelled">{t.cancelledOrders}</option></select></label><label>{t.orderKind}<select class="filter-input" bind:value={orderKindFilter} onchange={changeOrderFilters}><option value="all">{t.all}</option><option value="listing">{t.listingsOnly}</option><option value="buy-order">{t.buyRequestsOnly}</option></select></label></div>{#if visibleOrders.length===0}<div class="state-panel">{t.noOrders}</div>{:else}<div class="orders-list">{#each visibleOrders as order (order.id)}<article class="order-card"><div class="order-icon">{order.kind==='listing'?'↗':'⌕'}</div><div class="order-main"><div class="order-kind">{order.kind==='listing'?t.sell:t.buy}</div><h3>{order.title}</h3><p>{new Date(order.createdAt * 1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')} {'· '+order.owner} {order.note?'· '+order.note:''} {order.events?.length?'· '+order.events.map(event=>event.type==='created'?(lang==='ru'?'создан':'created'):event.type==='updated'?(lang==='ru'?'изменено':'updated'):event.type==='simulated_fill'?(lang==='ru'?'смоделировано исполнение':'simulated fill'):(lang==='ru'?'снят':'cancelled')).join(' → '):''}</p>{#if order.seller_profile_id}<button class="seller-link" onclick={()=>openSellerProfile(order.seller_profile_id!)}>{lang==='ru'?'Профиль продавца':'Seller profile'} ↗</button>{/if}</div><div class="order-price"><strong>{order.price} {order.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class={'status-'+order.status}>{order.status==='active'?t.active:order.status==='matched'?t.matched:t.cancelled}</span></div>{#if order.status==='active' && order.isMine}
                  <div class="order-actions"><button class="match-button" onclick={()=>startEditOrder(order)}>{t.editOrder}</button><button class="cancel-button" onclick={()=>cancelOrder(order.id)}>{t.cancel}</button></div>
                  {#if editingOrderId===order.id}
                    <div class="order-edit-form">
                      <label>{t.editTitle}<input class="form-input" bind:value={editTitle} maxlength="120"/></label>
                      <label>{t.editPrice}<input class="form-input" type="number" min="0.01" step="0.01" bind:value={editPrice}/></label>
                      <label>{t.editNote}<textarea class="form-input note-input" bind:value={editNote} maxlength="300"></textarea></label>
                      <div class="order-actions"><button class="match-button" onclick={()=>saveOrderEdit(order.id)} disabled={orderEditBusy}>{orderEditBusy?'…':t.saveOrder}</button><button class="cancel-button" onclick={()=>editingOrderId=''} disabled={orderEditBusy}>{t.cancelEdit}</button></div>
                    </div>
                  {/if}{:else if order.status==='active' && signedIn}<div class="order-actions"><input class="form-input" type="number" min="0.01" step="0.01" bind:value={offerAmount} placeholder={t.offerAmount}/><button class="match-button" onclick={()=>createOffer(order.id)}>{t.sendOffer}</button><button class="cancel-button" onclick={()=>startOrderReport(order.id)}>{t.reportOrder}</button></div>{#if reportingOrderId===order.id}<div class="order-edit-form"><label>{t.reportReason}<select class="form-input" bind:value={reportReason}><option value="scam">{t.reportScam}</option><option value="misleading">{t.reportMisleading}</option><option value="inappropriate">{t.reportInappropriate}</option><option value="spam">{t.reportSpam}</option><option value="other">{t.reportOther}</option></select></label><label>{t.reportDetails}<textarea class="form-input note-input" bind:value={reportDetails} maxlength="500"></textarea></label><div class="order-actions"><button class="match-button" onclick={()=>submitOrderReport(order.id)} disabled={reportBusy}>{reportBusy?'…':t.sendReport}</button><button class="cancel-button" onclick={()=>reportingOrderId=''} disabled={reportBusy}>{t.cancelReport}</button></div></div>{/if}{/if}</article>{/each}</div>{#if publicOrdersOffset < publicOrdersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOrders} disabled={ordersLoadingMore}>{ordersLoadingMore?'…':t.loadMore}</button></div>{/if}{/if}
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
        {#if deals.length===0}<div class="state-panel">{t.noDeals}</div>{:else}<div class="orders-list">{#each deals as deal (deal.id)}<article class="order-card"><div class="order-icon">✓</div><div class="order-main"><div class="order-kind">{deal.title}</div><h3>{deal.amount} {deal.currency==='XTR'?'⭐ Stars':'TON · DEMO'}</h3><p>{deal.listing_owner} · {deal.offer_sender} · {new Date(deal.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><span class="status-payment-disabled">{lang==='ru'?'Оплата: Telegram Stars (XTR), пока отключена':'Telegram Stars (XTR) · not live yet'}</span></div></article>{/each}</div>{/if}
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
    {:else if view==='payments'}
      <section class="page-heading">
        <div class="eyebrow">TON TESTNET · PAYMENT CONSOLE</div>
        <h1>{lang==='ru'?'Платежи и эскроу':'Payments & escrow'}</h1>
        <p>{lang==='ru'?'Проверяемый тестовый маршрут сделки внутри GIFT RADAR. Здесь отображаются только реальные ответы backend, а не имитация оплаты.':'A verifiable test deal flow inside GIFT RADAR. This screen displays backend evidence, never simulated payment success.'}</p>
      </section>
      <section class="profile-card wallet-connect-card">
        <div class="eyebrow">TESTNET ROUTE · tUSDT</div>
        <h2>{lang==='ru'?'Тестовая оплата через TON escrow':'TON escrow test payment'}</h2>
        <p>{lang==='ru'?'Используется только платформенный тестовый Jetton tUSDT в TON testnet. Это не настоящий USDT, не mainnet и не реальные деньги. Stars и реальные платежи здесь не активированы.':'Platform-issued test Jetton tUSDT on TON testnet only. This is not real USDT, not mainnet and not real money. Stars and real payments are not enabled here.'}</p>
        <div class="account-stat-grid">
          <div><strong>{walletVerified?'✓':'—'}</strong><span>{lang==='ru'?'TON Proof':'TON Proof'}</span></div>
          <div><strong>{testnetListings.length}</strong><span>{lang==='ru'?'Доступные лоты':'Available listings'}</span></div>
          <div><strong>{testnetDeal?.status || '—'}</strong><span>{lang==='ru'?'Статус сделки':'Deal status'}</span></div>
        </div>
        {#if !signedIn}
          <div class="state-panel">{lang==='ru'?'Открой Mini App через Telegram, чтобы войти.':'Open the Mini App through Telegram to sign in.'}</div>
        {:else if !walletVerified}
          <div class="state-panel">{lang==='ru'?'Для создания тестовой сделки сначала подтверди кошелёк в разделе «Профиль».':'Verify your wallet in Profile before creating a test deal.'}<button class="publish-button" onclick={()=>view='profile'}>{lang==='ru'?'Открыть профиль':'Open profile'}</button></div>
        {/if}
      </section>
      <section class="market-section">
        <div class="section-title"><div><div class="eyebrow">ESCROW-BACKED INVENTORY</div><h2>{lang==='ru'?'Лоты, готовые к резервированию':'Listings ready to reserve'}</h2></div><button class="refresh-button" onclick={loadTestnetListings} disabled={testnetListingsBusy}>{testnetListingsBusy?'…':t.refresh}</button></div>
        {#if testnetListingsBusy}
          <div class="state-panel">{t.loading}</div>
        {:else if testnetListings.length===0}
          <div class="state-panel">{lang==='ru'?'Пока нет активных NFT-лотов, подтверждённых в эскроу. Лот появится здесь только после реального подтверждения депозита в блокчейне.':'No active NFT listings with confirmed escrow custody yet. A listing appears here only after an on-chain deposit is verified.'}</div>
        {:else}
          <div class="orders-list">
            {#each testnetListings as listing (listing.listing_id)}
              <article class="order-card">
                <div class="order-icon">₮</div>
                <div class="order-main"><div class="order-kind">{listing.asset_type}</div><h3>{listing.collection_address} · #{listing.token_id}</h3><p>{listing.listing_id}</p></div>
                <div class="order-price"><strong>{(Number(listing.price_raw)/1000000).toFixed(2)} tUSDT</strong><span class="status-active">{lang==='ru'?'Эскроу подтверждён':'Escrow verified'}</span></div>
                <div class="order-actions"><button class="match-button" onclick={()=>startTestnetPayment(listing.listing_id)} disabled={!signedIn || !walletVerified || walletNetwork !== '-3' || testnetPaymentBusy}>{testnetPaymentBusy?(lang==='ru'?'Создаём…':'Creating…'):(lang==='ru'?'Купить через тестовый эскроу':'Buy via test escrow')}</button></div>
              </article>
            {/each}
          </div>
        {/if}
      </section>
      {#if testnetDeal}
        <section class="profile-card seller-profile-editor">
          <div class="notification-heading"><div><div class="eyebrow">DEAL TRACKING</div><h2>{lang==='ru'?'Тестовая сделка':'Test deal'}</h2></div><button class="refresh-button" onclick={refreshTestnetDeal}>{lang==='ru'?'Обновить статус':'Refresh status'}</button></div>
          <div class="trait-row"><span>Deal ID</span><strong>{testnetDeal.deal_id}</strong></div>
          <div class="trait-row"><span>{lang==='ru'?'Состояние':'State'}</span><strong>{testnetDeal.status}</strong></div>
          {#if testnetIntent}
            <div class="eyebrow">PAYMENT INTENT · {testnetIntent.network}</div>
            <div class="trait-row"><span>{lang==='ru'?'Сумма':'Amount'}</span><strong>{(Number(testnetIntent.amount_raw)/1000000).toFixed(2)} tUSDT</strong></div>
            <div class="trait-row"><span>{lang==='ru'?'Получатель escrow':'Escrow recipient'}</span><strong>{testnetIntent.escrow_address}</strong></div>
            <div class="trait-row"><span>Jetton master</span><strong>{testnetIntent.jetton_master}</strong></div>
            <div class="trait-row"><span>{lang==='ru'?'Jetton-кошелёк эскроу':'Escrow Jetton wallet'}</span><strong>{testnetIntent.escrow_jetton_wallet_address}</strong></div>
            <div class="trait-row"><span>{lang==='ru'?'Комментарий сделки':'Deal reference'}</span><strong>{testnetIntent.reference}</strong></div>
            <button class="publish-button" onclick={sendTestnetPayment} disabled={testnetPaymentBusy || !walletVerified || walletNetwork !== '-3'}>
              {testnetPaymentBusy?'…':lang==='ru'?'Оплатить tUSDT через TON Connect':'Pay tUSDT with TON Connect'} →
            </button>
            <div class="order-actions">
              <button class="match-button" onclick={()=>copyTestnetValue(testnetIntent.escrow_address)}>{lang==='ru'?'Копировать адрес эскроу':'Copy escrow address'}</button>
              <button class="match-button" onclick={()=>copyTestnetValue(testnetIntent.reference)}>{lang==='ru'?'Копировать reference':'Copy reference'}</button>
            </div>
            <p class="safety-note">{lang==='ru'?'Кошелёк покажет точную сумму tUSDT и запросит подтверждение. Используется только TON testnet. Никакие seed-фразы или приватные ключи не запрашиваются. После отправки платёж не считается подтверждённым, пока TON Center и TonAPI не увидят одну и ту же финализированную транзакцию.':'Your wallet will show the exact tUSDT amount and request approval. TON testnet only. No seed phrase or private key is requested. Submission is not payment confirmation until TON Center and TonAPI independently observe the same finalized transaction.'}</p>
          {:else}
            <div class="state-panel">{lang==='ru'?'Реквизиты платежа не получены. Backend мог отклонить маршрут или конфигурация тестовой сети не завершена.':'Payment intent was not returned. The backend may have rejected the route or testnet configuration is incomplete.'}</div>
          {/if}
          {#if isAdmin}
            <button class="publish-button" onclick={verifyTestnetPayment} disabled={testnetVerifyBusy || !testnetIntent}>{testnetVerifyBusy?'…':lang==='ru'?'Проверить платёж двумя провайдерами':'Verify payment with two providers'}</button>
            <small>{lang==='ru'?'Админская проверка только читает блокчейн. Она не подписывает и не отправляет транзакции.':'Admin verification is read-only. It does not sign or broadcast transactions.'}</small>
          {/if}
        </section>
      {/if}
    {:else if view==='inventory'}
      <section class="page-heading"><div class="eyebrow">SELLER INVENTORY</div><h1>{lang==='ru'?'Мои NFT':'My NFTs'}</h1><p>{lang==='ru'?'Только твои реальные коллекционные активы. Сначала докажи владение, затем отправь NFT в эскроу.':'Only collectibles you actually own. Prove ownership first, then transfer the NFT to escrow.'}</p></section>
      <section class="profile-card wallet-connect-card">
        <div class="eyebrow">TON CONNECT · TON PROOF</div>
        {#if walletVerified}
          <div class="wallet-address"><strong>{walletAppName || 'TON wallet'}</strong><code>{connectedWallet}</code></div>
          <span class="activity-demo-tag">{lang==='ru'?'ВЛАДЕНИЕ КОШЕЛЬКОМ ПОДТВЕРЖДЕНО':'WALLET OWNERSHIP VERIFIED'}</span>
        {:else}
          <p>{lang==='ru'?'Чтобы импортировать NFT, сначала подтверди свой TON-кошелёк в профиле.':'Verify your TON wallet in Profile before importing an NFT.'}</p>
          <button class="publish-button" onclick={()=>view='profile'}>{lang==='ru'?'Открыть профиль':'Open profile'}</button>
        {/if}
      </section>
      <section class="profile-card seller-profile-editor">
        <div class="eyebrow">{lang==='ru'?'ИМПОРТ NFT':'IMPORT NFT'}</div>
        <label>{lang==='ru'?'Тип актива':'Asset type'}
          <select class="form-input" bind:value={nftAssetType}>
            {#if walletNetwork === '-3'}
              <option value="testnet_mock_nft">{lang==='ru'?'Тестовый NFT (testnet)':'Test NFT (testnet)'}</option>
            {:else}
              <option value="ton_nft">TON NFT</option>
              <option value="collectible_username">{lang==='ru'?'Коллекционный username':'Collectible username'}</option>
              <option value="collectible_phone_number">{lang==='ru'?'Коллекционный номер +888':'Collectible +888 number'}</option>
            {/if}
          </select>
        </label>
        <label>{lang==='ru'?'Адрес коллекции':'Collection address'}<input class="form-input" bind:value={nftCollectionAddress} placeholder="EQ…"/></label>
        <label>{lang==='ru'?'Индекс / token ID':'Index / token ID'}<input class="form-input" bind:value={nftTokenId} placeholder="12345"/></label>
        <label>{lang==='ru'?'Адрес NFT item':'NFT item address'}<input class="form-input" bind:value={nftItemAddress} placeholder="EQ…"/></label>
        <button class="publish-button" onclick={importOwnedNft} disabled={!signedIn || !walletVerified || nftImportBusy || !nftCollectionAddress.trim() || !nftTokenId.trim() || !nftItemAddress.trim()}>{nftImportBusy?'…':lang==='ru'?'Проверить владение и импортировать':'Verify ownership and import'} →</button>
        <small>{lang==='ru'?'Проверка идёт по двум независимым TON-провайдерам. Скриншоты и введённые вручную данные не считаются доказательством.':'Ownership is checked against two independent TON providers. Screenshots and manual claims are not proof.'}</small>
      </section>
      <section class="market-section">
        <div class="section-title"><div><div class="eyebrow">ON-CHAIN INVENTORY</div><h2>{lang==='ru'?'Твои активы':'Your assets'}</h2></div><button class="refresh-button" onclick={loadMyInventory} disabled={inventoryBusy}>{inventoryBusy?'…':t.refresh}</button></div>
        {#if inventoryBusy}<div class="state-panel">{t.loading}</div>
        {:else if inventoryItems.length===0}<div class="state-panel">{lang==='ru'?'Пока нет импортированных NFT. Добавь свой актив выше.':'No imported NFTs yet. Import an asset above.'}</div>
        {:else}<div class="orders-list">{#each inventoryItems as item (item.asset_id)}
          <article class="order-card"><div class="order-icon">◈</div><div class="order-main"><div class="order-kind">{item.asset_type}</div><h3>{item.collection_address} · #{item.token_id}</h3><p>{item.custody_state} · {item.ownership_state}</p>
            {#if item.status==='active'}<span class="status-active">{lang==='ru'?'Опубликован':'Active listing'}</span>
              {#if item.listing_id}<button class="cancel-button" onclick={()=>cancelInventoryListing(item.listing_id)} disabled={inventoryCancelBusy===item.listing_id}>{inventoryCancelBusy===item.listing_id?'…':lang==='ru'?'Снять объявление':'Cancel listing'}</button>{/if}
            {:else if item.status==='draft'}<span class="status-payment-disabled">{lang==='ru'?'Черновик · NFT в твоём кошельке':'Draft · NFT remains in your wallet'}</span>
              {#if item.listing_id}<button class="cancel-button" onclick={()=>cancelInventoryListing(item.listing_id)} disabled={inventoryCancelBusy===item.listing_id}>{inventoryCancelBusy===item.listing_id?'…':lang==='ru'?'Удалить черновик':'Delete draft'}</button>{/if}
            {:else if item.status==='pending_verification'}<span class="status-payment-disabled">{lang==='ru'?'Ожидает проверки эскроу':'Awaiting escrow verification'}</span>
            {:else if !item.listing_id || item.status==='cancelled' || item.status==='expired' || item.status==='rejected'}
              <div class="order-edit-form">
                <label>{lang==='ru'?'Цена':'Price'}<input class="form-input" type="number" min="0.000000001" step="0.000000001" bind:value={inventoryPrices[item.asset_id]} placeholder="1"/></label>
                <label>{lang==='ru'?'Валюта':'Currency'}<select class="form-input" bind:value={inventoryCurrencies[item.asset_id]}><option value="GRAM">GRAM</option><option value="USDT">USDT</option><option value="XTR">⭐ Stars</option></select></label>
                <button class="match-button" onclick={()=>createInventoryListing(item.asset_id)} disabled={inventoryListingBusy===item.asset_id || !inventoryPrices[item.asset_id]}>{inventoryListingBusy===item.asset_id?'…':lang==='ru'?'Создать лот':'Create listing'}</button>
              </div>
            {/if}
          </div></article>
        {/each}</div>{/if}
      </section>
    {:else if view==='favorites'}
      <section class="page-heading"><div class="eyebrow">YOUR COLLECTION</div><h1>{t.favoritesTitle}</h1><p>{favorites.length} saved items</p></section>
      {#if shownGifts.length}<div class="gift-grid">{#each shownGifts as gift (keyOf(gift))}<article class="gift-card"><button class="gift-visual" onclick={()=>void openGift(gift)}><GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-media"/></button><div class="gift-card-body"><div class="gift-card-title">{gift.name||'Telegram Gift'}</div><div class="gift-card-bottom"><strong>{fmt(gift.price,gift.currency||'TON')}</strong><button class="heart-button hearted" onclick={()=>toggleFavorite(gift)}>♥</button></div></div></article>{/each}</div>{:else}<div class="state-panel">{t.noFavorites}<button class="search-button" onclick={()=>view='market'}>{t.market}</button></div>{/if}
    {:else}
      <section class="page-heading"><div class="eyebrow">ACCOUNT CENTER</div><h1>{t.profile}</h1><p>{t.profileText}</p></section>
      <section class="profile-layout"><article class="profile-card profile-main"><div class="profile-avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser?telegramUser.slice(0,1).toUpperCase():'G'}</span>{/if}</div><div><h2>{telegramUser||t.guest}</h2><p>{signedIn?t.signed:t.authHint}</p>{#if telegramUsername}<small class="profile-handle">{telegramUsername}</small>{/if}{#if telegramPremium}<span class="profile-premium">TELEGRAM PREMIUM</span>{/if}</div><button class="publish-button" onclick={()=>telegramLogin()}>{t.signIn} →</button></article>
      <article class="profile-card wallet-connect-card">
        <div class="eyebrow">TON CONNECT · NON-CUSTODIAL</div>
        <h2>{lang==='ru'?'Подключение TON-кошелька':'Connect TON wallet'}</h2>
        <p>{lang==='ru'?'Показывается только публичный адрес. Seed phrase, приватный ключ и подпись транзакции не запрашиваются.':'Only the public address is shown. No seed phrase, private key or transaction signature is requested.'}</p>
        {#if connectedWallet}
          <div class="wallet-address"><strong>{walletAppName || 'TON wallet'}</strong><code>{connectedWallet}</code></div>
          <span class="activity-demo-tag">{walletVerified?(lang==='ru'?'КОШЕЛЁК ПОДТВЕРЖДЁН':'WALLET VERIFIED'):(lang==='ru'?'НЕ ПОДТВЕРЖДЁН':'NOT VERIFIED')} · {walletNetwork === '-3' ? 'TON TESTNET' : walletNetwork === '-239' ? 'TON MAINNET' : 'NETWORK UNKNOWN'}</span>
          {#if !walletVerified}<button class="publish-button" onclick={connectTonWallet} disabled={walletBusy || !signedIn}>{walletBusy?'…':lang==='ru'?'Подтвердить TON Proof':'Verify TON Proof'}</button>{/if}
          <button class="cancel-button" onclick={disconnectTonWallet} disabled={walletBusy}>{lang==='ru'?'Отключить кошелёк':'Disconnect wallet'}</button>
        {:else}
          <span class="activity-demo-tag">{lang==='ru'?'КОШЕЛЁК НЕ ПОДКЛЮЧЁН':'WALLET NOT CONNECTED'}</span>
          <button class="publish-button" onclick={connectTonWallet} disabled={walletBusy || !tonConnectUI || !signedIn}>{walletBusy?'…':lang==='ru'?'Подключить через TON Connect':'Connect with TON Connect'}</button>
        {/if}
        <small>{lang==='ru'?'Подключение не означает оплату. Платежи и подпись отключены.':'Connection does not authorize payment. Payments and signing remain disabled.'}</small>
      </article>
      {#if signedIn}
         <article class="profile-card seller-profile-editor">
           <div class="eyebrow">{t.sellerCardTitle}</div>
           <label>{t.sellerDisplayName}<input class="form-input" bind:value={sellerDisplayName} maxlength="48" /></label>
           <label>{t.sellerBio}<textarea class="form-input note-input" bind:value={sellerBio} maxlength="280"></textarea></label>
           {#if sellerProfile}<small class="profile-handle">{t.sellerProfileId}: {sellerProfile.profile_id}</small>{/if}
           <button class="publish-button" onclick={saveSellerProfile} disabled={sellerProfileBusy || !sellerDisplayName.trim()}>{sellerProfileBusy?'…':t.sellerSave}</button>
         </article>
       {/if}
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
      <article class="profile-card"><div class="eyebrow">WALLET AREA</div><h2>{t.demoWallet}</h2><p>{t.demoWalletText}</p><div class="wallet-placeholder"><span>◈</span><div><strong>Wallet connection disabled</strong><small>Demo profile · no assets stored</small></div><b>DEMO</b></div></article><article class="profile-card"><div class="eyebrow">PREFERENCES</div><h2>GIFT RADAR</h2><p>Native marketplace · TON / Stars labels · RU / EN</p><button class="language wide" onclick={()=>lang=lang==='ru'?'en':'ru'}>Language: {lang.toUpperCase()}</button></article>{#if isAdmin}<article class="profile-card"><div class="notification-heading"><div><div class="eyebrow">MODERATION</div><h2>{t.adminReports}</h2></div><button class="refresh-button" onclick={loadAdminReports} disabled={adminReportsBusy}>{adminReportsBusy?'…':t.refresh}</button></div>{#if adminReportsBusy}<div class="state-panel">{t.loading}</div>{:else if adminReports.length===0}<div class="state-panel">{t.noAdminReports}</div>{:else}<div class="orders-list">{#each adminReports as report (report.id)}<article class="order-card"><div class="order-main"><div class="order-kind">{report.order_title} · {report.order_owner}</div><h3>{reportReasonLabel(report.reason)}</h3><p>{report.reporter_name} · {new Date(report.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p>{#if report.details}<p>{report.details}</p>{/if}</div><div class="order-actions"><button class="match-button" onclick={()=>moderateOrderReport(report.id,'dismiss')} disabled={adminReportActionBusy===report.id}>{t.dismissReport}</button><button class="cancel-button" onclick={()=>moderateOrderReport(report.id,'remove_order')} disabled={adminReportActionBusy===report.id}>{t.removeReportedOrder}</button></div></article>{/each}</div>{/if}</article>{/if}</section>
    {/if}

    {#if notice}<div class="toast" role="status">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
    <footer class="footer"><span>✳ GIFT RADAR</span><span>GIFT RADAR · NATIVE MARKET</span><span>© 2026</span></footer>
  </main>
</div>

<nav class="mobile-nav">
  {#each [{id:'market',label:t.market,icon:'▦'},{id:'inventory',label:'NFT',icon:'◈'},{id:'payments',label:lang==='ru'?'Платежи':'Pay',icon:'₮'},{id:'orders',label:t.orders,icon:'⇄'},{id:'create',label:t.create,icon:'＋'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
    <button class:active={view===item.id} onclick={()=>{view=item.id as View;notice='';if(item.id==='inventory')void loadMyInventory();if(item.id==='payments')void loadTestnetListings();}}><span>{item.icon}</span><small>{item.label}</small></button>
  {/each}
</nav>

{#if selected}
  <div class="modal-backdrop" role="presentation" onclick={()=>selected=null}>
    <div class="gift-modal" role="dialog" tabindex="-1" aria-modal="true" aria-label={selected.name||'Gift details'} onclick={e=>e.stopPropagation()} onkeydown={e=>e.stopPropagation()}>
      <button class="modal-close" onclick={()=>selected=null}>×</button>
      <div class="modal-media"><GiftMedia image={selected.image} animationUrl={selected.animation_url} hasAnimation={selected.has_animation} alt={selected.name||'Gift'} className="gift-media"/></div>
      <div class="modal-content"><div class="eyebrow">GIFT RADAR COLLECTIBLE</div><h2>{selected.name||'Telegram Gift'} {selected.number?'#'+selected.number:''}</h2><div class="modal-price"><span>{t.price}</span><strong>{fmt(selected.price,selected.currency||'TON')}</strong></div><div class="modal-price"><span>{t.floor}</span><strong>{fmt(selected.floor_price,selected.currency||'TON')}</strong></div><h3>{t.traits}</h3>{#each selected.attributes||[] as trait}<div class="trait-row"><span>{trait.type||trait.name}</span><strong>{trait.value||'—'}</strong></div>{/each}<h3>{lang==='ru'?'История данных':'Metadata history'}</h3>{#if historyLoading}<div class="trait-row"><span>{lang==='ru'?'Загружаем снимки…':'Loading snapshots…'}</span></div>{:else if selectedHistory.length}{#each selectedHistory as snapshot}<div class="trait-row"><span>{new Date(snapshot.observed_at * 1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</span><strong>{fmt(snapshot.item.price, String(snapshot.item.currency||'GRAM'))}</strong></div>{/each}{:else}<div class="trait-row"><span>{lang==='ru'?'История пока не накоплена':'No history snapshots yet'}</span></div>{/if}<button class="publish-button" onclick={()=>toggleFavorite(selected!)}>{favorites.includes(keyOf(selected))?t.saved:t.save} <span>♡</span></button></div>
    </div>
  </div>
{/if}
