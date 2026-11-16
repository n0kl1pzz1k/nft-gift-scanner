<script lang="ts">
  import { onMount } from 'svelte';
  import AssetCard from './lib/AssetCard.svelte';
  import BottomNav from './lib/BottomNav.svelte';

  type MarketplaceOrder = {
    id: string; kind: 'listing' | 'buy-order'; title: string;
    price: string | number; currency: 'TON' | 'XTR'; status: 'active' | 'matched' | 'cancelled';
    createdAt: number; updatedAt: number; owner: string; isMine: boolean; note?: string; seller_profile_id?: string | null; events?: {type:string;createdAt:number}[];
  };
  type MarketplaceOffer = { id:string; order_id:string; sender_name:string; amount:string; currency:'TON'|'XTR'; note:string; status:'pending'|'accepted'|'declined'|'withdrawn'; created_at:number; is_mine:boolean; order_title:string; order_owner_id:string; order_owner_name:string; order_status:string };
  
  type AccountNotification = { id:string; type:'offer_received'|'offer_accepted'|'offer_declined'|'offer_withdrawn'; offer_id:string; order_id:string; order_title:string; actor_name:string; created_at:number; read_at:number|null };
  type AccountStats = { active_orders:number; completed_orders:number; pending_offers_sent:number; pending_offers_received:number; unread_notifications:number };
  type AccountActivity = { id:string; type:string; order_id:string; title:string; actor_name:string; created_at:number; amount:string; currency:'TON'|'XTR'; };
  type OrderReport = { id:string; order_id:string; reporter_name:string; reason:'scam'|'misleading'|'inappropriate'|'spam'|'other'; details:string; status:'open'|'dismissed'|'removed'; created_at:number; order_title:string; order_status:string; order_owner:string };
  type SellerProfile = { profile_id:string; display_name:string; bio:string; created_at:number; updated_at:number; username:string };
  type PublicSellerProfile = SellerProfile & { active_listings:number; completed_trades:number; rating:number|null; listings:{id:string;title:string;price:string;currency:'TON'|'XTR';createdAt:number;note:string}[] };
  type MarketplaceStats = {
    active_listings: number; active_buy_requests: number; matched_orders: number;
    volume_ton: number; volume_stars: number; floor_ton: number | null; floor_stars: number | null; total_orders: number;
  };
  type View = 'orders' | 'offers' | 'create' | 'favorites' | 'profile' | 'seller';
  type AssetTypeFilter = 'all' | 'gift' | 'username' | 'number';

  const API = (import.meta.env.VITE_API_BASE_URL || window.API_BASE || 'https://hassle-nft-market.up.railway.app').replace(/\/$/, '');
  let lang = $state<'ru' | 'en'>('ru');
  let view = $state<View>('orders');
  let marketSearch = $state('');
  let marketplaceLoading = $state(true);
  let marketError = $state('');
  let createAssetType = $state<'gift'|'username'|'number'>('gift');
  let assetTypeFilter = $state<AssetTypeFilter>('all');
  let marketSort = $state<'recent'|'price-low'|'price-high'>('recent');
  let marketCurrency = $state<'all'|'TON'|'XTR'>('all');
  let marketMinPrice = $state('');
  let marketMaxPrice = $state('');
  let showSellModal = $state(false);
  let createOrderBusy = $state(false);
  let showOfferModal = $state(false);
  let selectedOfferOrder = $state<MarketplaceOrder | null>(null);
  let selectedAsset = $state<MarketplaceOrder | null>(null);
  let showAssetDetails = $state(false);
  let offerSubmitBusy = $state(false);
  let orders = $state<MarketplaceOrder[]>([]);
  let favoriteOrderIds = $state<string[]>([]);
  let publicOrdersTotal = $state(0);
  let publicOrdersOffset = $state(0);
  let ordersLoadingMore = $state(false);
  let offers = $state<MarketplaceOffer[]>([]);
  let offersTotal = $state(0);
  let offersLoadingMore = $state(false);
  let notifications = $state<AccountNotification[]>([]);
  let notificationTotal = $state(0);
  let notificationsLoadingMore = $state(false);
  let accountActivity = $state<AccountActivity[]>([]);
  let activityTotal = $state(0);
  let activityBusy = $state(false);
  let accountStats = $state<AccountStats>({active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,unread_notifications:0});
  let marketplaceStats = $state<MarketplaceStats>({active_listings:0,active_buy_requests:0,matched_orders:0,volume_ton:0,volume_stars:0,floor_ton:null,floor_stars:null,total_orders:0});
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
  let orderStatusFilter = $state<'all' | 'active' | 'matched' | 'cancelled'>('active');
  let orderKindFilter = $state<'all' | 'listing' | 'buy-order'>('all');

  const ru = {
    market: 'Маркет', orders: 'Маркет', create: 'Инвентарь', favorites: 'Избранное', profile: 'Профиль', readonly: 'HASSLE NFT', catalogOnly: 'Независимый маркетплейс Telegram NFT-подарков. Покупай, продавай и договаривайся напрямую.',
    headline: 'Твой рынок Telegram NFT-подарков', subtitle: 'Маркетплейс Telegram NFT-подарков от n0kl1pzz1k.',
    brandEyebrow: 'ПОКУПАЙ · ПРОДАВАЙ · HASSLE NFT', heroLine1: 'Telegram Gifts.', heroLine2: 'Твой маркет. Твои сделки.', heroDescription: 'Покупай, продавай и отправляй офферы на подарки, username и номера 888.', openCatalogue: 'Открыть маркет', connectedLabel: 'HASSLE NFT · HASSLE NFT MARKET', exploreMarket: 'МАРКЕТПЛЕЙС HASSLE NFT', collectionsTitle: 'Активы', collectionsCount: 'активов сообщества', discoverLabel: 'МАРКЕТ', portals: 'Активы сообщества', search: 'Название актива, username или номер', min: 'Цена от', max: 'Цена до',
    listings: 'Активы', results: 'найдено', loading: 'Загружаем маркетплейс…', loadMore: 'Показать ещё',
    empty: 'Пока нет подходящих активов.', failed: 'Не удалось загрузить маркетплейс.',
    details: 'Об активе', floor: 'Флор', price: 'Цена', traits: 'Характеристики',
    createTitle: 'Выставить актив', sell: 'Продажа актива', buy: 'Поиск актива', titleLabel: 'Название актива',
    priceLabel: 'Цена', noteLabel: 'Комментарий', publish: 'Выставить актив',
    createHint: 'Выставь Telegram-подарок, username или номер 888. Укажи цену в TON или Stars.',
    reportOrder: 'Пожаловаться', reportReason: 'Причина жалобы', reportDetails: 'Подробности', sendReport: 'Отправить жалобу', cancelReport: 'Отмена', reportSent: 'Жалоба отправлена на проверку.', reportScam: 'Мошенничество', reportMisleading: 'Вводящее в заблуждение описание', reportInappropriate: 'Недопустимый контент', reportSpam: 'Спам', reportOther: 'Другое', adminReports: 'Жалобы на активы', noAdminReports: 'Открытых жалоб нет.', dismissReport: 'Отклонить', removeReportedOrder: 'Снять актив', reportResolved: 'Решение по жалобе сохранено.',
    ordersTitle: 'Маркет', noOrders: 'Пока нет активных предложений или заявок.',
    offersTitle: 'Офферы', dealsTitle: 'История сделок', sendOffer: 'Отправить оффер', offerAmount: 'Сумма оффера', acceptOffer: 'Принять', declineOffer: 'Отклонить', withdrawOffer: 'Отозвать', noOffers: 'Офферов пока нет.', noDeals: 'История сделок пока пуста.', incoming: 'Входящий', outgoing: 'Исходящий', offerStatusFilter: 'Статус оффера', offerPending: 'Ожидает ответа', offerAccepted: 'Принят', offerDeclined: 'Отклонён', offerWithdrawn: 'Отозван',
    accountStatsTitle: 'Статистика аккаунта', activeOrdersStat: 'Активные позиции', completedOrdersStat: 'Завершённые', sentOffersStat: 'Мои офферы', receivedOffersStat: 'Входящие офферы', notificationsTitle: 'Уведомления', markAllRead: 'Прочитать все', noNotifications: 'Новых уведомлений нет.', offerReceivedNotice: 'отправил(а) оффер', offerAcceptedNotice: 'принят оффер по', offerDeclinedNotice: 'отклонил(а) оффер по', offerWithdrawnNotice: 'отозвал(а) оффер по', activityTitle: 'История активности', noActivity: 'Активность пока не появилась.', loadMoreActivity: 'Показать ещё', orderCreatedActivity: 'Актив выставлен', orderUpdatedActivity: 'Параметры актива изменены', orderFilledActivity: 'Позиция закрыта', orderCancelledActivity: 'Актив снят с продажи', offerReceivedActivity: 'Получен оффер', offerAcceptedActivity: 'Оффер принят', offerDeclinedActivity: 'Оффер отклонён', offerWithdrawnActivity: 'Оффер отозван', dealCreatedActivity: 'Создана сделка',
    active: 'Активен', matched: 'Закрыт', cancelled: 'Отменён', cancel: 'Снять', match: 'Исполнить', editOrder: 'Изменить', saveOrder: 'Сохранить', cancelEdit: 'Отмена', editTitle: 'Название', editPrice: 'Цена', editNote: 'Комментарий', orderUpdated: 'Параметры актива обновлены.',
    demo: 'HASSLE NFT MARKETPLACE', demoWallet: 'Подключение кошелька', demoWalletText: 'Подключение TON-кошелька появится в следующем этапе.',
    signIn: 'Подтвердить Telegram', guest: 'Гость', authHint: 'Открой Mini App через Telegram, чтобы подтвердить профиль.',
    signed: 'Telegram подтверждён', profileText: 'Профиль Telegram и управление публичной карточкой продавца.', sellerCardTitle: 'Публичная карточка продавца', sellerDisplayName: 'Отображаемое имя', sellerBio: 'О себе', sellerSave: 'Сохранить профиль', sellerSaved: 'Профиль продавца обновлён.', sellerProfileId: 'Публичный ID',
    favoritesTitle: 'Избранные активы', noFavorites: 'Сохранённых активов пока нет.',
    save: 'В избранное', saved: 'В избранном', currency: 'TON', success: 'Актив выставлен на маркет.', statsListings: 'Активные предложения', statsBuys: 'Заявки на покупку', statsVolume: 'Оборот', statsVolumeHint: 'По данным маркетплейса', statsCatalog: 'Активов всего', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'HASSLE NFT Market', refresh: 'Обновить', all: 'Все', orderStatus: 'Статус', orderKind: 'Тип заявки', activeOrders: 'Активные', matchedOrders: 'Закрытые', cancelledOrders: 'Снятые', listingsOnly: 'Активы на продаже', buyRequestsOnly: 'Поиск активов', invalidPrice: 'Проверь диапазон цены: значения должны быть неотрицательными, а минимальная цена — не выше максимальной.',
    sourceStatusTitle: 'СОСТОЯНИЕ ИСТОЧНИКОВ', sourceStatusSubtitle: 'Что подключено к приложению', sourceCatalogue: 'Внешний каталог', sourceChanges: 'Изменения подарков', sourceDailyFloors: 'История floor-цен', sourceStatusChecked: 'Проверено', sourceStatusChecking: 'Проверяем статус…', sourceStatusUnavailable: 'Не удалось получить статус API.', sourceRefreshInterval: 'Интервал фонового обновления', sourceConfigured: 'Настроен · только чтение', sourceNeedsKey: 'Требуется API-ключ', sourcePublic: 'Публичный источник', sourceDaily: 'Публичная ежедневная история', sourceUnknown: 'Нет данных',
  };
  const en = {
    market: 'Market', orders: 'Market', create: 'Inventory', favorites: 'Favorites', profile: 'Profile', readonly: 'HASSLE NFT', catalogOnly: 'An independent Telegram NFT gift marketplace. Buy, sell and negotiate directly.',
    headline: 'Discover gifts worth keeping', subtitle: 'HASSLE NFT is a Telegram NFT gift marketplace.',
    brandEyebrow: 'BUY · SELL · HASSLE NFT', heroLine1: 'Telegram Gifts.', heroLine2: 'Your market. Your deals.', heroDescription: 'Buy, sell and make offers on Telegram gifts, usernames and anonymous numbers.', openCatalogue: 'Open marketplace', connectedLabel: 'HASSLE NFT MARKETPLACE', exploreMarket: 'HASSLE NFT MARKETPLACE', collectionsTitle: 'Assets', collectionsCount: 'community assets', discoverLabel: 'MARKET', portals: 'Community assets', search: 'Asset name, username or number', min: 'Min price', max: 'Max price',
    listings: 'Marketplace assets', results: 'found', loading: 'Loading marketplace…', loadMore: 'Load more',
    empty: 'No matching assets yet.', failed: 'Could not load the marketplace.',
    details: 'Asset details', floor: 'Floor', price: 'Price', traits: 'Attributes',
    createTitle: 'List an asset', sell: 'Asset for sale', buy: 'Asset wanted', titleLabel: 'Asset name',
    priceLabel: 'Price', noteLabel: 'Note', publish: 'List asset',
    createHint: 'List a Telegram gift, username or anonymous number. Set your price in TON or Stars.',
    reportOrder: 'Report', reportReason: 'Report reason', reportDetails: 'Details', sendReport: 'Submit report', cancelReport: 'Cancel', reportSent: 'Report submitted for review.', reportScam: 'Scam', reportMisleading: 'Misleading asset details', reportInappropriate: 'Inappropriate content', reportSpam: 'Spam', reportOther: 'Other', adminReports: 'Asset reports', noAdminReports: 'No open reports.', dismissReport: 'Dismiss', removeReportedOrder: 'Remove asset', reportResolved: 'Report action saved.',
    ordersTitle: 'Marketplace', noOrders: 'No active listings or requests yet.',
    offersTitle: 'Offers', dealsTitle: 'Trade history', sendOffer: 'Send offer', offerAmount: 'Offer amount', acceptOffer: 'Accept', declineOffer: 'Decline', withdrawOffer: 'Withdraw', noOffers: 'No offers yet.', noDeals: 'No trade history yet.', incoming: 'Incoming', outgoing: 'Outgoing', offerStatusFilter: 'Offer status', offerPending: 'Awaiting response', offerAccepted: 'Accepted', offerDeclined: 'Declined', offerWithdrawn: 'Withdrawn',
    accountStatsTitle: 'Account stats', activeOrdersStat: 'Active positions', completedOrdersStat: 'Completed', sentOffersStat: 'Offers sent', receivedOffersStat: 'Offers received', notificationsTitle: 'Notifications', markAllRead: 'Mark all read', noNotifications: 'No notifications yet.', offerReceivedNotice: 'sent you an offer for', offerAcceptedNotice: 'accepted your offer for', offerDeclinedNotice: 'declined your offer for', offerWithdrawnNotice: 'withdrew an offer for', activityTitle: 'Activity history', noActivity: 'No activity yet.', loadMoreActivity: 'Load more', orderCreatedActivity: 'Asset listed', orderUpdatedActivity: 'Asset details updated', orderFilledActivity: 'Position closed', orderCancelledActivity: 'Asset delisted', offerReceivedActivity: 'Offer received', offerAcceptedActivity: 'Offer accepted', offerDeclinedActivity: 'Offer declined', offerWithdrawnActivity: 'Offer withdrawn', dealCreatedActivity: 'Trade created',
    active: 'Active', matched: 'Closed', cancelled: 'Cancelled', cancel: 'Cancel', match: 'Complete', editOrder: 'Edit', saveOrder: 'Save changes', cancelEdit: 'Discard', editTitle: 'Title', editPrice: 'Price', editNote: 'Note', orderUpdated: 'Asset details updated.',
    demo: 'HASSLE NFT MARKETPLACE', demoWallet: 'Wallet connection', demoWalletText: 'TON wallet connection will be available in the next release.',
    signIn: 'Verify Telegram', guest: 'Guest', authHint: 'Open the Mini App in Telegram to verify your profile.',
    signed: 'Telegram verified', profileText: 'Your Telegram account and public seller card.', sellerCardTitle: 'Public seller card', sellerDisplayName: 'Display name', sellerBio: 'About', sellerSave: 'Save profile', sellerSaved: 'Seller profile updated.', sellerProfileId: 'Public ID',
    favoritesTitle: 'Saved assets', noFavorites: 'No saved assets yet.',
    save: 'Save', saved: 'Saved', currency: 'TON', success: 'Asset listed on the market.', statsListings: 'Active listings', statsBuys: 'Buy requests', statsVolume: 'Volume', statsVolumeHint: 'Marketplace records', statsCatalog: 'Total assets', stars: 'Stars', ton: 'TON',
    noImage: 'GIFT', online: 'HASSLE NFT Market', refresh: 'Refresh', all: 'All', orderStatus: 'Status', orderKind: 'Order type', activeOrders: 'Active', matchedOrders: 'Closed', cancelledOrders: 'Cancelled', listingsOnly: 'Assets for sale', buyRequestsOnly: 'Wanted assets', invalidPrice: 'Check the price range: values must be non-negative and the minimum cannot exceed the maximum.',
    sourceStatusTitle: 'SOURCE STATUS', sourceStatusSubtitle: 'Connected data sources', sourceCatalogue: 'Marketplace catalogue', sourceChanges: 'Gift metadata changes', sourceDailyFloors: 'Floor price history', sourceStatusChecked: 'Checked', sourceStatusChecking: 'Checking status…', sourceStatusUnavailable: 'Could not load API status.', sourceRefreshInterval: 'Background refresh interval', sourceConfigured: 'Configured · read-only', sourceNeedsKey: 'API key required', sourcePublic: 'Public source', sourceDaily: 'Public daily history', sourceUnknown: 'No data',
  };
  const t = $derived(lang === 'ru' ? ru : en);
  const favoriteOrders = $derived(orders.filter(order => favoriteOrderIds.includes(order.id)));
  const visibleOrders = $derived(orders.filter(order =>
    (orderStatusFilter === 'all' || order.status === orderStatusFilter) &&
    (orderKindFilter === 'all' || order.kind === orderKindFilter)
  ));
  const visibleOffers = $derived(offers.filter(offer => offerStatusFilter === 'all' || offer.status === offerStatusFilter));
  const marketAssets = $derived.by(() => {
    const filtered = orders.filter(order => {
      if (order.kind !== 'listing' || order.status !== 'active') return false;
      const type = assetTypeOf(order.title);
      const query = marketSearch.trim().toLocaleLowerCase();
      const matchesType = assetTypeFilter === 'all' || type === assetTypeFilter;
      const matchesCurrency = marketCurrency === 'all' || order.currency === marketCurrency;
      const price = Number(order.price);
      const matchesMin = marketCurrency === 'all' || !marketMinPrice || price >= Number(marketMinPrice);
      const matchesMax = marketCurrency === 'all' || !marketMaxPrice || price <= Number(marketMaxPrice);
      const haystack = [order.title, order.owner, order.note || ''].join(' ').toLocaleLowerCase();
      return matchesType && matchesCurrency && matchesMin && matchesMax && (!query || haystack.includes(query));
    });
    return [...filtered].sort((a,b) => {
      if (marketSort === 'recent') return b.createdAt-a.createdAt;
      if (a.currency !== b.currency) return a.currency === 'TON' ? -1 : 1;
      const delta = Number(a.price)-Number(b.price);
      return marketSort === 'price-low' ? delta : -delta;
    });
  });
  const inventoryAssets = $derived(orders.filter(order => order.isMine && order.kind === 'listing'));
  const tonFloor = $derived(marketplaceStats.floor_ton && marketplaceStats.floor_ton > 0 ? marketplaceStats.floor_ton.toFixed(2) : '—');
  const starsFloor = $derived(marketplaceStats.floor_stars && marketplaceStats.floor_stars > 0 ? marketplaceStats.floor_stars.toFixed(0) : '—');
  function assetTypeOf(title: string): AssetTypeFilter {
    if (/888|anonymous.*number|number.*888/i.test(title)) return 'number';
    if (/^@|t[.]me[/]|username/i.test(title)) return 'username';
    return 'gift';
  }
  function assetTypeName(type: AssetTypeFilter) {
    return type === 'number' ? (lang === 'ru' ? 'Номер 888' : 'Anonymous number')
      : type === 'username' ? 'Username'
      : type === 'gift' ? (lang === 'ru' ? 'NFT-подарок' : 'NFT Gift')
      : (lang === 'ru' ? 'Все активы' : 'All assets');
  }
  function haptic(kind: 'light'|'medium'|'success'|'error' = 'light') {
    try {
      const feedback = (window as any).Telegram?.WebApp?.HapticFeedback;
      if (kind === 'success') feedback?.notificationOccurred?.('success');
      else if (kind === 'error') feedback?.notificationOccurred?.('error');
      else feedback?.impactOccurred?.(kind);
    } catch {}
  }

  function reportReasonLabel(reason: OrderReport['reason']) {
    const labels = {scam:t.reportScam,misleading:t.reportMisleading,inappropriate:t.reportInappropriate,spam:t.reportSpam,other:t.reportOther};
    return labels[reason] || reason;
  }

  function offerStatusLabel(status: MarketplaceOffer['status']) {
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
    marketplaceLoading = true;
    marketError = '';
    try {
      const [publicData, mineData, statsData] = await Promise.all([
        request('/api/marketplace/orders?' + orderFilterQuery()),        authToken ? request('/api/marketplace/mine') : Promise.resolve({items:[]}),
        request('/api/marketplace/stats'),
      ]);
      const publicItems = Array.isArray(publicData.items) ? publicData.items as MarketplaceOrder[] : [];
      const mineItems = Array.isArray(mineData.items) ? mineData.items as MarketplaceOrder[] : [];
      const combined = new Map<string, MarketplaceOrder>();
      for (const item of [...publicItems, ...mineItems]) combined.set(item.id, item);
      orders = [...combined.values()].sort((a,b) => b.createdAt - a.createdAt);
      publicOrdersTotal = Number(publicData.total) || publicItems.length;
      publicOrdersOffset = publicItems.length;
      marketplaceStats = { ...marketplaceStats, ...statsData };
    } catch (e) {
      marketError = e instanceof Error ? e.message : String(e);
      notice = marketError;
    } finally {
      marketplaceLoading = false;
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
      const next = Array.isArray(data.items) ? data.items as MarketplaceOrder[] : [];
      const combined = new Map<string, MarketplaceOrder>(orders.map(order => [order.id, order] as const));
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
      accountStats = {active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,unread_notifications:0};
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
    if (!window.Telegram?.WebApp?.initData) { offers = []; await loadAccountOverview(); return; }
    offersBusy = true;
    try {
      const offerStatusQuery = offerStatusFilter === 'all' ? '' : '&status=' + encodeURIComponent(offerStatusFilter);
      const results = await Promise.all([request('/api/marketplace/offers?limit=100&offset=0' + offerStatusQuery), loadAccountOverview()]);
      offers = Array.isArray(results[0].items) ? results[0].items : [];
      offersTotal = Number(results[0].total) || offers.length;
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
      const next = Array.isArray(data.items) ? data.items as MarketplaceOffer[] : [];
      const combined = new Map<string, MarketplaceOffer>(offers.map(offer => [offer.id, offer] as const));
      for (const offer of next) combined.set(offer.id, offer);
      offers = [...combined.values()];
      offersTotal = Number(data.total) || offersTotal;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offersLoadingMore = false; }
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
      offer_accepted:t.offerAcceptedActivity, offer_declined:t.offerDeclinedActivity,      offer_withdrawn:t.offerWithdrawnActivity, demo_deal_created:t.dealCreatedActivity,
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

  async function createOffer(orderId: string): Promise<boolean> {
    const amount = Number(offerAmount);
    if (!Number.isFinite(amount) || amount <= 0) { notice = lang === 'ru' ? 'Укажи положительную сумму оффера.' : 'Enter a positive offer amount.'; return false; }
    try {
      await request('/api/marketplace/offers', undefined, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:orderId,amount,note:offerNote.trim()}) });
      offerAmount = ''; offerNote = '';
      haptic('success');
      notice = lang === 'ru' ? 'Оффер отправлен владельцу актива.' : 'Offer sent to the asset owner.';
      await loadOffersAndDeals();
      return true;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); haptic('error'); return false; }
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
  function toggleFavoriteOrder(order: MarketplaceOrder) {
    haptic('light');
    favoriteOrderIds = favoriteOrderIds.includes(order.id)
      ? favoriteOrderIds.filter(id => id !== order.id)
      : [...favoriteOrderIds, order.id];
    try { localStorage.setItem('hassle-nft-favorite-orders-v1', JSON.stringify(favoriteOrderIds)); } catch {}
  }

  async function createOrder() {
    if (createOrderBusy) return;
    const price = Number(createPrice);
    if (createKind === 'listing' && assetTypeOf(createTitle) !== createAssetType) {
      notice = createAssetType === 'username'
        ? (lang === 'ru' ? 'Для Username укажи @username или ссылку t.me/username.' : 'Enter @username or a t.me/username link.')
        : createAssetType === 'number'
          ? (lang === 'ru' ? 'Укажи номер 888 в названии актива.' : 'Include the 888 number in the asset name.')
          : (lang === 'ru' ? 'Укажи название Telegram-подарка.' : 'Enter the Telegram gift name.');
      haptic('error');
      return;
    }
    if (!createTitle.trim() || !Number.isFinite(price) || price <= 0) {
      notice = lang === 'ru' ? 'Укажи название подарка и положительную цену.' : 'Enter a gift name and a positive price.';
      return;
    }
    if (!window.Telegram?.WebApp?.initData) {
      notice = lang === 'ru' ? 'Открой приложение через Telegram, чтобы выставлять активы.' : 'Open the app in Telegram to publish listings.';
      return;
    }
    createOrderBusy = true;
    try {
      await request('/api/marketplace/orders', undefined, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({kind:createKind,title:createTitle.trim(),price,currency:createCurrency,note:createNote.trim()}),
      });
      createTitle = ''; createPrice = ''; createNote = ''; createCurrency = 'TON'; createAssetType = 'gift';
      notice = t.success;
      showSellModal = false;
      haptic('success');
      view = 'orders';
      await loadMarketplace();
      await loadOffersAndDeals();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
      haptic('error');
    } finally {
      createOrderBusy = false;
    }
  }

  function startEditOrder(order: MarketplaceOrder) {
    editingOrderId = order.id;
    editTitle = order.title;
    editPrice = String(order.price);
    editNote = order.note || '';
  }

  async function saveOrderEdit(id: string) {
    const price = Number(editPrice);
    if (!editTitle.trim() || !Number.isFinite(price) || price <= 0) {
      notice = lang === 'ru' ? 'Проверь название и положительную цену.' : 'Enter a title and a positive price.';
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
    if (isAdmin) await loadAdminReports();
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

  async function telegramLogin(showNotice = true) {
    const initData = window.Telegram?.WebApp?.initData || '';
    if (!initData) {
      isAdmin = false;
      signedIn = false;
      if (showNotice) notice = t.authHint;
      return;
    }
    // Fail closed while refreshing the signed Telegram session.
    isAdmin = false;    signedIn = false;
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
      await loadMarketplace();
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
    const webApp = (window as any).Telegram?.WebApp;
    webApp?.ready?.();
    webApp?.expand?.();
    const syncTelegramTheme = () => {
      const params = webApp?.themeParams || {};
      const root = document.documentElement;
      const themeMap: Record<string,string> = {
        '--tg-theme-bg-color': params.bg_color || '#0e1116',
        '--tg-theme-secondary-bg-color': params.secondary_bg_color || '#171c24',
        '--tg-theme-text-color': params.text_color || '#f2f6fc',
        '--tg-theme-hint-color': params.hint_color || '#8794a7',
        '--tg-theme-button-color': params.button_color || '#3298ff',
        '--tg-theme-button-text-color': params.button_text_color || '#ffffff',
        '--tg-theme-link-color': params.link_color || '#62b8ff',
      };
      for (const [key,value] of Object.entries(themeMap)) root.style.setProperty(key,value);
      root.dataset.telegramColorScheme = webApp?.colorScheme === 'light' ? 'light' : 'dark';
      root.style.colorScheme = webApp?.colorScheme === 'light' ? 'light' : 'dark';
    };
    syncTelegramTheme();
    webApp?.onEvent?.('themeChanged', syncTelegramTheme);
    try {
      const savedOrders = JSON.parse(localStorage.getItem('hassle-nft-favorite-orders-v1') || '[]');
      if (Array.isArray(savedOrders)) favoriteOrderIds = savedOrders.filter((id): id is string => typeof id === 'string');
    } catch {}
    void loadMarketplace();
    if (window.Telegram?.WebApp?.initData) void telegramLogin(false);
    return () => webApp?.offEvent?.('themeChanged', syncTelegramTheme);
  });
</script>

<svelte:head>
  <title>HASSLE NFT — Marketplace</title>
  <meta name="theme-color" content="#0e1116" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
</svelte:head>

<div class="market-app">
  <aside class="sidebar">
    <a class="brand" href="#marketplace"><span class="brand-symbol">H</span><span>HASSLE NFT<small>THE GIFT MARKET</small></span></a>
    <div class="nav-caption">{lang === 'ru' ? 'МЕНЮ' : 'MENU'}</div>
    <nav class="side-nav">
      {#each [{id:'orders',label:lang==='ru'?'Маркет':'Market',icon:'▦'},{id:'create',label:lang==='ru'?'Инвентарь':'Inventory',icon:'◈'},{id:'offers',label:lang==='ru'?'Активность':'Activity',icon:'↗'},{id:'profile',label:lang==='ru'?'Профиль':'Profile',icon:'◉'}] as item}
        <button class:active={view===item.id} class="nav-item" onclick={()=>{haptic();view=item.id as View;notice='';}}><span>{item.icon}</span>{item.label}</button>
      {/each}
    </nav>
    <div class="sidebar-bottom"><div class="sidebar-version">NFT GIFTS · TON · STARS</div></div>
  </aside>

  <main class="main-content">
    <header class="topbar">
      <div class="crumb"><span>HASSLE NFT</span><b>/</b><span>{view==='orders'?'Market':view==='offers'?'Activity':view==='create'?'Inventory':view==='favorites'?'Saved':t.profile}</span></div>
      <div class="top-actions"><button class="language" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang.toUpperCase()}</button><button class="avatar-button" onclick={()=>view='profile'}>{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser ? telegramUser.slice(0,1).toUpperCase() : 'G'}</span>{/if}</button></div>
    </header>

    {#if view==='seller'}
      <section class="page-heading"><div class="eyebrow">HASSLE NFT · SELLER</div><h1>{lang==='ru'?'Профиль продавца':'Seller profile'}</h1><p>{lang==='ru'?'Публичная информация и активы на продаже':'Public information and active listings'}</p></section>
      {#if publicSellerBusy}<div class="state-panel">{t.loading}</div>
      {:else if publicSellerProfile}
        <section class="public-seller-layout">
          <article class="profile-card public-seller-card">
            <div class="profile-avatar"><span>{publicSellerProfile.display_name.slice(0,1).toUpperCase()}</span></div>
            <div class="public-seller-identity"><h2>{publicSellerProfile.display_name}</h2>{#if publicSellerProfile.username}<p>@{publicSellerProfile.username}</p>{/if}<p>{publicSellerProfile.bio || (lang==='ru'?'Продавец HASSLE NFT':'HASSLE NFT seller')}</p><small>{lang==='ru'?'На платформе с':'Member since'} {new Date(publicSellerProfile.created_at*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</small></div>
            <div class="public-seller-stats"><div><strong>{publicSellerProfile.active_listings}</strong><span>{lang==='ru'?'Активы на продаже':'Active listings'}</span></div><div><strong>{publicSellerProfile.completed_trades}</strong><span>{lang==='ru'?'Завершённые сделки':'Completed trades'}</span></div></div>
            <small class="seller-reputation-note">{lang==='ru'?'Рейтинг появится после запуска проверяемых завершённых сделок.':'Ratings will appear after verified completed trades are supported.'}</small>
          </article>
          <div class="section-title"><div><div class="eyebrow">MARKETPLACE</div><h2>{lang==='ru'?'Активы продавца':'Seller assets'}</h2></div><button class="refresh-button" onclick={()=>view='orders'}>{lang==='ru'?'Назад':'Back'}</button></div>
          {#if publicSellerProfile.listings.length}
            <div class="orders-list">{#each publicSellerProfile.listings as listing (listing.id)}
              <article class="order-card"><div class="order-icon">↗</div><div class="order-main"><div class="order-kind">{lang==='ru'?'Предложение':'Listing'}</div><h3>{listing.title}</h3><p>{listing.note || (lang==='ru'?'Без комментария':'No note')} · {new Date(listing.createdAt*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><strong>{listing.price} {listing.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class="status-active">{t.active}</span></div></article>
            {/each}</div>
          {:else}<div class="state-panel">{lang==='ru'?'У продавца пока нет активов на продаже.':'This seller has no active assets for sale yet.'}</div>{/if}
        </section>
      {:else}<div class="state-panel">{lang==='ru'?'Профиль не найден.':'Seller profile not found.'}</div>{/if}
    {:else if view==='orders'}
      <section class="ton-hero">
        <div class="ton-hero-copy">
          <div class="ton-kicker"><span class="ton-live-dot"></span> HASSLE NFT MARKET <i>·</i> TON ECOSYSTEM</div>
          <h1>{lang==='ru'?'ТВОИ NFT. ТВОЯ СДЕЛКА.':'YOUR NFT. YOUR DEAL.'}</h1>
          <p>{lang==='ru'?'Маркетплейс Telegram-подарков. Покупай, продавай и находи редкое в одном месте.':'The Telegram gift marketplace. Buy, sell and discover rare finds in one place.'}</p>
          <div class="ton-hero-actions">
            <button class="ton-primary" onclick={()=>{document.getElementById('asset-feed')?.scrollIntoView({behavior:'smooth'});haptic();}}>{lang==='ru'?'Открыть маркет':'Explore market'} →</button>
            <span class="ton-network"><b></b> TON NETWORK</span>
          </div>
        </div>
        <div class="ton-stage" aria-hidden="true">
          <div class="ton-aura"></div><div class="ton-orbit ton-orbit-a"></div><div class="ton-orbit ton-orbit-b"></div>
          <div class="ton-coin"><div class="ton-coin-rim"></div><div class="ton-coin-face"><span class="ton-mark">T</span></div></div>
          <span class="ton-float-tag tag-ton">TON <b>◆</b></span><span class="ton-float-tag tag-nft">NFT <b>✦</b></span>
          <span class="ton-particle particle-a"></span><span class="ton-particle particle-b"></span><span class="ton-particle particle-c"></span>
        </div>
        <div class="ton-hero-foot"><span>01 / MARKETPLACE</span><span>POWERED BY TON</span><span>HASSLE NFT © 2026</span></div>
      </section>
      <section class="market-overview" id="asset-feed">
        <div class="market-heading-row"><div><div class="eyebrow">LIVE ASSET MARKET</div><h2>{lang==='ru'?'Найди свой следующий актив':'Discover your next asset'}</h2><p>{lang==='ru'?'Подарки, username и номера 888 в одном каталоге':'Gifts, usernames and anonymous numbers in one curated feed'}</p></div><div class="floor-chip"><div><small>TON FLOOR</small><strong>{tonFloor} <span>TON</span></strong></div><i></i><div><small>STARS FLOOR</small><strong>{starsFloor} <span>⭐</span></strong></div></div></div>
        <div class="market-toolbar">
          <label class="market-search"><span>⌕</span><input bind:value={marketSearch} placeholder={lang==='ru'?'Поиск подарка, username или номера':'Search gifts, usernames or numbers'} aria-label="Search assets"/></label>
          <select class="market-select" bind:value={assetTypeFilter} aria-label="Asset type"><option value="all">{lang==='ru'?'Все активы':'All assets'}</option><option value="gift">{lang==='ru'?'NFT-подарки':'NFT Gifts'}</option><option value="username">Username</option><option value="number">{lang==='ru'?'Номера 888':'Anonymous numbers'}</option></select>
          <select class="market-select" bind:value={marketCurrency} aria-label="Price currency"><option value="all">{lang==='ru'?'Любая валюта':'Any currency'}</option><option value="TON">TON</option><option value="XTR">⭐ Stars</option></select>
          <select class="market-select" bind:value={marketSort} aria-label="Sort assets"><option value="recent">{lang==='ru'?'Сначала новые':'Recently listed'}</option><option value="price-low">{lang==='ru'?'Сначала дешевле':'Price: low to high'}</option><option value="price-high">{lang==='ru'?'Сначала дороже':'Price: high to low'}</option></select>
          <label class="price-filter"><span>{lang==='ru'?'От':'Min'}</span><input type="number" min="0" step="any" bind:value={marketMinPrice} disabled={marketCurrency==='all'} placeholder="0"/></label>
          <label class="price-filter"><span>{lang==='ru'?'До':'Max'}</span><input type="number" min="0" step="any" bind:value={marketMaxPrice} disabled={marketCurrency==='all'} placeholder="∞"/></label>
        </div>
        <div class="market-meta"><span>{marketAssets.length} {lang==='ru'?'активов в каталоге':'assets in market'}</span><span>{lang==='ru'?'Сделки в TON и Stars':'TON & Stars'}</span></div>
        {#if marketplaceLoading && orders.length===0}
          <div class="asset-grid-skeleton">{#each [1,2,3,4,5,6] as item}<div class="asset-skeleton"><span></span><i></i><b></b></div>{/each}</div>
        {:else if marketError && orders.length===0}
          <div class="state-panel"><strong>{lang==='ru'?'Не удалось загрузить маркет':'Market failed to load'}</strong><small>{marketError}</small><button class="asset-offer-button" onclick={()=>loadMarketplace()}>{t.refresh} ↻</button></div>
        {:else if marketAssets.length===0}
          <div class="state-panel"><span class="state-icon">⌕</span><strong>{lang==='ru'?'Ничего не найдено':'No assets found'}</strong><small>{lang==='ru'?'Попробуй изменить поиск или фильтры':'Try changing your search or filters'}</small></div>
        {:else}
          <div class="asset-grid">
            {#each marketAssets as order (order.id)}
              <AssetCard order={order} favorite={favoriteOrderIds.includes(order.id)} {lang} onFavorite={()=>toggleFavoriteOrder(order)} onOffer={()=>{if(!signedIn){notice=t.authHint;haptic('error');}else{selectedOfferOrder=order;offerAmount=String(order.price);offerNote='';showOfferModal=true;haptic('medium');}}} onOpen={()=>{selectedAsset=order;showAssetDetails=true;haptic();}} />
            {/each}
          </div>
          {#if publicOrdersOffset < publicOrdersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOrders} disabled={ordersLoadingMore}>{ordersLoadingMore?'…':t.loadMore}</button></div>{/if}
        {/if}
      </section>
     {:else if view==='offers'}
       <section class="page-heading compact-heading"><div class="eyebrow">ACCOUNT</div><h1>{t.offersTitle}</h1></section>
      <section class="market-section account-offers">
        <div class="section-title"><div><div class="eyebrow">ACCOUNT ACTIVITY</div><h2>{t.offersTitle}</h2></div><button class="refresh-button" onclick={()=>loadOffersAndDeals()} disabled={offersBusy}>↻ {t.refresh}</button></div>
        <label class="offer-filter-label">{t.offerStatusFilter}<select class="filter-input" value={offerStatusFilter} onchange={changeOfferStatus}><option value="all">{t.all}</option><option value="pending">{t.offerPending}</option><option value="accepted">{t.offerAccepted}</option><option value="declined">{t.offerDeclined}</option><option value="withdrawn">{t.offerWithdrawn}</option></select></label>
        {#if offersBusy}<div class="state-panel">{t.loading}</div>{:else if offers.length===0}<div class="state-panel">{t.noOffers}</div>{:else if visibleOffers.length===0}<div class="state-panel">{lang==='ru'?'Нет офферов с таким статусом.':'No offers with this status.'}</div>{:else}
          <div class="orders-list">{#each visibleOffers as offer (offer.id)}
            <article class="order-card"><div class="order-icon">↗</div><div class="order-main"><div class="order-kind">{offer.is_mine?t.outgoing:t.incoming} · {offer.order_title}</div><h3>{offer.amount} {offer.currency==='XTR'?'⭐ Stars':'TON'}</h3><p>{offer.note || offer.sender_name} · {new Date(offer.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><span class={'status-'+(offer.status==='pending'?'active':offer.status==='accepted'?'matched':'cancelled')}>{offerStatusLabel(offer.status)}</span></div>
            {#if offer.status==='pending' && offer.is_mine}<div class="order-actions"><button class="cancel-button" onclick={()=>respondToOffer(offer.id,'withdraw')} disabled={offerActionBusy===offer.id}>{offerActionBusy===offer.id?'…':t.withdrawOffer}</button></div>{:else if offer.status==='pending' && !offer.is_mine}<div class="order-actions"><button class="cancel-button" onclick={()=>respondToOffer(offer.id,'decline')} disabled={offerActionBusy===offer.id}>{t.declineOffer}</button></div><p class="safety-note">{lang==='ru'?'Принятие оффера станет доступно после подключения эскроу TON/Stars и реальной передачи NFT.':'Offer acceptance will be enabled after TON/Stars escrow and real NFT transfer are connected.'}</p>{/if}</article>
          {/each}</div>{/if}
        {#if offers.length < offersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOffers} disabled={offersLoadingMore}>{offersLoadingMore?'…':t.loadMore}</button></div>{/if}

        <div class="activity-history-panel">
          <div class="section-title"><div><div class="eyebrow">TRADE JOURNAL</div><h2>{lang==='ru'?'История операций':'Recent activity'}</h2></div><span>{accountActivity.length} / {activityTotal}</span></div>
          {#if accountActivity.length}
            <div class="activity-timeline">{#each accountActivity as item (item.id)}<div class="activity-row"><span class="activity-marker"></span><div class="activity-row-main"><strong>{activityLabel(item)}</strong><span>{item.title}</span><small>{item.actor_name} · {new Date(item.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</small></div><div class="activity-row-value"><strong>{item.amount} {item.currency==='XTR'?'⭐':'TON'}</strong></div></div>{/each}</div>
            {#if accountActivity.length<activityTotal}<button class="refresh-button activity-more" onclick={loadMoreActivity} disabled={activityBusy}>{activityBusy?'…':t.loadMoreActivity}</button>{/if}
          {:else}<div class="state-panel">{t.noActivity}</div>{/if}
        </div>
       </section>
    
     {:else if view==='create'}
      <section class="page-heading compact-heading"><div class="eyebrow">YOUR COLLECTION</div><h1>Inventory</h1><p>{lang==='ru'?'Твои активы и позиции, выставленные на HASSLE NFT':'Your assets and active positions on HASSLE NFT'}</p></section>
      <div class="inventory-note"><span>◈</span><div><strong>{lang==='ru'?'Подключение кошелька':'Wallet inventory'}</strong><p>{lang==='ru'?'Здесь пока отображаются активы, которые ты уже выставил на маркет. Импорт NFT из TON-кошелька появится после подключения TON Connect.':'This view currently shows assets you have listed on the marketplace. Wallet NFT import will appear when TON Connect inventory is integrated.'}</p></div></div>
      <div class="inventory-toolbar"><div><strong>{inventoryAssets.length}</strong><span>{lang==='ru'?'активов выставлено':'listed assets'}</span></div><button class="ton-primary" onclick={()=>{showSellModal=true;haptic();}}>{lang==='ru'?'＋ Выставить актив':'＋ List an asset'}</button></div>
      {#if inventoryAssets.length}
        <div class="asset-grid inventory-grid">
          {#each inventoryAssets as order (order.id)}
            <article class="asset-card">
              <div class={'asset-art asset-art-'+assetTypeOf(order.title)}>
                {#if assetTypeOf(order.title)==='username'}<span class="asset-at">@</span><strong>{order.title.replace(/^@/,'')}</strong><small>TELEGRAM USERNAME</small>
                {:else if assetTypeOf(order.title)==='number'}<span class="asset-number">{order.title.replace(/[^0-9+]/g,'') || '888'}</span><small>ANONYMOUS NUMBER</small>
                {:else}<span class="asset-gift-symbol">✳</span><small>TELEGRAM NFT GIFT</small>{/if}
                <span class="asset-type-pill">{order.status==='active'?(lang==='ru'?'На маркете':'Listed'):order.status}</span>
              </div>
              <div class="asset-card-content"><div class="asset-title-row"><h3>{order.title}</h3><span class="asset-id">#{order.id.slice(-5)}</span></div><div class="asset-card-footer"><div><small>{lang==='ru'?'Твоя цена':'Your price'}</small><strong>{order.price} <span>{order.currency==='XTR'?'⭐':'TON'}</span></strong></div><div class="inventory-actions">{#if order.status==='active'}<button class="asset-manage-button" onclick={()=>startEditOrder(order)}>{lang==='ru'?'Изменить':'Edit'}</button><button class="asset-remove-button" onclick={()=>cancelOrder(order.id)}>{lang==='ru'?'Снять':'Unlist'}</button>{:else}<span class="status-cancelled">{t.cancelled}</span>{/if}</div></div>
                {#if editingOrderId===order.id}<div class="inventory-edit"><input class="form-input" bind:value={editTitle} aria-label="Asset name"/><input class="form-input" type="number" min="0.01" step="0.01" bind:value={editPrice} aria-label="Asset price"/><button class="asset-offer-button" onclick={()=>saveOrderEdit(order.id)} disabled={orderEditBusy}>{t.saveOrder}</button></div>{/if}
              </div>
            </article>
          {/each}
        </div>
      {:else}
        <div class="state-panel inventory-empty"><span class="state-icon">◈</span><strong>{lang==='ru'?'Твой инвентарь пока пуст':'Your inventory is empty'}</strong><small>{lang==='ru'?'Добавь первый актив или подключи TON-кошелёк, чтобы увидеть NFT':'List your first asset or connect a TON wallet to discover your NFTs'}</small><button class="asset-offer-button" onclick={()=>{showSellModal=true;haptic();}}>{lang==='ru'?'Выставить актив':'List an asset'} →</button></div>
      {/if}
      {#if showAssetDetails && selectedAsset}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)showAssetDetails=false;}}>
          <div class="sell-modal asset-detail-modal" role="dialog" aria-modal="true" aria-labelledby="asset-detail-title">
            <button class="modal-close" onclick={()=>showAssetDetails=false} aria-label="Close">×</button>
            <div class={'asset-detail-art asset-art-'+assetTypeOf(selectedAsset.title)}>
              {#if assetTypeOf(selectedAsset.title)==='username'}<span class="asset-at">@</span><strong>{selectedAsset.title.replace(/^@/,'')}</strong><small>TELEGRAM USERNAME</small>
              {:else if assetTypeOf(selectedAsset.title)==='number'}<span class="asset-number">{selectedAsset.title.replace(/[^0-9+]/g,'') || '888'}</span><small>ANONYMOUS NUMBER</small>
              {:else}<span class="asset-gift-symbol">✳</span><small>TELEGRAM NFT GIFT</small>{/if}
            </div>
            <div class="eyebrow">HASSLE NFT · ASSET</div><h2 id="asset-detail-title">{selectedAsset.title}</h2>
            <div class="asset-detail-meta"><span>ID <b>#{selectedAsset.id}</b></span><span>{lang==='ru'?'Продавец':'Seller'} <b>{selectedAsset.owner}</b></span></div>
            <div class="asset-detail-price"><small>{lang==='ru'?'Цена продавца':'Seller price'}</small><strong>{selectedAsset.price} {selectedAsset.currency==='XTR'?'⭐ Stars':'TON'}</strong></div>
            {#if selectedAsset.note}<p class="asset-detail-note">{selectedAsset.note}</p>{/if}
            <div class="asset-detail-unavailable"><strong>{lang==='ru'?'Характеристики актива':'Asset traits'}</strong><span>{lang==='ru'?'Метаданные и traits появятся после подключения источника NFT-метаданных.':'Metadata and traits will appear after an NFT metadata source is connected.'}</span></div>
            <button class="ton-primary offer-submit" onclick={()=>{if(!signedIn){notice=t.authHint;haptic('error');return;}const asset=selectedAsset;if(!asset)return;selectedOfferOrder=asset;offerAmount=String(asset.price);offerNote='';showAssetDetails=false;showOfferModal=true;}}>{t.sendOffer} ↗</button>
            <button class="asset-buy-disabled" disabled title={lang==='ru'?'Покупка включится после запуска защищённой оплаты и escrow':'Buying will be enabled after secure payments and escrow are live'}>{lang==='ru'?'Покупка скоро':'Buy · Coming soon'}</button>
          </div>
        </div>
      {/if}
      {#if showOfferModal && selectedOfferOrder}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !offerSubmitBusy)showOfferModal=false;}}>
          <div class="sell-modal offer-modal" role="dialog" aria-modal="true" aria-labelledby="offer-title">
            <button class="modal-close" onclick={()=>showOfferModal=false} aria-label="Close" disabled={offerSubmitBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · OFFER</div>
            <h2 id="offer-title">{lang==='ru'?'Предложить цену':'Make an offer'}</h2>
            <div class="offer-asset-preview"><span>{assetTypeOf(selectedOfferOrder.title)==='username'?'＠':assetTypeOf(selectedOfferOrder.title)==='number'?'888':'✳'}</span><div><strong>{selectedOfferOrder.title}</strong><small>{lang==='ru'?'Цена продавца':'Seller price'}: {selectedOfferOrder.price} {selectedOfferOrder.currency==='XTR'?'⭐':'TON'}</small></div></div>
            <label>{lang==='ru'?'Твоё предложение':'Your offer'}<input class="form-input" type="number" min="0.01" step="0.01" bind:value={offerAmount} placeholder="1.25"/></label>
            <label>{lang==='ru'?'Сообщение продавцу':'Message to seller'}<textarea class="form-input note-input" bind:value={offerNote} maxlength="500" placeholder={lang==='ru'?'Необязательно':'Optional'}></textarea></label>
            <button class="ton-primary offer-submit" disabled={offerSubmitBusy} onclick={async()=>{if(!selectedOfferOrder)return;offerSubmitBusy=true;try{const sent=await createOffer(selectedOfferOrder.id);if(sent){showOfferModal=false;selectedOfferOrder=null;}}finally{offerSubmitBusy=false;}}}>{offerSubmitBusy?(lang==='ru'?'Отправляем…':'Sending…'):t.sendOffer+' →'}</button>
            <small class="sell-modal-hint">{lang==='ru'?'Оффер отправится владельцу актива. Это не перевод средств и не завершение сделки.':'The offer goes to the asset owner. It does not transfer funds or complete a trade.'}</small>
          </div>
        </div>
      {/if}
      {#if showSellModal}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !createOrderBusy)showSellModal=false;}}>
          <div class="sell-modal" role="dialog" aria-modal="true" aria-labelledby="sell-title">
            <button class="modal-close" onclick={()=>showSellModal=false} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={createOrderBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · SELL</div><h2 id="sell-title">{lang==='ru'?'Выставить актив':'List an asset'}</h2><p>{lang==='ru'?'Добавь актив в каталог маркетплейса':'Add an asset to the marketplace'}</p>
            <div class="sell-type-grid"><button class:chosen={createAssetType==='gift'} type="button" onclick={()=>createAssetType='gift'}>✳ <span>Telegram Gift</span></button><button class:chosen={createAssetType==='username'} type="button" onclick={()=>createAssetType='username'}>＠ <span>Username</span></button><button class:chosen={createAssetType==='number'} type="button" onclick={()=>createAssetType='number'}>888 <span>Number</span></button></div>
            <label>{lang==='ru'?'Название актива':'Asset name'}<input class="form-input" bind:value={createTitle} maxlength="120" placeholder={createAssetType==='username'?'@username':createAssetType==='number'?'+888 123 4567':'Plush Pepe #042'}/></label>
            <label>{lang==='ru'?'Цена':'Price'}<div class="currency-choice"><button class:chosen={createCurrency==='TON'} onclick={()=>createCurrency='TON'}>TON</button><button class:chosen={createCurrency==='XTR'} onclick={()=>createCurrency='XTR'}>⭐ Stars</button></div><input class="form-input" type="number" min="0.01" step="0.01" bind:value={createPrice} placeholder="1.25"/></label>
            <label>{lang==='ru'?'Описание':'Description'}<textarea class="form-input note-input" bind:value={createNote} maxlength="300"></textarea></label>
            <button class="publish-button" onclick={createOrder} disabled={createOrderBusy}>{createOrderBusy?(lang==='ru'?'Публикуем…':'Publishing…'):(lang==='ru'?'Выставить на маркет':'List on market')} <span>→</span></button>
            <small class="sell-modal-hint">{lang==='ru'?'Тип актива определяется по его идентификатору. Передача и проверка владения через TON Connect ещё не подключены.':'Asset type is inferred from its identifier. On-chain ownership verification and TON Connect transfer are not connected yet.'}</small>
          </div>
        </div>
      {/if}
    {:else if view==='favorites'}
      <section class="page-heading compact-heading"><div class="eyebrow">HASSLE NFT</div><h1>{t.favoritesTitle}</h1></section>
      {#if favoriteOrders.length}<div class="orders-list">{#each favoriteOrders as order (order.id)}<article class="order-card"><div class="order-icon">{order.kind==='listing'?'↗':'⌕'}</div><div class="order-main"><div class="order-kind">{order.kind==='listing'?t.sell:t.buy}</div><h3>{order.title}</h3><p>{order.owner}</p></div><div class="order-price"><strong>{order.price} {order.currency==='XTR'?'⭐ Stars':'TON'}</strong><span class={'status-'+order.status}>{order.status==='active'?t.active:order.status==='matched'?t.matched:t.cancelled}</span></div><button class="heart-button hearted" onclick={()=>toggleFavoriteOrder(order)}>♥</button></article>{/each}</div>{:else}<div class="state-panel">{t.noFavorites}<button class="search-button" onclick={()=>view='orders'}>{lang==='ru'?'К активам':'Browse assets'}</button></div>{/if}
    {:else}
      <section class="page-heading compact-heading"><div class="eyebrow">ACCOUNT</div><h1>{t.profile}</h1></section>
      <section class="profile-layout"><article class="profile-card profile-main"><div class="profile-avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser?telegramUser.slice(0,1).toUpperCase():'G'}</span>{/if}</div><div><h2>{telegramUser||t.guest}</h2><p>{signedIn?t.signed:t.authHint}</p>{#if telegramUsername}<small class="profile-handle">{telegramUsername}</small>{/if}{#if telegramPremium}<span class="profile-premium">TELEGRAM PREMIUM</span>{/if}</div><button class="publish-button" onclick={()=>telegramLogin()}>{t.signIn} →</button></article>
      <article class="profile-card wallet-card">
        <div class="wallet-card-icon">◈</div><div class="wallet-card-copy"><div class="eyebrow">TON CONNECT</div><h2>{lang==='ru'?'Кошелёк':'Wallet'}</h2><p>{lang==='ru'?'Кошелёк ещё не подключён. Подключение и отображение баланса появятся после интеграции TON Connect.':'No wallet connected. Wallet connection and balance display will be enabled after TON Connect integration.'}</p></div><span class="wallet-status">{lang==='ru'?'Не подключён':'Not connected'}</span>
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
        </div>
      </article>
      <article class="profile-card activity-card">
        <div class="notification-heading"><div><div class="eyebrow">{t.activityTitle}</div><strong>{accountActivity.length} / {activityTotal}</strong></div></div>
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
        {:else}<div class="state-panel">{t.noActivity}</div>{/if}      </article>
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
      <article class="profile-card preferences-card"><div class="eyebrow">PREFERENCES</div><h2>HASSLE NFT</h2><button class="language wide" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang==='ru'?'Язык интерфейса':'Interface language'} · {lang.toUpperCase()}</button></article>{#if isAdmin}
        <article class="profile-card moderation-card">
          <div class="notification-heading">
            <div><div class="eyebrow">MODERATION</div><h2>{t.adminReports}</h2></div>
            <button class="refresh-button" onclick={loadAdminReports} disabled={adminReportsBusy}>{adminReportsBusy?'…':t.refresh}</button>
          </div>
          {#if adminReportsBusy}
            <div class="state-panel">{t.loading}</div>
          {:else if adminReports.length===0}
            <div class="state-panel">{t.noAdminReports}</div>
          {:else}
            <div class="orders-list">
              {#each adminReports as report (report.id)}
                <article class="order-card">
                  <div class="order-main">
                    <div class="order-kind">{report.order_title} · {report.order_owner}</div>
                    <h3>{reportReasonLabel(report.reason)}</h3>
                    <p>{report.reporter_name} · {new Date(report.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</p>
                    {#if report.details}<p>{report.details}</p>{/if}
                  </div>
                  <div class="order-actions">
                    <button class="match-button" onclick={()=>moderateOrderReport(report.id,'dismiss')} disabled={adminReportActionBusy===report.id}>{t.dismissReport}</button>
                    <button class="cancel-button" onclick={()=>moderateOrderReport(report.id,'remove_order')} disabled={adminReportActionBusy===report.id}>{t.removeReportedOrder}</button>
                  </div>
                </article>
              {/each}
            </div>
          {/if}
        </article>
      {/if}</section>
    {/if}

    {#if notice}<div class="toast" role="status">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
    <footer class="footer"><span>✳ HASSLE NFT</span><span>Telegram NFT Gift Marketplace · n0kl1pzz1k</span><span>© 2026</span></footer>
  </main>
</div>

<BottomNav lang={lang} active={view as 'orders'|'create'|'offers'|'profile'} onNavigate={(target)=>{haptic();view=target;notice='';}} />
