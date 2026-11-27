<script lang="ts">
  export let lang: 'ru' | 'en' = 'ru';
</script>

<section class="hassle-boot" role="status" aria-live="polite" aria-label="HASSLE NFT loading">
  <div class="boot-atmosphere" aria-hidden="true">
    <span class="boot-orbit boot-orbit-a"></span>
    <span class="boot-orbit boot-orbit-b"></span>
    <span class="boot-particle boot-particle-a"></span>
    <span class="boot-particle boot-particle-b"></span>
    <span class="boot-particle boot-particle-c"></span>
  </div>

  <div class="boot-stage" aria-hidden="true">
    <div class="boot-halo"></div>
    <div class="boot-coin-shadow"></div>
    <div class="boot-coin">
      <div class="boot-coin-side"></div>
      <div class="boot-coin-face boot-coin-front">
        <svg viewBox="0 0 320 320" role="presentation">
          <path d="M99 85H221C229 85 236 89 241 96L257 145C260 152 258 158 252 164L168 247C163 252 157 252 152 247L66 163C60 157 59 151 63 144L94 95C96 89 102 85 109 85Z" fill="#fff" />
          <path d="M184 109C187 109 189 111 190 115L199 139L222 148C227 150 227 156 222 158L199 167L190 190C188 195 181 195 179 190L170 167L147 158C142 156 142 150 147 148L170 139L179 115C180 111 182 109 184 109Z" fill="#30A1F5" />
        </svg>
      </div>
      <div class="boot-coin-face boot-coin-back">
        <span>GRAM</span>
      </div>
    </div>
    <div class="boot-floor-ring"></div>
  </div>

  <div class="boot-brand">
    <div class="boot-wordmark">HASSLE<span>NFT</span></div>
    <div class="boot-tagline">THE NFT MARKETPLACE</div>
  </div>

  <div class="boot-loading">
    <div class="boot-loading-track"><i></i></div>
    <small>{lang === 'ru' ? 'ЗАГРУЖАЕМ МАРКЕТПЛЕЙС' : 'LOADING MARKETPLACE'}</small>
  </div>
</section>

