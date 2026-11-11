<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { animate as motionAnimateRaw } from 'motion';
  import GiftMedia from './GiftMedia.svelte';
  const motionAnimate = motionAnimateRaw as unknown as (target: Element, keyframes: Record<string, Array<number|string>>, options: {delay:number;duration:number;easing:string}) => unknown;

  type Gift = { id?: string|number; name?: string; number?: string|number; image?: string; animation_url?: string; has_animation?: boolean; price?: string|number; floor_price?: string|number|null; currency?: string; source?: string; status?: string; listed_at?: string|number; attributes?: {type?:string;name?:string;value?:string}[]; collection_id?: string|number };
  type Collection = { id?: string|number; name?: string; short_name?: string; image?: string; floor_price?: string|number; day_volume?: string|number; supply?: string|number };
  type Status = { sources?: Record<string,string>; marketplace_visibility?: Record<string,boolean>; note?:string; data_pipeline?:Record<string,unknown> };
  type GiftChange = { id?:string|number; type?:string; gift?:string; giftName?:string; gift_name?:string; giftId?:string|number; at?:string|number; timestamp?:string|number; created_at?:string; model?:string; symbol?:string; backdrop?:string; name?:string };

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
  let giftChanges = $state<GiftChange[]>([]);
  let dailyArchiveAvailable = $state(false);
  let dailyArchiveCount = $state(0);
  let dailyArchiveDate = $state('');
  let referenceLoading = $state(true);
  let referenceError = $state('');

  const t = $derived(lang === 'ru' ? {
    catalog:'Каталог',collections:'Коллекции',favorites:'Избранное',profile:'Профиль',headline:'Подарки Telegram',sub:'Публичные каталоги и данные маркетплейсов — в одном рабочем пространстве.',system:'Состояние источников',listings:'Публичные объявления',search:'Поиск по названию или номеру',min:'Цена от',max:'Цена до',apply:'Найти',empty:'Нет публичных объявлений по этому запросу.',loading:'Загружаем данные…',viewOnly:'Только просмотр · без транзакций',details:'Подробнее',floor:'Флор',favorite:'В избранное',saved:'В избранном',back:'Назад',signIn:'Подтвердить Telegram',guest:'Гость',profileText:'Профиль Mini App. Авторизация доступна только внутри Telegram.',noFav:'Пока нет сохранённых карточек.',noCollections:'Источник пока не вернул коллекции.',error:'Не удалось получить данные',refresh:'Обновлено',notConnected:'Не подключён',admin:'Управление источниками',connected:'Подключён',mrktOff:'Отключён до подтверждения публичного API',public:'Публичные данные',
    changesTitle:'Обновления каталога',changesLoading:'Загружаем публичные изменения…',changesEmpty:'Свежих изменений каталога пока нет.',changesError:'Лента изменений временно недоступна.',dailyArchive:'Ежедневный архив TG Gifts · CC BY 4.0',dailyArchiveInfo:'Исторические данные по коллекциям обновляются ежедневно. Это не live-объявления.'
  } : {
    catalog:'Catalogue',collections:'Collections',favorites:'Favorites',profile:'Profile',headline:'Telegram gifts',sub:'Public catalogues and marketplace data in one workspace.',system:'Source status',listings:'Public listings',search:'Search by name or number',min:'Min price',max:'Max price',apply:'Search',empty:'No public listings match this query.',loading:'Loading data…',viewOnly:'Read-only · no transactions',details:'Details',floor:'Floor',favorite:'Save',saved:'Saved',back:'Back',signIn:'Verify Telegram',guest:'Guest',profileText:'Mini App profile. Sign-in is available only inside Telegram.',noFav:'No saved cards yet.',noCollections:'The source has not returned collections.',error:'Could not load data',refresh:'Updated',notConnected:'Not connected',admin:'Source controls',connected:'Connected',mrktOff:'Disabled until a public API is verified',public:'Public data',
    changesTitle:'Catalogue updates',changesLoading:'Loading public metadata changes…',changesEmpty:'No recent catalogue changes were returned.',changesError:'The change feed is temporarily unavailable.',dailyArchive:'TG Gifts daily archive · CC BY 4.0',dailyArchiveInfo:'Historical collection data is updated daily. These are not live listings.'
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
  function changeTime(value:GiftChange['at']|GiftChange['timestamp']|GiftChange['created_at']) {
    if(value===undefined||value===null||value==='')return '—';
    const numeric=typeof value==='number'?value:Number(value);
    const date=Number.isFinite(numeric)&&String(value).trim()!==''?new Date(numeric>(1e12)?numeric:numeric*1000):new Date(String(value));
    return Number.isNaN(date.getTime())?String(value):date.toLocaleString(lang==='ru'?'ru-RU':'en-GB',{dateStyle:'short',timeStyle:'short'});
  }
  async function loadReferenceFeeds() {
    referenceLoading=true; referenceError='';
    const [changesResult,dailyResult]=await Promise.allSettled([
      request('/api/reference/gift-changes?limit=25'),
      request('/api/reference/daily-floors')
    ]);
    if(changesResult.status==='fulfilled'){
      const raw=changesResult.value?.data;
      const rows=Array.isArray(raw)?raw:(Array.isArray(raw?.changes)?raw.changes:(Array.isArray(raw?.items)?raw.items:(Array.isArray(raw?.events)?raw.events:[])));
      giftChanges=rows.slice(0,8);
    }else{
      giftChanges=[];
      referenceError=changesResult.reason instanceof Error?changesResult.reason.message:String(changesResult.reason);
    }
    dailyArchiveAvailable=dailyResult.status==='fulfilled';
    if(dailyResult.status==='fulfilled'){
      dailyArchiveCount=Number(dailyResult.value?.data?.collection_count)||0;
      dailyArchiveDate=String(dailyResult.value?.data?.latest_snapshot_date||'');
    }else{
      dailyArchiveCount=0; dailyArchiveDate='';
    }
    referenceLoading=false;
  }
  async function loadCatalogue(term='') {
    busy=true; error='';
    try {
      const [s,c] = await Promise.all([request('/api/status'),request('/api/collections')]);
      status=s; visibility={...visibility,...(s.marketplace_visibility||{})}; collections=Array.isArray(c.items)?c.items:[];
      let endpoint = market==='tonnel' ? '/api/tonnel/gifts?limit=30&page=1&search='+encodeURIComponent(term) : '/api/gifts?limit=40&offset=0&search='+encodeURIComponent(term)+(minPrice?'&min_price='+encodeURIComponent(minPrice):'')+(maxPrice?'&max_price='+encodeURIComponent(maxPrice):'');
      if(market==='mrkt'){gifts=[];total=0;return;}
      const g=await request(endpoint); gifts=Array.isArray(g.items)?g.items:[]; total=Math.max(Number(g.total_count)||0,gifts.length); refreshed=new Date().toLocaleTimeString(lang==='ru'?'ru-RU':'en-US',{hour:'2-digit',minute:'2-digit'});
    } catch(e) { error=e instanceof Error?e.message:String(e); }
    finally { busy=false; await tick(); const cards=document.querySelectorAll('.gift-card,.collection-card'); cards.forEach((card,index)=>{motionAnimate(card,{opacity:[0,1],y:[12,0]},{delay:index*.035,duration:.28,easing:'ease-out'});}); }
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
    void loadReferenceFeeds();
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
    <section class="market-heading">
      <div>
        <div class="kicker">GIFT RADAR <span class="heading-separator">/</span> TELEGRAM GIFTS</div>
        <h1>{view==='catalog'?'Маркетплейс':view==='collections'?t.collections:view==='favorites'?t.favorites:t.profile}</h1>
        <p>{view==='catalog'?'Исследуйте коллекции и публичные предложения в одном месте.':t.viewOnly}</p>
      </div>
      <span class="market-live"><i></i> LIVE</span>
    </section>

    {#if view==='catalog'}
      <section>
        <div class="section-head"><div><h2>{t.listings} <span class="muted">· {total}</span></h2></div><span class="muted">{refreshed? t.refresh+': '+refreshed:''}</span></div>
        <div class="tabs">
          <button class:active={market==='portals'} class="market-tab" onclick={()=>selectMarket('portals')}>◈ Portals</button>
          <button class:active={market==='tonnel'} class="market-tab" onclick={()=>selectMarket('tonnel')} disabled={!visibility.tonnel||sourceLabel('tonnel')==='public_access_unavailable'}>◉ Tonnel · {sourceLabel('tonnel')==='public_access_unavailable'?'offline':'public'}</button>
          <button class:active={market==='mrkt'} class="market-tab" onclick={()=>selectMarket('mrkt')} disabled={!visibility.mrkt}>◇ MRKT</button>
        </div>
        <div class="toolbar">
          <div class="search-wrap"><span>⌕</span><input class="field" bind:value={search} placeholder={t.search} onkeydown={(e)=>e.key==='Enter'&&loadCatalogue(search)}/></div>
          <input class="field price-filter" type="number" min="0" bind:value={minPrice} placeholder={t.min}/>
          <input class="field price-filter" type="number" min="0" bind:value={maxPrice} placeholder={t.max}/>
          <button class="primary" onclick={()=>loadCatalogue(search)}>{t.apply}</button>
        </div>
        {#if busy}<div class="empty">{t.loading}</div>
        {:else if error}<div class="empty">{t.error}: {error}</div>
        {:else if market==='mrkt'}<div class="empty">{t.mrktOff}</div>
        {:else if gifts.length===0}<div class="empty">{t.empty}</div>
        {:else}<div class="grid">
          {#each gifts as gift (keyOf(gift))}
            <div class="gift-card">
              <button type="button" class="gift-open" onclick={()=>selected=gift}>
                <GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-image"/>
                <div class="gift-info"><div class="gift-title">{gift.name||'Telegram Gift'} {gift.number?'#'+gift.number:''}</div><div class="gift-meta">{gift.source||market.toUpperCase()} · {gift.status||'—'}</div><div class="price-row"><span class="price">{formatPrice(gift.price,gift.currency||'GRAM')}</span></div></div>
              </button>
              <button class="favorite card-favorite" aria-label={t.favorite} onclick={()=>toggleFavorite(gift)}>{favorites.includes(keyOf(gift))?'♥':'♡'}</button>
            </div>
          {/each}
        </div>{/if}
      </section>
        <section class="panel reference-panel">
          <div class="panel-head"><div><div class="kicker">PUBLIC REFERENCE DATA</div><h2>{t.changesTitle}</h2></div><span class="muted">{dailyArchiveAvailable?'TG Gifts · '+dailyArchiveCount+' · '+dailyArchiveDate+' · CC BY 4.0':t.notConnected}</span></div>
          {#if referenceLoading}
            <p class="muted">{t.changesLoading}</p>
          {:else if referenceError}
            <p class="muted">{t.changesError}</p>
          {:else if giftChanges.length}
            <div class="change-feed">
              {#each giftChanges as change,index (String(change.id??change.giftId??index))}
                <article class="change-item">
                  <div><strong>{[change.type,change.gift||change.giftName||change.gift_name].filter(Boolean).join(' · ')||'Gift metadata update'}</strong>
                    {#if [change.model,change.symbol,change.backdrop,change.name].some(Boolean)}<span>{[change.model,change.symbol,change.backdrop,change.name].filter(Boolean).join(' · ')}</span>{/if}
                  </div>
                  <time>{changeTime(change.at??change.timestamp??change.created_at)}</time>
                </article>
              {/each}
            </div>
          {:else}
            <p class="muted">{t.changesEmpty}</p>
          {/if}
          <p class="muted archive-note">{t.dailyArchiveInfo} <a href="https://tggifts.com/data" target="_blank" rel="noreferrer">Data: TG Gifts (tggifts.com)</a> · CC BY 4.0</p>
        </section>
    {:else if view==='collections'}
      <section><div class="section-head"><div class="kicker">COLLECTION INDEX</div><h2>{t.collections}</h2></div>
        {#if collections.length}<div class="collection-grid">{#each collections as c (String(c.id??c.short_name))}<article class="collection-card">{#if c.image}<img src={c.image} alt="" loading="lazy"/>{:else}<div class="gift-placeholder" style="width:48px;min-width:48px;aspect-ratio:1;font-size:20px">🎁</div>{/if}<div><strong>{c.name||c.short_name}</strong><span>{t.floor}: {formatPrice(c.floor_price,'GRAM')}</span></div></article>{/each}</div>{:else}<div class="empty">{t.noCollections}</div>{/if}
      </section>
    {:else if view==='favorites'}
      <section><div class="section-head"><div class="kicker">PERSONAL LIST</div><h2>{t.favorites}</h2></div>
        {#if shownGifts.length}<div class="grid">{#each shownGifts as gift (keyOf(gift))}<div class="gift-card"><button type="button" class="gift-open" onclick={()=>selected=gift}><GiftMedia image={gift.image} animationUrl={gift.animation_url} hasAnimation={gift.has_animation} alt={gift.name||'Telegram gift'} className="gift-image"/><div class="gift-info"><div class="gift-title">{gift.name||'Telegram Gift'}</div><div class="price-row"><span class="price">{formatPrice(gift.price,gift.currency)}</span></div></div></button><button class="favorite card-favorite" onclick={()=>toggleFavorite(gift)}>♥</button></div>{/each}</div>{:else}<div class="empty">{t.noFav}</div>{/if}
      </section>
    {:else}
      <section class="profile-screen"><div class="section-head"><div><div class="kicker">ACCOUNT & PREFERENCES</div><h2>{t.profile}</h2></div><span class="market-live"><i></i> PERSONAL SPACE</span></div>
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
    <div class="modal" role="dialog" tabindex="-1" aria-modal="true" aria-label={selected.name||'Gift details'} onclick={(e)=>e.stopPropagation()} onkeydown={(e)=>e.stopPropagation()}>
      <button class="modal-close" onclick={()=>selected=null}>×</button>
      <GiftMedia image={selected.image} animationUrl={selected.animation_url} hasAnimation={selected.has_animation} alt={selected.name||'Gift'} className="modal-gift-media"/>
      <div class="modal-body"><div class="kicker">{selected.source||market}</div><h2>{selected.name||'Telegram Gift'} #{selected.number??'—'}</h2>
        <div class="price-row"><span class="muted">Listing price</span><strong>{formatPrice(selected.price,selected.currency||'GRAM')}</strong></div>
        <div class="price-row"><span class="muted">{t.floor}</span><strong>{formatPrice(selected.floor_price,selected.currency||'GRAM')}</strong></div>
        <div class="price-row"><span class="muted">Status</span><strong>{selected.status||'—'}</strong></div>
        <h3>Attributes</h3>{#each selected.attributes||[] as a}<div class="price-row"><span class="muted">{a.type||a.name}</span><strong>{a.value}</strong></div>{/each}
        <button class="primary" style="margin-top:18px" onclick={()=>toggleFavorite(selected!)}>{favorites.includes(keyOf(selected))?'♥ '+t.saved:'♡ '+t.favorite}</button>
      </div>
    </div>
  </div>
{/if}
