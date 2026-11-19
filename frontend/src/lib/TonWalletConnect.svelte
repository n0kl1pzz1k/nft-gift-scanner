<script lang="ts">
  import { onMount } from 'svelte';
  import type { TonConnectUI as TonConnectUIInstance } from '@tonconnect/ui';

  type Props = {
    api: string;
    authToken: string;
    lang: 'ru' | 'en';
    onVerified: (address: string) => void;
    deliveryRequest?: {
      dealId: string;
      sellerWallet: string;
      transaction: { validUntil: number; network: string; messages: Array<{ address: string; amount: string; payload?: string }> };
    } | null;
    onDeliverySent?: (dealId: string, boc: string) => void;
  };

  let { api, authToken, lang, onVerified, deliveryRequest = null, onDeliverySent = () => {} }: Props = $props();
  let tonConnect = $state<TonConnectUIInstance | null>(null);
  let busy = $state(false);
  let address = $state('');
  let network = $state('');
  let status = $state<'idle' | 'connected' | 'verified' | 'error'>('idle');
  let message = $state('');

  const copy = $derived({
    connect: lang === 'ru' ? 'Подключить TON-кошелёк' : 'Connect TON wallet',
    loading: lang === 'ru' ? 'Загружаем TON Connect…' : 'Loading TON Connect…',
    verify: lang === 'ru' ? 'Подтвердить кошелёк' : 'Verify wallet',
    verified: lang === 'ru' ? 'Кошелёк подтверждён' : 'Wallet verified',
    disconnect: lang === 'ru' ? 'Отключить' : 'Disconnect',
    disconnectFailed: lang === 'ru' ? 'Не удалось отключить кошелёк. Попробуй ещё раз.' : 'Could not disconnect the wallet. Please try again.',
    waiting: lang === 'ru' ? 'Ожидаем подтверждение в кошельке…' : 'Confirm the request in your wallet…',
    signIn: lang === 'ru' ? 'Сначала подтверди Telegram-профиль' : 'Verify your Telegram profile first',
    unsupported: lang === 'ru' ? 'Этот кошелёк не поддерживает TON Proof. Попробуй Tonkeeper или другой совместимый кошелёк.' : 'This wallet does not support TON Proof. Try Tonkeeper or another compatible wallet.',
    failed: lang === 'ru' ? 'Не удалось подтвердить кошелёк. Подключи его заново.' : 'Wallet verification failed. Please reconnect.',
  });

  function authHeaders() {
    return { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': authToken };
  }

  async function loadLinkedWallet() {
    if (!authToken) return;
    try {
      const response = await fetch(api + '/api/ton-proof/wallets/me', {
        headers: { 'X-Telegram-Init-Data': authToken },
      });
      if (!response.ok) return;
      const data = await response.json();
      const first = Array.isArray(data.items) ? data.items[0] : null;
      if (first?.address) {
        address = first.address;
        network = first.network || '';
        status = 'verified';
        onVerified(first.address);
      }
    } catch {
      // A temporary read failure must not manufacture a verified wallet state.
    }
  }

  onMount(() => {
    let active = true;
    let unsubscribe: (() => void) | undefined;
    busy = true;

    // Keep the wallet SDK out of the initial Mini App bundle. Some Telegram iOS
    // WebViews are stricter than desktop browsers; marketplace browsing must not
    // depend on wallet SDK startup or browser-specific APIs.
    void import('@tonconnect/ui')
      .then(({ TonConnectUI }) => {
        if (!active) return;
        tonConnect = new TonConnectUI({
          manifestUrl: 'https://hassle-nft-market.fj7kmzkhn9.workers.dev/tonconnect-manifest.json',
        });

        unsubscribe = tonConnect.onStatusChange(async (wallet) => {
          if (!active) return;
          if (!wallet) {
            if (status !== 'verified') status = 'idle';
            return;
          }
          network = wallet.account.chain;
          const item = wallet.connectItems?.tonProof;
          if (!item || !('proof' in item)) {
            status = 'error';
            message = copy.unsupported;
            return;
          }
          const stateInit = wallet.account.walletStateInit;
          if (!stateInit) {
            status = 'error';
            message = copy.failed;
            return;
          }
          busy = true;
          status = 'connected';
          message = copy.waiting;
          try {
            const response = await fetch(api + '/api/ton-proof/verify', {
              method: 'POST',
              headers: authHeaders(),
              body: JSON.stringify({
                address: wallet.account.address,
                network: wallet.account.chain,
                public_key: wallet.account.publicKey || undefined,
                state_init: stateInit,
                proof: item.proof,
              }),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.detail || copy.failed);
            address = result.address || wallet.account.address;
            status = 'verified';
            message = copy.verified;
            onVerified(address);
          } catch (error) {
            status = 'error';
            message = error instanceof Error ? error.message : copy.failed;
          } finally {
            busy = false;
          }
        });

        void loadLinkedWallet();
      })
      .catch(() => {
        if (!active) return;
        status = 'error';
        message = lang === 'ru'
          ? 'TON Connect не загрузился в этом WebView. Обнови Telegram и попробуй позже.'
          : 'TON Connect could not load in this WebView. Update Telegram and try again.';
      })
      .finally(() => {
        if (active) busy = false;
      });

    return () => {
      active = false;
      unsubscribe?.();
    };
  });

  async function revokeWallet() {
    if (!authToken || !address || !tonConnect || busy) return;
    busy = true;
    message = '';
    try {
      if (!network) throw new Error(copy.disconnectFailed);
      const response = await fetch(
        api + '/api/ton-proof/wallets/' + encodeURIComponent(network) + '/' + encodeURIComponent(address),
        { method: 'DELETE', headers: { 'X-Telegram-Init-Data': authToken } },
      );
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || copy.disconnectFailed);
      // The server-side revocation is authoritative. Clear all gated UI even if
      // the wallet SDK cannot close its modal or clear its local connection.
      address = '';
      network = '';
      status = 'idle';
      onVerified('');
      message = '';
      try {
        await tonConnect.disconnect();
      } catch {
        // A local SDK disconnect failure must not restore a revoked wallet.
      }
    } catch (error) {
      status = 'error';
      message = error instanceof Error ? error.message : copy.disconnectFailed;
    } finally {
      busy = false;
    }
  }

  async function sendNftDelivery() {
    if (!tonConnect || !deliveryRequest || busy || status !== 'verified') return;
    if (tonConnect.wallet?.account.address !== deliveryRequest.sellerWallet) {
      message = lang === 'ru'
        ? 'Подключённый кошелёк не совпадает с кошельком продавца. Переподключи правильный адрес.'
        : 'The connected wallet is not the seller wallet. Connect the correct address.';
      status = 'error';
      return;
    }
    busy = true;
    message = '';
    try {
      const result = await tonConnect.sendTransaction(deliveryRequest.transaction);
      onDeliverySent(deliveryRequest.dealId, result.boc);
      message = lang === 'ru'
        ? 'Транзакция отправлена. Покупатель сможет подтвердить передачу после обновления TON.'
        : 'Transaction submitted. The buyer can verify delivery once TON updates.';
    } catch (error) {
      message = error instanceof Error ? error.message : (lang === 'ru' ? 'Кошелёк отклонил или не отправил транзакцию.' : 'Wallet rejected or failed to submit the transaction.');
      status = 'error';
    } finally {
      busy = false;
    }
  }

  async function connectWallet() {
    if (!authToken) {
      message = copy.signIn;
      status = 'error';
      return;
    }
    if (!tonConnect || busy) return;
    busy = true;
    message = '';
    status = 'idle';
    try {
      if (tonConnect.wallet) await tonConnect.disconnect();
      const response = await fetch(api + '/api/ton-proof/payload', {
        method: 'POST',
        headers: { 'X-Telegram-Init-Data': authToken },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.payload) throw new Error(data.detail || copy.failed);
      tonConnect.setConnectRequestParameters({
        state: 'ready',
        value: { tonProof: data.payload },
      });
      await tonConnect.openModal();
    } catch (error) {
      status = 'error';
      message = error instanceof Error ? error.message : copy.failed;
    } finally {
      busy = false;
    }
  }
</script>

<div class="ton-wallet-connect">
  {#if status === 'verified' && address}
    <div class="ton-wallet-verified">
      <span class="ton-wallet-check">✓</span>
      <div class="ton-wallet-address">
        <strong>{copy.verified}</strong>
        <small>{address.slice(0, 8)}…{address.slice(-8)}</small>
      </div>
      <span class="ton-wallet-network">TON</span>
      <button class="ton-wallet-disconnect" type="button" onclick={revokeWallet} disabled={busy}>{busy ? '…' : copy.disconnect}</button>
    </div>
  {:else}
    <button class="ton-wallet-button" type="button" onclick={connectWallet} disabled={busy || !authToken || !tonConnect}>
      <span class="ton-wallet-symbol">◈</span>
      {busy ? (tonConnect ? copy.waiting : copy.loading) : (status === 'connected' ? copy.waiting : copy.connect)}
    </button>
  {/if}
  {#if deliveryRequest && status === 'verified' && address === deliveryRequest.sellerWallet}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendNftDelivery} disabled={busy}>
      {busy ? '…' : (lang === 'ru' ? 'Передать NFT покупателю' : 'Transfer NFT to buyer')}
    </button>
    <p class="ton-wallet-message">{lang === 'ru' ? 'Подтверди перевод NFT в своём TON-кошельке. HASSLE NFT не получает доступ к ключам.' : 'Approve the NFT transfer in your TON wallet. HASSLE NFT never receives your keys.'}</p>
  {/if}
  {#if message}
    <p class:ton-wallet-error={status === 'error'} class="ton-wallet-message">{message}</p>
  {/if}
</div>

<style>
  .ton-wallet-connect { display: grid; gap: 8px; width: 100%; }
  .ton-wallet-button {
    display: flex; align-items: center; justify-content: center; gap: 10px;
    width: 100%; min-height: 48px; padding: 0 16px; border: 1px solid rgba(80, 210, 190, .3);
    border-radius: 15px; color: #dffcf7; background: linear-gradient(120deg, rgba(29, 133, 120, .28), rgba(26, 77, 111, .35));
    font: inherit; font-weight: 700; cursor: pointer; transition: transform .2s ease, border-color .2s ease;
  }
  .ton-wallet-button:active { transform: scale(.985); }
  .ton-wallet-button:disabled { opacity: .55; cursor: wait; }
  .ton-wallet-symbol { font-size: 21px; color: #55e1ca; }
  .ton-wallet-verified { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .ton-wallet-check { display: grid; place-items: center; width: 28px; height: 28px; flex: 0 0 28px; border-radius: 50%; color: #071b17; background: #5de0be; font-weight: 900; }
  .ton-wallet-address { display: grid; gap: 2px; min-width: 0; }
  .ton-wallet-address strong { color: #dffcf7; font-size: 13px; }
  .ton-wallet-address small { color: rgba(222, 239, 244, .58); font-size: 11px; overflow-wrap: anywhere; }
  .ton-wallet-network { margin-left: auto; color: #65d9c5; font-size: 11px; font-weight: 800; letter-spacing: .08em; }
  .ton-wallet-disconnect { border: 1px solid rgba(255, 154, 154, .28); border-radius: 10px; padding: 7px 10px; color: #ffaaaa; background: rgba(120, 35, 45, .18); font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
  .ton-wallet-disconnect:disabled { opacity: .55; cursor: wait; }
  .ton-wallet-message { margin: 0; color: rgba(221, 238, 242, .68); font-size: 12px; line-height: 1.45; }
  .ton-wallet-message.ton-wallet-error { color: #ff9a9a; }
</style>
