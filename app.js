const copy={
  en:{
    eyebrow:"READ-ONLY MARKET MONITOR",heading:"Telegram gifts <em>and market data</em> in one place.",sub:"Collections, public metrics and marketplace status — without the noise.",
    api:"Connection status",checking:"Checking connection…",online:"Backend online",offline:"Backend unavailable",
    foot:"Public information only. No purchases, sales, transfers or trading signals.",collections:"Portals collections",
    noData:"No collection data is connected yet. The app will not invent market values.",
    portals:"Portals",tonnel:"Tonnel",mrkt:"MRKT",needsKey:"Access not configured",
    unavailable:"Official read-only API not verified",configured:"Access configured · approved GET routes only",
    sourceOnline:"Data endpoint responded",floor:"Collection floor",volume:"Reported 24h volume",supply:"Reported supply",
    sourceNote:"Current coverage: Portals collection-level data only. Tonnel and MRKT remain unconnected until official read-only access is verified.",
    dataNote:"Collection-level figures are not individual gift listings and cannot be compared across marketplaces.",
    updated:"Last response",never:"Not available",error:"Could not load marketplace data.",
    public:"Public data",readonly:"View only",nomoney:"No transactions",system:"SYSTEM",overview:"DATA OVERVIEW",count:"COLLECTIONS",live:"LIVE MONITOR",
    catalog:"Marketplace listings",collectionKicker:"COLLECTION",viewCollection:"Open collection",backCollections:"← All collections",collectionLoading:"Loading gifts in this collection…",collectionEmpty:"No public listings were found in this collection.",loadMore:"Load more",loadingMore:"Loading…",lots:"LISTINGS",searchPlaceholder:"Search by gift name or number",searchButton:"Search",catalogWaiting:"Catalogue will appear when a verified data source is connected.",catalogNote:"Public listing information only. No purchase or gift-action buttons.",giftPrice:"Listing price",giftFloor:"Collection floor",giftNumber:"Gift #",giftEmpty:"No matching public listings were returned.",catalogUpdated:"Catalogue response",
    connected:"CONNECTED",pending:"NOT CONNECTED",details:"View details",status:"Listing status",listed:"Listed",attributes:"Attributes",noAttributes:"No public attributes supplied",detailNote:"Public listing details only. No transaction actions.",marketUnavailable:"This marketplace is not connected yet.",marketReasonTonnel:"Tonnel public listings are requested without account authentication. If the marketplace refuses public access, the app will show that honestly; no anti-bot bypass is used.",marketReasonMrkt:"MRKT currently requires account-linked Telegram authorization. This app does not collect Telegram sessions or API credentials.",marketTonnelError:"Tonnel public listings are temporarily unavailable or the source refused the request.",notConnected:"Not connected",modalClose:"Close"
  },
  ru:{
    eyebrow:"МОНИТОРИНГ РЫНКА · ТОЛЬКО ЧТЕНИЕ",heading:"Весь рынок Telegram-подарков <em>в одном месте.</em>",sub:"Коллекции, публичные показатели и состояние маркетплейсов — без лишнего шума.",
    api:"Статус подключения",checking:"Проверяем соединение…",online:"Backend работает",offline:"Backend недоступен",
    foot:"Только публичная информация. Без покупок, продаж, переводов и торговых сигналов.",collections:"Коллекции Portals",
    noData:"Данные коллекций пока не подключены. Приложение не будет придумывать рыночные значения.",
    portals:"Portals",tonnel:"Tonnel",mrkt:"MRKT",needsKey:"Доступ не настроен",
    unavailable:"Официальный read-only API не подтверждён",configured:"Доступ настроен · только разрешённые GET-запросы",
    sourceOnline:"Источник ответил",floor:"Флор коллекции",volume:"Объём за 24 ч (по данным источника)",supply:"Количество выпущенных",
    sourceNote:"Данные коллекций Portals и публичные объявления Tonnel запрашиваются только для просмотра. MRKT пока не подключён: безопасный публичный read-only доступ не подтверждён.",
    dataNote:"Показатели коллекций — не объявления отдельных подарков и не основание для сравнения цен между маркетами.",
    updated:"Последний ответ",never:"Нет данных",error:"Не удалось загрузить данные маркетплейсов.",
    public:"Публичные данные",readonly:"Только просмотр",nomoney:"Без транзакций",system:"СИСТЕМА",overview:"ОБЗОР ДАННЫХ",count:"КОЛЛЕКЦИЙ",live:"LIVE МОНИТОРИНГ",
    catalog:"Объявления маркетплейсов",collectionKicker:"КОЛЛЕКЦИЯ",viewCollection:"Открыть коллекцию",backCollections:"← Все коллекции",collectionLoading:"Загружаем подарки этой коллекции…",collectionEmpty:"В этой коллекции не найдено публичных объявлений.",loadMore:"Показать ещё",loadingMore:"Загружаем…",lots:"ЛОТОВ",searchPlaceholder:"Поиск по названию или номеру",searchButton:"Найти",catalogWaiting:"Каталог появится после подключения подтверждённого источника данных.",catalogNote:"Только публичная информация об объявлениях. Без торговых действий.",giftPrice:"Цена объявления",giftFloor:"Флор коллекции",giftNumber:"Подарок №",giftEmpty:"По этому запросу публичных объявлений не найдено.",catalogUpdated:"Ответ каталога",
    connected:"ПОДКЛЮЧЁН",pending:"НЕ ПОДКЛЮЧЁН",details:"Подробнее",status:"Статус объявления",listed:"Выставлен",attributes:"Атрибуты",noAttributes:"Источник не передал публичные атрибуты",detailNote:"Только публичная информация об объявлении. Без торговых операций.",marketUnavailable:"Этот маркетплейс пока не подключён.",marketReasonTonnel:"Публичные объявления Tonnel запрашиваются без авторизации аккаунта. Если площадка не разрешит публичный доступ, приложение честно покажет это. Обход антибот-защиты не используется.",marketReasonMrkt:"Сейчас MRKT требует авторизацию, связанную с Telegram-аккаунтом. Приложение не собирает Telegram-сессии и API-ключи.",marketTonnelError:"Публичные объявления Tonnel временно недоступны или источник отклонил запрос.",notConnected:"Не подключён",modalClose:"Закрыть"
  }
};
let lang="ru";
let activeMarket="portals";
let giftItems=[];
let portalsGiftItems=[];
let tonnelGiftItems=[];
let sourceStates={};
let collectionItems=[];
let selectedCollection=null;
let selectedCollectionItems=[];
let selectedCollectionTotal=0;
let collectionLoadingMore=false;
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function renderText(){
  const t=copy[lang];
  $("eyebrow").textContent=t.eyebrow;
  $("heading").innerHTML=t.heading;
  $("sub").textContent=t.sub;
  $("api-title").textContent=t.api;
  $("foot").textContent=t.foot;
  $("collections-title").textContent=t.collections;
  $("coverage-note").textContent=t.sourceNote;
  $("data-note").textContent=t.dataNote;
  $("tag-public").textContent=t.public;
  $("tag-readonly").textContent=t.readonly;
  $("tag-nomoney").textContent=t.nomoney;
  $("connection-kicker").textContent=t.system;
  $("collections-kicker").textContent=t.overview;
  $("count-label").textContent=t.count;
  $("live-label").textContent=t.live;
  $("catalog-kicker").textContent="MARKETPLACES";
  $("catalog-title").textContent=selectedCollection?(selectedCollection.name||t.catalog):t.catalog;
  $("catalog-kicker").textContent=selectedCollection?t.collectionKicker:"MARKETPLACES";
  $("gifts-count-label").textContent=t.lots;
  if($("gift-search")) $("gift-search").placeholder=t.searchPlaceholder;
  if($("gift-search-button")) $("gift-search-button").textContent=t.searchButton;
  $("catalog-note").textContent=t.catalogNote;
  $("modal-price-label").textContent=t.giftPrice;
  $("modal-floor-label").textContent=t.giftFloor;
  $("modal-status-label").textContent=t.status;
  $("modal-date-label").textContent=t.listed;
  $("modal-attributes-title").textContent=t.attributes;
  $("modal-note").textContent=t.detailNote;
  $("lang").textContent=lang==="en"?"RU":"EN";
  renderMarketTabs();
  if(activeMarket==="tonnel"&&sourceStates.tonnel==="online_read_only") renderGifts({items:tonnelGiftItems,total_count:tonnelGiftItems.length,connected:true});
  else if(activeMarket!=="portals") renderUnavailableMarket(activeMarket);
  else if(selectedCollection) renderGifts({items:selectedCollectionItems,total_count:selectedCollectionTotal,connected:true});
  else renderGifts({items:portalsGiftItems,total_count:portalsGiftItems.length,connected:sourceStates.portals!=="needs_api_key"});
}
function sourceLabel(name,state,kind,letter){
  const t=copy[lang];
  let label=t.unavailable;
  if(state==="online"||state==="online_read_only") label=t.sourceOnline;
  else if(state==="configured_read_only") label=t.configured;
  else if(state==="needs_api_key") label=t.needsKey;
  return '<div class="source"><span class="source-logo source-'+kind+'">'+letter+'</span><span class="source-copy"><b>'+name+'</b><small>'+label+'</small></span></div>';
}
function formatTime(value){
  if(!value) return copy[lang].never;
  const date=typeof value==="number"?new Date(value*1000):new Date(value);
  return Number.isNaN(date.getTime())?String(value):date.toLocaleString(lang==="ru"?"ru-RU":"en-GB");
}
function renderMarketTabs(){
  const t=copy[lang];
  document.querySelectorAll(".market-tab").forEach(button=>{
    const market=button.dataset.market;
    button.classList.toggle("active",market===activeMarket);
    const state=sourceStates[market]||"official_api_not_verified";
    const connected=state==="online_read_only"||(market==="portals"&&(state==="configured_read_only"||state==="online"));
    $("tab-state-"+market).textContent=connected?t.connected:t.pending;
    $("tab-state-"+market).classList.toggle("is-connected",connected);
  });
}
function renderUnavailableMarket(market){
  const t=copy[lang];
  const reason=market==="tonnel"?(sourceStates.tonnel==="rate_limited"||sourceStates.tonnel==="temporarily_unavailable"||sourceStates.tonnel==="public_access_unavailable"?t.marketTonnelError:t.marketReasonTonnel):t.marketReasonMrkt;
  $("gifts-count").textContent="—";
  $("gifts-updated").textContent=t.notConnected;
  $("market-view").innerHTML='<div class="market-empty"><div class="market-empty-icon">'+(market==="tonnel"?"T":"M")+'</div><h3>'+escapeHtml(t.marketUnavailable)+'</h3><p>'+escapeHtml(reason)+'</p></div>';
}
async function load(){
  const t=copy[lang];
  $("api-status").textContent=t.checking;
  try{
    const health=await fetch(window.API_BASE+"/health");
    if(!health.ok) throw new Error("Backend offline");
    $("api-status").textContent=t.online;
    const [statusRes,dataRes,giftsRes]=await Promise.all([fetch(window.API_BASE+"/api/status"),fetch(window.API_BASE+"/api/collections"),fetch(window.API_BASE+"/api/gifts?limit=24&offset=0")]);
    if(!statusRes.ok||!dataRes.ok) throw new Error("Marketplace API unavailable");
    const status=await statusRes.json();
    const data=await dataRes.json();
    const gifts=giftsRes.ok?await giftsRes.json():{items:[],total_count:0};
    sourceStates={...(status.sources||{}),...(data.sources||{})};
    const s={...sourceStates};
    $("sources").innerHTML=sourceLabel(t.portals,s.portals,"portals","P")+sourceLabel(t.tonnel,s.tonnel,"tonnel","T")+sourceLabel(t.mrkt,s.mrkt,"mrkt","M");
    const items=Array.isArray(data.items)?data.items:[];
    collectionItems=items;
    $("collections-count").textContent=items.length;
    $("updated").textContent=t.updated+": "+formatTime(data.updated_at);
    $("collections").innerHTML=items.length?items.map((item,index)=>{
      const image=item.image?'<img loading="lazy" src="'+escapeHtml(item.image)+'" alt="">':'<div class="placeholder">✦</div>';
      return '<article class="collection-card" role="button" tabindex="0" data-collection-index="'+index+'" aria-label="'+escapeHtml(item.name||"Collection")+' · '+escapeHtml(t.viewCollection)+'">'+image+'<div class="card-info"><b>'+escapeHtml(item.name)+'</b><span>'+t.floor+': <strong>'+escapeHtml(item.floor_price??"—")+'</strong></span><span>'+t.volume+': '+escapeHtml(item.day_volume??"—")+'</span><span>'+t.supply+': '+escapeHtml(item.supply??"—")+'</span><small>'+escapeHtml(item.source||"")+'</small><span class="collection-open-hint">'+t.viewCollection+' ↗</span></div></article>';
    }).join(""):'<p class="empty">'+t.noData+'</p>';
    portalsGiftItems=Array.isArray(gifts.items)?gifts.items:[];
    giftItems=portalsGiftItems;
    renderMarketTabs();
    if(activeMarket==="portals") renderGifts(gifts);
    else renderUnavailableMarket(activeMarket);
  }catch(e){
    $("api-status").textContent=t.offline;
    $("collections-count").textContent="—";
    $("updated").textContent=t.updated+": "+t.never;
    $("collections").innerHTML='<p class="empty">'+t.error+'</p>';
    $("market-view").innerHTML='<p class="empty">'+t.error+'</p>';
  }
}
function renderGifts(data){
  const t=copy[lang], items=Array.isArray(data.items)?data.items:[];
  giftItems=items;
  if(selectedCollection){selectedCollectionItems=items;selectedCollectionTotal=Number(data.total_count??items.length)||items.length;}
  $("gifts-count").textContent=data.total_count??items.length;
  $("gifts-updated").textContent=t.catalogUpdated+": "+formatTime(data.updated_at);
  const backButton=selectedCollection?'<div class="collection-detail-toolbar"><button class="back-collections" type="button" data-back-collections>'+escapeHtml(t.backCollections)+'</button><span>'+escapeHtml(selectedCollection.name||"")+'</span></div>':"";
  const emptyText=selectedCollection?t.collectionEmpty:(data.connected===false?t.catalogWaiting:t.giftEmpty);
  const moreButton=selectedCollection&&items.length<selectedCollectionTotal?'<button class="load-more-gifts" type="button" data-load-more-gifts>'+escapeHtml(t.loadMore)+'</button>':"";
  $("market-view").innerHTML=backButton+'<div class="catalog-toolbar"><input id="gift-search" type="search" placeholder="'+escapeHtml(t.searchPlaceholder)+'" value="'+escapeHtml($("gift-search")?.value||"")+'"><button id="gift-search-button" type="button">'+escapeHtml(t.searchButton)+'</button></div><p class="updated"><span class="update-dot"></span><span id="gifts-updated">'+escapeHtml(t.catalogUpdated)+': '+escapeHtml(formatTime(data.updated_at))+'</span></p><section class="gift-grid" id="gift-grid">'+(items.length?items.map((gift,index)=>{
    const image=gift.image?'<img loading="lazy" src="'+escapeHtml(gift.image)+'" alt="">':'<div class="placeholder">🎁</div>';
    const attrs=Array.isArray(gift.attributes)?gift.attributes.map(a=>[a.type||a.name,a.value||a.attribute_name].filter(Boolean).join(": ")).filter(Boolean).slice(0,3).join(" · "):"";
    return '<article class="gift-card" role="button" tabindex="0" data-gift-index="'+index+'" aria-label="'+escapeHtml((gift.name||"Telegram Gift")+" "+t.details)+'">'+image+'<div class="gift-info"><b>'+escapeHtml(gift.name||"Telegram Gift")+'</b><span>'+t.giftNumber+' '+escapeHtml(gift.number??"—")+'</span><strong>'+t.giftPrice+': '+escapeHtml(gift.price??"—")+' '+escapeHtml(gift.currency||"GRAM")+'</strong><small>'+t.giftFloor+': '+escapeHtml(gift.floor_price??"—")+' '+escapeHtml(gift.currency||"GRAM")+'</small><small>'+escapeHtml(attrs)+'</small><span class="gift-details-hint">'+t.details+' ↗</span></div></article>';
  }).join(""):'<p class="empty">'+emptyText+'</p>')+'</section>'+moreButton;
  $("gift-search-button").onclick=searchGifts;
  $("gift-search").addEventListener("keydown",e=>{if(e.key==="Enter")searchGifts()});
}
async function loadTonnelGifts(search=""){
  const t=copy[lang];
  $("gifts-count").textContent="…";
  $("gifts-updated").textContent=t.checking;
  $("market-view").innerHTML='<p class="empty">'+t.checking+'</p>';
  try{
    const response=await fetch(window.API_BASE+"/api/tonnel/gifts?limit=24&page=1&search="+encodeURIComponent(search));
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||"Tonnel unavailable");
    tonnelGiftItems=Array.isArray(data.items)?data.items:[];
    sourceStates.tonnel="online_read_only";
    renderMarketTabs();
    renderGifts(data);
  }catch(e){
    const message=String(e.message||"");
    sourceStates.tonnel=message.toLowerCase().includes("rate limit")?"rate_limited":message.toLowerCase().includes("public unauthenticated")?"public_access_unavailable":"temporarily_unavailable";
    renderMarketTabs();
    renderUnavailableMarket("tonnel");
  }
}
async function searchGifts(){
  const term=$("gift-search").value.trim();
  const t=copy[lang];
  $("gift-grid").innerHTML='<p class="empty">'+t.checking+'</p>';
  if(activeMarket==="tonnel"){
    await loadTonnelGifts(term);
    return;
  }
  if(activeMarket!=="portals") return;
  try{
    const params=new URLSearchParams({limit:"24",offset:"0",search:term});
    if(selectedCollection) params.set("collection_id",String(selectedCollection.id??selectedCollection.short_name??""));
    const response=await fetch(window.API_BASE+"/api/gifts?"+params.toString());
    if(!response.ok) throw new Error("Catalog unavailable");
    const data=await response.json();
    if(selectedCollection){selectedCollectionItems=Array.isArray(data.items)?data.items:[];selectedCollectionTotal=Number(data.total_count??selectedCollectionItems.length)||selectedCollectionItems.length;}
    else portalsGiftItems=Array.isArray(data.items)?data.items:[];
    renderGifts(data);
  }catch(e){$("gift-grid").innerHTML='<p class="empty">'+t.error+'</p>';}
}
function openCollection(index){
  const collection=collectionItems[index];
  if(!collection) return;
  const collectionId=collection.id??collection.short_name;
  if(collectionId===undefined||collectionId===null||String(collectionId)==="") return;
  selectedCollection=collection;
  selectedCollectionItems=[];
  selectedCollectionTotal=0;
  collectionLoadingMore=false;
  activeMarket="portals";
  $("catalog-title").textContent=collection.name||copy[lang].catalog;
  $("catalog-kicker").textContent=copy[lang].collectionKicker;
  renderMarketTabs();
  $("gifts-count").textContent="…";
  $("market-view").innerHTML='<div class="collection-detail-toolbar"><button class="back-collections" type="button" data-back-collections>'+escapeHtml(copy[lang].backCollections)+'</button><span>'+escapeHtml(collection.name||"")+'</span></div><p class="empty">'+escapeHtml(copy[lang].collectionLoading)+'</p>';
  $("marketplaces").scrollIntoView({behavior:"smooth",block:"start"});
  loadCollectionGifts(0,false,"");
}
async function loadCollectionGifts(offset=0,append=false,searchTerm=""){
  if(!selectedCollection) return;
  const t=copy[lang];
  const collectionId=selectedCollection.id??selectedCollection.short_name;
  collectionLoadingMore=true;
  const moreButton=document.querySelector("[data-load-more-gifts]");
  if(moreButton){moreButton.disabled=true;moreButton.textContent=t.loadingMore;}
  try{
    const params=new URLSearchParams({limit:"24",offset:String(offset),collection_id:String(collectionId)});
    if(searchTerm) params.set("search",searchTerm);
    const response=await fetch(window.API_BASE+"/api/gifts?"+params.toString());
    const data=await response.json();
    if(!response.ok) throw new Error(data.detail||"Could not load collection listings");
    const page=Array.isArray(data.items)?data.items:[];
    selectedCollectionItems=append?selectedCollectionItems.concat(page):page;
    selectedCollectionTotal=Number(data.total_count??selectedCollectionItems.length)||selectedCollectionItems.length;
    renderGifts({...data,items:selectedCollectionItems,total_count:selectedCollectionTotal});
  }catch(error){
    $("market-view").innerHTML='<div class="collection-detail-toolbar"><button class="back-collections" type="button" data-back-collections>'+escapeHtml(t.backCollections)+'</button><span>'+escapeHtml(selectedCollection.name||"")+'</span></div><p class="empty">'+escapeHtml(t.error)+'</p>';
  }finally{collectionLoadingMore=false;}
}
function loadMoreCollectionGifts(){
  if(!selectedCollection||collectionLoadingMore||selectedCollectionItems.length>=selectedCollectionTotal) return;
  const term=$("gift-search")?.value.trim()||"";
  loadCollectionGifts(selectedCollectionItems.length,true,term);
}
function closeCollection(){
  selectedCollection=null;
  selectedCollectionItems=[];
  selectedCollectionTotal=0;
  $("catalog-title").textContent=copy[lang].catalog;
  $("catalog-kicker").textContent="MARKETPLACES";
  renderGifts({items:portalsGiftItems,total_count:portalsGiftItems.length,connected:sourceStates.portals!=="needs_api_key"});
  document.querySelector(".collections-section")?.scrollIntoView({behavior:"smooth",block:"start"});
}
function openGiftDetails(index){
  const gift=giftItems[index];
  if(!gift) return;
  const t=copy[lang];
  $("modal-source").textContent=gift.source||"PORTALS";
  $("modal-title").textContent=gift.name||"Telegram Gift";
  $("modal-number").textContent=t.giftNumber+" "+(gift.number??"—");
  $("modal-price").textContent=(gift.price??"—")+" "+(gift.currency||"GRAM");
  $("modal-floor").textContent=(gift.floor_price??"—")+" "+(gift.currency||"GRAM");
  $("modal-status").textContent=gift.status||"—";
  $("modal-date").textContent=formatTime(gift.listed_at);
  $("modal-image-wrap").innerHTML=gift.image?'<img src="'+escapeHtml(gift.image)+'" alt="'+escapeHtml(gift.name||"Gift")+'">':'<div class="modal-placeholder">🎁</div>';
  const attrs=Array.isArray(gift.attributes)?gift.attributes:[];
  $("modal-attributes").innerHTML=attrs.length?attrs.map(attr=>{
    const name=attr.type||attr.name||attr.attribute_type||"Attribute";
    const value=attr.value||attr.attribute_name||attr.name_value||"—";
    return '<div class="attribute-chip"><small>'+escapeHtml(name)+'</small><b>'+escapeHtml(value)+'</b></div>';
  }).join(""):'<span class="note">'+escapeHtml(t.noAttributes)+'</span>';
  $("gift-modal").classList.add("open");
  $("gift-modal").setAttribute("aria-hidden","false");
  document.body.classList.add("modal-open");
  $("modal-close").focus();
}
function closeGiftDetails(){
  $("gift-modal").classList.remove("open");
  $("gift-modal").setAttribute("aria-hidden","true");
  document.body.classList.remove("modal-open");
}
$("collections").addEventListener("click",event=>{
  const card=event.target.closest("[data-collection-index]");
  if(card) openCollection(Number(card.dataset.collectionIndex));
});
$("collections").addEventListener("keydown",event=>{
  const card=event.target.closest("[data-collection-index]");
  if(card&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openCollection(Number(card.dataset.collectionIndex));}
});
$("market-view").addEventListener("click",event=>{
  if(event.target.closest("[data-back-collections]")){closeCollection();return;}
  if(event.target.closest("[data-load-more-gifts]")){loadMoreCollectionGifts();return;}
  const card=event.target.closest("[data-gift-index]");
  if(card) openGiftDetails(Number(card.dataset.giftIndex));
});
$("market-view").addEventListener("keydown",event=>{
  const card=event.target.closest("[data-gift-index]");
  if(card&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openGiftDetails(Number(card.dataset.giftIndex));}
});
document.querySelectorAll(".market-tab").forEach(button=>button.addEventListener("click",()=>{
  activeMarket=button.dataset.market;
  if(activeMarket!=="portals") selectedCollection=null;
  $("catalog-title").textContent=selectedCollection?(selectedCollection.name||copy[lang].catalog):copy[lang].catalog;
  $("catalog-kicker").textContent=selectedCollection?copy[lang].collectionKicker:"MARKETPLACES";
  renderMarketTabs();
  if(activeMarket==="portals") renderGifts(selectedCollection?{items:selectedCollectionItems,total_count:selectedCollectionTotal,connected:true}:{items:portalsGiftItems,total_count:portalsGiftItems.length,connected:sourceStates.portals!=="needs_api_key"});
  else if(activeMarket==="tonnel"){
    if(sourceStates.tonnel==="online_read_only") renderGifts({items:tonnelGiftItems,total_count:tonnelGiftItems.length,connected:true});
    else loadTonnelGifts();
  }else renderUnavailableMarket(activeMarket);
}));
$("modal-close").addEventListener("click",closeGiftDetails);
$("gift-modal").addEventListener("click",event=>{if(event.target.dataset.closeModal==="true")closeGiftDetails()});
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&$("gift-modal").classList.contains("open"))closeGiftDetails()});
$("lang").onclick=()=>{lang=lang==="ru"?"en":"ru";renderText();};