<style>
  .hassle-boot{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;overflow:hidden;padding:24px;background:radial-gradient(ellipse at 50% 43%,#123b58 0%,#0a1c30 29%,#070d18 65%,#050810 100%);color:#f5fbff;isolation:isolate}
  .hassle-boot:before,.hassle-boot:after{position:absolute;inset:-35%;content:"";pointer-events:none}
  .hassle-boot:before{background:conic-gradient(from 0deg at 50% 50%,transparent 0 26%,#2ba9e20c 31%,transparent 38% 62%,#7b6cff0b 70%,transparent 78%);animation:boot-ambient 18s linear infinite}
  .hassle-boot:after{background:radial-gradient(ellipse at 50% 50%,transparent 20%,#02050ab8 78%);z-index:2}
  .boot-atmosphere{position:absolute;inset:0;overflow:hidden}
  .boot-orbit{position:absolute;left:50%;top:43%;width:min(78vw,520px);height:min(78vw,520px);border:1px solid #6bd8ff16;border-radius:50%;transform:translate(-50%,-50%) rotateX(68deg)}
  .boot-orbit-a{animation:boot-orbit 16s linear infinite}
  .boot-orbit-b{width:min(58vw,390px);height:min(58vw,390px);border-color:#9c8aff18;transform:translate(-50%,-50%) rotateX(68deg) rotateZ(52deg);animation:boot-orbit-reverse 21s linear infinite}
  .boot-particle{position:absolute;width:4px;height:4px;border-radius:50%;background:#a6eaff;box-shadow:0 0 16px 4px #39b9f455}
  .boot-particle-a{left:20%;top:29%;animation:boot-float 5s ease-in-out infinite}
  .boot-particle-b{right:19%;top:52%;width:3px;height:3px;animation:boot-float 6s ease-in-out -2s infinite}
  .boot-particle-c{left:29%;bottom:23%;width:2px;height:2px;animation:boot-float 4s ease-in-out -1s infinite}
  .boot-stage{position:relative;width:min(76vw,330px);height:min(76vw,330px);display:grid;place-items:center;perspective:1000px;z-index:3}
  .boot-halo{position:absolute;width:82%;height:82%;border-radius:50%;background:radial-gradient(circle,#39b9f52c 0%,#3979f315 42%,transparent 72%);filter:blur(13px);animation:boot-halo 3.2s ease-in-out infinite}
  .boot-coin-shadow{position:absolute;left:17%;right:17%;bottom:12%;height:15%;border-radius:50%;background:#000a;filter:blur(18px);transform:scaleX(.8);animation:boot-shadow 4.8s ease-in-out infinite}
  .boot-coin{position:relative;width:58%;aspect-ratio:1;border-radius:22%;transform-style:preserve-3d;animation:boot-coin-spin 8s cubic-bezier(.45,.04,.55,.96) infinite}
  .boot-coin-side{position:absolute;inset:0;border-radius:22%;background:linear-gradient(145deg,#d9f3ff 0%,#30A1F5 35%,#075da9 100%);transform:translateZ(-8px) scale(.99);box-shadow:0 0 0 2px #d6f1ff66,0 16px 42px #0008}
  .boot-coin-face{position:absolute;inset:0;display:grid;place-items:center;overflow:hidden;border-radius:22%;backface-visibility:hidden;border:1px solid #d5f4ffbd;background:linear-gradient(145deg,#58bdff 0%,#30A1F5 48%,#0875ca 100%);box-shadow:inset 0 3px 7px #fff9,inset 0 -12px 25px #00182b99,0 0 38px #39bafc55}
  .boot-coin-face:before{position:absolute;inset:5%;border:1px solid #d5f5ff80;border-radius:18%;content:"";box-shadow:inset 0 0 0 3px #ffffff12,inset 0 0 18px #fff3}
  .boot-coin-face:after{position:absolute;inset:-20%;content:"";background:linear-gradient(112deg,transparent 32%,#ffffffa8 47%,#ffffff18 52%,transparent 63%);transform:translateX(-90%);animation:boot-shine 4.8s ease-in-out infinite}
  .boot-coin-front svg{position:relative;z-index:1;width:47%;height:47%;fill:#e9fbff;filter:drop-shadow(0 3px 5px #002b4b99)}
  .boot-coin-back{transform:rotateY(180deg) translateZ(8px);background:linear-gradient(145deg,#58bdff,#147ed2 62%,#0863ae)}
  .boot-coin-back span{font:900 clamp(18px,5vw,27px)/1 Inter,system-ui,sans-serif;letter-spacing:.18em;color:#fff;text-shadow:0 2px 8px #07518d}
  .boot-floor-ring{position:absolute;bottom:2%;width:75%;height:17%;border:1px solid #61d7ff70;border-radius:50%;transform:rotateX(72deg);box-shadow:0 0 20px #39b9f533,inset 0 0 18px #39b9f522;animation:boot-ring 3s ease-in-out infinite}
  .boot-brand{position:absolute;top:calc(50% + min(35vw,160px));z-index:4;text-align:center;transform:translateY(-8px)}
  .boot-wordmark{font:800 clamp(21px,5vw,27px)/1 Inter,system-ui,sans-serif;letter-spacing:.22em;text-shadow:0 0 24px #48baff35}
  .boot-wordmark span{margin-left:7px;color:#75d8ff}
  .boot-tagline{margin-top:10px;color:#8daec5;font:600 8px/1 Inter,system-ui,sans-serif;letter-spacing:.34em}
  .boot-loading{position:absolute;bottom:max(42px,calc(env(safe-area-inset-bottom) + 28px));z-index:4;width:min(210px,58vw);text-align:center}
  .boot-loading-track{height:2px;overflow:hidden;border-radius:4px;background:#d9f4ff20}
  .boot-loading-track i{display:block;width:34%;height:100%;border-radius:4px;background:linear-gradient(90deg,#4a9eff,#9ceeff,#b8a5ff);box-shadow:0 0 12px #61cfff;animation:boot-progress 1.65s ease-in-out infinite}
  .boot-loading small{display:block;margin-top:12px;color:#7897ad;font:600 8px/1 Inter,system-ui,sans-serif;letter-spacing:.2em}
  @keyframes boot-coin-spin{0%,100%{transform:rotateY(-18deg) rotateX(13deg) rotateZ(-5deg)}50%{transform:rotateY(342deg) rotateX(13deg) rotateZ(5deg)}}
  @keyframes boot-shine{0%,18%{transform:translateX(-90%)}55%,100%{transform:translateX(90%)}}
  @keyframes boot-halo{0%,100%{opacity:.55;transform:scale(.88)}50%{opacity:1;transform:scale(1.12)}}
  @keyframes boot-shadow{0%,100%{opacity:.55;transform:scaleX(.72)}50%{opacity:.9;transform:scaleX(1)}}
  @keyframes boot-ring{0%,100%{opacity:.35;transform:rotateX(72deg) scale(.9)}50%{opacity:.9;transform:rotateX(72deg) scale(1.08)}}
  @keyframes boot-progress{0%{transform:translateX(-110%)}100%{transform:translateX(320%)}}
  @keyframes boot-orbit{to{transform:translate(-50%,-50%) rotateX(68deg) rotateZ(360deg)}}
  @keyframes boot-orbit-reverse{to{transform:translate(-50%,-50%) rotateX(68deg) rotateZ(-308deg)}}
  @keyframes boot-ambient{to{transform:rotate(360deg)}}
  @keyframes boot-float{0%,100%{opacity:.25;transform:translate3d(0,0,0)}50%{opacity:1;transform:translate3d(8px,-16px,0)}}
  @media(prefers-reduced-motion:reduce){.boot-coin,.boot-orbit,.boot-orbit-b,.boot-coin-face:after,.boot-halo,.boot-coin-shadow,.boot-floor-ring,.boot-loading-track i,.boot-particle,.hassle-boot:before{animation-duration:12s}}
</style>
