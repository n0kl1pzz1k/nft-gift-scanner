<script lang="ts">
  import { onMount } from 'svelte';
  import { fade } from 'svelte/transition';
  import AssetCard from './lib/AssetCard.svelte';
  import GramToken from './lib/GramToken.svelte';
  import BottomNav from './lib/BottomNav.svelte';
  import TonWalletConnect from './lib/TonWalletConnect.svelte';

  type VerifiedAsset = { id:string; source:string; external_asset_id:string; title:string; asset_type:'gift'|'username'|'number'; image_url:string; verified_at:number; active_listing_id:string|null; traits_json?:string };
  type MarketplaceOrder = {
    id: string; external_asset_id?: string | null; collection?: string | null; kind: 'listing' | 'buy-order'; title: string; asset_id?: string | null; image_url?: string; asset_type?: 'gift' | 'username' | 'number' | null;
    price: string | number; currency: 'TON' | 'XTR'; status: 'active' | 'matched' | 'cancelled';
    createdAt: number; updatedAt: number; owner: string; isMine: boolean; note?: string; seller_profile_id?: string | null; events?: {type:string;createdAt:number}[];
  };
  type MarketplaceOffer = { id:string; order_id:string; sender_name:string; amount:string; currency:'TON'|'XTR'; note:string; status:'pending'|'accepted'|'declined'|'withdrawn'; created_at:number; is_mine:boolean; order_title:string; order_owner_id:string; order_owner_name:string; order_status:string; order_kind:'listing'|'buy-order' };
  type MarketplaceDeal = { id:string; order_id:string; offer_id:string; is_seller:boolean; order_title:string; agreed_amount:string; currency:'TON'|'XTR'; status:'payment_not_enabled'|'awaiting_payment'|'payment_pending'|'paid'|'delivery_pending'|'completed'|'disputed'|'cancelled'|'expired'; order_status:string; created_at:number; reservation_active:boolean; payments_enabled:boolean; delivery_completed:boolean; asset_source?:string|null };
  
  type DealTimelineEvent = {id:string;actor_user_id:string;type:string;data:Record<string,unknown>;created_at:number};
  type AccountNotification = { id:string; type:'offer_received'|'offer_accepted'|'offer_declined'|'offer_withdrawn'; offer_id:string; order_id:string; order_title:string; actor_name:string; created_at:number; read_at:number|null };
  type SellerPendingSettlement = { currency:'TON'|'XTR'; amount:string; status:'pending_manual_payout'|'internal_xtr_credit'; automatic_withdrawal_available:boolean };
  type AccountStats = { active_orders:number; completed_orders:number; pending_offers_sent:number; pending_offers_received:number; unread_notifications:number; deals:number; seller_pending_settlements:SellerPendingSettlement[] };
  type AccountActivity = { id:string; type:string; order_id:string; title:string; actor_name:string; created_at:number; amount:string; currency:'TON'|'XTR'; };
  type OrderReport = { id:string; order_id:string; reporter_name:string; reason:'scam'|'misleading'|'inappropriate'|'spam'|'other'; details:string; status:'open'|'dismissed'|'removed'; created_at:number; order_title:string; order_status:string; order_owner:string };
  type SellerProfile = { profile_id:string; display_name:string; bio:string; created_at:number; updated_at:number; username:string };
  type PublicSellerProfile = SellerProfile & { active_listings:number; completed_trades:number; rating:number|null; listings:{id:string;title:string;price:string;currency:'TON'|'XTR';createdAt:number;note:string}[] };
  type MarketCollectionStat = {
    collection: string; active_listings: number; trades_24h: number;
    volume_ton_24h: number; volume_stars_24h: number;
    floor_ton: number | null; floor_stars: number | null;
  };
  type MarketplaceStats = {
    active_listings: number; active_buy_requests: number; matched_orders: number;
    volume_ton: number; volume_stars: number; floor_ton: number | null; floor_stars: number | null; total_orders: number;
    volume_ton_24h: number; volume_stars_24h: number; completed_trades_24h: number;
    collections: MarketCollectionStat[];
  };
  type View = 'orders' | 'auctions' | 'offers' | 'create' | 'favorites' | 'profile' | 'seller';
  type AssetTypeFilter = 'all' | 'gift' | 'username' | 'number';

  const API = (import.meta.env.VITE_API_BASE_URL || window.API_BASE || 'https://hassle-nft-market.up.railway.app').replace(/\/$/, '');
  let lang = $state<'ru' | 'en'>('ru');
  let showSplash = $state(true);
  let showGramInfo = $state(false);
  let showFilterSheet = $state(false);
  let gramAction = $state<'deposit'|'withdraw'|null>(null);
  let showAboutModal = $state(false);
  let view = $state<View>('orders');
  let marketSearch = $state('');
  let marketplaceLoading = $state(true);
  let marketError = $state('');
  let createAssetType = $state<'gift'|'username'|'number'>('gift');
  let assetTypeFilter = $state<AssetTypeFilter>('all');
  let marketSort = $state<'recent'|'price-low'|'price-high'|'id-low'|'id-high'>('recent');
  let marketCollectionSearch = $state('');
  let marketNftIdSearch = $state('');
  let nftBackground = $state<'midnight'|'violet'|'blue'|'emerald'|'rose'|'gold'>('midnight');
  let marketCurrency = $state<'all'|'TON'|'XTR'>('all');
  let marketMinPrice = $state('');
  let marketMaxPrice = $state('');
  let showSellModal = $state(false);
  let showBuyRequestModal = $state(false);
  let createOrderBusy = $state(false);
  let showOfferModal = $state(false);
  let selectedOfferOrder = $state<MarketplaceOrder | null>(null);
  let selectedAsset = $state<MarketplaceOrder | null>(null);
  let showAssetDetails = $state(false);
  let offerSubmitBusy = $state(false);
  let orders = $state<MarketplaceOrder[]>([]);
  let verifiedAssets = $state<VerifiedAsset[]>([]);
  let giftPreviewUrls = $state<Record<string,string>>({});
  let verifiedAssetsLoading = $state(false);
  let telegramOwnershipSnapshotComplete = $state(false);
  let lastOwnershipRefreshAt = 0;
  let inventoryFilter = $state<'all'|'telegram_gift'|'ton_nft'>('all');
  let inventoryListingFilter = $state<'all'|'listed'|'unlisted'>('all');
  let inventoryTraitFilter = $state<'all'|'model'|'symbol'|'backdrop'>('all');
  let inventoryTraitQuery = $state('');
  let assetListingEnabled = $state(false);
  let selectedInventoryAsset = $state<VerifiedAsset | null>(null);
  let tonNftImportBusy = $state(false);
  let tonNftImportMessage = $state('');
  let telegramGiftImportBusy = $state(false);
  let telegramGiftImportMessage = $state('');
  let businessGiftImportBusy = $state(false);
  let businessGiftImportMessage = $state('');
  let telegramBusinessStatus = $state({connected:false,can_view_gifts_and_stars:false,can_transfer_and_upgrade_gifts:false,can_transfer_stars:false,gift_import_ready:false,gift_transfer_ready:false});
  let favoriteOrderIds = $state<string[]>([]);
  let publicOrdersTotal = $state(0);
  let publicOrdersOffset = $state(0);
  let ordersLoadingMore = $state(false);
  let offers = $state<MarketplaceOffer[]>([]);
  let offersTotal = $state(0);
  let deals = $state<MarketplaceDeal[]>([]);
  let offersLoadingMore = $state(false);
  let editingOfferId = $state('');
  let editOfferAmount = $state('');
  let editOfferNote = $state('');
  let offerEditBusy = $state(false);
  let notifications = $state<AccountNotification[]>([]);
  let notificationTotal = $state(0);
  let notificationsLoadingMore = $state(false);
  let accountActivity = $state<AccountActivity[]>([]);
  let activityTotal = $state(0);
  let activityBusy = $state(false);
  let accountStats = $state<AccountStats>({active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,unread_notifications:0,deals:0,seller_pending_settlements:[]});
  let marketplaceStats = $state<MarketplaceStats>({active_listings:0,active_buy_requests:0,matched_orders:0,volume_ton:0,volume_stars:0,floor_ton:null,floor_stars:null,total_orders:0,volume_ton_24h:0,volume_stars_24h:0,completed_trades_24h:0,collections:[]});
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
  let starsCheckoutBusy = $state('');
  let tonDeliveryRequest = $state<{dealId:string;sellerWallet:string;transaction:{validUntil:number;network:string;messages:Array<{address:string;amount:string;payload?:string}>}}|null>(null);
  let tonPaymentRequest = $state<{ dealId:string; buyerWallet:string; transaction:{ validUntil:number; network:string; messages:Array<{ address:string; amount:string; payload?:string }> } } | null>(null);
  let tonDeliveryBusy = $state('');
  let businessGiftDeliveryBusy = $state('');
  let expandedDealTimeline = $state('');
  let dealTimelineItems = $state<Record<string,DealTimelineEvent[]>>({});
  let dealTimelineBusy = $state('');
  let disputeBusy = $state('');
  let offerStatusFilter = $state<'all'|'pending'|'accepted'|'declined'|'withdrawn'>('all');
  let telegramUser = $state('');
  let telegramPhoto = $state('');
  let telegramUsername = $state('');
  let telegramPremium = $state(false);
  let signedIn = $state(false);
  let authBusy = $state(false);
  let walletConnected = $state(false);
  let walletAddress = $state('');
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
  let adminOverview = $state<Record<string,number> | null>(null);
  let adminUsers = $state<Array<Record<string,unknown>>>([]);
  let adminAssets = $state<Array<Record<string,unknown>>>([]);
  let adminDisputes = $state<Array<Record<string,unknown>>>([]);
  let adminAudit = $state<Array<Record<string,unknown>>>([]);
  let adminReconciliation = $state<{payments:Array<Record<string,unknown>>;transfers:Array<Record<string,unknown>>}>({payments:[],transfers:[]});
  let adminUserSearch = $state('');
  let adminBusy = $state(false);
  let adminRole = $state('owner');
  let adminPermissions = $state<string[]>([]);
  let orderStatusFilter = $state<'all' | 'active' | 'matched' | 'cancelled'>('active');
  let orderKindFilter = $state<'all' | 'listing' | 'buy-order'>('all');

  const ru = {
    market: 'Маркет', orders: 'Маркет', create: 'Профиль', favorites: 'Избранное', profile: 'Профиль', readonly: 'HASSLE NFT', catalogOnly: 'Независимый маркетплейс Telegram NFT-подарков. Покупай, продавай и договаривайся напрямую.',
    headline: 'Твой рынок Telegram NFT-подарков', subtitle: 'Маркетплейс Telegram NFT-подарков от n0kl1pzz1k.',
    brandEyebrow: 'HASSLE NFT · VERIFIED ASSETS', heroLine1: 'Telegram Gifts.', heroLine2: 'Твой маркет. Твои сделки.', heroDescription: 'Импортируй подарки Telegram, коллекционные Username и анонимные номера +888. Владение NFT подтверждается напрямую смарт-контрактами TON.', openCatalogue: 'Открыть маркет', connectedLabel: 'HASSLE NFT · HASSLE NFT MARKET', exploreMarket: 'МАРКЕТПЛЕЙС HASSLE NFT', collectionsTitle: 'Активы', collectionsCount: 'активов сообщества', discoverLabel: 'МАРКЕТ', search: 'Название актива, username или номер', min: 'Цена от', max: 'Цена до',
    listings: 'Активы', results: 'найдено', loading: 'Загружаем маркетплейс…', loadMore: 'Показать ещё',
    empty: 'Пока нет подходящих активов.', failed: 'Не удалось загрузить маркетплейс.',
    details: 'Об активе', floor: 'Флор', price: 'Цена', traits: 'Характеристики',
    createTitle: 'Выставить актив', sell: 'Продажа актива', buy: 'Поиск актива', titleLabel: 'Название актива',
    priceLabel: 'Цена', noteLabel: 'Комментарий', publish: 'Выставить актив',
    createHint: 'Создание объявления с реальным активом откроется после завершения безопасной проверки владения и передачи.',
    reportOrder: 'Пожаловаться', reportReason: 'Причина жалобы', reportDetails: 'Подробности', sendReport: 'Отправить жалобу', cancelReport: 'Отмена', reportSent: 'Жалоба отправлена на проверку.', reportScam: 'Мошенничество', reportMisleading: 'Вводящее в заблуждение описание', reportInappropriate: 'Недопустимый контент', reportSpam: 'Спам', reportOther: 'Другое', adminReports: 'Жалобы на активы', noAdminReports: 'Открытых жалоб нет.', dismissReport: 'Отклонить', removeReportedOrder: 'Снять актив', reportResolved: 'Решение по жалобе сохранено.',
    ordersTitle: 'Маркет', noOrders: 'Пока нет активных предложений или заявок.',
    offersTitle: 'Офферы', dealsTitle: 'История сделок', sendOffer: 'Отправить оффер', offerAmount: 'Сумма оффера', acceptOffer: 'Принять', declineOffer: 'Отклонить', withdrawOffer: 'Отозвать', noOffers: 'Офферов пока нет.', noDeals: 'История сделок пока пуста.', incoming: 'Входящий', outgoing: 'Исходящий', offerStatusFilter: 'Статус оффера', offerPending: 'Ожидает ответа', offerAccepted: 'Принят', offerDeclined: 'Отклонён', offerWithdrawn: 'Отозван',
    accountStatsTitle: 'Статистика аккаунта', activeOrdersStat: 'Активные позиции', completedOrdersStat: 'Завершённые', sentOffersStat: 'Мои офферы', receivedOffersStat: 'Входящие офферы', notificationsTitle: 'Уведомления', markAllRead: 'Прочитать все', noNotifications: 'Новых уведомлений нет.', offerReceivedNotice: 'отправил(а) оффер', offerAcceptedNotice: 'принят оффер по', offerDeclinedNotice: 'отклонил(а) оффер по', offerWithdrawnNotice: 'отозвал(а) оффер по', activityTitle: 'История активности', noActivity: 'Активность пока не появилась.', loadMoreActivity: 'Показать ещё', orderCreatedActivity: 'Актив выставлен', orderUpdatedActivity: 'Параметры актива изменены', orderFilledActivity: 'Позиция закрыта', orderCancelledActivity: 'Актив снят с продажи', offerReceivedActivity: 'Получен оффер', offerAcceptedActivity: 'Оффер принят', offerDeclinedActivity: 'Оффер отклонён', offerWithdrawnActivity: 'Оффер отозван', dealCreatedActivity: 'Создана сделка',
    active: 'Активен', matched: 'Закрыт', cancelled: 'Отменён', cancel: 'Снять', match: 'Исполнить', editOrder: 'Изменить', saveOrder: 'Сохранить', cancelEdit: 'Отмена', editTitle: 'Название', editPrice: 'Цена', editNote: 'Комментарий', orderUpdated: 'Параметры актива обновлены.',
    demo: 'HASSLE NFT MARKETPLACE', demoWallet: 'Подключение кошелька', demoWalletText: 'Подключение TON-кошелька появится в следующем этапе.',
    signIn: 'Подтвердить Telegram', guest: 'Гость', authHint: 'Открой Mini App через Telegram, чтобы подтвердить профиль.',
    signed: 'Telegram подтверждён', profileText: 'Профиль Telegram и управление публичной карточкой продавца.', sellerCardTitle: 'Публичная карточка продавца', sellerDisplayName: 'Отображаемое имя', sellerBio: 'О себе', sellerSave: 'Сохранить профиль', sellerSaved: 'Профиль продавца обновлён.', sellerProfileId: 'Публичный ID',
    favoritesTitle: 'Избранные активы', noFavorites: 'Сохранённых активов пока нет.',
    save: 'В избранное', saved: 'В избранном', currency: 'GRAM', success: 'Актив выставлен на маркет.', statsListings: 'Активные предложения', statsBuys: 'Заявки на покупку', statsVolume: 'Оборот', statsVolumeHint: 'По данным маркетплейса', statsCatalog: 'Активов всего', stars: 'Stars', ton: 'GRAM',
    noImage: 'GIFT', online: 'HASSLE NFT Market', refresh: 'Обновить', all: 'Все', orderStatus: 'Статус', orderKind: 'Тип заявки', activeOrders: 'Активные', matchedOrders: 'Закрытые', cancelledOrders: 'Снятые', listingsOnly: 'Активы на продаже', buyRequestsOnly: 'Поиск активов', invalidPrice: 'Проверь диапазон цены: значения должны быть неотрицательными, а минимальная цена — не выше максимальной.',
  };
  const en = {
    market: 'Market', orders: 'Market', create: 'Profile', favorites: 'Favorites', profile: 'Profile', readonly: 'HASSLE NFT', catalogOnly: 'An independent Telegram NFT gift marketplace. Assets are listed only after ownership verification.',
    headline: 'Discover gifts worth keeping', subtitle: 'HASSLE NFT is a Telegram NFT gift marketplace.',
    brandEyebrow: 'HASSLE NFT · VERIFIED ASSETS', heroLine1: 'Telegram Gifts.', heroLine2: 'Your market. Your deals.', heroDescription: 'Import Telegram gifts, collectible usernames and anonymous +888 numbers. NFT ownership is verified directly against TON smart contracts.', openCatalogue: 'Open marketplace', connectedLabel: 'HASSLE NFT MARKETPLACE', exploreMarket: 'HASSLE NFT MARKETPLACE', collectionsTitle: 'Assets', collectionsCount: 'community assets', discoverLabel: 'MARKET', search: 'Asset name, username or number', min: 'Min price', max: 'Max price',
    listings: 'Marketplace assets', results: 'found', loading: 'Loading marketplace…', loadMore: 'Load more',
    empty: 'No matching assets yet.', failed: 'Could not load the marketplace.',
    details: 'Asset details', floor: 'Floor', price: 'Price', traits: 'Attributes',
    createTitle: 'List an asset', sell: 'Asset for sale', buy: 'Asset wanted', titleLabel: 'Asset name',
    priceLabel: 'Price', noteLabel: 'Note', publish: 'List asset',
    createHint: 'Asset listings will open after secure ownership verification and transfer are complete.',
    reportOrder: 'Report', reportReason: 'Report reason', reportDetails: 'Details', sendReport: 'Submit report', cancelReport: 'Cancel', reportSent: 'Report submitted for review.', reportScam: 'Scam', reportMisleading: 'Misleading asset details', reportInappropriate: 'Inappropriate content', reportSpam: 'Spam', reportOther: 'Other', adminReports: 'Asset reports', noAdminReports: 'No open reports.', dismissReport: 'Dismiss', removeReportedOrder: 'Remove asset', reportResolved: 'Report action saved.',
    ordersTitle: 'Marketplace', noOrders: 'No active listings or requests yet.',
    offersTitle: 'Offers', dealsTitle: 'Trade history', sendOffer: 'Send offer', offerAmount: 'Offer amount', acceptOffer: 'Accept', declineOffer: 'Decline', withdrawOffer: 'Withdraw', noOffers: 'No offers yet.', noDeals: 'No trade history yet.', incoming: 'Incoming', outgoing: 'Outgoing', offerStatusFilter: 'Offer status', offerPending: 'Awaiting response', offerAccepted: 'Accepted', offerDeclined: 'Declined', offerWithdrawn: 'Withdrawn',
    accountStatsTitle: 'Account stats', activeOrdersStat: 'Active positions', completedOrdersStat: 'Completed', sentOffersStat: 'Offers sent', receivedOffersStat: 'Offers received', notificationsTitle: 'Notifications', markAllRead: 'Mark all read', noNotifications: 'No notifications yet.', offerReceivedNotice: 'sent you an offer for', offerAcceptedNotice: 'accepted your offer for', offerDeclinedNotice: 'declined your offer for', offerWithdrawnNotice: 'withdrew an offer for', activityTitle: 'Activity history', noActivity: 'No activity yet.', loadMoreActivity: 'Load more', orderCreatedActivity: 'Asset listed', orderUpdatedActivity: 'Asset details updated', orderFilledActivity: 'Position closed', orderCancelledActivity: 'Asset delisted', offerReceivedActivity: 'Offer received', offerAcceptedActivity: 'Offer accepted', offerDeclinedActivity: 'Offer declined', offerWithdrawnActivity: 'Offer withdrawn', dealCreatedActivity: 'Trade created',
    active: 'Active', matched: 'Closed', cancelled: 'Cancelled', cancel: 'Cancel', match: 'Complete', editOrder: 'Edit', saveOrder: 'Save changes', cancelEdit: 'Discard', editTitle: 'Title', editPrice: 'Price', editNote: 'Note', orderUpdated: 'Asset details updated.',
    demo: 'HASSLE NFT MARKETPLACE', demoWallet: 'Wallet connection', demoWalletText: 'TON wallet connection will be available in the next release.',
    signIn: 'Verify Telegram', guest: 'Guest', authHint: 'Open the Mini App in Telegram to verify your profile.',
    signed: 'Telegram verified', profileText: 'Your Telegram account and public seller card.', sellerCardTitle: 'Public seller card', sellerDisplayName: 'Display name', sellerBio: 'About', sellerSave: 'Save profile', sellerSaved: 'Seller profile updated.', sellerProfileId: 'Public ID',
    favoritesTitle: 'Saved assets', noFavorites: 'No saved assets yet.',
    save: 'Save', saved: 'Saved', currency: 'GRAM', success: 'Asset listed on the market.', statsListings: 'Active listings', statsBuys: 'Buy requests', statsVolume: 'Volume', statsVolumeHint: 'Marketplace records', statsCatalog: 'Total assets', stars: 'Stars', ton: 'GRAM',
    noImage: 'GIFT', online: 'HASSLE NFT Market', refresh: 'Refresh', all: 'All', orderStatus: 'Status', orderKind: 'Order type', activeOrders: 'Active', matchedOrders: 'Closed', cancelledOrders: 'Cancelled', listingsOnly: 'Assets for sale', buyRequestsOnly: 'Wanted assets', invalidPrice: 'Check the price range: values must be non-negative and the minimum cannot exceed the maximum.',
  };
  const t = $derived(lang === 'ru' ? ru : en);
  const favoriteOrders = $derived(orders.filter(order => favoriteOrderIds.includes(order.id)));
  const visibleOrders = $derived(orders.filter(order =>
    (orderStatusFilter === 'all' || order.status === orderStatusFilter) &&
    (orderKindFilter === 'all' || order.kind === orderKindFilter)
  ));
  const visibleOffers = $derived(offers.filter(offer => offerStatusFilter === 'all' || offer.status === offerStatusFilter));
  const publicBuyRequests = $derived(orders.filter(order => order.kind === 'buy-order' && order.status === 'active').sort((a,b) => b.createdAt-a.createdAt));
  const myListings = $derived(orders.filter(order => order.isMine).sort((a, b) => b.createdAt - a.createdAt));
  const invalidPriceRange = $derived(marketCurrency !== 'all' && ((marketMinPrice !== '' && (!Number.isFinite(Number(marketMinPrice)) || Number(marketMinPrice) < 0)) || (marketMaxPrice !== '' && (!Number.isFinite(Number(marketMaxPrice)) || Number(marketMaxPrice) < 0)) || (marketMinPrice !== '' && marketMaxPrice !== '' && Number(marketMinPrice) > Number(marketMaxPrice))));
  const hasMarketFilters = $derived(Boolean(marketSearch.trim() || marketCollectionSearch.trim() || marketNftIdSearch.trim() || assetTypeFilter !== 'all' || marketCurrency !== 'all' || marketMinPrice !== '' || marketMaxPrice !== ''));
  function resetMarketFilters() {
    marketSearch = '';
    marketCollectionSearch = '';
    marketNftIdSearch = '';
    assetTypeFilter = 'all';
    marketCurrency = 'all';
    marketMinPrice = '';
    marketMaxPrice = '';
    marketSort = 'recent';
  }
  const marketAssets = $derived.by(() => {
    const filtered = orders.filter(order => {
      if (order.kind !== 'listing' || order.status !== 'active' || invalidPriceRange) return false;
      const type = assetTypeOf(order);
      const query = marketSearch.trim().toLocaleLowerCase();
      const collectionQuery = marketCollectionSearch.trim().toLocaleLowerCase();
      const nftIdQuery = marketNftIdSearch.trim().toLocaleLowerCase();
      const matchesType = assetTypeFilter === 'all' || type === assetTypeFilter;
      const matchesCurrency = marketCurrency === 'all' || order.currency === marketCurrency;
      const price = Number(order.price);
      const matchesMin = marketCurrency === 'all' || !marketMinPrice || price >= Number(marketMinPrice);
      const matchesMax = marketCurrency === 'all' || !marketMaxPrice || price <= Number(marketMaxPrice);
      const haystack = [order.collection || '', order.title, order.owner, order.note || ''].join(' ').toLocaleLowerCase();
      const idHaystack = [order.id, order.asset_id || '', order.external_asset_id || ''].join(' ').toLocaleLowerCase();
      return matchesType && matchesCurrency && matchesMin && matchesMax && (!query || haystack.includes(query)) && (!collectionQuery || haystack.includes(collectionQuery)) && (!nftIdQuery || idHaystack.includes(nftIdQuery));
    });
    return [...filtered].sort((a,b) => {
      if (marketSort === 'recent') return b.createdAt-a.createdAt;
      if (marketSort === 'id-low') return (a.external_asset_id || a.id).localeCompare(b.external_asset_id || b.id, undefined, {numeric:true});
      if (marketSort === 'id-high') return (b.external_asset_id || b.id).localeCompare(a.external_asset_id || a.id, undefined, {numeric:true});
      if (a.currency !== b.currency) return a.currency === 'TON' ? -1 : 1;
      const delta = Number(a.price)-Number(b.price);
      return marketSort === 'price-low' ? delta : -delta;
    });
  });
  const inventoryAssets = $derived(orders.filter(order => order.isMine && order.kind === 'listing'));
  const visibleVerifiedAssets = $derived(verifiedAssets.filter(asset => asset.source !== 'telegram_gift' || telegramOwnershipSnapshotComplete));
  const inventoryFilteredAssets = $derived(visibleVerifiedAssets.filter(asset => {
    const sourceMatch = inventoryFilter === 'all' || asset.source === inventoryFilter;
    const statusMatch = inventoryListingFilter === 'all' || (inventoryListingFilter === 'listed' ? Boolean(asset.active_listing_id) : !asset.active_listing_id);
    const traits = assetTraits(asset);
    const traitMatch = inventoryTraitFilter === 'all' || traits.some(trait => trait.key === inventoryTraitFilter);
    const query = inventoryTraitQuery.trim().toLocaleLowerCase();
    const queryMatch = !query || traits.some(trait => trait.name.toLocaleLowerCase().includes(query));
    return sourceMatch && statusMatch && traitMatch && queryMatch;
  }));
  const tonFloor = $derived(marketplaceStats.floor_ton && marketplaceStats.floor_ton > 0 ? marketplaceStats.floor_ton.toFixed(2) : '—');
  const starsFloor = $derived(marketplaceStats.floor_stars && marketplaceStats.floor_stars > 0 ? marketplaceStats.floor_stars.toFixed(0) : '—');
  function assetTypeOf(order: MarketplaceOrder): AssetTypeFilter | 'unknown' {
    // Never infer an asset category from a display title. Only server-owned
    // metadata supplied by the verified asset import pipeline is authoritative.
    return order.asset_type === 'gift' || order.asset_type === 'username' || order.asset_type === 'number'
      ? order.asset_type
      : 'unknown';
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

  function openProfile() {
    haptic('light');
    // Switch the route synchronously. Never let scrolling or a network request
    // prevent the profile screen from rendering inside Telegram's iOS WebView.
    view = 'profile';
    notice = '';
    try { window.scrollTo(0, 0); } catch {}
    if (signedIn) {
      void Promise.all([loadSellerProfile(), refreshOwnershipInventory(), loadOffersAndDeals()])
        .catch(error => { notice = error instanceof Error ? error.message : String(error); });
      return;
    }
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData && !authBusy) void telegramLogin(false);
  }

  function reportReasonLabel(reason: OrderReport['reason']) {
    const labels = {scam:t.reportScam,misleading:t.reportMisleading,inappropriate:t.reportInappropriate,spam:t.reportSpam,other:t.reportOther};
    return labels[reason] || reason;
  }

  function offerStatusLabel(status: MarketplaceOffer['status']) {
    return status === 'pending' ? t.offerPending : status === 'accepted' ? t.offerAccepted : status === 'declined' ? t.offerDeclined : t.offerWithdrawn;
  }

  function dealStatusLabel(status: MarketplaceDeal['status']) {
    const labels: Record<MarketplaceDeal['status'], string> = {
      payment_not_enabled: lang === 'ru' ? 'Резерв · оплата не подключена' : 'Reserved · payment unavailable',
      awaiting_payment: lang === 'ru' ? 'Ожидает оплаты' : 'Awaiting payment',
      payment_pending: lang === 'ru' ? 'Проверка оплаты' : 'Payment pending',
      paid: lang === 'ru' ? 'Оплачено' : 'Paid',
      delivery_pending: lang === 'ru' ? 'Ожидает передачи' : 'Awaiting delivery',
      completed: lang === 'ru' ? 'Завершена' : 'Completed',
      disputed: lang === 'ru' ? 'Спор' : 'Disputed',
      cancelled: lang === 'ru' ? 'Отменена' : 'Cancelled',
      expired: lang === 'ru' ? 'Истекла' : 'Expired',
    };
    return labels[status];
  }

  const authHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    if (authToken) headers['X-Telegram-Init-Data'] = authToken;
    return headers;
  };

  async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = 12000): Promise<Response> {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: init.signal || controller.signal });
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  async function request(path: string, signal?: AbortSignal, init: RequestInit = {}) {
    const headers = new Headers(authHeaders());
    new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
    const response = await fetchWithTimeout(API + path, { ...init, signal, headers });
    const data = await response.json().catch(() => ({ detail: 'Invalid server response' }));
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : 'Request failed')) as Error & { detail?: unknown };
      error.detail = detail;
      throw error;
    }
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
      const rawPublicItems = Array.isArray(publicData.items) ? publicData.items as MarketplaceOrder[] : [];
      const publicItems = rawPublicItems.map(item => (
        item.image_url || !item.asset_id || item.asset_type !== 'gift'
          ? item
          : { ...item, image_url: API + '/api/marketplace/public-assets/' + encodeURIComponent(item.asset_id) + '/preview' }
      ));
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

  async function loadTelegramBusinessStatus() {
    if (!signedIn || !authToken) {
      telegramBusinessStatus = {connected:false,can_view_gifts_and_stars:false,can_transfer_and_upgrade_gifts:false,can_transfer_stars:false,gift_import_ready:false,gift_transfer_ready:false};
      return;
    }
    try {
      const status = await request('/api/telegram-business/status');
      telegramBusinessStatus = {
        connected: status.connected === true,
        can_view_gifts_and_stars: status.can_view_gifts_and_stars === true,
        can_transfer_and_upgrade_gifts: status.can_transfer_and_upgrade_gifts === true,
        can_transfer_stars: status.can_transfer_stars === true,
        gift_import_ready: status.gift_import_ready === true,
        gift_transfer_ready: status.gift_transfer_ready === true,
      };
    } catch {
      telegramBusinessStatus = {connected:false,can_view_gifts_and_stars:false,can_transfer_and_upgrade_gifts:false,can_transfer_stars:false,gift_import_ready:false,gift_transfer_ready:false};
    }
  }


  function assetTraits(asset: VerifiedAsset): { key:string; name:string; rarity?:number }[] {
    try {
      const data = JSON.parse(asset.traits_json || '{}');
      return ['model','symbol','backdrop'].flatMap(key => {
        const trait = data?.[key];
        if (!trait || typeof trait.name !== 'string') return [];
        return [{ key, name: trait.name, rarity: typeof trait.rarity_percent === 'number' ? trait.rarity_percent : undefined }];
      });
    } catch { return []; }
  }

  function assetPreviewUrl(asset: { id: string; image_url?: string | null } | null) {
    if (!asset) return '';
    return asset.image_url || giftPreviewUrls[asset.id] || '';
  }

  async function loadGiftPreviews(items: VerifiedAsset[]) {
    const candidates = items.filter(asset => asset.source === 'telegram_gift' && !asset.image_url && !giftPreviewUrls[asset.id]).slice(0, 24);
    await Promise.all(candidates.map(async asset => {
      try {
        const response = await fetch(API + '/api/marketplace/assets/' + encodeURIComponent(asset.id) + '/preview', { headers: authHeaders() });
        if (!response.ok) return;
        const blob = await response.blob();
        if (!['image/webp','image/png','image/jpeg'].includes(blob.type) || !blob.size || blob.size > 2_500_000) return;
        giftPreviewUrls = { ...giftPreviewUrls, [asset.id]: URL.createObjectURL(blob) };
      } catch {
        // A missing model thumbnail should leave the designed placeholder in place.
      }
    }));
  }

  async function loadVerifiedInventory() {
    if (!signedIn || !authToken) { verifiedAssets = []; assetListingEnabled = false; return; }
    verifiedAssetsLoading = true;
    try {
      const data = await request('/api/marketplace/assets/me');
      verifiedAssets = Array.isArray(data.items) ? data.items as VerifiedAsset[] : [];
      assetListingEnabled = data.listing_enabled === true;
      await Promise.all([loadTelegramBusinessStatus(), loadGiftPreviews(verifiedAssets)]);
    } catch (e) {
      verifiedAssets = [];
      assetListingEnabled = false;
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      verifiedAssetsLoading = false;
    }
  }

  async function importTonWalletAssets() {
    if (!signedIn || !authToken || !walletConnected || tonNftImportBusy) return;
    tonNftImportBusy = true;
    tonNftImportMessage = '';
    try {
      const result = await request('/api/marketplace/assets/import-ton', undefined, { method: 'POST' });
      await loadVerifiedInventory();
      const count = Number(result.imported) || 0;
      tonNftImportMessage = lang === 'ru'
        ? (count ? `Проверено TON NFT: ${count}` : 'В подключённом TON-кошельке не найдены NFT, выпущенные в блокчейне TON.')
        : (count ? `TON NFTs verified: ${count}` : 'No NFTs minted on TON were found in the connected wallet.');
      if (result.ownership_scan_complete === false) {
        tonNftImportMessage += lang === 'ru' ? ' Проверка продолжится при следующем обновлении.' : ' Ownership verification was incomplete; refresh again later.';
      }
    } catch (e) {
      tonNftImportMessage = e instanceof Error ? e.message : String(e);
    } finally {
      tonNftImportBusy = false;
    }
  }

  async function importBusinessGifts() {
    if (!signedIn || !authToken || businessGiftImportBusy) return;
    // Business permission can become available after the panel was rendered.
    // Refresh it on demand instead of silently returning on stale UI state.
    if (!telegramBusinessStatus.gift_import_ready) {
      await loadTelegramBusinessStatus();
    }
    if (!telegramBusinessStatus.gift_import_ready) {
      businessGiftImportMessage = lang === 'ru'
        ? 'Нет разрешения на просмотр подарков Telegram Business. Проверь подключение Business и доступ к подаркам.'
        : 'Telegram Business gift-view permission is missing. Check the Business connection and gift access.';
      return;
    }
    businessGiftImportBusy = true;
    businessGiftImportMessage = '';
    try {
      const result = await request('/api/marketplace/assets/import-business-gifts', undefined, { method: 'POST' });
      telegramOwnershipSnapshotComplete = result.ownership_scan_complete === true;
      await loadVerifiedInventory();
      const count = Number(result.imported) || 0;
      businessGiftImportMessage = lang === 'ru'
        ? (count ? `Импортировано из Telegram Business: ${count}` : 'В бизнес-аккаунте нет коллекционных подарков.')
        : (count ? `Imported from Telegram Business: ${count}` : 'No collectible gifts found in the business account.');
      if (result.ownership_scan_complete === false) {
        businessGiftImportMessage += lang === 'ru' ? ' Проверка владения не завершена, повтори позже.' : ' The scan was partial; refresh again later.';
      }
    } catch (e) {
      telegramOwnershipSnapshotComplete = false;
      businessGiftImportMessage = e instanceof Error ? e.message : String(e);
    } finally {
      businessGiftImportBusy = false;
    }
  }

  async function importTelegramGifts() {
    if (!signedIn || !authToken || telegramGiftImportBusy) return;
    // Always refresh Business permissions before choosing the import provider.
    // The status may have changed since the inventory screen was first rendered.
    await loadTelegramBusinessStatus();
    if (telegramBusinessStatus.gift_import_ready) {
      await importBusinessGifts();
      return;
    }
    telegramGiftImportBusy = true;
    telegramGiftImportMessage = '';
    try {
      const result = await request('/api/marketplace/assets/import-telegram', undefined, { method: 'POST' });
      telegramOwnershipSnapshotComplete = result.ownership_scan_complete === true;
      await loadVerifiedInventory();
      const count = Number(result.imported) || 0;
      telegramGiftImportMessage = lang === 'ru'
        ? (count ? 'Подтверждено коллекционных подарков: ' + count : 'Коллекционные подарки Telegram не найдены. Обычные подарки без уникального ID не добавляются.')
        : (count ? 'Verified collectible gifts: ' + count : 'No collectible Telegram gifts found. Regular gifts without a stable item ID are not imported.');
      if (result.ownership_scan_complete === false) {
        telegramGiftImportMessage += lang === 'ru' ? ' Проверка владения не завершена; попробуй позже.' : ' Ownership verification was incomplete; try again later.';
      }
    } catch (e) {
      telegramOwnershipSnapshotComplete = false;
      telegramGiftImportMessage = e instanceof Error ? e.message : String(e);
    } finally {
      telegramGiftImportBusy = false;
    }
  }
  async function refreshOwnershipInventory() {
    if (!signedIn || !authToken) {
      await loadVerifiedInventory();
      return;
    }
    const now = Date.now();
    if (telegramOwnershipSnapshotComplete && now - lastOwnershipRefreshAt < 45_000) {
      await loadVerifiedInventory();
      return;
    }
    lastOwnershipRefreshAt = now;
    telegramOwnershipSnapshotComplete = false;
    await importTelegramGifts();
    if (walletConnected) await importTonWalletAssets();
    await loadVerifiedInventory();
  }

  function startAssetListing(asset: VerifiedAsset) {
    if (!assetListingEnabled || asset.active_listing_id) return;
    selectedInventoryAsset = asset;
    createPrice = '';
    createCurrency = 'TON';
    createNote = '';
    showSellModal = true;
  }

  async function submitAssetListing() {
    const asset = selectedInventoryAsset;
    const price = Number(createPrice);
    if (!asset || !assetListingEnabled || createOrderBusy) return;
    if (!Number.isFinite(price) || price <= 0 || (createCurrency === 'XTR' && !Number.isInteger(price))) {
      notice = lang === 'ru'
        ? (createCurrency === 'XTR' ? 'Цена в Stars должна быть целым числом.' : 'Укажи корректную цену в GRAM больше нуля.')
        : (createCurrency === 'XTR' ? 'Star prices must be whole numbers.' : 'Enter a valid price greater than zero.');
      return;
    }
    createOrderBusy = true;
    try {
      await request('/api/marketplace/orders', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'listing',
          asset_id: asset.id,
          title: asset.title,
          price,
          currency: createCurrency,
          note: createNote.trim(),
        }),
      });
      showSellModal = false;
      selectedInventoryAsset = null;
      notice = lang === 'ru' ? 'NFT выставлен на маркетплейсе.' : 'NFT listing published.';
      haptic('success');
      await Promise.all([loadVerifiedInventory(), loadMarketplace()]);
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
      haptic('error');
    } finally {
      createOrderBusy = false;
    }
  }

  function startBuyRequest() {
    if (!signedIn) {
      notice = lang === 'ru' ? 'Войди через Telegram, чтобы создать заявку на покупку.' : 'Sign in with Telegram to create a buy request.';
      return;
    }
    createTitle = '';
    createPrice = '';
    createCurrency = 'TON';
    createNote = '';
    showBuyRequestModal = true;
  }

  async function submitBuyRequest() {
    const title = createTitle.trim();
    const price = Number(createPrice);
    if (!signedIn || !title || !Number.isFinite(price) || price <= 0 || (createCurrency === 'XTR' && !Number.isInteger(price)) || createOrderBusy) {
      notice = lang === 'ru'
        ? (createCurrency === 'XTR' ? 'Укажи название и целую сумму в Stars.' : 'Укажи название актива и корректную цену в GRAM.')
        : (createCurrency === 'XTR' ? 'Enter an asset name and a whole-number Stars amount.' : 'Enter an asset name and a valid price.');
      return;
    }
    createOrderBusy = true;
    try {
      await request('/api/marketplace/orders', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'buy-order', title, price, currency: createCurrency, note: createNote.trim() }),
      });
      showBuyRequestModal = false;
      notice = lang === 'ru'
        ? 'Заявка на покупку опубликована. Это запрос, а не оплаченный заказ.'
        : 'Buy request published. This is a request, not a paid order.';
      haptic('success');
      await loadMarketplace();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
      haptic('error');
    } finally {
      createOrderBusy = false;
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
    if (!signedIn) { sellerProfile = null; sellerDisplayName = ''; sellerBio = ''; return; }
    try {
      const data = await request('/api/marketplace/profiles/me');
      sellerProfile = data.profile as SellerProfile;
      sellerDisplayName = sellerProfile.display_name || '';
      sellerBio = sellerProfile.bio || '';
    } catch (e) {
      sellerProfile = null;
      notice = e instanceof Error ? e.message : String(e);
    }
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
      accountStats = {active_orders:0,completed_orders:0,pending_offers_sent:0,pending_offers_received:0,unread_notifications:0,deals:0,seller_pending_settlements:[]};
      return;
    }
    const [stats, feed, activity] = await Promise.all([
      request('/api/marketplace/account/stats'),
      request('/api/marketplace/notifications?limit=40&offset=0'),
      request('/api/marketplace/activity?limit=20&offset=0'),
    ]);
    accountStats = {
      active_orders: Number(stats.active_orders) || 0,
      completed_orders: Number(stats.completed_orders) || 0,
      pending_offers_sent: Number(stats.pending_offers_sent) || 0,
      pending_offers_received: Number(stats.pending_offers_received) || 0,
      unread_notifications: Number(stats.unread_notifications) || 0,
      deals: Number(stats.deals) || 0,
      seller_pending_settlements: Array.isArray(stats.seller_pending_settlements) ? stats.seller_pending_settlements : [],
    };
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
      const results = await Promise.all([
        request('/api/marketplace/offers?limit=100&offset=0' + offerStatusQuery),
        loadAccountOverview(),
        request('/api/marketplace/deals?limit=100&offset=0'),
      ]);
      offers = Array.isArray(results[0].items) ? results[0].items : [];
      offersTotal = Number(results[0].total) || offers.length;
      deals = Array.isArray(results[2].items) ? results[2].items as MarketplaceDeal[] : [];
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offersBusy = false; }
  }

  function dealEventLabel(event: DealTimelineEvent) {
    const labels: Record<string,string> = {
      deal_created: lang==='ru'?'Создана сделка':'Deal created',
      listing_reserved: lang==='ru'?'Актив зарезервирован':'Listing reserved',
      deal_payment_requested: lang==='ru'?'Запрошена оплата':'Payment requested',
      payment_intent_created: lang==='ru'?'Создан платёжный счёт':'Payment invoice created',
      payment_pending: lang==='ru'?'Проверка платежа':'Payment pending',
      payment_confirmed: lang==='ru'?'Платёж подтверждён':'Payment confirmed',
      escrow_funded: lang==='ru'?'Средства учтены в эскроу-журнале':'Escrow journal funded',
      asset_transfer_requested: lang==='ru'?'Подготовлена передача актива':'Asset transfer requested',
      asset_transfer_submitted: lang==='ru'?'Кошелёк отправил транзакцию':'Wallet transaction submitted',
      asset_transfer_confirmed: lang==='ru'?'Новый владелец подтверждён в TON':'New owner verified on TON',
      deal_completed: lang==='ru'?'Сделка завершена':'Deal completed',
      deal_disputed: lang==='ru'?'Открыт спор':'Dispute opened',
      deal_cancelled: lang==='ru'?'Сделка отменена':'Deal cancelled',
      deal_expired: lang==='ru'?'Резерв истёк':'Reservation expired',
      dispute_resolution_approved: lang==='ru'?'Администратор одобрил решение':'Admin approved resolution',
      dispute_resolution_ready: lang==='ru'?'Набран порог одобрений':'Approval threshold reached',
    };
    return labels[event.type] || event.type.replaceAll('_',' ');
  }

  async function toggleDealTimeline(dealId: string) {
    if (expandedDealTimeline === dealId) {
      expandedDealTimeline = '';
      return;
    }
    expandedDealTimeline = dealId;
    if (dealTimelineItems[dealId]) return;
    dealTimelineBusy = dealId;
    try {
      const data = await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/events');
      dealTimelineItems = { ...dealTimelineItems, [dealId]: Array.isArray(data.items) ? data.items as DealTimelineEvent[] : [] };
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      dealTimelineBusy = '';
    }
  }

  async function openDealDispute(dealId: string) {
    if (!dealId || disputeBusy) return;
    const reason = window.prompt(lang==='ru' ? 'Опиши проблему по сделке:' : 'Describe the issue with this deal:');
    if (!reason || !reason.trim()) return;
    disputeBusy = dealId;
    try {
      await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/disputes', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason.trim().slice(0,1000), idempotency_key: crypto.randomUUID() }),
      });
      notice = lang==='ru' ? 'Спор открыт. Сделка заблокирована для обычного завершения до решения.' : 'Dispute opened. Normal completion is blocked pending resolution.';
      await loadOffersAndDeals();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      disputeBusy = '';
    }
  }

  async function prepareTonNftDelivery(dealId: string) {
    if (!dealId || tonDeliveryBusy) return;
    tonDeliveryBusy = dealId;
    try {
      const data = await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/ton-nft-delivery/prepare', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      if (!data.transaction || !data.seller_wallet) throw new Error(lang === 'ru' ? 'Сервер не вернул запрос перевода NFT.' : 'The server did not return an NFT transfer request.');
      tonDeliveryRequest = { dealId, sellerWallet: String(data.seller_wallet), transaction: data.transaction };
      view = 'profile';
      notice = lang === 'ru'
        ? 'Запрос передачи подготовлен. Подключи кошелёк продавца и подтверди перевод.'
        : 'Delivery request prepared. Connect the seller wallet and approve the transfer.';
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      tonDeliveryBusy = '';
    }
  }

  async function verifyTonNftDelivery(dealId: string) {
    if (!dealId || tonDeliveryBusy) return;
    tonDeliveryBusy = dealId;
    try {
      await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/ton-nft-delivery/confirm', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      notice = lang === 'ru'
        ? 'TON подтвердил нового владельца. Сделка завершена, NFT добавлен в инвентарь покупателя.'
        : 'TON confirmed the new owner. The deal is complete and the NFT is in the buyer inventory.';
      await Promise.all([loadOffersAndDeals(), loadMarketplace(), loadVerifiedInventory()]);
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      tonDeliveryBusy = '';
    }
  }

  async function transferTelegramBusinessGift(dealId: string) {
    if (!dealId || businessGiftDeliveryBusy) return;
    businessGiftDeliveryBusy = dealId;
    const idempotencyKey = crypto.randomUUID();
    const sendTransfer = (acceptTransferFee: boolean) => request(
      '/api/marketplace/deals/' + encodeURIComponent(dealId) + '/telegram-business-gift-delivery/transfer',
      undefined,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: idempotencyKey, accept_transfer_fee: acceptTransferFee }),
      },
    );
    try {
      try {
        await sendTransfer(false);
      } catch (error) {
        const detail = (error as Error & { detail?: { code?: string; transfer_star_count?: number } }).detail;
        if (detail?.code !== 'seller_fee_consent_required') throw error;
        const fee = Number(detail.transfer_star_count) || 0;
        const message = lang === 'ru'
          ? `Telegram требует комиссию ${fee} Stars за передачу подарка. Разрешить списание этой комиссии с подключённого Business-аккаунта?`
          : `Telegram requires a ${fee} Stars transfer fee. Allow this fee to be charged to the connected Business account?`;
        const webApp = (window as any).Telegram?.WebApp;
        const accepted = await new Promise<boolean>((resolve) => {
          if (typeof webApp?.showConfirm === 'function') {
            webApp.showConfirm(message, (confirmed: boolean) => resolve(Boolean(confirmed)));
          } else {
            resolve(window.confirm(message));
          }
        });
        if (!accepted) {
          notice = lang === 'ru' ? 'Передача отменена. Комиссия не списывалась.' : 'Transfer cancelled. No fee was charged.';
          return;
        }
        await sendTransfer(true);
      }
      haptic('success');
      notice = lang === 'ru'
        ? 'Telegram подтвердил передачу подарка. Сделка завершена, начисление продавцу записано.'
        : 'Telegram confirmed the gift transfer. The deal is complete and the seller credit is recorded.';
      await Promise.all([loadOffersAndDeals(), loadMarketplace(), loadVerifiedInventory(), loadAccountOverview()]);
    } catch (error) {
      haptic('error');
      notice = error instanceof Error ? error.message : String(error);
    } finally {
      businessGiftDeliveryBusy = '';
    }
  }

  async function startTonCheckout(dealId: string) {
    if (!dealId || tonDeliveryBusy) return;
    tonDeliveryBusy = dealId;
    try {
      const data = await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/ton-checkout', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      if (!data.transaction || !data.buyer_wallet) throw new Error(lang === 'ru' ? 'Сервер не подготовил TON-платёж.' : 'The server did not prepare the TON payment.');
      tonPaymentRequest = { dealId, buyerWallet: String(data.buyer_wallet), transaction: data.transaction };
      view = 'profile';
      notice = lang === 'ru'
        ? 'Проверь сумму и адрес в TON-кошельке. После подписи маркетплейс проверит транзакцию в блокчейне.'
        : 'Check the amount and address in your TON wallet. After signing, the marketplace will verify the on-chain transaction.';
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      tonDeliveryBusy = '';
    }
  }

  async function confirmTonPayment(dealId: string) {
    try {
      await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/ton-payment/confirm', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      tonPaymentRequest = null;
      notice = lang === 'ru'
        ? 'TON подтвердил оплату. Средства учтены в эскроу сделки.'
        : 'TON confirmed the payment. Funds are recorded in deal escrow.';
      await loadOffersAndDeals();
    } catch (e) {
      notice = lang === 'ru'
        ? (e instanceof Error ? e.message : 'TON ещё не подтвердил перевод. Обнови статус позже, не отправляй деньги повторно.')
        : (e instanceof Error ? e.message : 'TON has not confirmed the transfer yet. Check again later; do not pay twice.');
      await loadOffersAndDeals();
    }
  }

  async function startStarsCheckout(dealId: string) {
    if (!dealId || starsCheckoutBusy) return;
    if (!window.Telegram?.WebApp?.initData) {
      notice = lang === 'ru' ? 'Открой маркетплейс через Telegram.' : 'Open the marketplace inside Telegram.';
      return;
    }
    starsCheckoutBusy = dealId;
    try {
      const data = await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/stars-checkout', undefined, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      const invoiceUrl = String(data.invoice_url || '');
      if (!invoiceUrl.startsWith('https://')) throw new Error(lang === 'ru' ? 'Telegram не вернул ссылку на оплату.' : 'Telegram did not return a payment link.');
      const webApp = window.Telegram?.WebApp as any;
      if (webApp?.openLink) webApp.openLink(invoiceUrl);
      else window.open(invoiceUrl, '_blank', 'noopener,noreferrer');
      notice = lang === 'ru'
        ? 'Счёт Stars создан. После оплаты Telegram подтвердит платёж в боте.'
        : 'Stars invoice created. Telegram will confirm payment in the bot after checkout.';
      await loadOffersAndDeals();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      starsCheckoutBusy = '';
    }
  }

  async function cancelDealReservation(dealId: string) {
    if (!dealId || offerActionBusy) return;
    offerActionBusy = dealId;
    try {
      await request('/api/marketplace/deals/' + encodeURIComponent(dealId) + '/actions', undefined, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'cancel' }),
      });
      notice = lang === 'ru' ? 'Резерв снят. Объявление снова доступно.' : 'Reservation released. The listing is available again.';
      await Promise.all([loadOffersAndDeals(), loadMarketplace()]);
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { offerActionBusy = ''; }
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
      order_created:t.orderCreatedActivity, order_updated:t.orderUpdatedActivity, order_filled:t.orderFilledActivity,
      order_cancelled:t.orderCancelledActivity, offer_received:t.offerReceivedActivity,
      offer_accepted:t.offerAcceptedActivity, offer_declined:t.offerDeclinedActivity, offer_withdrawn:t.offerWithdrawnActivity,
      deal_created:lang==='ru'?'Сделка создана':'Deal created',
      listing_reserved:lang==='ru'?'Объявление зарезервировано':'Listing reserved',
      payment_not_enabled:lang==='ru'?'Оплата пока недоступна':'Payment not enabled',
      deal_cancelled:lang==='ru'?'Резерв отменён':'Reservation cancelled',
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
    if (!signedIn || !authToken) {
      notice = lang === 'ru' ? 'Войди через Telegram, чтобы отправить оффер.' : 'Sign in with Telegram to send an offer.';
      return false;
    }
    const amount = Number(offerAmount);
    if (!Number.isFinite(amount) || amount <= 0 || (selectedOfferOrder?.currency === 'XTR' && !Number.isInteger(amount)) || (selectedOfferOrder?.kind === 'buy-order' && !offerNote.trim())) {
      notice = selectedOfferOrder?.kind === 'buy-order'
        ? (lang === 'ru' ? 'Опиши актив, который предлагаешь покупателю.' : 'Describe the asset you are offering to the buyer.')
        : selectedOfferOrder?.currency === 'XTR'
          ? (lang === 'ru' ? 'Сумма оффера в Stars должна быть целым числом.' : 'Star offers must be a whole number.')
          : (lang === 'ru' ? 'Укажи положительную сумму оффера.' : 'Enter a positive offer amount.');
      return false;
    }
    try {
      await request('/api/marketplace/offers', undefined, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:orderId,amount,note:offerNote.trim()}) });
      offerAmount = ''; offerNote = '';
      haptic('success');
      notice = selectedOfferOrder?.kind === 'buy-order'
        ? (lang === 'ru' ? 'Ответное предложение отправлено покупателю. Это ещё не резерв и не сделка.' : 'Your seller response was sent. It is not a reservation or a completed trade.')
        : (lang === 'ru' ? 'Оффер отправлен владельцу актива.' : 'Offer sent to the asset owner.');
      await loadOffersAndDeals();
      return true;
    } catch (e) { notice = e instanceof Error ? e.message : String(e); haptic('error'); return false; }
  }

  function startEditOffer(offer: MarketplaceOffer) {
    editingOfferId = offer.id;
    editOfferAmount = String(offer.amount);
    editOfferNote = offer.note || '';
  }

  async function saveOfferEdit() {
    const offer = offers.find(item => item.id === editingOfferId);
    const amount = Number(editOfferAmount);
    if (!offer || !Number.isFinite(amount) || amount <= 0 || (offer.currency === 'XTR' && !Number.isInteger(amount))) {
      notice = lang === 'ru'
        ? (offer?.currency === 'XTR' ? 'Сумма оффера в Stars должна быть целым числом.' : 'Укажи корректную положительную сумму.')
        : (offer?.currency === 'XTR' ? 'Star offers must be a whole number.' : 'Enter a valid positive amount.');
      return;
    }
    if (offerEditBusy) return;
    offerEditBusy = true;
    try {
      await request('/api/marketplace/offers/' + encodeURIComponent(editingOfferId), undefined, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'edit', amount, note: editOfferNote.trim() }),
      });
      editingOfferId = '';
      notice = lang === 'ru' ? 'Оффер обновлён.' : 'Offer updated.';
      await loadOffersAndDeals();
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      offerEditBusy = false;
    }
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
  async function loadFavorites() {
    if (!signedIn || !authToken) return;
    try {
      const data = await request('/api/marketplace/favorites');
      const ids: string[] = Array.isArray(data.items)
        ? (data.items as Array<{id?: unknown}>)
            .map(item => typeof item?.id === 'string' ? item.id : '')
            .filter((id): id is string => id.length > 0)
        : [];
      favoriteOrderIds = Array.from(new Set<string>(ids)).slice(0, 300);
      try { localStorage.setItem('hassle-nft-favorite-orders-v1', JSON.stringify(favoriteOrderIds)); } catch {}
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    }
  }

  async function toggleFavoriteOrder(order: MarketplaceOrder) {
    haptic('light');
    const wasSaved = favoriteOrderIds.includes(order.id);
    const previous = favoriteOrderIds;
    favoriteOrderIds = wasSaved
      ? favoriteOrderIds.filter(id => id !== order.id)
      : [...favoriteOrderIds, order.id];
    try { localStorage.setItem('hassle-nft-favorite-orders-v1', JSON.stringify(favoriteOrderIds)); } catch {}
    if (!signedIn || !authToken) return;
    try {
      if (wasSaved) {
        await request('/api/marketplace/favorites/' + encodeURIComponent(order.id), undefined, { method: 'DELETE' });
      } else {
        await request('/api/marketplace/favorites/' + encodeURIComponent(order.id), undefined, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ gift: {
            id: order.id, title: order.title, price: order.price, currency: order.currency,
            kind: order.kind, status: order.status, createdAt: order.createdAt,
            owner: order.owner, note: order.note || '', image_url: order.image_url || '',
            asset_type: order.asset_type || null,
          } }),
        });
      }
    } catch (e) {
      favoriteOrderIds = previous;
      try { localStorage.setItem('hassle-nft-favorite-orders-v1', JSON.stringify(favoriteOrderIds)); } catch {}
      notice = e instanceof Error ? e.message : String(e);
      haptic('error');
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
    const order = orders.find(item => item.id === id);
    if (!Number.isFinite(price) || price <= 0 || (order?.currency === 'XTR' && !Number.isInteger(price))) {
      notice = lang === 'ru'
        ? (order?.currency === 'XTR' ? 'Цена в Stars должна быть целым числом.' : 'Укажи корректную положительную цену.')
        : (order?.currency === 'XTR' ? 'Star prices must be whole numbers.' : 'Enter a valid positive price.');
      return;
    }
    if (orderEditBusy) return;
    orderEditBusy = true;
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id), undefined, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(order?.kind === 'buy-order' ? { title: editTitle.trim() } : {}),
          price,
          note: editNote.trim(),
        }),
      });
      editingOrderId = '';
      notice = t.orderUpdated;
      await loadMarketplace();
      if (selectedAsset?.id === id) selectedAsset = orders.find(item => item.id === id) || selectedAsset;
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      orderEditBusy = false;
    }
  }

  async function cancelOrder(id: string) {
    if (orderEditBusy) return;
    const order = orders.find(item => item.id === id);
    orderEditBusy = true;
    try {
      await request('/api/marketplace/orders/' + encodeURIComponent(id), undefined, {method:'DELETE'});
      editingOrderId = '';
      if (selectedAsset?.id === id) showAssetDetails = false;
      notice = order?.kind === 'buy-order'
        ? (lang === 'ru' ? 'Заявка на покупку отменена.' : 'Buy request cancelled.')
        : (lang === 'ru' ? 'Объявление снято с продажи.' : 'Listing removed from sale.');
      await Promise.all([loadMarketplace(), loadOffersAndDeals(), loadVerifiedInventory()]);
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
    finally { orderEditBusy = false; }
  }

  async function loadAdminOverview() {
    if (!isAdmin || adminBusy) return;
    adminBusy = true;
    try {
      const roleData = await request('/api/marketplace/admin/roles/me');
      adminRole = String(roleData.role || 'owner');
      adminPermissions = Array.isArray(roleData.permissions) ? roleData.permissions as string[] : [];
      const can = (permission: string) => adminPermissions.includes(permission);
      const [overview, reports, users, assets, reconciliation, disputes, audit] = await Promise.all([
        request('/api/marketplace/admin/overview'),
        can('reports') ? request('/api/marketplace/admin/reports?status=open&limit=50') : Promise.resolve({items:[]}),
        can('users_read') ? request('/api/marketplace/admin/users?limit=25') : Promise.resolve({items:[]}),
        can('assets_read') ? request('/api/marketplace/admin/assets?status=verified&limit=25') : Promise.resolve({items:[]}),
        can('reconciliation_read') ? request('/api/marketplace/admin/reconciliation?limit=50') : Promise.resolve({payments:[],transfers:[]}),
        can('deals_read') ? request('/api/marketplace/admin/deals?status=disputed&limit=50') : Promise.resolve({items:[]}),
        (adminRole==='owner' || adminRole==='finance') ? request('/api/marketplace/admin/security-audit?limit=25') : Promise.resolve({items:[]}),
      ]);
      adminOverview = overview as Record<string,number>;
      adminReports = Array.isArray(reports.items) ? reports.items as OrderReport[] : [];
      adminUsers = Array.isArray(users.items) ? users.items as Array<Record<string,unknown>> : [];
      adminAssets = Array.isArray(assets.items) ? assets.items as Array<Record<string,unknown>> : [];
      adminDisputes = Array.isArray(disputes.items) ? disputes.items as Array<Record<string,unknown>> : [];
      adminAudit = Array.isArray(audit.items) ? audit.items as Array<Record<string,unknown>> : [];
      adminReconciliation = {
        payments: Array.isArray(reconciliation.payments) ? reconciliation.payments as Array<Record<string,unknown>> : [],
        transfers: Array.isArray(reconciliation.transfers) ? reconciliation.transfers as Array<Record<string,unknown>> : [],
      };
    } catch (e) {
      notice = e instanceof Error ? e.message : String(e);
    } finally {
      adminBusy = false;
    }
  }

  async function searchAdminUsers() {
    if (!isAdmin || !adminPermissions.includes('users_read')) return;
    try {
      const suffix = adminUserSearch.trim() ? '&q=' + encodeURIComponent(adminUserSearch.trim()) : '';
      const data = await request('/api/marketplace/admin/users?limit=25' + suffix);
      adminUsers = Array.isArray(data.items) ? data.items as Array<Record<string,unknown>> : [];
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function revokeAdminUserSessions(telegramId: string) {
    if (!isAdmin || !adminPermissions.includes('users_manage') || !/^\\d+$/.test(telegramId)) return;
    try {
      await request('/api/marketplace/admin/users/' + encodeURIComponent(telegramId) + '/revoke-sessions', undefined, {method:'POST'});
      notice = lang==='ru' ? 'Активные сессии пользователя отозваны.' : 'User sessions revoked.';
      await searchAdminUsers();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function revokeAdminAsset(assetId: string) {
    if (!isAdmin || !adminPermissions.includes('assets_moderate') || !assetId) return;
    if (!window.confirm(lang==='ru' ? 'Отозвать подтверждение владения? Активные объявления будут сняты.' : 'Revoke asset verification? Active listings will be removed.')) return;
    try {
      await request('/api/marketplace/admin/assets/' + encodeURIComponent(assetId) + '/revoke', undefined, {method:'POST'});
      notice = lang==='ru' ? 'Актив отозван, активные объявления сняты.' : 'Asset revoked and active listings removed.';
      await loadAdminOverview();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
  }

  async function approveAdminDispute(dealId: string, decision: 'refund_buyer'|'release_to_seller') {
    if (!isAdmin || (adminRole!=='owner' && adminRole!=='finance')) return;
    try {
      const idempotencyKey = 'admin-dispute:' + dealId + ':' + decision + ':' + crypto.randomUUID();
      const result = await request('/api/marketplace/admin/deals/' + encodeURIComponent(dealId) + '/dispute-approvals', undefined, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({decision,idempotency_key:idempotencyKey}),
      });
      notice = lang==='ru'
        ? 'Согласование записано: ' + result.approvals + ' / ' + result.required + '. Исполнение не запускается автоматически.'
        : 'Approval recorded: ' + result.approvals + ' / ' + result.required + '. Execution is not automatic.';
      await loadAdminOverview();
    } catch (e) { notice = e instanceof Error ? e.message : String(e); }
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

  async function restoreTelegramSession(): Promise<boolean> {
    let savedToken = '';
    try { savedToken = sessionStorage.getItem('hassle-nft-session-v1') || ''; } catch {}
    if (!savedToken.startsWith('grs_')) return false;
    authToken = savedToken;
    try {
      const response = await fetchWithTimeout(API + '/api/auth/me', { headers: authHeaders() }, 8000);
      const data = await response.json();
      if (!response.ok || data.authenticated !== true || !data.user) throw new Error('Session expired');
      telegramUser = data.user.first_name || 'Telegram user';
      telegramPhoto = data.user.photo_url || '';
      telegramUsername = data.user.username ? '@' + data.user.username : '';
      telegramPremium = data.user.is_premium === true;
      isAdmin = data.is_admin === true;
      signedIn = true;
      if (isAdmin) await loadAdminOverview();
      await loadMarketplace();
      await loadFavorites();
      await loadOffersAndDeals();
      await loadSellerProfile();
      await loadVerifiedInventory();
      return true;
    } catch {
      authToken = '';
      signedIn = false;
      telegramOwnershipSnapshotComplete = false;
      lastOwnershipRefreshAt = 0;
      walletConnected = false;
      isAdmin = false;
      try { sessionStorage.removeItem('hassle-nft-session-v1'); } catch {}
      return false;
    }
  }

  async function telegramLogin(showNotice = true) {
    if (signedIn || authBusy) return;
    const initData = window.Telegram?.WebApp?.initData || '';
    if (!initData) {
      isAdmin = false;
      signedIn = false;
      if (showNotice) notice = t.authHint;
      return;
    }
    // Keep the successful session visible; never exchange the same initData twice from this view.
    authBusy = true;
    isAdmin = false;
    try {
      const response = await fetchWithTimeout(API + '/api/auth/telegram', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: initData }),
      }, 12000);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Authentication failed');
      if (typeof data.session_token !== 'string' || !data.session_token.startsWith('grs_')) throw new Error('Server did not issue a valid session');
      authToken = data.session_token;
      try { sessionStorage.setItem('hassle-nft-session-v1', authToken); } catch {}
      telegramUser = data.user?.first_name || 'Telegram user';
      telegramPhoto = data.user?.photo_url || '';
      telegramUsername = data.user?.username ? '@' + data.user.username : '';
      telegramPremium = data.user?.is_premium === true;
      isAdmin = data.is_admin === true;
      signedIn = true;
      if (isAdmin) await loadAdminOverview();
      await loadMarketplace();
      await loadFavorites();
      await loadOffersAndDeals();
      await loadSellerProfile();
      await loadVerifiedInventory();
      if (showNotice) notice = t.signed;
    } catch (e) {
      isAdmin = false;
      signedIn = false;
      authToken = '';
      telegramOwnershipSnapshotComplete = false;
      lastOwnershipRefreshAt = 0;
      try { sessionStorage.removeItem('hassle-nft-session-v1'); } catch {}
      telegramUser = '';
      telegramPhoto = '';
      telegramUsername = '';
      telegramPremium = false;
      if (showNotice) notice = e instanceof Error ? e.message : String(e);
    } finally {
      authBusy = false;
    }
  }

  onMount(() => {
    const splashTimer = window.setTimeout(() => { showSplash = false; }, 1050);
    // Telegram iOS WebView can swallow the delegated child-button handler after
    // auth state changes. Capture the profile pointer gesture at document level
    // and fall back only if the normal button callback did not change the route.
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
    // Load the public marketplace immediately. Session restoration must never
    // block the first screen: an expired Telegram session or a slow auth endpoint
    // previously left the Mini App on its initial loading state.
    void loadMarketplace();
    void restoreTelegramSession().then(restored => {
      if (restored) return;
      if (window.Telegram?.WebApp?.initData) void telegramLogin(false);
    }).catch(() => {
      // Public browsing remains available even when Telegram authentication fails.
    });
    return () => { window.clearTimeout(splashTimer); webApp?.offEvent?.('themeChanged', syncTelegramTheme); };
  });
</script>

<svelte:head>
  <title>HASSLE NFT — Marketplace</title>
  <meta name="theme-color" content="#0e1116" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1, user-scalable=no" />
</svelte:head>

<div class="market-app" data-auth-state={authBusy ? "authenticating" : signedIn ? "authenticated" : "guest"} data-active-view={view}>
  <aside class="sidebar">
    <a class="brand" href="#marketplace"><span class="brand-symbol"><img src="/brand/hassle-mark.webp?v=224" alt=""/></span><span>HASSLE NFT<small>DIGITAL COLLECTIBLES</small></span></a>
    <div class="nav-caption">{lang === 'ru' ? 'МЕНЮ' : 'MENU'}</div>
    <nav class="side-nav">
      {#each [{id:'orders',label:lang==='ru'?'Маркет':'Market',icon:'▦'},{id:'auctions',label:lang==='ru'?'Аукционы':'Auctions',icon:'♢'},{id:'offers',label:lang==='ru'?'Активность':'Activity',icon:'◫'},{id:'profile',label:lang==='ru'?'Профиль':'Profile',icon:'◉'}] as item}
        <button class:active={view===item.id || (view==='favorites' && item.id==='orders') || (view==='seller' && item.id==='profile')} class="nav-item" onclick={()=>{if(item.id==='profile'){void openProfile();return;}haptic();view=item.id as View;notice='';}}><span>{item.icon}</span>{item.label}</button>
      {/each}
    </nav>
    <div class="sidebar-bottom"><div class="sidebar-version">HASSLE · DIGITAL ASSETS</div></div>
  </aside>

  <main class="main-content">
    <header class="topbar topbar-minimal">
      <div class="topbar-brand-mobile"><span class="brand-symbol"><img src="/brand/hassle-mark.webp?v=224" alt=""/></span><strong>HASSLE <i>NFT</i></strong></div>
      <div class="top-actions"><button class="language" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang.toUpperCase()}</button><button class="avatar-button" aria-label={lang==='ru'?'Открыть профиль':'Open profile'} onclick={openProfile} data-profile-entry="avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser ? telegramUser.slice(0,1).toUpperCase() : 'G'}</span>{/if}</button></div>
    </header>

    {#if view==='profile' && !signedIn}
      <section class="page-heading compact-heading">
        <div class="eyebrow">HASSLE NFT · ACCOUNT</div>
        <h1>{t.profile}</h1>
        <p>{lang==='ru'?'Аккаунт Telegram и публичная карточка продавца':'Your Telegram account and public seller card'}</p>
      </section>
      <section class="state-panel profile-auth-panel" data-profile-guest-view>
        <strong>{t.guest}</strong>
        <small>{t.authHint}</small>
        <button class="ton-primary" type="button" onclick={()=>telegramLogin()} disabled={authBusy || !window.Telegram?.WebApp?.initData}>{authBusy?(lang==='ru'?'Подтверждаем…':'Verifying…'):(lang==='ru'?'Войти через Telegram':'Sign in with Telegram')}</button>
        <small>{lang==='ru'?'Если кнопка недоступна, открой Mini App через кнопку в боте.':'If sign-in is unavailable, open the Mini App using the bot button.'}</small>
      </section>
    {:else if view==='seller'}
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
              <article class="order-card"><div class="order-icon">↗</div><div class="order-main"><div class="order-kind">{lang==='ru'?'Предложение':'Listing'}</div><h3>{listing.title}</h3><p>{listing.note || (lang==='ru'?'Без комментария':'No note')} · {new Date(listing.createdAt*1000).toLocaleDateString(lang==='ru'?'ru-RU':'en-US')}</p></div><div class="order-price"><strong>{#if listing.currency==='XTR'}{listing.price} Stars{:else}<span class="gram-amount"><GramToken size={14}/>{listing.price}</span>{/if}</strong><span class="status-active">{t.active}</span></div></article>
            {/each}</div>
          {:else}<div class="state-panel">{lang==='ru'?'У продавца пока нет активов на продаже.':'This seller has no active assets for sale yet.'}</div>{/if}
        </section>
      {:else}<div class="state-panel">{lang==='ru'?'Профиль не найден.':'Seller profile not found.'}</div>{/if}
    {:else if view==='orders'}
      <section class="market-overview catalog-first" id="asset-feed">
        <div class="market-heading-row catalog-heading"><div><div class="eyebrow">HASSLE NFT MARKET</div><h2>{lang==='ru'?'Каталог NFT-подарков':'NFT Gift Catalogue'}</h2></div><button class="refresh-button" type="button" onclick={startBuyRequest}>{lang==='ru'?'＋ Хочу купить':'＋ Post buy request'}</button></div>
        <div class="market-live-stats" aria-label={lang==='ru'?'Показатели маркетплейса':'Marketplace statistics'}>
          <div><span>{lang==='ru'?'Активные лоты':'Active listings'}</span><strong>{marketplaceStats.active_listings.toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong></div>
          <div><span>{lang==='ru'?'Заявки на покупку':'Buy requests'}</span><strong>{marketplaceStats.active_buy_requests.toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong></div>
          <div><span>{lang==='ru'?'Сделки закрыты':'Matched deals'}</span><strong>{marketplaceStats.matched_orders.toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong></div>
        </div>
        <div class="market-category-rail" aria-label={lang==='ru'?'Категории':'Categories'}><button class:chosen={assetTypeFilter==='all'} onclick={()=>assetTypeFilter='all'}>{lang==='ru'?'Все':'All'}</button><button class:chosen={assetTypeFilter==='gift'} onclick={()=>assetTypeFilter='gift'}>{lang==='ru'?'Подарки':'Gifts'}</button><button class:chosen={assetTypeFilter==='username'} onclick={()=>assetTypeFilter='username'}>Username</button><button class:chosen={assetTypeFilter==='number'} onclick={()=>assetTypeFilter='number'}>+888</button></div>
        <div class="market-toolbar">
          <label class="market-search market-search-primary"><span class="market-search-icon"><svg viewBox="0 0 24 24" fill="none"><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></svg></span><input type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" spellcheck="false" bind:value={marketSearch} placeholder={lang==='ru'?'Поиск подарка, username или номера':'Search gifts, usernames or numbers'} aria-label={lang==='ru'?'Поиск активов':'Search assets'}/></label>
          <button class="filter-open-button" type="button" onclick={()=>showFilterSheet=true}><span class="filter-icon"><svg viewBox="0 0 24 24" fill="none"><path d="M4 7h16M7 12h10M10 17h4"/></svg></span>{lang==='ru'?'Фильтры':'Filters'}{#if hasMarketFilters}<i>•</i>{/if}</button>
        </div>
        {#if showFilterSheet}
          <div class="modal-backdrop filter-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)showFilterSheet=false;}}>
            <section class="sell-modal market-filter-sheet" role="dialog" aria-modal="true" aria-labelledby="market-filter-title">
              <button class="modal-close" type="button" onclick={()=>showFilterSheet=false} aria-label={lang==='ru'?'Закрыть':'Close'}>×</button>
              <div class="eyebrow">HASSLE NFT · DISCOVERY</div><h2 id="market-filter-title">{lang==='ru'?'Фильтры каталога':'Marketplace filters'}</h2>
              <div class="filter-sheet-grid compact-filter-grid">
                <label>{lang==='ru'?'Тип':'Type'}<select class="form-input" bind:value={assetTypeFilter}><option value="all">{lang==='ru'?'Все':'All'}</option><option value="gift">{lang==='ru'?'Подарки':'Gifts'}</option><option value="username">Username</option><option value="number">+888</option></select></label>
                <label>{lang==='ru'?'Валюта':'Currency'}<select class="form-input" bind:value={marketCurrency}><option value="all">{lang==='ru'?'Любая':'Any'}</option><option value="TON">GRAM</option><option value="XTR">Stars</option></select></label>
                <label>{lang==='ru'?'Цена от':'Min price'}<input class="form-input" type="number" min="0" step="any" bind:value={marketMinPrice} disabled={marketCurrency==='all'} placeholder="0" inputmode="decimal"/></label>
                <label>{lang==='ru'?'Цена до':'Max price'}<input class="form-input" type="number" min="0" step="any" bind:value={marketMaxPrice} disabled={marketCurrency==='all'} placeholder="∞" inputmode="decimal"/></label>
                <label>{lang==='ru'?'Коллекция':'Collection'}<input class="form-input" type="search" bind:value={marketCollectionSearch} placeholder={lang==='ru'?'Например, Eternal Rose':'e.g. Eternal Rose'} aria-label={lang==='ru'?'Фильтр по коллекции':'Filter by collection'}/></label>
                <label>{lang==='ru'?'Номер NFT / ID':'NFT number / ID'}<input class="form-input" type="search" inputmode="numeric" bind:value={marketNftIdSearch} placeholder="#93222" aria-label={lang==='ru'?'Фильтр по номеру NFT':'Filter by NFT number'}/></label>
                <label class="filter-sheet-wide">{lang==='ru'?'Сортировка':'Sort'}<select class="form-input" bind:value={marketSort}><option value="recent">{lang==='ru'?'Сначала новые':'Newest'}</option><option value="price-low">{lang==='ru'?'Сначала дешевле':'Price: low to high'}</option><option value="price-high">{lang==='ru'?'Сначала дороже':'Price: high to low'}</option><option value="id-low">NFT ID ↑</option><option value="id-high">NFT ID ↓</option></select></label>
              </div>
              <div class="filter-sheet-actions"><button class="filter-reset-button" type="button" onclick={()=>{resetMarketFilters();showFilterSheet=false;}}>{lang==='ru'?'Сбросить':'Reset'}</button><button class="ton-primary" type="button" onclick={()=>showFilterSheet=false}>{lang==='ru'?'Показать результаты':'Show results'}</button></div>
            </section>
          </div>
        {/if}
        {#if invalidPriceRange}<div class="state-panel filter-error" role="alert">{t.invalidPrice}</div>{/if}
        <div class="market-meta"><span>{marketAssets.length} {lang==='ru'?'активов в каталоге':'assets in market'}</span><span class="market-currency-legend"><GramToken size={15} class="gram-token-icon" /> GRAM <i>·</i> ⭐ Stars</span>{#if hasMarketFilters}<button class="market-reset-filters" onclick={resetMarketFilters}>{lang==='ru'?'Сбросить фильтры':'Clear filters'} ×</button>{/if}</div>
        {#if marketplaceLoading && orders.length===0}
          <div class="asset-grid-skeleton">{#each [1,2,3,4,5,6] as item}<div class="asset-skeleton"><span></span><i></i><b></b></div>{/each}</div>
        {:else if marketError && orders.length===0}
          <div class="state-panel"><strong>{lang==='ru'?'Не удалось загрузить маркет':'Market failed to load'}</strong><small>{marketError}</small><button class="asset-offer-button" onclick={()=>loadMarketplace()}>{t.refresh} ↻</button></div>
        {:else if marketAssets.length===0}
          <div class="state-panel market-empty"><img class="state-brand-art" src="/brand/hassle-mark.webp?v=224" alt="HASSLE NFT"/><strong>{hasMarketFilters ? (lang==='ru'?'По этим фильтрам ничего не найдено':'No assets match these filters') : (lang==='ru'?'Пока нет выставленных NFT-подарков':'No NFT gifts listed yet')}</strong><small>{hasMarketFilters ? (lang==='ru'?'Попробуй изменить запрос или сбросить фильтры.':'Try changing your search or clearing the filters.') : (lang==='ru'?'Здесь появятся реальные подарки, когда владельцы выставят их на маркетплейс.':'Real gifts will appear here when their owners list them on the marketplace.')}</small>{#if hasMarketFilters}<button class="market-empty-reset" onclick={resetMarketFilters}>{lang==='ru'?'Сбросить фильтры':'Clear filters'}</button>{/if}</div>
        {:else}
          <div class="asset-grid">
            {#each marketAssets as order (order.id)}
              <AssetCard order={order} favorite={favoriteOrderIds.includes(order.id)} backgroundColor="midnight" {lang} sellerProfileId={order.seller_profile_id || null} onSeller={()=>openSellerProfile(order.seller_profile_id || '')} onFavorite={()=>toggleFavoriteOrder(order)} canOffer={signedIn && !order.isMine} isMine={order.isMine} onManage={()=>{selectedAsset=order;startEditOrder(order);}} onOffer={()=>{selectedOfferOrder=order;offerAmount=String(order.price);offerNote='';showOfferModal=true;haptic();}} onOpen={()=>{selectedAsset=order;showAssetDetails=true;haptic();}} />
            {/each}
          </div>
          {#if publicOrdersOffset < publicOrdersTotal}<div class="load-more-wrap"><button class="load-more" onclick={loadMoreOrders} disabled={ordersLoadingMore}>{ordersLoadingMore?'…':t.loadMore}</button></div>{/if}
        {/if}
      </section>
       {:else if view==='auctions'}
       <section class="page-heading compact-heading"><div class="eyebrow">HASSLE NFT · LIVE BIDDING</div><h1>{lang==='ru'?'Аукционы':'Auctions'}</h1><p>{lang==='ru'?'Торги появятся после подключения серверной логики аукционов.':'Auctions will appear when the server-side bidding engine is connected.'}</p></section>
       <section class="auction-intro-card"><div class="auction-emblem">♢</div><div><span class="eyebrow">{lang==='ru'?'СЛЕДУЮЩИЙ ЭТАП':'COMING NEXT'}</span><h2>{lang==='ru'?'Торги без фальшивых лотов':'Real bidding, no placeholder lots'}</h2><p>{lang==='ru'?'Здесь будут реальные NFT-подарки с таймером, текущей ставкой и историей торгов. Пока backend-аукционы не включены, каталог остаётся пустым.':'Real NFT gifts, countdowns, live bids and bidding history will live here. No demo listings are shown while the auction backend is unavailable.'}</p></div><span class="auction-status">{lang==='ru'?'СКОРО':'SOON'}</span></section>
     {:else if view==='offers'}
       <section class="market-activity-page">
         <header class="market-activity-header">
           <div><div class="eyebrow">HASSLE NFT · MARKET PULSE</div><h1>{lang==='ru'?'Активность рынка':'Market activity'}</h1><p>{lang==='ru'?'Объём торгов, закрытые позиции и состояние коллекций за последние 24 часа.':'Trading volume, matched positions and collection activity over the last 24 hours.'}</p></div>
           <button class="refresh-button" type="button" onclick={loadMarketplace} disabled={marketplaceLoading}>↻ {t.refresh}</button>
         </header>
         <div class="market-period-label"><span></span>{lang==='ru'?'ПОСЛЕДНИЕ 24 ЧАСА':'LAST 24 HOURS'}</div>
         <div class="market-activity-kpis">
           <article class="market-kpi market-kpi-primary"><small>{lang==='ru'?'Объём торгов':'Trading volume'}</small><strong>{Number(marketplaceStats.volume_ton_24h||0).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:2})} <span class="gram-amount"><GramToken size={16}/> GRAM</span></strong><span>{lang==='ru'?'По завершённым сделкам':'Based on completed trades'}</span></article>
           <article class="market-kpi"><small>{lang==='ru'?'Объём в Stars':'Stars volume'}</small><strong>{Number(marketplaceStats.volume_stars_24h||0).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:0})} <i>⭐</i></strong><span>{lang==='ru'?'Отдельно от TON':'Reported separately from GRAM'}</span></article>
           <article class="market-kpi"><small>{lang==='ru'?'Завершённые сделки':'Completed trades'}</small><strong>{Number(marketplaceStats.completed_trades_24h||0).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong><span>{lang==='ru'?'За 24 часа':'In the last 24 hours'}</span></article>
           <article class="market-kpi"><small>{lang==='ru'?'Активные объявления':'Active listings'}</small><strong>{Number(marketplaceStats.active_listings||0).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong><span>{lang==='ru'?'Сейчас на площадке':'Currently on the marketplace'}</span></article>
         </div>
         <section class="market-collections-panel">
           <div class="market-collections-heading"><div><div class="eyebrow">COLLECTION INDEX</div><h2>{lang==='ru'?'Коллекции':'Collections'}</h2><p>{lang==='ru'?'Флор, число активных лотов и оборот за сутки':'Floor prices, active listings and daily trading volume'}</p></div><span>{marketplaceStats.collections?.length||0} {lang==='ru'?'коллекций':'collections'}</span></div>
           {#if marketplaceLoading && !marketplaceStats.collections?.length}
             <div class="state-panel">{t.loading}</div>
           {:else if marketplaceStats.collections?.length}
             <div class="market-collection-list">
               {#each marketplaceStats.collections as collection (collection.collection)}
                 <button type="button" class="market-collection-row" onclick={()=>{marketCollectionSearch=collection.collection;marketSearch="";view="orders";showFilterSheet=false;}}>
                   <div class="market-collection-identity"><img class="collection-monogram" src={orders.find(order=>order.collection===collection.collection && order.image_url)?.image_url || "/brand/hassle-mark.webp?v=224"} alt={collection.collection} loading="lazy"/><div><strong>{collection.collection}</strong><small>{collection.active_listings} {lang==='ru'?'активных лотов':'active listings'} · {collection.trades_24h} {lang==='ru'?'закрытых позиций за 24ч':'matched in 24h'}</small></div></div>
                   <div class="market-collection-metric"><small>{lang==='ru'?'Флор':'Floor'}</small><strong>{collection.floor_ton===null?'—':Number(collection.floor_ton).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:2})}</strong>{#if collection.floor_ton!==null}<small class="gram-amount"><GramToken size={13}/> GRAM</small>{/if}{#if collection.floor_stars!==null}<span>{Number(collection.floor_stars).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:0})} ⭐</span>{/if}</div>
                   <div class="market-collection-metric market-collection-volume"><small>{lang==='ru'?'Оборот за 24ч':'24h volume'}</small><strong>{Number(collection.volume_ton_24h||0).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:2})}</strong><small class="gram-amount"><GramToken size={13}/> GRAM</small>{#if collection.volume_stars_24h}<span>{Number(collection.volume_stars_24h).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:0})} ⭐</span>{/if}</div>
                 </button>
               {/each}
             </div>
           {:else}
             <div class="market-collections-empty"><span>◈</span><strong>{lang==='ru'?'Пока нет статистики по коллекциям':'No collection statistics yet'}</strong><small>{lang==='ru'?'Данные появятся после реальных объявлений и закрытых позиций. Мы не подставляем демонстрационные цифры.':'Statistics will appear as real listings and matched positions are recorded. No demo figures are used.'}</small></div>
           {/if}
         </section>
         <section class="market-longterm-stats">
           <div><small>{lang==='ru'?'Общий оборот GRAM':'All-time GRAM volume'}</small><strong>{Number(marketplaceStats.volume_ton||0).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:2})}</strong><small class="gram-amount"><GramToken size={13}/> GRAM</small></div>
           <div><small>{lang==='ru'?'Общий оборот Stars':'All-time Stars volume'}</small><strong>{Number(marketplaceStats.volume_stars||0).toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:0})} ⭐</strong></div>
           <div><small>{lang==='ru'?'Всего позиций':'Total positions'}</small><strong>{Number(marketplaceStats.total_orders||0).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong></div>
           <div><small>{lang==='ru'?'Заявки покупателей':'Buy requests'}</small><strong>{Number(marketplaceStats.active_buy_requests||0).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</strong></div>
         </section>
         <p class="market-data-footnote">{lang==='ru'?'Объём торгов не равен капитализации. Показатели считаются по данным реестра HASSLE NFT; GRAM и Stars не суммируются между собой.':'Trading volume is not market capitalization. Metrics come from the HASSLE NFT order registry; GRAM and Stars are kept separate.'}</p>
       </section>
     {:else if view==='create'}
       <section class="page-heading compact-heading"><div class="eyebrow">YOUR COLLECTION</div><h1>{lang==='ru'?'Мои подарки':'My Gifts'}</h1><p>{lang==='ru'?'Твои подтверждённые подарки Telegram и NFT из TON-кошелька':'Your verified Telegram gifts and NFTs from your TON wallet'}</p></section>
       <div class="inventory-connect-panel"><div class="inventory-connect-icon">◈</div><div class="inventory-connect-copy"><span class="eyebrow">YOUR ASSETS · TON CONNECT</span><h2>{lang==='ru'?'Твои активы':'Your assets'}</h2><p>{lang==='ru'?'Telegram Business нужен только для импорта подарков из Telegram и не обязателен для покупателей. NFT из TON можно добавить через TON Connect и TON Proof. Мы никогда не запрашиваем seed-фразу или приватный ключ.':'Telegram Business is only needed to import gifts from Telegram and is not required for buyers. TON NFTs can be imported with TON Connect and TON Proof. We never ask for your seed phrase or private key.'}</p>{#if signedIn}<button class="ton-wallet-button inventory-import-button" type="button" onclick={importTelegramGifts} disabled={telegramGiftImportBusy}>{telegramGiftImportBusy?(lang==='ru'?'Проверяем подарки…':'Checking gifts…'):(lang==='ru'?'↓ Загрузить подарки Telegram':'↓ Import Telegram gifts')}</button>{/if}{#if telegramGiftImportMessage}<p class="ton-wallet-message">{telegramGiftImportMessage}</p>{/if}
        {#if telegramBusinessStatus.gift_import_ready}
          <button class="ton-wallet-button inventory-import-button" type="button" onclick={importBusinessGifts} disabled={businessGiftImportBusy}>{businessGiftImportBusy?(lang==='ru'?'Загружаем бизнес-подарки…':'Importing business gifts…'):(lang==='ru'?'↓ Импортировать подарки Business':'↓ Import Business gifts')}</button>
          {#if businessGiftImportMessage}<p class="ton-wallet-message">{businessGiftImportMessage}</p>{/if}
        {/if}
        <div class="business-connection-card">
          <div class="business-connection-heading"><strong>Telegram Business <small>{lang==='ru'?'· необязательно':'· optional'}</small></strong><span class:ready={telegramBusinessStatus.gift_import_ready}>{telegramBusinessStatus.gift_import_ready?(lang==='ru'?'Импорт доступен':'Import ready'):(telegramBusinessStatus.connected?(lang==='ru'?'Не все права выданы':'Missing permissions'):(lang==='ru'?'Не подключён':'Not connected'))}</span></div>
          {#if telegramBusinessStatus.connected}
            <div class="business-rights-list">
              <span class:granted={telegramBusinessStatus.can_view_gifts_and_stars}>{lang==='ru'?'Просмотр подарков':'View gifts'}</span>
              <span class:granted={telegramBusinessStatus.can_transfer_and_upgrade_gifts}>{lang==='ru'?'Разрешение передачи':'Transfer permission'}</span>
              <span class:granted={telegramBusinessStatus.can_transfer_stars}>{lang==='ru'?'Комиссия в Stars':'Stars transfer fee'}</span>
            </div>
            {#if !telegramBusinessStatus.gift_import_ready}<small>{lang==='ru'?'Выдай боту право просмотра подарков в настройках Telegram Business.':'Grant the bot permission to view gifts in Telegram Business settings.'}</small>{/if}
            <p>{lang==='ru'?'После оплаты продавец может передать подарок покупателю через Telegram Business. Если Telegram требует комиссию в Stars, приложение отдельно запросит твоё согласие до передачи.':'After payment, the seller can transfer the gift to the buyer through Telegram Business. If Telegram requires a Stars fee, the app asks for your consent before the transfer.'}</p>
          {:else}
            <p>{lang==='ru'?'Это дополнительный способ импорта Telegram-подарков. Подключённые бизнес-боты доступны и без Premium. Покупателям подключать Telegram Business вообще не нужно; NFT из TON можно добавлять через кошелёк.':'This is an optional way to import Telegram gifts. The Telegram Business account owner needs Premium to connect it. Without it, users can still browse and buy, and import TON NFTs through a wallet.'}</p>
          {/if}
          <button class="refresh-button" type="button" onclick={loadTelegramBusinessStatus}>{lang==='ru'?'Проверить подключение':'Check connection'} ↻</button>
        </div>
        <TonWalletConnect api={API} authToken={authToken} {lang} deliveryRequest={tonDeliveryRequest} paymentRequest={tonPaymentRequest} onPaymentSent={confirmTonPayment} onDeliverySent={async (dealId,boc)=>{tonDeliveryRequest=null;view='offers';try{await request('/api/marketplace/deals/'+encodeURIComponent(dealId)+'/ton-nft-delivery/submitted',undefined,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({boc})});notice=lang==='ru'?'Транзакция передачи отправлена. Покупатель должен проверить нового владельца.':'NFT transfer submitted. The buyer must verify the new owner.';}catch{notice=lang==='ru'?'Кошелёк отправил транзакцию. Не повторяй перевод, дождись обновления TON и проверь владельца.':'Wallet submitted the transaction. Do not send it again; wait for TON to update and verify ownership.';}await loadOffersAndDeals();}} onVerified={(address)=>{walletAddress=address;walletConnected=Boolean(address);}} />{#if walletConnected}<button class="ton-wallet-button inventory-import-button" type="button" onclick={importTonWalletAssets} disabled={tonNftImportBusy}>{tonNftImportBusy?(lang==='ru'?'Проверяем NFT в TON…':'Verifying TON NFTs…'):(lang==='ru'?'↓ Загрузить NFT из TON':'↓ Import NFTs from TON')}</button>{/if}{#if tonNftImportMessage}<p class="ton-wallet-message">{tonNftImportMessage}</p>{/if}</div></div>
       {#if verifiedAssets.some(asset=>asset.source==='telegram_gift') && !telegramOwnershipSnapshotComplete}<div class="ownership-verification-notice" role="status">{lang==='ru'?'Подарки Telegram временно скрыты: актуальное владение ещё не подтверждено. Нажми «Обновить», чтобы проверить аккаунт.':'Telegram gifts are temporarily hidden because current ownership has not been confirmed. Tap Refresh to verify your account.'}</div>{/if}{#if verifiedAssetsLoading}
         <div class="inventory-empty-state"><span class="state-icon">◌</span><strong>{lang==='ru'?'Проверяем инвентарь…':'Checking inventory…'}</strong></div>
       {:else if visibleVerifiedAssets.length}
         <div class="inventory-toolbar">
             <div class="inventory-toolbar-groups">
               <div class="inventory-filter-group" role="group" aria-label={lang==='ru'?'Источник активов':'Asset source'}>
                 <button class:active={inventoryFilter==='all'} type="button" onclick={()=>inventoryFilter='all'}>{lang==='ru'?'Все источники':'All sources'} <span>{visibleVerifiedAssets.length}</span></button>
                 <button class:active={inventoryFilter==='telegram_gift'} type="button" onclick={()=>inventoryFilter='telegram_gift'}>Telegram <span>{visibleVerifiedAssets.filter(asset=>asset.source==='telegram_gift').length}</span></button>
                 <button class:active={inventoryFilter==='ton_nft'} type="button" onclick={()=>inventoryFilter='ton_nft'}>TON NFT <span>{visibleVerifiedAssets.filter(asset=>asset.source==='ton_nft').length}</span></button>
               </div>
               <div class="inventory-filter-group inventory-status-tabs" role="group" aria-label={lang==='ru'?'Статус объявления':'Listing status'}>
                 <button class:active={inventoryListingFilter==='all'} type="button" onclick={()=>inventoryListingFilter='all'}>{lang==='ru'?'Все':'All'}</button>
                 <button class:active={inventoryListingFilter==='listed'} type="button" onclick={()=>inventoryListingFilter='listed'}>{lang==='ru'?'Выставленные':'Listed'} <span>{visibleVerifiedAssets.filter(asset=>Boolean(asset.active_listing_id)).length}</span></button>
                 <button class:active={inventoryListingFilter==='unlisted'} type="button" onclick={()=>inventoryListingFilter='unlisted'}>{lang==='ru'?'Не выставленные':'Unlisted'} <span>{visibleVerifiedAssets.filter(asset=>!asset.active_listing_id).length}</span></button>
               </div>
             </div>
             <button class="refresh-button" type="button" onclick={refreshOwnershipInventory} disabled={verifiedAssetsLoading || telegramGiftImportBusy || businessGiftImportBusy || tonNftImportBusy}>{lang==='ru'?'Обновить':'Refresh'} ↻</button>
           </div>
           <div class="inventory-trait-toolbar">
             <div class="inventory-filter-group" role="group" aria-label={lang==='ru'?'Характеристики NFT':'NFT traits'}>
               <button class:active={inventoryTraitFilter==='all'} type="button" onclick={()=>inventoryTraitFilter='all'}>{lang==='ru'?'Все свойства':'All traits'}</button>
               <button class:active={inventoryTraitFilter==='model'} type="button" onclick={()=>inventoryTraitFilter='model'}>Model</button>
               <button class:active={inventoryTraitFilter==='symbol'} type="button" onclick={()=>inventoryTraitFilter='symbol'}>Symbol</button>
               <button class:active={inventoryTraitFilter==='backdrop'} type="button" onclick={()=>inventoryTraitFilter='backdrop'}>Backdrop</button>
             </div>
             <input class="inventory-trait-search" type="search" bind:value={inventoryTraitQuery} placeholder={lang==='ru'?'Найти свойство…':'Search trait…'} aria-label={lang==='ru'?'Поиск по характеристикам':'Search NFT traits'} />
           </div>
         {#if inventoryFilteredAssets.length}(inventoryFilter==='all'||asset.source===inventoryFilter)&&(inventoryListingFilter==='all'||(inventoryListingFilter==='listed'?Boolean(asset.active_listing_id):!asset.active_listing_id))).length}
         <div class="verified-inventory-list">
           {#each inventoryFilteredAssets as asset (asset.id)}
             <article class="verified-inventory-item">
               {#if assetPreviewUrl(asset)}<img src={assetPreviewUrl(asset)} alt={asset.title} loading="lazy" />{:else}<div class="verified-inventory-placeholder"><span>HASSLE</span><small>{asset.source==='telegram_gift'?(lang==='ru'?'КОЛЛЕКЦИОННЫЙ ПОДАРОК':'COLLECTIBLE GIFT'):asset.asset_type==='username'?'TELEGRAM USERNAME':asset.asset_type==='number'?'ANONYMOUS NUMBER':'VERIFIED ASSET'}</small></div>{/if}
               <div class="verified-inventory-details">
                 <div class="verified-inventory-title-row"><strong>{asset.title}</strong><span class="verified-asset-status">{lang==='ru'?'ПОДТВЕРЖДЁН':'VERIFIED'}</span></div>
                 {#if asset.source==='telegram_gift'}
                    <div class="verified-gift-number">{lang==='ru'?'Коллекционный номер':'Collectible number'} <b>{asset.title.match(/[#-](\d+)$/)?.[1] || '—'}</b></div>
                   <small class="verified-gift-id">{lang==='ru'?'Уникальный ID':'Unique ID'} · {asset.external_asset_id}</small>
                   <a class="inventory-gift-link" href={'https://t.me/nft/'+encodeURIComponent(asset.external_asset_id)} target="_blank" rel="noopener noreferrer">{lang==='ru'?'Открыть оригинал в Telegram':'Open original in Telegram'} ↗</a>
                 {:else}
                   <small>{asset.asset_type==='username'?(lang==='ru'?'Коллекционное имя Telegram':'Telegram collectible username'):asset.asset_type==='number'?(lang==='ru'?'Анонимный номер +888':'Anonymous +888 number'):(lang==='ru'?'TON NFT':'TON NFT')} · {asset.external_asset_id}</small>
                 {/if}
                 <span>{asset.active_listing_id?(lang==='ru'?'Уже выставлен':'Listed'):(lang==='ru'?'В инвентаре':'In inventory')}</span>
                 {#if asset.active_listing_id}<small class="inventory-listing-note">{lang==='ru'?'Объявление уже опубликовано':'Listing is already live'}</small>{#if orders.find(order=>order.id===asset.active_listing_id)}<button class="inventory-list-button" type="button" onclick={()=>{const listing=orders.find(order=>order.id===asset.active_listing_id);if(listing)startEditOrder(listing);}}>{lang==='ru'?'Управлять объявлением':'Manage listing'} ↗</button>{/if}{:else if asset.source==='telegram_gift'}<small class="inventory-listing-note">{lang==='ru'?'Продажа откроется после проверки безопасной передачи подарка':'Sales unlock after secure gift transfer is verified'}</small>{:else if assetListingEnabled}<button class="inventory-list-button" type="button" onclick={()=>startAssetListing(asset)}>{lang==='ru'?'Выставить на продажу':'List for sale'} ↗</button>{:else}<small class="inventory-listing-note">{lang==='ru'?'Публикация откроется после завершения проверки':'Listing will open after final verification'}</small>{/if}
               </div>
             </article>
           {/each}
         </div>
         {:else}
           <div class="inventory-empty-state"><img class="state-brand-art" src="/brand/hassle-mark.webp?v=224" alt="HASSLE NFT"/><strong>{lang==='ru'?'В этой категории пока пусто':'No assets in this category yet'}</strong><small>{lang==='ru'?'Выбери другой источник или обнови инвентарь после импорта.':'Choose another source or refresh after importing assets.'}</small></div>
         {/if}
       {:else}
         <div class="inventory-empty-state"><span class="state-icon">✦</span><strong>{lang==='ru'?'Пока нет подтверждённых активов':'No verified assets yet'}</strong><small>{lang==='ru'?'Ручное создание карточек отключено. Импортируй подарки Telegram или подключи TON-кошелёк для проверки NFT.':'Manual asset creation is disabled. Import Telegram gifts or connect a TON wallet to verify on-chain NFTs.'}</small></div>
       {/if}
      {:else if view==='favorites'}
      <section class="page-heading compact-heading favorites-heading"><div><div class="eyebrow">HASSLE NFT</div><h1>{t.favoritesTitle}</h1></div><button class="refresh-button" onclick={()=>view='orders'}>{lang==='ru'?'Назад в маркет':'Back to market'}</button></section>
      {#if favoriteOrders.length}<div class="orders-list">{#each favoriteOrders as order (order.id)}<article class="order-card"><div class="order-icon">{order.kind==='listing'?'↗':'⌕'}</div><div class="order-main"><div class="order-kind">{order.kind==='listing'?t.sell:t.buy}</div><h3>{order.title}</h3><p>{order.owner}</p></div><div class="order-price"><strong>{#if order.currency==='XTR'}{order.price} Stars{:else}<span class="gram-amount"><GramToken size={14}/>{order.price}</span>{/if}</strong><span class={'status-'+order.status}>{order.status==='active'?t.active:order.status==='matched'?t.matched:t.cancelled}</span></div><button class="heart-button hearted" onclick={()=>toggleFavoriteOrder(order)}>♥</button></article>{/each}</div>{:else}<div class="state-panel">{t.noFavorites}<button class="search-button" onclick={()=>view='orders'}>{lang==='ru'?'К активам':'Browse assets'}</button></div>{/if}
    {:else if view==='profile'}
      <section class="page-heading compact-heading"><div class="eyebrow">ACCOUNT</div><h1>{t.profile}</h1></section>
      <section class="profile-layout" data-profile-view><article class="profile-card profile-main"><div class="profile-avatar">{#if telegramPhoto}<img src={telegramPhoto} alt="Profile"/>{:else}<span>{telegramUser?telegramUser.slice(0,1).toUpperCase():'G'}</span>{/if}</div><div><h2>{telegramUser||t.guest}</h2><p>{signedIn?t.signed:t.authHint}</p>{#if telegramUsername}<small class="profile-handle">{telegramUsername}</small>{/if}{#if telegramPremium}<span class="profile-premium">TELEGRAM PREMIUM</span>{/if}</div>{#if !signedIn}<button class="publish-button" onclick={()=>telegramLogin()} disabled={authBusy}>{authBusy?(lang==='ru'?'Подтверждаем…':'Verifying…'):t.signIn+' →'}</button>{:else}<span class="telegram-verified-state">✓ {lang==='ru'?'Подтверждено':'Verified'}</span>{/if}</article>
      <div class="account-stats-grid profile-stats-grid">
        {#each [{label:lang==='ru'?'Активные объявления':'Active listings',value:accountStats.active_orders},{label:lang==='ru'?'Завершённые сделки':'Completed trades',value:accountStats.completed_orders},{label:lang==='ru'?'Отправлено офферов':'Offers sent',value:accountStats.pending_offers_sent},{label:lang==='ru'?'Входящие офферы':'Incoming offers',value:accountStats.pending_offers_received}] as metric}
          <div class="account-stat"><small>{metric.label}</small><strong>{metric.value}</strong></div>
        {/each}
      </div>
      <article class="profile-card gram-profile-card">
        <div class="gram-profile-mark"><GramToken size={34} class="gram-token-icon" /></div><div class="gram-profile-copy"><div class="eyebrow">HASSLE BALANCE</div><h2>GRAM</h2><strong>{lang==='ru'?'Баланс пока не подключён':'Balance not connected'}</strong><small>{lang==='ru'?'Внутренний ledger ещё не активирован. Реальные суммы не подменяются заглушками.':'The internal ledger is not active. No placeholder balance is shown.'}</small></div><button class="refresh-button" type="button" onclick={()=>showGramInfo=true}>{lang==='ru'?'Подробнее':'Details'}</button>
      </article>
      <article class="profile-card wallet-card">
        <div class="wallet-card-icon">◈</div><div class="wallet-card-copy"><div class="eyebrow">TON CONNECT · PROOF</div><h2>{lang==='ru'?'TON-кошелёк':'TON wallet'}</h2><p>{lang==='ru'?'Подтверждённый адрес кошелька, связанный с твоим Telegram-профилем.':'Wallet address cryptographically linked to your Telegram profile.'}</p><TonWalletConnect api={API} authToken={authToken} {lang} deliveryRequest={tonDeliveryRequest} paymentRequest={tonPaymentRequest} onPaymentSent={confirmTonPayment} onDeliverySent={async (dealId,boc)=>{tonDeliveryRequest=null;view='offers';try{await request('/api/marketplace/deals/'+encodeURIComponent(dealId)+'/ton-nft-delivery/submitted',undefined,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({boc})});notice=lang==='ru'?'Транзакция передачи отправлена. Покупатель должен проверить нового владельца.':'NFT transfer submitted. The buyer must verify the new owner.';}catch{notice=lang==='ru'?'Кошелёк отправил транзакцию. Не повторяй перевод, дождись обновления TON и проверь владельца.':'Wallet submitted the transaction. Do not send it again; wait for TON to update and verify ownership.';}await loadOffersAndDeals();}} onVerified={(address)=>{walletAddress=address;walletConnected=Boolean(address);}} /></div>
      </article>
      <article class="profile-card profile-inventory-card">
        <div class="profile-inventory-heading"><div><div class="eyebrow">YOUR COLLECTION</div><h2>{lang==='ru'?'Подарки и инвентарь':'Gifts & inventory'}</h2><small>{visibleVerifiedAssets.length} {lang==='ru'?'подтверждённых активов':'verified assets'}</small></div><button class="refresh-button" type="button" onclick={refreshOwnershipInventory} disabled={verifiedAssetsLoading || telegramGiftImportBusy || businessGiftImportBusy || tonNftImportBusy}>↻</button></div>
        <div class="profile-import-actions">
          <button type="button" class="profile-import-button" onclick={importTelegramGifts} disabled={!signedIn || telegramGiftImportBusy}>{telegramGiftImportBusy?'…':(lang==='ru'?'＋ Импорт Telegram':'＋ Import Telegram')}</button>
          <button type="button" class="profile-import-button" onclick={importBusinessGifts} disabled={!signedIn || !telegramBusinessStatus.gift_import_ready || businessGiftImportBusy}>{businessGiftImportBusy?'…':(lang==='ru'?'Business gifts':'Business gifts')}</button>
          <button type="button" class="profile-import-button" onclick={importTonWalletAssets} disabled={!signedIn || !walletConnected || tonNftImportBusy}>{tonNftImportBusy?'…':(lang==='ru'?'Импорт TON NFT':'Import TON NFTs')}</button>
        </div>
        <div class="profile-business-status"><span class:ready={telegramBusinessStatus.gift_import_ready}></span><span>{telegramBusinessStatus.gift_import_ready?(lang==='ru'?'Telegram Business подключён':'Telegram Business connected'):(lang==='ru'?'Telegram Business не подключён или нет прав':'Telegram Business not connected or missing permissions')}</span><button type="button" onclick={loadTelegramBusinessStatus}>{lang==='ru'?'Проверить':'Check'}</button></div>
        {#if telegramGiftImportMessage}<p class="profile-import-message">{telegramGiftImportMessage}</p>{/if}
        {#if businessGiftImportMessage}<p class="profile-import-message">{businessGiftImportMessage}</p>{/if}
        {#if tonNftImportMessage}<p class="profile-import-message">{tonNftImportMessage}</p>{/if}
        {#if verifiedAssets.some(asset=>asset.source==='telegram_gift') && !telegramOwnershipSnapshotComplete}<div class="ownership-verification-notice" role="status">{lang==='ru'?'Подарки Telegram временно скрыты: актуальное владение ещё не подтверждено. Нажми «Обновить», чтобы проверить аккаунт.':'Telegram gifts are temporarily hidden because current ownership has not been confirmed. Tap Refresh to verify your account.'}</div>{/if}{#if verifiedAssetsLoading}
          <div class="profile-inventory-empty">{lang==='ru'?'Обновляем инвентарь…':'Refreshing inventory…'}</div>
        {:else if visibleVerifiedAssets.length}
          <div class="profile-inventory-list">
            {#each visibleVerifiedAssets as asset (asset.id)}
              <article class="profile-asset-row">
                {#if assetPreviewUrl(asset)}<img src={assetPreviewUrl(asset)} alt={asset.title} loading="lazy"/>{:else}<img class="profile-asset-placeholder" src="/brand/hassle-mark.webp?v=224" alt="HASSLE NFT"/>{/if}
                <div class="profile-asset-copy"><strong>{asset.title}</strong><small>{asset.source==='telegram_gift'?'Telegram Gift':asset.source==='ton_nft'?'TON NFT':asset.asset_type==='username'?'Username':'+888'} · {asset.active_listing_id?(lang==='ru'?'На продаже':'Listed'):(lang==='ru'?'В инвентаре':'In inventory')}</small>{#if assetTraits(asset).length}<div class="asset-trait-chips profile-traits">{#each assetTraits(asset) as trait (trait.key)}<span class="asset-trait-chip"><small>{trait.key.toUpperCase()}</small><b>{trait.name}</b></span>{/each}</div>{/if}</div>
                {#if asset.active_listing_id}
                  {#if orders.find(order=>order.id===asset.active_listing_id)}<button class="profile-asset-action" type="button" onclick={()=>{const listing=orders.find(order=>order.id===asset.active_listing_id);if(listing)startEditOrder(listing);}}>{lang==='ru'?'Управлять':'Manage'}</button>{:else}<span class="profile-asset-listed">{lang==='ru'?'Выставлен':'Listed'}</span>{/if}
                {:else if asset.source==='telegram_gift'}<span class="profile-asset-listed">{lang==='ru'?'Передача не готова':'Transfer pending'}</span>
                {:else if assetListingEnabled}<button class="profile-asset-action" type="button" onclick={()=>startAssetListing(asset)}>{lang==='ru'?'Продать':'List'}</button>
                {:else}<span class="profile-asset-listed">{lang==='ru'?'Проверка':'Review'}</span>{/if}
              </article>
            {/each}
          </div>
        {:else}
          <div class="profile-inventory-empty"><strong>{lang==='ru'?'Инвентарь пока пуст':'Your inventory is empty'}</strong><small>{lang==='ru'?'Импортируй коллекционные подарки Telegram или NFT из подключённого TON-кошелька.':'Import collectible Telegram gifts or NFTs from your connected TON wallet.'}</small></div>
        {/if}
      </article>
      <details class="profile-trades-details">
        <summary><span><strong>{lang==='ru'?'Мои офферы и сделки':'My offers & deals'}</strong><small>{lang==='ru'?'Личные операции доступны только здесь, в профиле':'Private account operations stay inside your profile'}</small></span><b>{offers.length} / {deals.length}</b></summary>
        <div class="profile-trades-content">
          <div class="profile-trades-heading"><h3>{lang==='ru'?'Офферы':'Offers'}</h3><button class="refresh-button" type="button" onclick={loadOffersAndDeals} disabled={offersBusy}>↻</button></div>
          {#if offers.length}
            {#each visibleOffers.slice(0,12) as offer (offer.id)}
              <article class="profile-trade-row">
                <div><strong>{offer.order_title}</strong><small>{offer.is_mine?(lang==='ru'?'Мой оффер':'Sent by me'):(lang==='ru'?'Входящий оффер':'Received offer')} · {#if offer.currency==='XTR'}{offer.amount} ⭐{:else}<span class="gram-amount"><GramToken size={14}/>{offer.amount}</span>{/if} · {offerStatusLabel(offer.status)}</small></div>
                {#if offer.status==='pending' && offer.is_mine}
                  <div class="profile-trade-actions"><button type="button" onclick={()=>startEditOffer(offer)}>{lang==='ru'?'Изменить':'Edit'}</button><button type="button" onclick={()=>respondToOffer(offer.id,'withdraw')} disabled={offerActionBusy===offer.id}>{lang==='ru'?'Отозвать':'Withdraw'}</button></div>
                {:else if offer.status==='pending' && !offer.is_mine && offer.order_kind==='listing'}
                  <div class="profile-trade-actions"><button type="button" onclick={()=>respondToOffer(offer.id,'accept')} disabled={offerActionBusy===offer.id}>{lang==='ru'?'Принять':'Accept'}</button><button type="button" onclick={()=>respondToOffer(offer.id,'decline')} disabled={offerActionBusy===offer.id}>{lang==='ru'?'Отклонить':'Decline'}</button></div>
                {:else if offer.status==='pending' && !offer.is_mine}
                  <span class="profile-asset-listed">{lang==='ru'?'Ответ пока недоступен':'Response unavailable'}</span>
                {/if}
              </article>
            {/each}
          {:else}<div class="profile-inventory-empty">{lang==='ru'?'Офферов пока нет':'No offers yet'}</div>{/if}

          <div class="profile-trades-heading"><h3>{lang==='ru'?'Сделки и резервы':'Deals & reservations'}</h3><span>{deals.length}</span></div>
          {#if deals.length}
            {#each deals.slice(0,12) as deal (deal.id)}
              <article class="profile-deal-row">
                <div class="profile-deal-main"><strong>{deal.order_title}</strong><small>{deal.is_seller?(lang==='ru'?'Ты продавец':'You are the seller'):(lang==='ru'?'Ты покупатель':'You are the buyer')} · {#if deal.currency==='XTR'}{deal.agreed_amount} ⭐{:else}<span class="gram-amount"><GramToken size={14}/>{deal.agreed_amount}</span>{/if}</small><span>{dealStatusLabel(deal.status)}</span></div>
                <div class="profile-deal-actions">
                  <button type="button" onclick={()=>toggleDealTimeline(deal.id)}>{expandedDealTimeline===deal.id?(lang==='ru'?'Скрыть журнал':'Hide log'):(lang==='ru'?'Журнал':'Timeline')}</button>
                  {#if deal.status==='paid' || deal.status==='delivery_pending'}<button type="button" onclick={()=>openDealDispute(deal.id)} disabled={disputeBusy===deal.id}>{lang==='ru'?'Спор':'Dispute'}</button>{/if}
                  {#if (deal.status==='paid' || deal.status==='delivery_pending') && deal.is_seller && deal.asset_source==='telegram_gift'}<button type="button" onclick={()=>transferTelegramBusinessGift(deal.id)} disabled={businessGiftDeliveryBusy===deal.id}>{lang==='ru'?'Передать подарок':'Transfer gift'}</button>{/if}
                  {#if deal.status==='paid' && deal.is_seller && deal.asset_source==='ton_nft'}<button type="button" onclick={()=>prepareTonNftDelivery(deal.id)} disabled={tonDeliveryBusy===deal.id}>{lang==='ru'?'Подготовить передачу':'Prepare transfer'}</button>{:else if deal.status==='delivery_pending' && !deal.is_seller && deal.asset_source==='ton_nft'}<button type="button" onclick={()=>verifyTonNftDelivery(deal.id)} disabled={tonDeliveryBusy===deal.id}>{lang==='ru'?'Проверить TON':'Verify TON'}</button>{/if}
                  {#if deal.status==='payment_not_enabled' && !deal.is_seller && deal.payments_enabled && deal.currency==='TON'}<button type="button" onclick={()=>startTonCheckout(deal.id)} disabled={tonDeliveryBusy===deal.id}>{lang==='ru'?'Оплатить GRAM':'Pay GRAM'}</button>{/if}
                  {#if deal.status==='payment_not_enabled' && !deal.is_seller && deal.payments_enabled && deal.currency==='XTR'}<button type="button" onclick={()=>startStarsCheckout(deal.id)} disabled={starsCheckoutBusy===deal.id}>{lang==='ru'?'Оплатить Stars':'Pay Stars'}</button>{/if}
                  {#if deal.status==='awaiting_payment' && !deal.is_seller && deal.payments_enabled && deal.currency==='TON'}<button type="button" onclick={()=>startTonCheckout(deal.id)} disabled={tonDeliveryBusy===deal.id}>{lang==='ru'?'Продолжить оплату':'Continue payment'}</button>{/if}
                  {#if deal.status==='awaiting_payment' && !deal.is_seller && deal.payments_enabled && deal.currency==='XTR'}<button type="button" onclick={()=>startStarsCheckout(deal.id)} disabled={starsCheckoutBusy===deal.id}>{lang==='ru'?'Создать счёт':'Create invoice'}</button>{/if}
                  {#if deal.reservation_active && deal.status!=='paid' && deal.status!=='delivery_pending'}<button type="button" onclick={()=>cancelDealReservation(deal.id)} disabled={offerActionBusy===deal.id}>{lang==='ru'?'Снять резерв':'Release'}</button>{/if}
                </div>
                {#if expandedDealTimeline===deal.id}
                  {#if dealTimelineBusy===deal.id}<small>{lang==='ru'?'Загружаем журнал…':'Loading timeline…'}</small>
                  {:else if (dealTimelineItems[deal.id]||[]).length}
                    <div class="profile-deal-timeline">{#each dealTimelineItems[deal.id] as event (event.id)}<div><strong>{dealEventLabel(event)}</strong><small>{new Date(event.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</small></div>{/each}</div>
                  {:else}<small>{lang==='ru'?'Событий пока нет':'No events yet'}</small>{/if}
                {/if}
              </article>
            {/each}
          {:else}<div class="profile-inventory-empty">{lang==='ru'?'Сделок пока нет':'No deals yet'}</div>{/if}
        </div>
      </details>
       {#if signedIn && accountStats.seller_pending_settlements.length}
         <article class="profile-card activity-card">
           <div class="notification-heading"><div><div class="eyebrow">SELLER SETTLEMENT</div><strong>{lang==='ru'?'Начисления продавцу':'Seller earnings'}</strong></div><span>{accountStats.seller_pending_settlements.length}</span></div>
           <div class="activity-timeline">
             {#each accountStats.seller_pending_settlements as settlement (settlement.currency)}
               <div class="activity-row">
                 <span class="activity-marker"></span>
                 <div class="activity-row-main">
                   <strong>{#if settlement.currency==='XTR'}Telegram Stars{:else}<span class="gram-amount"><GramToken size={14}/>GRAM</span>{/if}</strong>
                   <small>{settlement.status==='pending_manual_payout'?(lang==='ru'?'Ожидает ручной выплаты через мультиподпись 2 из 3':'Awaiting manual 2-of-3 multisig payout'):(lang==='ru'?'Внутреннее начисление Stars. Автоматический вывод недоступен.':'Internal Stars credit. Automatic withdrawal is unavailable.')}</small>
                 </div>
                 <div class="activity-row-value"><strong>{#if settlement.currency==='XTR'}{settlement.amount} ⭐{:else}<span class="gram-amount"><GramToken size={14}/>{settlement.amount}</span>{/if}</strong></div>
               </div>
             {/each}
           </div>
         </article>
       {/if}
      {#if signedIn}
         <article class="profile-card seller-profile-editor">
           <div class="eyebrow">{t.sellerCardTitle}</div>
           <label>{t.sellerDisplayName}<input class="form-input" bind:value={sellerDisplayName} maxlength="48" /></label>
           <label>{t.sellerBio}<textarea class="form-input note-input" bind:value={sellerBio} maxlength="280"></textarea></label>
           {#if sellerProfile}<small class="profile-handle">{t.sellerProfileId}: {sellerProfile.profile_id}</small>{/if}
           <button class="publish-button" onclick={saveSellerProfile} disabled={sellerProfileBusy || !sellerDisplayName.trim()}>{sellerProfileBusy?'…':t.sellerSave}</button>
         </article>
       {/if}
       <article class="profile-card activity-card">
        <div class="notification-heading"><div><div class="eyebrow">{t.activityTitle}</div><strong>{accountActivity.length} / {activityTotal}</strong></div></div>
        {#if accountActivity.length}
          <div class="activity-timeline">
            {#each accountActivity as item (item.id)}
              <div class="activity-row">
                <span class="activity-marker"></span>
                <div class="activity-row-main"><strong>{activityLabel(item)}</strong><span>{item.title}</span><small>{item.actor_name} · {new Date(item.created_at*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')}</small></div>
                <div class="activity-row-value"><strong>{#if item.currency==='XTR'}{item.amount} ⭐{:else}<span class="gram-amount"><GramToken size={14}/>{item.amount}</span>{/if}</strong><small>{item.order_id}</small></div>
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
      <article class="profile-card preferences-card"><div class="eyebrow">PREFERENCES</div><h2>HASSLE NFT</h2><button class="language wide" onclick={()=>lang=lang==='ru'?'en':'ru'}>{lang==='ru'?'Язык интерфейса':'Interface language'} · {lang.toUpperCase()}</button><div class="profile-quick-actions"><button type="button" disabled title={lang==='ru'?'Контакт поддержки ещё не настроен':'Support contact is not configured yet'}>{lang==='ru'?'Поддержка · скоро':'Support · soon'}</button><button type="button" onclick={()=>showAboutModal=true}>{lang==='ru'?'О проекте':'About HASSLE'}</button></div></article>{#if isAdmin}
        <article class="profile-card moderation-card">
          <div class="notification-heading"><div><div class="eyebrow">HASSLE NFT · ADMIN</div><h2>{lang==='ru'?'Операционный центр':'Operations center'}</h2></div><button class="refresh-button" onclick={loadAdminOverview} disabled={adminBusy}>{adminBusy?'…':t.refresh}</button></div>
          {#if adminOverview}
            <div class="account-stats-grid">
              {#each [{label:lang==='ru'?'Пользователи':'Users',value:adminOverview.users},{label:lang==='ru'?'Проверенные активы':'Verified assets',value:adminOverview.verified_assets},{label:lang==='ru'?'Объявления':'Active listings',value:adminOverview.active_listings},{label:lang==='ru'?'Жалобы':'Open reports',value:adminOverview.open_reports},{label:lang==='ru'?'Споры':'Open disputes',value:adminOverview.open_disputes},{label:lang==='ru'?'Платежи на проверке':'Pending payments',value:adminOverview.pending_payments}] as metric}
                <div class="account-stat"><small>{metric.label}</small><strong>{metric.value}</strong></div>
              {/each}
            </div>
          {/if}
          {#if adminPermissions.includes('users_read')}
          <div class="notification-heading"><h3>{lang==='ru'?'Пользователи':'Users'}</h3></div>
          <div class="report-actions admin-user-search-row"><input class="form-input admin-user-search" type="search" inputmode="search" autocomplete="off" autocapitalize="off" spellcheck="false" bind:value={adminUserSearch} placeholder={lang==='ru'?'Telegram ID или @username':'Telegram ID or @username'} aria-label={lang==='ru'?'Поиск пользователя по Telegram ID или username':'Find user by Telegram ID or username'} /><button class="refresh-button" onclick={searchAdminUsers}>{lang==='ru'?'Найти':'Search'}</button></div>
          {#each adminUsers as user (String(user.telegram_id))}
            <div class="activity-row admin-user-row"><div class="activity-row-main"><strong>{user.first_name || 'Telegram user'} {user.username ? '@'+user.username : ''}</strong><small>ID {user.telegram_id} · {lang==='ru'?'активных сессий':'active sessions'}: {user.active_sessions}</small></div><button class="cancel-button" onclick={()=>revokeAdminUserSessions(String(user.telegram_id))}>{lang==='ru'?'Завершить сессии':'Revoke sessions'}</button></div>
          {/each}
          {/if}
          {#if adminPermissions.includes('deals_read')}
          <div class="notification-heading"><h3>{lang==='ru'?'Споры':'Disputes'} · {adminDisputes.length}</h3></div>
          {#each adminDisputes as dispute (String(dispute.id))}
            <div class="activity-row">
              <div class="activity-row-main"><strong>{String(dispute.id)}</strong><small>{lang==='ru'?'Покупатель':'Buyer'} {dispute.buyer_telegram_id} · {lang==='ru'?'Продавец':'Seller'} {dispute.seller_telegram_id} · {dispute.status}</small><small>{lang==='ru'?'Подтверждения':'Approvals'}: {dispute.approval_count || 0} / 2 · {dispute.approved_decision || (lang==='ru'?'нет решения':'no decision')}</small></div>
              {#if adminRole==='owner' || adminRole==='finance'}
                <div class="order-actions">
                  <button class="match-button" onclick={()=>approveAdminDispute(String(dispute.id),'refund_buyer')}>{lang==='ru'?'За возврат':'Approve refund'}</button>
                  <button class="match-button" onclick={()=>approveAdminDispute(String(dispute.id),'release_to_seller')}>{lang==='ru'?'За продавца':'Approve seller'}</button>
                </div>
              {/if}
            </div>
          {/each}
          {/if}
          {#if adminPermissions.includes('reconciliation_read')}
          <div class="notification-heading"><h3>{lang==='ru'?'Ручная сверка':'Manual reconciliation'}</h3></div>
          <div class="state-panel">{lang==='ru'?'Ожидают проверки платежи':'Payments awaiting review'}: {adminReconciliation.payments.length} · {lang==='ru'?'передачи активов':'asset transfers'}: {adminReconciliation.transfers.length}</div>
          {#each adminReconciliation.payments as item (String(item.id))}
            <div class="activity-row"><div class="activity-row-main"><strong>{item.provider} · {item.amount} {item.currency}</strong><small>{item.deal_id} · {item.status}</small></div></div>
          {/each}
          {#each adminReconciliation.transfers as item (String(item.id))}
            <div class="activity-row"><div class="activity-row-main"><strong>{lang==='ru'?'Передача актива ожидает сверки':'Asset transfer needs reconciliation'}</strong><small>{item.deal_id} · {item.event_type}</small></div></div>
          {/each}
          {/if}
          {#if adminRole==='owner' || adminRole==='finance'}
          <div class="notification-heading"><h3>{lang==='ru'?'Журнал операций':'Operations journal'}</h3></div>
          {#each adminAudit as event (String(event.id))}
            <div class="activity-row"><div class="activity-row-main"><strong>{event.action} · {event.outcome}</strong><small>{lang==='ru'?'Администратор':'Admin'} {event.actor_telegram_id} · {new Date(Number(event.created_at)*1000).toLocaleString(lang==='ru'?'ru-RU':'en-US')} · {String(event.target_hash).slice(0,12)}…</small></div></div>
          {/each}
          {/if}
          {#if adminPermissions.includes('assets_read')}
          <div class="notification-heading"><h3>{lang==='ru'?'Проверенные активы':'Verified assets'}</h3></div>
          {#each adminAssets as asset (String(asset.id))}
            <div class="activity-row"><div class="activity-row-main"><strong>{asset.title}</strong><small>{asset.source} · owner {asset.owner_telegram_id} · {lang==='ru'?'активных объявлений':'active listings'}: {asset.active_listings}</small></div>{#if adminPermissions.includes('assets_moderate')}<button class="cancel-button" onclick={()=>revokeAdminAsset(String(asset.id))}>{lang==='ru'?'Отозвать':'Revoke'}</button>{/if}</div>
          {/each}
          {/if}
        </article>
        {#if adminPermissions.includes('reports')}
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
                  {#if adminRole==='owner' || adminRole==='moderator'}
                    <div class="order-actions">
                      <button class="match-button" onclick={()=>moderateOrderReport(report.id,'dismiss')} disabled={adminReportActionBusy===report.id}>{t.dismissReport}</button>
                      <button class="cancel-button" onclick={()=>moderateOrderReport(report.id,'remove_order')} disabled={adminReportActionBusy===report.id}>{t.removeReportedOrder}</button>
                    </div>
                  {/if}
                </article>
              {/each}
            </div>
          {/if}
        </article>
        {/if}
      {/if}</section>
    {/if}

      {#if showAssetDetails && selectedAsset}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)showAssetDetails=false;}}>
          <div class="sell-modal asset-detail-modal" role="dialog" aria-modal="true" aria-labelledby="asset-detail-title">
            <button class="modal-close" onclick={()=>showAssetDetails=false} aria-label={lang==='ru'?'Закрыть':'Close'}>×</button>
            <div class={'asset-detail-art asset-art-'+assetTypeOf(selectedAsset)}>
              {#if assetTypeOf(selectedAsset)==='username'}<span class="asset-at">@</span><strong>{selectedAsset.title.replace(/^@/,'')}</strong><small>TELEGRAM USERNAME</small>
              {:else if assetTypeOf(selectedAsset)==='number'}<span class="asset-number">{selectedAsset.title.replace(/[^0-9+]/g,'') || '888'}</span><small>ANONYMOUS NUMBER</small>
              {:else if assetTypeOf(selectedAsset)==='gift'}{#if assetPreviewUrl(selectedAsset)}<img class="asset-detail-gift-image" src={assetPreviewUrl(selectedAsset)} alt={selectedAsset.title} />{:else}<div class="asset-preview-placeholder"><span>PREVIEW</span><small>{lang==='ru'?'Изображение появится после импорта подарка':'Gift preview appears after verified import'}</small></div>{/if}
               {:else}<div class="asset-preview-placeholder"><span>ASSET</span><small>{lang==='ru'?'Тип актива ещё не подтверждён':'Asset type is not verified yet'}</small></div>{/if}
            </div>
            <div class="eyebrow">HASSLE NFT · ASSET</div><h2 id="asset-detail-title">{selectedAsset.title}</h2>
            <div class="asset-detail-meta"><span>ID <b>#{selectedAsset.id}</b></span><span>{lang==='ru'?'Продавец':'Seller'} <b>{selectedAsset.owner}</b></span></div>
            <div class="asset-detail-price"><small>{lang==='ru'?'Цена продавца':'Seller price'}</small><strong>{#if selectedAsset.currency==='XTR'}{selectedAsset.price} Stars{:else}<span class="gram-amount"><GramToken size={18}/>{selectedAsset.price}</span>{/if}</strong></div>
            {#if selectedAsset.note}<p class="asset-detail-note">{selectedAsset.note}</p>{/if}
            <div class="asset-detail-unavailable"><strong>{lang==='ru'?'Характеристики актива':'Asset traits'}</strong><span>{lang==='ru'?'Метаданные и traits появятся после подключения источника NFT-метаданных.':'Metadata and traits will appear after an NFT metadata source is connected.'}</span></div>
            {#if selectedAsset.isMine}
              <button class="ton-primary offer-submit" onclick={()=>{startEditOrder(selectedAsset!);showAssetDetails=false;}}>{lang==='ru'?'Управлять объявлением':'Manage listing'}</button>
            {:else}
              <button class="ton-primary offer-submit" disabled={!signedIn} title={!signedIn?(lang==='ru'?'Войди через Telegram, чтобы отправить оффер':'Sign in with Telegram to make an offer'):undefined} onclick={()=>{if(!selectedAsset)return;selectedOfferOrder=selectedAsset;offerAmount=String(selectedAsset.price);offerNote='';showOfferModal=true;showAssetDetails=false;}}>{lang==='ru'?'Сделать оффер':'Make an offer'}</button>
              <button class="report-asset-button" onclick={()=>{const asset=selectedAsset;if(!asset)return;if(!signedIn){notice=t.authHint;haptic('error');return;}startOrderReport(asset.id);}}>{t.reportOrder}</button>
              <button class="asset-buy-disabled" disabled title={lang==='ru'?'Покупка включится после запуска защищённой оплаты и escrow':'Buying will be enabled after secure payments and escrow are live'}>{lang==='ru'?'Покупка скоро':'Buy · Coming soon'}</button>
            {/if}
          </div>
        </div>
      {/if}
      {#if showBuyRequestModal}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !createOrderBusy)showBuyRequestModal=false;}}>
          <div class="sell-modal offer-modal" role="dialog" aria-modal="true" aria-labelledby="buy-request-title">
            <button class="modal-close" onclick={()=>showBuyRequestModal=false} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={createOrderBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · BUY REQUEST</div>
            <h2 id="buy-request-title">{lang==='ru'?'Создать заявку на покупку':'Create a buy request'}</h2>
            <label>{lang==='ru'?'Какой актив ищешь?':'What asset are you looking for?'}<input class="form-input" bind:value={createTitle} maxlength="120" placeholder={lang==='ru'?'Например, Plush Pepe #123':'For example, Plush Pepe #123'} /></label>
            <label>{lang==='ru'?'Бюджет':'Budget'}<input class="form-input" type="number" min="0.01" step={createCurrency==='XTR'?'1':'0.01'} bind:value={createPrice} inputmode="decimal" /></label>
            <label>{lang==='ru'?'Валюта':'Currency'}<select class="form-input" bind:value={createCurrency}><option value="TON">GRAM</option><option value="XTR">Telegram Stars</option></select></label>
            <label>{lang==='ru'?'Условия (необязательно)':'Details (optional)'}<textarea class="form-input note-input" bind:value={createNote} maxlength="300" placeholder={lang==='ru'?'Опиши нужную коллекцию или состояние':'Describe the collection or condition you want'}></textarea></label>
            <button class="ton-primary offer-submit" onclick={submitBuyRequest} disabled={createOrderBusy}>{createOrderBusy?(lang==='ru'?'Публикуем…':'Publishing…'):(lang==='ru'?'Опубликовать заявку →':'Publish buy request →')}</button>
            <small class="sell-modal-hint">{lang==='ru'?'Заявка показывает продавцам твой запрос и бюджет. Деньги не резервируются и покупка не происходит автоматически.':'The request shows sellers what you want and your budget. No funds are reserved and no purchase happens automatically.'}</small>
          </div>
        </div>
      {/if}
      {#if showSellModal && selectedInventoryAsset}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !createOrderBusy){showSellModal=false;selectedInventoryAsset=null;}}}>
          <div class="sell-modal inventory-list-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-list-title">
            <button class="modal-close" onclick={()=>{showSellModal=false;selectedInventoryAsset=null;}} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={createOrderBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · NEW LISTING</div>
            <h2 id="inventory-list-title">{lang==='ru'?'Выставить NFT':'List NFT for sale'}</h2>
            <div class="offer-asset-preview">
              {#if assetPreviewUrl(selectedInventoryAsset)}<img src={assetPreviewUrl(selectedInventoryAsset)} alt={selectedInventoryAsset.title}/>{:else}<span>◈</span>{/if}
              <div><strong>{selectedInventoryAsset.title}</strong><small>{selectedInventoryAsset.source==='ton_nft'?'TON NFT':'Verified asset'} · {selectedInventoryAsset.external_asset_id}</small></div>
            </div>
            <label>{lang==='ru'?'Цена':'Price'}<input class="form-input" type="number" min="0.01" step={createCurrency==='XTR'?'1':'0.01'} bind:value={createPrice} placeholder={createCurrency==='XTR'?'100':'1.25'} /></label>
            <label>{lang==='ru'?'Валюта':'Currency'}
              <select class="form-input" bind:value={createCurrency}>
                <option value="TON">GRAM</option>
                <option value="XTR">Telegram Stars</option>
              </select>
            </label>
            <label>{lang==='ru'?'Описание (необязательно)':'Description (optional)'}<textarea class="form-input note-input" bind:value={createNote} maxlength="300" placeholder={lang==='ru'?'Краткая информация об активе':'A short note about this asset'}></textarea></label>
            <button class="ton-primary offer-submit" onclick={submitAssetListing} disabled={createOrderBusy || !assetListingEnabled}>{createOrderBusy?(lang==='ru'?'Публикуем…':'Publishing…'):(lang==='ru'?'Опубликовать объявление →':'Publish listing →')}</button>
            <small class="sell-modal-hint">{lang==='ru'?'Название и изображение берутся из подтверждённого NFT. Оплата и передача актива пока отключены.':'Name and artwork come from the verified NFT. Payment and asset transfer remain disabled.'}</small>
          </div>
        </div>
      {/if}
      {#if reportingOrderId}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !reportBusy)reportingOrderId='';}}>
          <div class="sell-modal report-modal" role="dialog" aria-modal="true" aria-labelledby="report-title">
            <button class="modal-close" onclick={()=>reportingOrderId=''} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={reportBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · REPORT</div>
            <h2 id="report-title">{t.reportOrder}</h2>
            <label>{t.reportReason}
              <select class="form-input" bind:value={reportReason}>
                <option value="scam">{t.reportScam}</option><option value="misleading">{t.reportMisleading}</option><option value="inappropriate">{t.reportInappropriate}</option><option value="spam">{t.reportSpam}</option><option value="other">{t.reportOther}</option>
              </select>
            </label>
            <label>{t.reportDetails}<textarea class="form-input note-input" bind:value={reportDetails} maxlength="1000" placeholder={lang==='ru'?'Опиши проблему (необязательно)':'Describe the issue (optional)'}></textarea></label>
            <div class="report-actions"><button class="refresh-button" onclick={()=>reportingOrderId=''} disabled={reportBusy}>{t.cancelReport}</button><button class="ton-primary" onclick={()=>submitOrderReport(reportingOrderId)} disabled={reportBusy}>{reportBusy?(lang==='ru'?'Отправляем…':'Sending…'):t.sendReport}</button></div>
          </div>
        </div>
      {/if}
      {#if editingOfferId}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !offerEditBusy)editingOfferId='';}}>
          <div class="sell-modal offer-modal" role="dialog" aria-modal="true" aria-labelledby="edit-offer-title">
            <button class="modal-close" onclick={()=>editingOfferId=''} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={offerEditBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · OFFER</div>
            <h2 id="edit-offer-title">{lang==='ru'?'Изменить оффер':'Edit offer'}</h2>
            <label>{lang==='ru'?'Новая сумма':'New amount'}<input class="form-input" type="number" min="0.01" step={offers.find(item=>item.id===editingOfferId)?.currency==='XTR'?'1':'0.01'} bind:value={editOfferAmount}/></label>
            <label>{lang==='ru'?'Сообщение продавцу':'Message to seller'}<textarea class="form-input note-input" bind:value={editOfferNote} maxlength="300"></textarea></label>
            <button class="ton-primary offer-submit" onclick={saveOfferEdit} disabled={offerEditBusy}>{offerEditBusy?(lang==='ru'?'Сохраняем…':'Saving…'):(lang==='ru'?'Сохранить оффер':'Save offer')}</button>
            <small class="sell-modal-hint">{lang==='ru'?'Изменить можно только свой ожидающий оффер. После ответа продавца редактирование закрывается.':'Only your pending offer can be edited. Editing closes once the seller responds.'}</small>
          </div>
        </div>
      {/if}
      {#if editingOrderId}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !orderEditBusy)editingOrderId='';}}>
          <div class="sell-modal offer-modal" role="dialog" aria-modal="true" aria-labelledby="edit-listing-title">
            <button class="modal-close" onclick={()=>editingOrderId=''} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={orderEditBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · LISTING</div>
            <h2 id="edit-listing-title">{orders.find(item=>item.id===editingOrderId)?.kind==='buy-order'?(lang==='ru'?'Изменить заявку на покупку':'Edit buy request'):(lang==='ru'?'Управление объявлением':'Manage listing')}</h2>
            {#if orders.find(item=>item.id===editingOrderId)?.kind==='buy-order'}<label>{lang==='ru'?'Название искомого актива':'Wanted asset'}<input class="form-input" bind:value={editTitle} maxlength="120" /></label>{/if}
            <label>{lang==='ru'?'Цена / бюджет':'Price / budget'}<input class="form-input" type="number" min="0.01" step={orders.find(item=>item.id===editingOrderId)?.currency==='XTR'?'1':'0.01'} bind:value={editPrice} /></label>
            <label>{lang==='ru'?'Комментарий':'Description'}<textarea class="form-input note-input" bind:value={editNote} maxlength="300"></textarea></label>
            <button class="ton-primary offer-submit" onclick={()=>saveOrderEdit(editingOrderId)} disabled={orderEditBusy}>{orderEditBusy?(lang==='ru'?'Сохраняем…':'Saving…'):(lang==='ru'?'Сохранить изменения':'Save changes')}</button>
            <button class="report-asset-button" onclick={()=>cancelOrder(editingOrderId)} disabled={orderEditBusy}>{orders.find(item=>item.id===editingOrderId)?.kind==='buy-order'?(lang==='ru'?'Отменить заявку':'Cancel buy request'):(lang==='ru'?'Снять объявление с продажи':'Remove listing from sale')}</button>
            <small class="sell-modal-hint">{orders.find(item=>item.id===editingOrderId)?.kind==='buy-order'?(lang==='ru'?'Заявка показывает твой интерес и бюджет. Отмена не списывает деньги.':'A buy request shows your interest and budget. Cancelling it does not charge funds.'):(lang==='ru'?'Название и данные NFT защищены проверкой владения. Можно изменить цену и комментарий. Снятие объявления не передаёт актив и не списывает средства.':'NFT name and metadata are locked to verified ownership. You can change the price and note. Removing a listing does not transfer the asset or charge funds.')}</small>
          </div>
        </div>
      {/if}
      {#if showOfferModal && selectedOfferOrder}
        <div class="modal-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget && !offerSubmitBusy)showOfferModal=false;}}>
          <div class="sell-modal offer-modal" role="dialog" aria-modal="true" aria-labelledby="offer-title">
            <button class="modal-close" onclick={()=>showOfferModal=false} aria-label={lang==='ru'?'Закрыть':'Close'} disabled={offerSubmitBusy}>×</button>
            <div class="eyebrow">HASSLE NFT · OFFER</div>
            <h2 id="offer-title">{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Ответить покупателю':'Respond to buyer'):(lang==='ru'?'Предложить цену':'Make an offer')}</h2>
            <div class="offer-asset-preview"><span>{assetTypeOf(selectedOfferOrder)==='username'?'＠':assetTypeOf(selectedOfferOrder)==='number'?'888':assetTypeOf(selectedOfferOrder)==='gift'?'NFT':'ASSET'}</span><div><strong>{selectedOfferOrder.title}</strong><small>{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Бюджет покупателя':'Buyer budget'):(lang==='ru'?'Цена продавца':'Seller price')}: {#if selectedOfferOrder.currency==='XTR'}{selectedOfferOrder.price} ⭐{:else}<span class="gram-amount"><GramToken size={13}/>{selectedOfferOrder.price}</span>{/if}</small></div></div>
            <label>{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Твоя цена покупателю':'Your price to buyer'):(lang==='ru'?'Твоё предложение':'Your offer')}<input class="form-input" type="number" min="0.01" step={selectedOfferOrder.currency==='XTR'?'1':'0.01'} bind:value={offerAmount} placeholder={selectedOfferOrder.currency==='XTR'?'100':'1.25'}/></label>
            <label>{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Опиши предлагаемый актив':'Describe the asset you offer'):(lang==='ru'?'Сообщение продавцу':'Message to seller')}<textarea class="form-input note-input" bind:value={offerNote} maxlength="300" placeholder={selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Название, коллекция и детали актива (обязательно)':'Asset name, collection and details (required)'):(lang==='ru'?'Необязательно':'Optional')}></textarea></label>
            <button class="ton-primary offer-submit" disabled={offerSubmitBusy || selectedOfferOrder.kind==='buy-order'} onclick={async()=>{if(!selectedOfferOrder || selectedOfferOrder.kind==='buy-order')return;offerSubmitBusy=true;try{const sent=await createOffer(selectedOfferOrder.id);if(sent){showOfferModal=false;selectedOfferOrder=null;}}finally{offerSubmitBusy=false;}}}>{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Ответы временно отключены':'Responses unavailable'):offerSubmitBusy?(lang==='ru'?'Отправляем…':'Sending…'):t.sendOffer+' →'}</button>
            <small class="sell-modal-hint">{selectedOfferOrder.kind==='buy-order'?(lang==='ru'?'Ответы на заявки покупателей временно отключены. Сначала подключим привязку конкретного проверенного актива и безопасное резервирование.':'Seller responses to buy requests are temporarily unavailable until a specific verified asset can be bound and safely reserved.'):(lang==='ru'?'Оффер отправится владельцу актива. Это не перевод средств и не завершение сделки.':'The offer goes to the asset owner. It does not transfer funds or complete a trade.')}</small>
          </div>
        </div>
      {/if}


    {#if notice}<div class="toast" role="status">{notice}<button onclick={()=>notice=''}>×</button></div>{/if}
    <footer class="footer"><span>✳ HASSLE NFT</span><span>Telegram NFT Gift Marketplace · n0kl1pzz1k</span><span>© 2026</span></footer>
  </main>
</div>

<BottomNav lang={lang} active={view==='favorites'?'orders':view==='seller'?'profile':view==='create'?'profile':view as 'orders'|'auctions'|'offers'|'profile'} onNavigate={(target)=>{if(target==='profile'){openProfile();return;}haptic();view=target;notice='';}} />

{#if showGramInfo}<div class="hassle-sheet-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)showGramInfo=false;}}><section class="hassle-info-sheet" role="dialog" aria-modal="true" aria-labelledby="gram-info-title"><button class="sheet-close" type="button" onclick={()=>showGramInfo=false} aria-label={lang==='ru'?'Закрыть':'Close'}>×</button><div class="gram-sheet-icon"><GramToken size={34} class="gram-token-icon" /></div><span class="eyebrow">HASSLE INTERNAL CURRENCY</span><h2 id="gram-info-title">GRAM</h2><p>{lang==='ru'?'Внутренний баланс GRAM ещё не подключён к серверному учёту. Сейчас реальные расчёты и балансы остаются в существующих платёжных механизмах. Мы не показываем выдуманную сумму и не конвертируем TON или Stars автоматически.':'The internal GRAM ledger is not connected yet. Existing payment rails and balances remain unchanged. No fabricated balance or automatic TON/Stars conversion is displayed.'}</p><div class="gram-sheet-note"><span>●</span>{lang==='ru'?'Баланс появится после интеграции и проверки ledger.':'Balance will appear after ledger integration and verification.'}</div><div class="gram-action-row"><button type="button" onclick={()=>{showGramInfo=false;gramAction='deposit';}}>{lang==='ru'?'Пополнить':'Deposit'}</button><button type="button" onclick={()=>{showGramInfo=false;gramAction='withdraw';}}>{lang==='ru'?'Вывести':'Withdraw'}</button></div><button class="sheet-primary" type="button" onclick={()=>{showGramInfo=false;view='profile';void loadSellerProfile();}}>{lang==='ru'?'Открыть профиль':'Open profile'}</button></section></div>{/if}
{#if gramAction}<div class="hassle-sheet-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)gramAction=null;}}><section class="hassle-info-sheet gram-action-sheet" role="dialog" aria-modal="true" aria-labelledby="gram-action-title"><button class="sheet-close" type="button" onclick={()=>gramAction=null} aria-label={lang==='ru'?'Закрыть':'Close'}>×</button><div class="gram-sheet-icon"><GramToken size={34} class="gram-token-icon" /></div><span class="eyebrow">GRAM · WALLET</span><h2 id="gram-action-title">{gramAction==='deposit'?(lang==='ru'?'Пополнение GRAM':'Deposit GRAM'):(lang==='ru'?'Вывод GRAM':'Withdraw GRAM')}</h2><p>{lang==='ru'?'Операции с внутренним балансом пока недоступны: серверный GRAM ledger и проверка транзакций ещё не подключены. Никакие средства не будут списаны или зачислены.':'Internal balance operations are not available yet. The GRAM ledger and transaction verification are not connected. No funds will be charged or credited.'}</p><div class="gram-sheet-note"><span>●</span>{lang==='ru'?'Функция появится после завершения платёжной интеграции и аудита.':'This feature will be enabled after payment integration and audit.'}</div><button class="sheet-primary" type="button" onclick={()=>gramAction=null}>{lang==='ru'?'Понятно':'Got it'}</button></section></div>{/if}
{#if showAboutModal}<div class="hassle-sheet-backdrop" role="presentation" onclick={(event)=>{if(event.target===event.currentTarget)showAboutModal=false;}}><section class="hassle-info-sheet" role="dialog" aria-modal="true" aria-labelledby="about-hassle-title"><button class="sheet-close" type="button" onclick={()=>showAboutModal=false} aria-label={lang==='ru'?'Закрыть':'Close'}>×</button><div class="gram-sheet-icon">H</div><span class="eyebrow">TELEGRAM NFT MARKETPLACE</span><h2 id="about-hassle-title">HASSLE NFT</h2><p>{lang==='ru'?'Независимый маркетплейс Telegram NFT-подарков, коллекционных имён и номеров. Платформа показывает только подтверждённые активы и не подменяет реальные балансы демонстрационными цифрами.':'An independent marketplace for Telegram NFT gifts, collectible usernames and numbers. The platform displays verified assets and never substitutes real balances with demo values.'}</p><button class="sheet-primary" type="button" onclick={()=>showAboutModal=false}>{lang==='ru'?'Закрыть':'Close'}</button></section></div>{/if}
{#if showSplash}<div class="hassle-splash" out:fade={{duration:260}} aria-label="HASSLE NFT loading"><div class="splash-content"><div class="splash-mark"><img src="/brand/hassle-mark.webp?v=224" alt="HASSLE NFT"/></div><div class="splash-wordmark">HASSLE <span>NFT</span></div><div class="splash-progress"><i></i></div><small>{lang==='ru'?'Загрузка…':'Loading…'}</small></div></div>{/if}
