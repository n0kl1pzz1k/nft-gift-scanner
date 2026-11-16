<script lang="ts">
  import { fade } from 'svelte/transition';

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
  };
  let { order, favorite, lang, sellerProfileId, onSeller, onFavorite, onOffer, onOpen, canOffer }: Props = $props();

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
  <div class={'asset-art asset-art-' + kind}>
    {#if kind === 'username'}
      <span class="asset-at">@</span><strong>{order.title.replace(/^@/, '')}</strong><small>TELEGRAM USERNAME</small>
    {:else if kind === 'number'}
      <span class="asset-number">{order.title.replace(/[^0-9+]/g, '') || '888'}</span><small>ANONYMOUS NUMBER</small>
    {:else if kind === 'gift'}
      {#if order.image_url}<img class="asset-gift-image" src={order.image_url} alt={order.title} loading="lazy" />{:else}<div class="asset-preview-placeholder"><span>PREVIEW</span><small>{lang === 'ru' ? 'Изображение появится после импорта подарка' : 'Gift preview appears after verified import'}</small></div>{/if}
    {:else}
      <div class="asset-preview-placeholder"><span>ASSET</span><small>{lang === 'ru' ? 'Тип актива ещё не подтверждён' : 'Asset type is not verified yet'}</small></div>
    {/if}
    <span class="asset-type-pill">{typeLabel}</span>
    <button class={'asset-favorite ' + (favorite ? 'is-favorite' : '')} onclick={onFavorite} aria-label={lang === 'ru' ? (favorite ? 'Убрать из избранного' : 'Добавить в избранное') : (favorite ? 'Remove from favorites' : 'Add to favorites')}>{favorite ? '♥' : '♡'}</button>
  </div>
  <div class="asset-card-content">
    <div class="asset-title-row"><button class="asset-title-button" onclick={onOpen}>{order.title}</button><span class="asset-id">#{order.id.slice(-5)}</span></div>
    <p class="asset-owner">{#if sellerProfileId}<button class="asset-seller-link" onclick={onSeller}>{order.owner}</button>{:else}{order.owner}{/if}{order.note ? ' · ' + order.note : ''}</p>
    <div class="asset-card-footer">
      <div><small>{lang === 'ru' ? 'Цена' : 'Price'}</small><strong>{order.price} <span>{order.currency === 'XTR' ? '⭐' : 'TON'}</span></strong></div>
      <button class="asset-offer-button" onclick={onOffer} disabled={!canOffer} title={!canOffer ? (lang === 'ru' ? 'Нужны TON Connect и подтверждённый актив' : 'TON Connect and a verified asset are required') : undefined}>{canOffer ? (lang === 'ru' ? 'Сделать оффер' : 'Make offer') : (lang === 'ru' ? 'После TON Connect' : 'After TON Connect')}</button>
    </div>
  </div>
</article>
