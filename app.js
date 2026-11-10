const copy={
  en:{
    eyebrow:"READ-ONLY MARKET MONITOR",
    heading:"Telegram gifts, market data in one place.",
    sub:"Public collections, floor prices and marketplace activity.",
    api:"Backend status",
    wait:"Checking connection…",
    foot:"Read-only public market data. No buying, selling or transfers.",
    collections:"Collections",
    loading:"Loading collections…",
    noData:"No collections yet. Configure the Portals API key in Railway.",
    portals:"Portals",
    tonnel:"Tonnel",
    mrkt:"MRKT",
    needsKey:"API key needed",
    unavailable:"Official API not verified",
    online:"Online",
    floor:"Floor",
    volume:"24h volume",
    supply:"Supply",
    error:"Could not load marketplace data."
  },
  ru:{
    eyebrow:"МОНИТОРИНГ РЫНКА · READ-ONLY",
    heading:"Подарки Telegram и рыночные данные в одном месте.",
    sub:"Публичные коллекции, floor и активность маркетплейсов.",
    api:"Статус backend",
    wait:"Проверяем соединение…",
    foot:"Только чтение публичных рыночных данных. Без покупок, продаж и переводов.",
    collections:"Коллекции",
    loading:"Загружаем коллекции…",
    noData:"Коллекций пока нет. Добавь API-ключ Portals в Railway.",
    portals:"Portals",
    tonnel:"Tonnel",
    mrkt:"MRKT",
    needsKey:"Нужен API-ключ",
    unavailable:"Официальный API не подтверждён",
    online:"В сети",
    floor:"Флор",
    volume:"Объём за 24 ч",
    supply:"Выпущено",
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
}
function sourceLabel(name,state){
  const t=copy[lang];
  const label=state==="online"?t.online:state==="needs_api_key"?t.needsKey:t.unavailable;
  return '<span class="source"><b>'+name+'</b><small>'+label+'</small></span>';
}
async function load(){
  const t=copy[lang];
  try{
    const health=await fetch(window.API_BASE+"/health");
    if(!health.ok) throw new Error("Backend offline");
    $("api-status").textContent="Online";
    const [statusRes,dataRes]=await Promise.all([
      fetch(window.API_BASE+"/api/status"),
      fetch(window.API_BASE+"/api/collections")
    ]);
    const status=await statusRes.json();
    if(!dataRes.ok) throw new Error("Marketplace API unavailable");
    const data=await dataRes.json();
    const s=status.sources||{};
    $("sources").innerHTML=
      sourceLabel(t.portals,s.portals)+
      sourceLabel(t.tonnel,s.tonnel)+
      sourceLabel(t.mrkt,s.mrkt);
    const items=data.items||[];
    $("collections-count").textContent=items.length;
    $("collections-empty").textContent=items.length?"":t.noData;
    $("collections").innerHTML=items.length?items.map(item=>{
      const image=item.image?'<img loading="lazy" src="'+escapeHtml(item.image)+'" alt="">':'<div class="placeholder">✦</div>';
      return '<article class="collection-card">'+image+
        '<div class="card-info"><b>'+escapeHtml(item.name)+'</b>'+
        '<span>'+t.floor+': <strong>'+escapeHtml(item.floor_price??"—")+' GRAM</strong></span>'+
        '<span>'+t.volume+': '+escapeHtml(item.day_volume??"—")+' GRAM</span>'+
        '<span>'+t.supply+': '+escapeHtml(item.supply??"—")+'</span>'+
        '<small>Portals</small></div></article>';
    }).join(""):'<p class="empty">'+t.noData+'</p>';
  }catch(e){
    $("api-status").textContent=t.error;
    $("collections-count").textContent="—";
    $("collections").innerHTML='<p class="empty">'+t.error+'</p>';
  }
}
$("lang").onclick=()=>{lang=lang==="ru"?"en":"ru";renderText();load()};
renderText();load();
