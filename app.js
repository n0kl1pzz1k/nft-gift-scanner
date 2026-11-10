const copy={
  en:{
    eyebrow:"READ-ONLY MARKET MONITOR",
    heading:"Telegram gifts, market data in one place.",
    sub:"Verified public collection data and marketplace coverage.",
    api:"Backend status",
    checking:"Checking connection…",
    online:"Backend online",
    offline:"Backend unavailable",
    foot:"Read-only public information. No purchases, sales, transfers or trading signals.",
    collections:"Portals collections",
    noData:"No collection data is connected yet. The app will not invent market values.",
    portals:"Portals",
    tonnel:"Tonnel",
    mrkt:"MRKT",
    needsKey:"Credential not configured",
    unavailable:"Official read-only API not verified",
    configured:"Credential configured · read-only routes only",
    sourceOnline:"Data endpoint responded",
    floor:"Collection floor",
    volume:"Reported 24h volume",
    supply:"Reported supply",
    sourceNote:"Current coverage: Portals collection-level data only. Tonnel and MRKT remain unconnected until official read-only access is verified.",
    dataNote:"Collection-level figures are not individual gift listings and cannot be compared across marketplaces.",
    updated:"Last response",
    never:"Not available",
    error:"Could not load marketplace data."
  },
  ru:{
    eyebrow:"МОНИТОРИНГ РЫНКА · ТОЛЬКО ЧТЕНИЕ",
    heading:"Подарки Telegram и рыночные данные в одном месте.",
    sub:"Проверенные публичные данные коллекций и покрытие маркетплейсов.",
    api:"Статус backend",
    checking:"Проверяем соединение…",
    online:"Backend работает",
    offline:"Backend недоступен",
    foot:"Только чтение публичной информации. Без покупок, продаж, переводов и торговых сигналов.",
    collections:"Коллекции Portals",
    noData:"Данные коллекций пока не подключены. Приложение не будет придумывать рыночные значения.",
    portals:"Portals",
    tonnel:"Tonnel",
    mrkt:"MRKT",
    needsKey:"Доступ не настроен",
    unavailable:"Официальный read-only API не подтверждён",
    configured:"Доступ настроен · только разрешённые GET-запросы",
    sourceOnline:"Источник ответил",
    floor:"Флор коллекции",
    volume:"Объём за 24 ч (по данным источника)",
    supply:"Количество выпущенных",
    sourceNote:"Сейчас доступны только агрегированные данные коллекций Portals. Tonnel и MRKT не подключены до подтверждения официального read-only доступа.",
    dataNote:"Показатели коллекций — не объявления отдельных подарков и не основание для сравнения цен между маркетами.",
    updated:"Последний ответ",
    never:"Нет данных",
    error:"Не удалось загрузить данные маркетплейсов."
  }
};
let lang="ru";
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function renderText(){
  const t=copy[lang];
  for(const [id,key] of [["eyebrow","eyebrow"],["heading","heading"],["sub","sub"],["api-title","api"],["foot","foot"],["collections-title","collections"]]) $(id).textContent=t[key];
  $("lang").textContent=lang==="en"?"RU":"EN";
  $("coverage-note").textContent=t.sourceNote;
  $("data-note").textContent=t.dataNote;
}
function sourceLabel(name,state){
  const t=copy[lang];
  let label=t.unavailable;
  if(state==="online"||state==="online_read_only") label=t.sourceOnline;
  else if(state==="configured_read_only") label=t.configured;
  else if(state==="needs_api_key") label=t.needsKey;
  return '<span class="source"><b>'+name+'</b><small>'+label+'</small></span>';
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
    const [statusRes,dataRes]=await Promise.all([
      fetch(window.API_BASE+"/api/status"),
      fetch(window.API_BASE+"/api/collections")
    ]);
    if(!statusRes.ok||!dataRes.ok) throw new Error("Marketplace API unavailable");
    const status=await statusRes.json();
    const data=await dataRes.json();
    const s={...(status.sources||{}),...(data.sources||{})};
    $("sources").innerHTML=sourceLabel(t.portals,s.portals)+sourceLabel(t.tonnel,s.tonnel)+sourceLabel(t.mrkt,s.mrkt);
    const items=Array.isArray(data.items)?data.items:[];
    $("collections-count").textContent=items.length;
    $("updated").textContent=t.updated+": "+formatTime(data.updated_at);
    $("collections").innerHTML=items.length?items.map(item=>{
      const image=item.image?'<img loading="lazy" src="'+escapeHtml(item.image)+'" alt="">':'<div class="placeholder">✦</div>';
      return '<article class="collection-card">'+image+
        '<div class="card-info"><b>'+escapeHtml(item.name)+'</b>'+
        '<span>'+t.floor+': <strong>'+escapeHtml(item.floor_price??"—")+'</strong></span>'+
        '<span>'+t.volume+': '+escapeHtml(item.day_volume??"—")+'</span>'+
        '<span>'+t.supply+': '+escapeHtml(item.supply??"—")+'</span>'+
        '<small>'+escapeHtml(item.source||"")+'</small></div></article>';
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
