<script lang="ts">
  import { onMount } from 'svelte';
  import type { TonConnectUI as TonConnectUIInstance } from '@tonconnect/ui';
  import GramToken from './GramToken.svelte';

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
    onDeliverySent?: (dealId: string, boc: string) => Promise<void> | void;
    paymentRequest?: { dealId: string; buyerWallet: string; transaction: { validUntil: number; network: string; messages: Array<{ address: string; amount: string; payload?: string }> } } | null;
    onPaymentSent?: (dealId: string) => Promise<void> | void;
    depositRequest?: { id: string; transaction: { validUntil: number; network: string; messages: Array<{ address: string; amount: string; payload?: string }> } } | null;
    onDepositSent?: (depositId: string) => Promise<void> | void;
    multisigApprovalRequest?: {
      orderSeqno: number;
      signerWallet: string;
      transaction: { validUntil: number; network: string; messages: Array<{ address: string; amount: string; payload?: string }> };
    } | null;
    onMultisigApprovalSent?: (orderSeqno: number) => Promise<void> | void;
    multisigProposalRequest?: {
      signerWallet: string;
      transaction: { validUntil: number; network: string; messages: Array<{ address: string; amount: string; payload?: string }> };
    } | null;
    onMultisigProposalSent?: () => Promise<void> | void;
  };

  let { api, authToken, lang, onVerified, deliveryRequest = null, onDeliverySent = () => {}, paymentRequest = null, onPaymentSent = () => {}, depositRequest = null, onDepositSent = () => {}, multisigApprovalRequest = null, onMultisigApprovalSent = () => {}, multisigProposalRequest = null, onMultisigProposalSent = () => {} }: Props = $props();
  let tonConnect = $state<TonConnectUIInstance | null>(null);
  let busy = $state(false);
  let address = $state('');
  let network = $state('');
  let status = $state<'idle' | 'connected' | 'verified' | 'error'>('idle');
  let message = $state('');
  let paymentSubmitted = $state(false);
  let depositSubmitted = $state(false);
  let multisigApprovalSubmitted = $state(false);
  let multisigProposalSubmitted = $state(false);

  $effect(() => {
    if (!paymentRequest) paymentSubmitted = false;
    if (!depositRequest) depositSubmitted = false;
    if (!multisigApprovalRequest) multisigApprovalSubmitted = false;
    if (!multisigProposalRequest) multisigProposalSubmitted = false;
  });

  const copy = $derived({
    connect: lang === 'ru' ? 'Подключить GRAM-кошелёк' : 'Connect GRAM wallet',
    loading: lang === 'ru' ? 'Подготавливаем кошелёк…' : 'Preparing wallet…',
    verify: lang === 'ru' ? 'Подтвердить кошелёк' : 'Verify wallet',
    verified: lang === 'ru' ? 'Кошелёк подтверждён' : 'Wallet verified',
    disconnect: lang === 'ru' ? 'Отключить' : 'Disconnect',
    disconnectFailed: lang === 'ru' ? 'Не удалось отключить кошелёк. Попробуй ещё раз.' : 'Could not disconnect the wallet. Please try again.',
    waiting: lang === 'ru' ? 'Ожидаем подтверждение в кошельке…' : 'Confirm the request in your wallet…',
    signIn: lang === 'ru' ? 'Сначала подтверди Telegram-профиль' : 'Verify your Telegram profile first',
    unsupported: lang === 'ru' ? 'Этот кошелёк не поддерживает криптографическое подтверждение владения. Попробуй Tonkeeper или другой совместимый кошелёк.' : 'This wallet does not support cryptographic ownership proof. Try Tonkeeper or another compatible wallet.',
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
          ? 'Не удалось загрузить подключение кошелька в этом WebView. Обнови Telegram и попробуй позже.'
          : 'Wallet connection could not load in this WebView. Update Telegram and try again.';
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

  async function sendTonPayment() {
    if (!tonConnect || !paymentRequest || busy || status !== 'verified') return;
    if (tonConnect.wallet?.account.address !== paymentRequest.buyerWallet) {
      message = lang === 'ru'
        ? 'Подключённый кошелёк не совпадает с кошельком покупателя. Переподключи подтверждённый адрес.'
        : 'The connected wallet does not match the verified buyer wallet. Reconnect the verified address.';
      status = 'error';
      return;
    }
    busy = true;
    message = '';
    try {
      await tonConnect.sendTransaction(paymentRequest.transaction);
      paymentSubmitted = true;
      await onPaymentSent(paymentRequest.dealId);
      message = lang === 'ru'
        ? 'Перевод отправлен. Не отправляй его повторно. Если блокчейн ещё не обновился, нажми «Проверить оплату».'
        : 'Transfer submitted. Do not send it again. If the chain has not updated yet, select “Verify payment”.';
    } catch (error) {
      message = error instanceof Error ? error.message : (lang === 'ru' ? 'Кошелёк отклонил или не отправил перевод.' : 'Wallet rejected or failed to submit the transfer.');
      status = 'error';
    } finally {
      busy = false;
    }
  }

  async function verifyTonPayment() {
    if (!paymentRequest || busy) return;
    busy = true;
    message = '';
    try {
      await onPaymentSent(paymentRequest.dealId);
    } finally {
      busy = false;
    }
  }

  async function sendGramDeposit() {
    if (!tonConnect || !depositRequest || busy || status !== 'verified') return;
    if (tonConnect.wallet?.account.chain !== depositRequest.transaction.network) {
      message = lang === 'ru'
        ? 'Сеть кошелька не совпадает с сетью заявки. Переключи сеть и подключи кошелёк заново.'
        : 'The wallet network does not match the deposit request. Switch networks and reconnect.';
      status = 'error';
      return;
    }
    busy = true;
    message = '';
    try {
      await tonConnect.sendTransaction(depositRequest.transaction);
      depositSubmitted = true;
      await onDepositSent(depositRequest.id);
      message = lang === 'ru'
        ? 'Перевод отправлен. Не отправляй его повторно. HASSLE проверит транзакцию в блокчейне.'
        : 'Transfer submitted. Do not send it again. HASSLE will verify the transaction on-chain.';
    } catch (error) {
      message = error instanceof Error ? error.message : (lang === 'ru' ? 'Кошелёк отклонил или не отправил перевод.' : 'Wallet rejected or failed to submit the transfer.');
      status = 'error';
    } finally {
      busy = false;
    }
  }

  async function sendMultisigProposal() {
    if (!tonConnect || !multisigProposalRequest || busy || status !== 'verified' || multisigProposalSubmitted) return;
    if (
      tonConnect.wallet?.account.address !== multisigProposalRequest.signerWallet
      || address !== multisigProposalRequest.signerWallet
    ) {
      message = lang === 'ru'
        ? 'Подключённый кошелёк не совпадает с проверенным кошельком подписанта.'
        : 'The connected wallet does not match the verified signer wallet.';
      status = 'error';
      return;
    }
    if (tonConnect.wallet?.account.chain !== multisigProposalRequest.transaction.network) {
      message = lang === 'ru'
        ? 'Для создания ордера переключи кошелёк на Mainnet.'
        : 'Switch the wallet to Mainnet before creating the order.';
      status = 'error';
      return;
    }
    busy = true;
    message = '';
    try {
      await tonConnect.sendTransaction(multisigProposalRequest.transaction);
      multisigProposalSubmitted = true;
      message = lang === 'ru'
        ? 'Заявка на создание ордера отправлена в сеть. Не отправляй её повторно.'
        : 'Order proposal submitted to the network. Do not submit it again.';
      try {
        await onMultisigProposalSent();
      } catch {
        // The wallet submission already happened; never suggest that it failed or resend it.
      }
    } catch (error) {
      message = error instanceof Error
        ? error.message
        : (lang === 'ru' ? 'Кошелёк отклонил создание ордера.' : 'The wallet rejected the order proposal.');
      status = 'error';
    } finally {
      busy = false;
    }
  }

  async function sendMultisigApproval() {
    if (!tonConnect || !multisigApprovalRequest || busy || status !== 'verified' || multisigApprovalSubmitted) return;
    if (
      tonConnect.wallet?.account.address !== multisigApprovalRequest.signerWallet
      || address !== multisigApprovalRequest.signerWallet
    ) {
      message = lang === 'ru'
        ? 'Подключённый кошелёк не совпадает с проверенным кошельком подписанта.'
        : 'The connected wallet does not match the verified signer wallet.';
      status = 'error';
      return;
    }
    if (tonConnect.wallet?.account.chain !== multisigApprovalRequest.transaction.network) {
      message = lang === 'ru'
        ? 'Для подписи ордера переключи кошелёк на Mainnet.'
        : 'Switch the wallet to Mainnet before approving this order.';
      status = 'error';
      return;
    }
    busy = true;
    message = '';
    try {
      await tonConnect.sendTransaction(multisigApprovalRequest.transaction);
      multisigApprovalSubmitted = true;
      message = lang === 'ru'
        ? 'Подпись отправлена в сеть. Дождись подтверждения и обнови список ордеров.'
        : 'Approval submitted to the network. Wait for confirmation and refresh the order list.';
      try {
        await onMultisigApprovalSent(multisigApprovalRequest.orderSeqno);
      } catch {
        // The wallet submission already happened; never suggest that it failed or resend it.
      }
    } catch (error) {
      message = error instanceof Error
        ? error.message
        : (lang === 'ru' ? 'Кошелёк отклонил подпись ордера.' : 'The wallet rejected the order approval.');
      status = 'error';
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
      await onDeliverySent(deliveryRequest.dealId, result.boc);
      message = lang === 'ru'
        ? 'Транзакция отправлена. Покупатель сможет подтвердить передачу после обновления блокчейна.'
        : 'Transaction submitted. The buyer can verify delivery once the blockchain updates.';
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
      <span class="ton-wallet-network">GRAM</span>
      <button class="ton-wallet-disconnect" type="button" onclick={revokeWallet} disabled={busy}>{busy ? '…' : copy.disconnect}</button>
    </div>
  {:else}
    <button class="ton-wallet-button" type="button" onclick={connectWallet} disabled={busy || !authToken || !tonConnect}>
      <GramToken size={20} />
      {busy ? (tonConnect ? copy.waiting : copy.loading) : (status === 'connected' ? copy.waiting : copy.connect)}
    </button>
  {/if}
  {#if paymentRequest && status === 'verified' && address === paymentRequest.buyerWallet}
    {#if paymentSubmitted}
      <button class="ton-wallet-button inventory-import-button" type="button" onclick={verifyTonPayment} disabled={busy}>
        {busy ? '…' : (lang === 'ru' ? 'Проверить оплату GRAM' : 'Verify GRAM payment')}
      </button>
    {:else if tonConnect?.wallet}
      <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendTonPayment} disabled={busy}>
        <GramToken size={17}/> {busy ? '…' : (lang === 'ru' ? 'Оплатить GRAM из кошелька' : 'Pay with GRAM')}
      </button>
    {:else}
      <button class="ton-wallet-button inventory-import-button" type="button" onclick={connectWallet} disabled={busy}>
        {lang === 'ru' ? 'Подключить кошелёк для оплаты' : 'Reconnect wallet to pay'}
      </button>
    {/if}
    <p class="ton-wallet-message">{lang === 'ru' ? 'Проверь адрес и сумму в кошельке. HASSLE NFT не получает доступ к ключам.' : 'Check the destination and amount in your wallet. HASSLE NFT never receives your keys.'}</p>
  {/if}
  {#if depositRequest && status === 'verified' && !depositSubmitted}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendGramDeposit} disabled={busy}>
      <GramToken size={17}/> {busy ? '…' : (lang === 'ru' ? 'Пополнить баланс GRAM' : 'Deposit GRAM')}
    </button>
    <p class="ton-wallet-message">{lang === 'ru' ? 'Проверь в кошельке точный адрес, сумму и комментарий HASSLE. Баланс будет зачислен только после проверки сети.' : 'Check the exact HASSLE destination, amount and comment in your wallet. Funds are credited only after chain verification.'}</p>
  {:else if depositRequest && depositSubmitted}
    <p class="ton-wallet-message">{lang === 'ru' ? 'Заявка отправлена. Ожидаем подтверждение GRAM в блокчейне…' : 'Deposit submitted. Waiting for on-chain GRAM confirmation…'}</p>
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={() => onDepositSent(depositRequest.id)} disabled={busy}>
      {busy ? '…' : (lang === 'ru' ? 'Проверить статус' : 'Check status')}
    </button>
  {/if}
  {#if multisigProposalRequest && !multisigProposalSubmitted && status === 'verified' && address === multisigProposalRequest.signerWallet && tonConnect?.wallet}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendMultisigProposal} disabled={busy}>
      {busy ? '…' : (lang === 'ru' ? 'Создать ордер выплаты' : 'Create payout order')}
    </button>
    <p class="ton-wallet-message">{lang === 'ru' ? 'Отправляется только запрос на создание ордера. Выплата произойдёт после двух независимых подписей.' : 'This submits only an order proposal. The payout occurs after two independent signer approvals.'}</p>
  {:else if multisigProposalRequest && !multisigProposalSubmitted}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={connectWallet} disabled={busy}>
      {lang === 'ru' ? 'Подключить кошелёк подписанта' : 'Connect signer wallet'}
    </button>
  {:else if multisigProposalRequest && multisigProposalSubmitted}
    <p class="ton-wallet-message">{lang === 'ru' ? 'Ордер отправлен на создание. Не отправляй повторно.' : 'Order proposal submitted. Do not submit again.'}</p>
  {/if}
  {#if multisigApprovalRequest && !multisigApprovalSubmitted && status === 'verified' && address === multisigApprovalRequest.signerWallet && tonConnect?.wallet}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendMultisigApproval} disabled={busy}>
      {busy ? '…' : (lang === 'ru' ? 'Подписать ордер #' + multisigApprovalRequest.orderSeqno : 'Approve order #' + multisigApprovalRequest.orderSeqno)}
    </button>
    <p class="ton-wallet-message">{lang === 'ru' ? 'Кошелёк покажет точную транзакцию. Подтверждай только после проверки получателя и суммы в админ-панели.' : 'Your wallet will show the exact transaction. Confirm only after reviewing the recipient and amount in the admin panel.'}</p>
  {:else if multisigApprovalRequest && !multisigApprovalSubmitted}
    <button class="ton-wallet-button inventory-import-button" type="button" onclick={connectWallet} disabled={busy}>
      {lang === 'ru' ? 'Подключить кошелёк подписанта' : 'Connect signer wallet'}
    </button>
  {:else if multisigApprovalRequest && multisigApprovalSubmitted}
    <p class="ton-wallet-message">{lang === 'ru' ? 'Подпись отправлена. Не отправляй её повторно.' : 'Approval submitted. Do not send it again.'}</p>
  {/if}
  {#if deliveryRequest && status === 'verified' && address === deliveryRequest.sellerWallet}
    {#if tonConnect?.wallet}
      <button class="ton-wallet-button inventory-import-button" type="button" onclick={sendNftDelivery} disabled={busy}>
        {busy ? '…' : (lang === 'ru' ? 'Передать NFT покупателю' : 'Transfer NFT to buyer')}
      </button>
    {:else}
      <button class="ton-wallet-button inventory-import-button" type="button" onclick={connectWallet} disabled={busy}>
        {lang === 'ru' ? 'Подключить кошелёк для подписи' : 'Reconnect wallet to sign transfer'}
      </button>
    {/if}
    <p class="ton-wallet-message">{lang === 'ru' ? 'Подтверди перевод NFT в своём GRAM-кошельке. HASSLE NFT не получает доступ к ключам.' : 'Approve the NFT transfer in your GRAM wallet. HASSLE NFT never receives your keys.'}</p>
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
