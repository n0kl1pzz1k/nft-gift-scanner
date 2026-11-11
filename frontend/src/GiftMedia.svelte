<script lang="ts">
  import { onMount, tick } from 'svelte';
  import lottie from 'lottie-web';
  import { gunzipSync } from 'fflate';

  type Props = {
    image?: string;
    animationUrl?: string;
    hasAnimation?: boolean;
    alt?: string;
    className?: string;
  };

  let { image, animationUrl, hasAnimation, alt = 'Telegram gift', className = 'gift-image' }: Props = $props();
  let container: HTMLDivElement;
  let visible = $state(false);
  let mode = $state<'image'|'lottie'|'video'>('image');
  let failed = $state(false);
  let animation: ReturnType<typeof lottie.loadAnimation> | undefined;
  let video: HTMLVideoElement | undefined;

  onMount(() => {
    let disposed = false;
    let started = false;
    const url = animationUrl?.trim();
    if (!url || hasAnimation === false) return;

    const isVideo = /\.webm(?:$|[?#])/i.test(url);
    mode = isVideo ? 'video' : 'lottie';

    // Do not decode or animate every card in the catalogue at once.
    // Only cards near the viewport are allowed to consume CPU/GPU.
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[0];
      if (!entry || disposed) return;
      visible = entry.isIntersecting;
      if (mode === 'video' && video) {
        if (visible) void video.play().catch(() => {});
        else video.pause();
      }
      if (mode === 'lottie' && animation) {
        if (visible) animation.play();
        else animation.pause();
      }
      if (visible && !started && mode === 'lottie') {
        started = true;
        void loadLottie();
      }
    }, { rootMargin: '140px 0px', threshold: 0.01 });

    if (container) observer.observe(container);

    async function loadLottie() {
      try {
        const response = await fetch(url!, { credentials: 'omit' });
        if (!response.ok) throw new Error('Animation file unavailable');
        const bytes = new Uint8Array(await response.arrayBuffer());
        const isTgs = /\.tgs(?:$|[?#])/i.test(url!) || (bytes[0] === 0x1f && bytes[1] === 0x8b);
        const decoded = isTgs ? gunzipSync(bytes) : bytes;
        const animationData = JSON.parse(new TextDecoder().decode(decoded));
        if (disposed) return;
        await tick();
        if (disposed || !container) return;
        animation = lottie.loadAnimation({
          container,
          renderer: 'canvas',
          loop: true,
          autoplay: false,
          animationData,
          rendererSettings: {
            preserveAspectRatio: 'xMidYMid meet',
            clearCanvas: true,
            progressiveLoad: true,
            dpr: Math.min(window.devicePixelRatio || 1, 1.5)
          }
        });
        animation.setSubframe(true);
        if (visible) animation.play();
      } catch {
        if (!disposed) {
          failed = true;
          mode = 'image';
        }
      }
    }

    return () => {
      disposed = true;
      observer.disconnect();
      animation?.destroy();
      video?.pause();
    };
  });
</script>

{#if mode === 'video' && animationUrl && !failed}
  <video bind:this={video} class={`${className} gift-media`} src={visible ? animationUrl : undefined} poster={image} autoplay={visible} loop muted playsinline preload="none" aria-label={alt}></video>
{:else if mode === 'lottie' && !failed}
  <div class={`${className} gift-media`} bind:this={container} role="img" aria-label={alt}></div>
{:else if image}
  <img class={className} src={image} alt={alt} loading="lazy" decoding="async"/>
{:else}
  <div class="gift-placeholder">🎁</div>
{/if}
