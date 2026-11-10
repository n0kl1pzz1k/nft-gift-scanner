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
    public:"Public data",readonly:"View only",nomoney:"No transactions",system:"SYSTEM",overview:"DATA OVERVIEW",count:"COLLECTIONS",live:"LIVE MONITOR"
  },
  ru:{
    eyebrow:"МОНИТОРИНГ РЫНКА · ТОЛЬКО ЧТЕНИЕ",heading:"Весь рынок Telegram-подарков <em>в одном месте.</em>",sub:"Коллекции, публичные показатели и состояние маркетплейсов — без лишнего шума.",
    api:"Статус подключения",checking:"Проверяем соединение…",online:"Backend работает",offline:"Backend недоступен",
    foot:"Только публичная информация. Без покупок, продаж, переводов и торговых сигналов.",collections:"Коллекции Portals",
    noData:"Данные коллекций пока не подключены. Приложение не будет придумывать рыночные значения.",
    portals:"Portals",tonnel:"Tonnel",mrkt:"MRKT",needsKey:"Доступ не настроен",
    unavailable:"Официальный read-only API не подтверждён",configured:"Доступ настроен · только разрешённые GET-запросы",
    sourceOnline:"Источник ответил",floor:"Флор коллекции",volume:"Объём за 24 ч (по данным источника)",supply:"Количество выпущенных",
    sourceNote:"Сейчас доступны только агрегированные данные коллекций Portals. Tonnel и MRKT не подключены до подтверждения официального read-only доступа.",
    dataNote:"Показатели коллекций — не объявления отдельных подарков и не основание для сравнения цен между маркетами.",
    updated:"Последний ответ",never:"Нет данных",error:"Не удалось загрузить данные маркетплейсов.",
    public:"Публичные данные",readonly:"Только просмотр",nomoney:"Без транзакций",system:"СИСТЕМА",overview:"ОБЗОР ДАННЫХ",count:"КОЛЛЕКЦИЙ",live:"LIVE МОНИТОРИНГ"
  }
};
let lang="ru";
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
  $("lang").textContent=lang==="en"?"RU":"EN";
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
  return new Date(value*1000).toLocaleString(lang==="ru"?"ru-RU":"en-GB");
}
async function load(){
  const t=copy[lang];
  $("api-status").textContent=t.checking;
  try{
    const health=await fetch(window.API_BASE+"/health");
    if(!health.ok) throw new Error("Backend offline");
    $("api-status").textContent=t.online;
    const [statusRes,dataRes]=await Promise.all([fetch(window.API_BASE+"/api/status"),fetch(window.API_BASE+"/api/collections")]);
    if(!statusRes.ok||!dataRes.ok) throw new Error("Marketplace API unavailable");
    const status=await statusRes.json();
    const data=await dataRes.json();
    const s={...(status.sources||{}),...(data.sources||{})};
    $("sources").innerHTML=sourceLabel(t.portals,s.portals,"portals","P")+sourceLabel(t.tonnel,s.tonnel,"tonnel","T")+sourceLabel(t.mrkt,s.mrkt,"mrkt","M");
    const items=Array.isArray(data.items)?data.items:[];
    $("collections-count").textContent=items.length;
    $("updated").textContent=t.updated+": "+formatTime(data.updated_at);
    $("collections").innerHTML=items.length?items.map(item=>{
      const image=item.image?'<img loading="lazy" src="'+escapeHtml(item.image)+'" alt="">':'<div class="placeholder">✦</div>';
      return '<article class="collection-card">'+image+'<div class="card-info"><b>'+escapeHtml(item.name)+'</b><span>'+t.floor+': <strong>'+escapeHtml(item.floor_price??"—")+'</strong></span><span>'+t.volume+': '+escapeHtml(item.day_volume??"—")+'</span><span>'+t.supply+': '+escapeHtml(item.supply??"—")+'</span><small>'+escapeHtml(item.source||"")+'</small></div></article>';
    }).join(""):'<p class="empty">'+t.noData+'</p>';
  }catch(e){
    $("api-status").textContent=t.offline;
    $("collections-count").textContent="—";
    $("updated").textContent=t.updated+": "+t.never;
    $("collections").innerHTML='<p class="empty">'+t.error+'</p>';
  }
}
$("lang").onclick=()=>{lang=lang==="ru"?"en":"ru";renderText();load()};
renderText();load();