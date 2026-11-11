<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { animate, stagger } from 'motion';

  type Gift = { id?: string|number; name?: string; number?: string|number; image?: string; price?: string|number; floor_price?: string|number|null; currency?: string; source?: string; status?: string; listed_at?: string|number; attributes?: {type?:string;name?:string;value?:string}[]; collection_id?: string|number };
  type Collection = { id?: string|number; name?: string; short_name?: string; image?: string; floor_price?: string|number; day_volume?: string|number; supply?: string|number };
  type Status = { sources?: Record<string,string>; marketplace_visibility?: Record<string,boolean>; note?:string; data_pipeline?:Record<string,unknown> };
  declare global { interface Window { API_BASE?: string; Telegram?: {WebApp?: {initData?:string;ready?:()=>void;expand?:()=>void;themeParams?:Record<string,string>}} } }

  const API = (import.meta.env.VITE_API_BASE_URL || window.API_BASE || 'https://nft-gift-scanner-production.up.railway.app').replace(/\/$/,'');
  let lang = $state<'ru'|'en'>('ru');
  let view = $state<'catalog'|'collections'|'favorites'|'profile'>('catalog');
  let market = $state<'portals'|'tonnel'|'mrkt'>('portals');
  let status = $state<Status>({});
  let collections = $state<Collection[]>([]);
  let gifts = $state<Gift[]>([]);
  let favorites = $state<string[]>([]);
  let search = $state('');
  let minPrice = $state('');
  let maxPrice = $state('');
  let busy = $state(true);
  let error = $state('');
  let selected = $state<Gift|null>(null);
  let telegramUser = $state('');
  let telegramAdmin = $state(false);
  let adminNote = $state('');
  let visibility = $state<Record<string,boolean>>({portals:true,tonnel:true,mrkt:false});
  let total = $state(0);
  let refreshed = $state('');

  const t = $derived(lang === 'ru' ? {
    catalog:'Каталог',collections:'Коллекции',favorites:'Избранное',profile:'Профиль',headline:'Подарки Telegram',sub:'Публичные каталоги и данные маркетплейсов — в одном рабочем пространстве.',system:'Состояние источников',listings:'Публичные объявления',search:'Поиск по названию или номеру',min:'Цена от',max:'Цена до',apply:'Найти',empty:'Нет публичных объявлений по этому запросу.',loading:'Загружаем данные…',viewOnly:'Только просмотр · без транзакций',details:'Подробнее',floor:'Флор',favorite:'В избранное',saved:'В избранном',back:'Назад',signIn:'Подтвердить Telegram',guest:'Гость',profileText:'Профиль Mini App. Авторизация доступна только внутри Telegram.',noFav:'Пока нет сохранённых карточек.',noCollections:'Источник пока не вернул коллекции.',error:'Не удалось получить данные',refresh:'Обновлено',notConnected:'Не подключён',admin:'Управление источниками',connected:'Подключён',mrktOff:'Отключён до подтверждения публичного API',public:'Публичные данные'
  } : {
    catalog:'Catalogue',collections:'Collections',favorites:'Favorites',profile:'Profile',headline:'Telegram gifts',sub:'Public catalogues and marketplace data in one workspace.',system:'Source status',listings:'Public listings',search:'Search by name or number',min:'Min price',max:'Max price',apply:'Search',empty:'No public listings match this query.',loading:'Loading data…',viewOnly:'Read-only · no transactions',details:'Details',floor:'Floor',favorite:'Save',saved:'Saved',back:'Back',signIn:'Verify Telegram',guest:'Guest',profileText:'Mini App profile. Sign-in is available only inside Telegram.',noFav:'No saved cards yet.',noCollections:'The source has not returned collections.',error:'Could not load data',refresh:'Updated',notConnected:'Not connected',admin:'Source controls',connected:'Connected',mrktOff:'Disabled until a public API is verified',public:'Public data'
  });

  const keyOf = (g:Gift) => String(g.source||market)+':'+String(g.id??g.number??g.name??'unknown');
  const shownGifts = $derived(view === 'favorites' ? gifts.filter(g=>favorites.includes(keyOf(g))) : gifts);
  const sourceLabel = (key:string) => status.sources?.[key] || (visibility[key]===false ? 'disabled' : 'unknown');
  const formatPrice = (v:unknown,currency='') => v===null||v===undefined||v==='' ? '—' : String(v)+(currency?' '+currency:'');

  async function request(path:string, init:RequestInit={}) {
    const response = await fetch(API+path,init);
    const data = await response.json().catch(()=>({detail:'Invalid server response'}));
    if(!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }
  async function loadCatalogue(term='') {
    busy=true; error='';
    try {
      const [s,c] = await Promise.all([request('/api/status'),request('/api/collections')]);
      status=s; visibility={...visibility,...(s.marketplace_visibility||{})}; collections=Array.isArray(c.items)?c.items:[];
      let endpoint = market==='tonnel' ? '/api/tonnel/gifts?limit=30&page=1&search='+encodeURIComponent(term) : '/api/gifts?limit=40&offset=0&search='+encodeURIComponent(term)+(minPrice?'&min_price='+encodeURIComponent(minPrice):'')+(maxPrice?'&max_price='+encodeURIComponent(maxPrice):'');
      if(market==='mrkt'){gifts=[];total=0;return;}
      const g=await request(endpoint); gifts=Array.isArray(g.items)?g.items:[]; total=Number(g.total_count??gifts.length); refreshed=new Date().toLocaleTimeString(lang==='ru'?'ru-RU':'en-US',{hour:'2-digit',minute:'2-digit'});
    } catch(e) { error=e instanceof Error?e.message:String(e); }
    finally { busy=false; await tick(); const cards=document.querySelectorAll('.gift-card,.collection-card'); if(cards.length) animate(cards,{opacity:[0,1],y:[12,0]},{delay:stagger(.035),duration:.28,ease:'ease-out'}); }
  }
  function toggleFavorite(g:Gift) { const k=keyOf(g); favorites=favorites.includes(k)?favorites.filter(x=>x!==k):[...favorites,k]; try{localStorage.setItem('gift-radar-favorites',JSON.stringify(favorites));}catch{} }
  function selectMarket(next:'portals'|'tonnel'|'mrkt'){market=next;void loadCatalogue(search);}
  async function telegramLogin(){
    const initData=window.Telegram?.WebApp?.initData||'';
    if(!initData){adminNote=lang==='ru'?'Открой Mini App через Telegram, чтобы подтвердить профиль.':'Open the Mini App through Telegram to verify your profile.';return;}
    try{const data=await request('/api/auth/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:initData})});telegramUser=data.user?.first_name||'Telegram user';telegramAdmin=!!data.is_admin;if(telegramAdmin)await refreshAdmin();}
    catch(e){adminNote=e instanceof Error?e.message:String(e);}
  }
  async function refreshAdmin(){
    const initData=window.Telegram?.WebApp?.initData||'';
    try{const data=await request('/api/admin/overview',{headers:{'X-Telegram-Init-Data':initData}});visibility={...visibility,...(data.marketplace_visibility||{})};}
    catch(e){adminNote=e instanceof Error?e.message:String(e);}
  }
  async function toggleMarket(key:string){
    const initData=window.Telegram?.WebApp?.initData||'';
    try{const data=await request('/api/admin/marketplaces/'+key,{method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Init-Data':initData},body:JSON.stringify({enabled:!visibility[key]})});visibility={...visibility,[key]:!!data.enabled};}
    catch(e){adminNote=e instanceof Error?e.message:String(e);}
  }
  onMount(()=>{
    window.Telegram?.WebApp?.ready?.(); window.Telegram?.WebApp?.expand?.();
    try{const saved=JSON.parse(localStorage.getItem('gift-radar-favorites')||'[]');if(Array.isArray(saved))favorites=saved;}catch{}
    void loadCatalogue();
  });
</script>

<svelte:head><title>GIFT RADAR — Market Monitor</title><meta name="theme-color" content="#080b14"/></svelte:head>
<div class="app">
  <aside class="sidebar">
    <a class="brand" href="#catalog"><span class="brand-mark">✳</span><span>GIFT RADAR<small>MARKET INTELLIGENCE</small></span></a>
    <div><div class="nav-label">WORKSPACE</div><div class="nav-list">
      {#each [{id:'catalog',label:t.catalog,icon:'▦'},{id:'collections',label:t.collections,icon:'▤'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
        <button class:active={view===item.id} class="nav-button" onclick={()=>view=item.id as typeof view}><span>{item.icon}</span>{item.label}</button>
      {/each}
    </div></div>
    <div class="sidebar-bottom"><div class="readonly">◈ {t.viewOnly}<br/><span>GIFT RADAR · v2.0</span></div></div>
  </aside>
  <main class="main">
    <div class="topbar"><span class="live">READ-ONLY MARKET MONITOR</span><button class="lang" onclick={()=>{lang=lang==='ru'?'en':'ru';}}>RU / EN</button></div>
    <section class="hero">
      <div class="kicker">TELEGRAM NFT · MARKET DATA</div>
      <h1>{t.headline} <span class="gradient">в одном месте.</span></h1>
      <p>{t.sub}</p>
      <div class="pills"><span class="pill">✦ {t.public}</span><span class="pill">◈ {t.viewOnly}</span><span class="pill">⌁ No transactions</span></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><div class="kicker">{t.system}</div><h2>{t.system}</h2></div><span class="pill">{busy?'SYNCING':error?'CHECK SOURCE':'LIVE API'}</span></div>
      <div class="status-grid">
        {#each ['portals','tonnel','mrkt'] as key}
          <div class="status-card"><strong><i class:ok={sourceLabel(key)==='online_read_only'||sourceLabel(key)==='configured'} class="dot"></i>{key.toUpperCase()}</strong><span>{key==='mrkt'&&!visibility.mrkt?t.mrktOff:sourceLabel(key)}</span></div>
        {/each}
      </div>
    </section>

    {#if view==='catalog'}
      <section>
        <div class="section-head"><div><div class="kicker">MARKETPLACE</div><h2>{t.listings} <span class="muted">· {total}</span></h2></div><span class="muted">{refreshed? t.refresh+': '+refreshed:''}</span></div>
        <div class="tabs">
          <button class:active={market==='portals'} class="market-tab" onclick={()=>selectMarket('portals')}>◈ Portals</button>
          <button class:active={market==='tonnel'} class="market-tab" onclick={()=>selectMarket('tonnel')}>◉ Tonnel</button>
          <button class:active={market==='mrkt'} class="market-tab" onclick={()=>selectMarket('mrkt')} disabled={!visibility.mrkt}>◇ MRKT</button>
        </div>
        <div class="toolbar">
          <input class="field" bind:value={search} placeholder={t.search} onkeydown={(e)=>e.key==='Enter'&&loadCatalogue(search)}/>
          <input class="field" type="number" min="0" bind:value={minPrice} placeholder={t.min}/>
          <input class="field" type="number" min="0" bind:value={maxPrice} placeholder={t.max}/>
          <button class="primary" onclick={()=>loadCatalogue(search)}>{t.apply}</button>
        </div>
        {#if busy}<div class="empty">{t.loading}</div>
        {:else if error}<div class="empty">{t.error}: {error}</div>
        {:else if market==='mrkt'}<div class="empty">{t.mrktOff}</div>
        {:else if gifts.length===0}<div class="empty">{t.empty}</div>
        {:else}<div class="grid">
          {#each gifts as gift (keyOf(gift))}
            <article class="gift-card" role="button" tabindex="0" onclick={()=>selected=gift} onkeydown={(e)=>e.key==='Enter'&&(selected=gift)}>
              {#if gift.image}<img class="gift-image" src={gift.image} alt={gift.name||'Telegram gift'} loading="lazy" onerror={(e)=>((e.currentTarget as HTMLImageElement).style.display='none')}/>{:else}<div class="gift-placeholder">🎁</div>{/if}
              <div class="gift-info"><div class="gift-title">{gift.name||'Telegram Gift'} {gift.number?'#'+gift.number:''}</div><div class="gift-meta">{gift.source||market.toUpperCase()} · {gift.status||'—'}</div>
                <div class="price-row"><span class="price">{formatPrice(gift.price,gift.currency||'GRAM')}</span><button class="favorite" aria-label={t.favorite} onclick={(e)=>{e.stopPropagation();toggleFavorite(gift);}}>{favorites.includes(keyOf(gift))?'♥':'♡'}</button></div>
              </div>
            </article>
          {/each}
        </div>{/if}
      </section>
    {:else if view==='collections'}
      <section><div class="section-head"><div class="kicker">COLLECTION INDEX</div><h2>{t.collections}</h2></div>
        {#if collections.length}<div class="collection-grid">{#each collections as c (String(c.id??c.short_name))}<article class="collection-card">{#if c.image}<img src={c.image} alt="" loading="lazy"/>{:else}<div class="gift-placeholder" style="width:48px;min-width:48px;aspect-ratio:1;font-size:20px">🎁</div>{/if}<div><strong>{c.name||c.short_name}</strong><span>{t.floor}: {formatPrice(c.floor_price,'GRAM')}</span></div></article>{/each}</div>{:else}<div class="empty">{t.noCollections}</div>{/if}
      </section>
    {:else if view==='favorites'}
      <section><div class="section-head"><div class="kicker">PERSONAL LIST</div><h2>{t.favorites}</h2></div>
        {#if shownGifts.length}<div class="grid">{#each shownGifts as gift (keyOf(gift))}<article class="gift-card" onclick={()=>selected=gift}>{#if gift.image}<img class="gift-image" src={gift.image} alt="" loading="lazy"/>{:else}<div class="gift-placeholder">🎁</div>{/if}<div class="gift-info"><div class="gift-title">{gift.name||'Telegram Gift'}</div><div class="price-row"><span class="price">{formatPrice(gift.price,gift.currency)}</span><button class="favorite" onclick={(e)=>{e.stopPropagation();toggleFavorite(gift);}}>♥</button></div></div></article>{/each}</div>{:else}<div class="empty">{t.noFav}</div>{/if}
      </section>
    {:else}
      <section><div class="section-head"><div class="kicker">ACCOUNT & PREFERENCES</div><h2>{t.profile}</h2></div>
        <div class="profile">
          <article class="profile-card"><h3>{telegramUser||t.guest}</h3><p class="muted">{t.profileText}</p><button class="primary" onclick={telegramLogin}>{t.signIn}</button><p class="muted">{adminNote}</p></article>
          <article class="profile-card"><h3>{t.admin}</h3><p class="muted">Server-side authorization required. Read-only catalogue visibility controls.</p>
            {#if telegramAdmin}
              {#each ['portals','tonnel','mrkt'] as key}<div class="price-row"><span>{key.toUpperCase()}</span><button class="secondary-button" onclick={()=>toggleMarket(key)} disabled={key==='mrkt'&&!visibility[key]}>{visibility[key]?'Enabled':'Disabled'}</button></div>{/each}
            {:else}<p class="muted">Admin tools appear only after server verification.</p>{/if}
          </article>
        </div>
      </section>
    {/if}
    <footer class="muted" style="margin-top:40px">GIFT RADAR · Public data only · No purchases, transfers, wallet connection or trading actions.</footer>
  </main>
</div>
<nav class="mobile-nav">
  {#each [{id:'catalog',label:t.catalog,icon:'▦'},{id:'collections',label:t.collections,icon:'▤'},{id:'favorites',label:t.favorites,icon:'♡'},{id:'profile',label:t.profile,icon:'◉'}] as item}
    <button class:active={view===item.id} class="nav-button" onclick={()=>view=item.id as typeof view}><span>{item.icon}</span>{item.label}</button>
  {/each}
</nav>
{#if selected}
  <div class="modal-backdrop" role="presentation" onclick={()=>selected=null} onkeydown={(e)=>e.key==='Escape'&&(selected=null)}>
    <article class="modal" role="dialog" aria-modal="true" aria-label={selected.name||'Gift details'} onclick={(e)=>e.stopPropagation()}>
      <button class="modal-close" onclick={()=>selected=null}>×</button>
      {#if selected.image}<img src={selected.image} alt={selected.name||'Gift'}/>{:else}<div class="gift-placeholder">🎁</div>{/if}
      <div class="modal-body"><div class="kicker">{selected.source||market}</div><h2>{selected.name||'Telegram Gift'} #{selected.number??'—'}</h2>
        <div class="price-row"><span class="muted">Listing price</span><strong>{formatPrice(selected.price,selected.currency||'GRAM')}</strong></div>
        <div class="price-row"><span class="muted">{t.floor}</span><strong>{formatPrice(selected.floor_price,selected.currency||'GRAM')}</strong></div>
        <div class="price-row"><span class="muted">Status</span><strong>{selected.status||'—'}</strong></div>
        <h3>Attributes</h3>{#each selected.attributes||[] as a}<div class="price-row"><span class="muted">{a.type||a.name}</span><strong>{a.value}</strong></div>{/each}
        <button class="primary" style="margin-top:18px" onclick={()=>toggleFavorite(selected!)}>{favorites.includes(keyOf(selected))?'♥ '+t.saved:'♡ '+t.favorite}</button>
      </div>
    </article>
  </div>
{/if}
