<script lang="ts">
  import { fade } from 'svelte/transition';

  type AssetOrder = {
    id: string;
    title: string;
    owner: string;
    price: string | number;
    currency: 'TON' | 'XTR';
    note?: string;
  };
  type Props = {
    order: AssetOrder;
    favorite: boolean;
    lang: 'ru' | 'en';
    onFavorite: () => void;
    onOffer: () => void;
  };
  let { order, favorite, lang, onFavorite, onOffer }: Props = $props();

  function assetType(title: string): 'gift' | 'username' | 'number' {
    if (/888|anonymous.*number|number.*888/i.test(title)) return 'number';
    if (/^@|t[.]me[/]|username/i.test(title)) return 'username';
    return 'gift';
  }
  const kind = $derived(assetType(order.title));
  const typeLabel = $derived(kind === 'number'
    ? (lang === 'ru' ? 'Номер 888' : 'Anonymous number')
    : kind === 'username' ? 'Username'
      : (lang === 'ru' ? 'NFT-подарок' : 'NFT Gift'));
</script>

<article class="asset-card" in:fade={{ duration: 160 }}>
  <div class={'asset-art asset-art-' + kind}>
    {#if kind === 'username'}
      <span class="asset-at">@</span><strong>{order.title.replace(/^@/, '')}</strong><small>TELEGRAM USERNAME</small>
    {:else if kind === 'number'}
      <span class="asset-number">{order.title.replace(/[^0-9+]/g, '') || '888'}</span><small>ANONYMOUS NUMBER</small>
    {:else}
      <span class="asset-gift-symbol">✳</span><small>TELEGRAM NFT GIFT</small>
    {/if}
    <span class="asset-type-pill">{typeLabel}</span>
    <button class={'asset-favorite ' + (favorite ? 'is-favorite' : '')} onclick={onFavorite} aria-label={favorite ? 'Remove from favorites' : 'Add to favorites'}>{favorite ? '♥' : '♡'}</button>
  </div>
  <div class="asset-card-content">
    <div class="asset-title-row"><h3>{order.title}</h3><span class="asset-id">#{order.id.slice(-5)}</span></div>
    <p class="asset-owner">{order.owner}{order.note ? ' · ' + order.note : ''}</p>
    <div class="asset-card-footer">
      <div><small>{lang === 'ru' ? 'Цена' : 'Price'}</small><strong>{order.price} <span>{order.currency === 'XTR' ? '⭐' : 'TON'}</span></strong></div>
      <button class="asset-offer-button" onclick={onOffer}>Make offer ↗</button>
    </div>
  </div>
</article>
