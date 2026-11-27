<script lang="ts">
  import { fade } from 'svelte/transition';
  import GramToken from './GramToken.svelte';

  type AssetOrder = {
    id: string;
    title: string;
    image_url?: string;
    asset_type?: 'gift' | 'username' | 'number' | null;
    owner: string;
    price: string | number;
    currency: 'TON' | 'XTR';
    note?: string;
  };
  type Props = {
    order: AssetOrder;
    favorite: boolean;
    lang: 'ru' | 'en';
    sellerProfileId: string | null;
    onSeller: () => void;
    onFavorite: () => void;
    onOffer: () => void;
    onOpen: () => void;
    canOffer: boolean;
    isMine: boolean;
    onManage: () => void;
    backgroundColor: 'midnight'|'violet'|'blue'|'emerald'|'rose'|'gold';
  };
  let { order, favorite, lang, sellerProfileId, onSeller, onFavorite, onOffer, onOpen, canOffer, isMine, onManage, backgroundColor }: Props = $props();
  let failedImageUrl = $state('');

  function assetType(order: AssetOrder): 'gift' | 'username' | 'number' | 'unknown' {
    return order.asset_type === 'gift' || order.asset_type === 'username' || order.asset_type === 'number'
      ? order.asset_type
      : 'unknown';
  }
  const kind = $derived(assetType(order));
  const typeLabel = $derived(kind === 'number'
    ? (lang === 'ru' ? 'Номер 888' : 'Anonymous number')
    : kind === 'username' ? 'Username'
      : kind === 'unknown' ? (lang === 'ru' ? 'Актив' : 'Asset')
        : (lang === 'ru' ? 'NFT-подарок' : 'NFT Gift'));
</script>

<article class="asset-card" in:fade={{ duration: 160 }}>
  <div class={'asset-art asset-art-' + kind} style={`background-color: ${({midnight:'#171c24',violet:'#39245d',blue:'#173b62',emerald:'#164a3d',rose:'#5a293d',gold:'#59451d'})[backgroundColor]}`}>
    {#if kind === 'username'}
      <img class="asset-brand-art" src="/brand/hassle-mark.webp" alt=""/><span class="asset-at">@</span><strong>{order.title.replace(/^@/, '')}</strong><small>TELEGRAM USERNAME</small>
    {:else if kind === 'number'}
      <img class="asset-brand-art" src="/brand/hassle-mark.webp" alt=""/><span class="asset-number">{order.title.replace(/[^0-9+]/g, '') || '888'}</span><small>ANONYMOUS NUMBER</small>
    {:else if kind === 'gift'}
      {#if order.image_url && failedImageUrl !== order.image_url}<img class="asset-gift-image" src={order.image_url} alt={order.title} loading="lazy" onerror={()=>failedImageUrl=order.image_url || ''} />{:else}<div class="asset-preview-placeholder"><img src="/brand/hassle-mark.webp" alt="HASSLE NFT"/><small>{lang === 'ru' ? 'Ожидается изображение подарка' : 'Gift artwork pending'}</small></div>{/if}
    {:else}
      <div class="asset-preview-placeholder"><img src="/brand/hassle-mark.webp" alt="HASSLE NFT"/><small>{lang === 'ru' ? 'Подтверждённый актив' : 'Verified asset'}</small></div>
    {/if}
    <span class="asset-type-pill">{typeLabel}</span>
    <button class={'asset-favorite ' + (favorite ? 'is-favorite' : '')} onclick={onFavorite} aria-label={lang === 'ru' ? (favorite ? 'Убрать из избранного' : 'Добавить в избранное') : (favorite ? 'Remove from favorites' : 'Add to favorites')}>{#if favorite}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.2 8.8c0 4.1-8.2 10-8.2 10s-8.2-5.9-8.2-10a4.1 4.1 0 0 1 8.2-1.3 4.1 4.1 0 0 1 8.2 1.3Z"/></svg>{:else}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.2 8.8c0 4.1-8.2 10-8.2 10s-8.2-5.9-8.2-10a4.1 4.1 0 0 1 8.2-1.3 4.1 4.1 0 0 1 8.2 1.3Z"/></svg>{/if}</button>
  </div>
  <div class="asset-card-content">
    <div class="asset-title-row"><button class="asset-title-button" onclick={onOpen}>{order.title}</button><span class="asset-id">#{order.id.slice(-5)}</span></div>
    <p class="asset-owner">{#if sellerProfileId}<button class="asset-seller-link" onclick={onSeller}>{order.owner}</button>{:else}{order.owner}{/if}{order.note ? ' · ' + order.note : ''}</p>
    <div class="asset-card-footer">
      <div><small>{lang === 'ru' ? 'Цена' : 'Price'}</small><strong>{order.price} {#if order.currency === 'XTR'}<span>Stars</span>{:else}<span class="asset-gram-price"><GramToken size={16}/> GRAM</span>{/if}</strong></div>
      {#if isMine}
        <button class="asset-offer-button" onclick={onManage}>{lang === 'ru' ? 'Управлять' : 'Manage listing'}</button>
      {:else}
        <div class="asset-card-actions">
          <button class="asset-details-button" type="button" onclick={onOpen}>{lang==='ru'?'Подробнее':'Details'}</button>
          <button class="asset-offer-button" onclick={onOffer} disabled={!canOffer} title={!canOffer ? (lang === 'ru' ? 'Войди через Telegram, чтобы отправить оффер' : 'Sign in with Telegram to make an offer') : undefined}>{canOffer ? (lang === 'ru' ? 'Оффер' : 'Offer') : (lang === 'ru' ? 'Войти' : 'Sign in')}</button>
        </div>
      {/if}
    </div>
  </div>
</article>