function setupMotionAndViewport(){
  const reduced=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const reveal=(root=document)=>{
    const nodes=root.querySelectorAll(".topbar,.hero,.status-panel,.collections-section,.catalog-section,footer,.collection-card,.gift-card,.source");
    nodes.forEach((node,index)=>{
      if(node.classList.contains("reveal-target")) return;
      node.classList.add("reveal-target");
      if(node.matches(".collection-card,.gift-card,.source")) node.style.setProperty("--reveal-delay",Math.min(index%6,5)*55+"ms");
      if(!("IntersectionObserver" in window)||reduced){node.classList.add("is-revealed");return;}
      observer.observe(node);
    });
  };
  const observer=("IntersectionObserver" in window)?new IntersectionObserver(entries=>{
    entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add("is-revealed");observer.unobserve(entry.target);}});
  },{threshold:.08,rootMargin:"0px 0px -35px 0px"}):null;
  reveal();
  if("MutationObserver" in window){
    const mutations=new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1)reveal(node);})));
    mutations.observe(document.body,{childList:true,subtree:true});
  }
  const progress=$("scroll-progress");
  let ticking=false;
  const updateProgress=()=>{if(!progress)return;const max=document.documentElement.scrollHeight-window.innerHeight;progress.style.width=(max>0?Math.min(100,window.scrollY/max*100):0)+"%";ticking=false;};
  window.addEventListener("scroll",()=>{if(!ticking){ticking=true;window.requestAnimationFrame(updateProgress);}},{passive:true});
  updateProgress();
  // Prevent accidental pinch zoom inside the mini app as requested.
  document.addEventListener("gesturestart",event=>event.preventDefault(),{passive:false});
  document.addEventListener("touchmove",event=>{if(event.touches&&event.touches.length>1)event.preventDefault();},{passive:false});
}

setupMotionAndViewport();
renderText();load();
