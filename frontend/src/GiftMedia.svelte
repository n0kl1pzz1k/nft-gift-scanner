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
  let mode = $state<'image'|'lottie'|'video'>('image');
  let failed = $state(false);
  let animation: ReturnType<typeof lottie.loadAnimation> | undefined;

  onMount(() => {
    let disposed = false;
    const url = animationUrl?.trim();
    if (!url || hasAnimation === false) return;

    if (/\.webm(?:$|[?#])/i.test(url)) {
      mode = 'video';
      return;
    }

    mode = 'lottie';
    void (async () => {
      try {
        const response = await fetch(url, { credentials: 'omit' });
        if (!response.ok) throw new Error('Animation file unavailable');
        const bytes = new Uint8Array(await response.arrayBuffer());
        const isTgs = /\.tgs(?:$|[?#])/i.test(url) || (bytes[0] === 0x1f && bytes[1] === 0x8b);
        const decoded = isTgs ? gunzipSync(bytes) : bytes;
        const animationData = JSON.parse(new TextDecoder().decode(decoded));
        if (disposed) return;
        await tick();
        if (disposed || !container) return;
        animation = lottie.loadAnimation({
          container,
          renderer: 'svg',
          loop: true,
          autoplay: true,
          animationData,
          rendererSettings: { preserveAspectRatio: 'xMidYMid meet', progressiveLoad: true }
        });
      } catch {
        if (!disposed) {
          failed = true;
          mode = 'image';
        }
      }
    })();

    return () => {
      disposed = true;
      animation?.destroy();
    };
  });
</script>

{#if mode === 'video' && animationUrl}
  <video class={className} src={animationUrl} poster={image} autoplay loop muted playsinline aria-label={alt}></video>
{:else if mode === 'lottie' && !failed}
  <div class={className} bind:this={container} role="img" aria-label={alt}></div>
{:else if image}
  <img class={className} src={image} alt={alt} loading="lazy"/>
{:else}
  <div class="gift-placeholder">🎁</div>
{/if}
