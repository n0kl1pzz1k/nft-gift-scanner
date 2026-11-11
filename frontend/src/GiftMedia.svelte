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

  type SlotRequest = {
    cancelled: boolean;
    resolve: (release: () => void) => void;
  };

  // A shared scheduler prevents dozens of independent Lottie render loops from
  // competing for the same main thread. Video and Lottie use the same budget.
  const slotQueue: SlotRequest[] = [];
  let activeSlots = 0;

  function maxActiveSlots() {
    if (typeof window === 'undefined') return 1;
    return window.matchMedia('(max-width: 720px)').matches ? 2 : 3;
  }

  function drainSlotQueue() {
    while (activeSlots < maxActiveSlots() && slotQueue.length) {
      const request = slotQueue.shift()!;
      if (request.cancelled) continue;
      activeSlots += 1;
      let released = false;
      request.resolve(() => {
        if (released) return;
        released = true;
        activeSlots = Math.max(0, activeSlots - 1);
        drainSlotQueue();
      });
    }
  }

  function requestAnimationSlot() {
    let request!: SlotRequest;
    const promise = new Promise<() => void>((resolve) => {
      request = { cancelled: false, resolve };
      slotQueue.push(request);
      drainSlotQueue();
    });
    return {
      promise,
      cancel() {
        request.cancelled = true;
        const index = slotQueue.indexOf(request);
        if (index >= 0) slotQueue.splice(index, 1);
      }
    };
  }

  // One network fetch per URL even when the same gift appears in several views.
  const byteCache = new Map<string, Promise<Uint8Array>>();
  function loadAnimationBytes(url: string): Promise<Uint8Array> {
    const cached = byteCache.get(url);
    if (cached) return cached;
    const task = fetch(url, { credentials: 'omit', cache: 'force-cache' }).then(async (response) => {
      if (!response.ok) throw new Error('Animation file unavailable');
      return new Uint8Array(await response.arrayBuffer());
    }).catch((error) => {
      byteCache.delete(url);
      throw error;
    });
    byteCache.set(url, task);
    while (byteCache.size > 32) {
      const oldest = byteCache.keys().next().value;
      if (oldest) byteCache.delete(oldest);
      else break;
    }
    return task;
  }

  let { image, animationUrl, hasAnimation, alt = 'Telegram gift', className = 'gift-image' }: Props = $props();
  let container = $state<HTMLDivElement>();
  let video = $state<HTMLVideoElement | undefined>();
  let visible = $state(false);
  let mode = $state<'image'|'lottie'|'video'>('image');
  let failed = $state(false);
  let animation: ReturnType<typeof lottie.loadAnimation> | undefined;
  let releaseSlot: (() => void) | undefined;
  let pendingSlot: ReturnType<typeof requestAnimationSlot> | undefined;
  let destroyTimer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  let startVersion = 0;

  onMount(() => {
    const url = animationUrl?.trim();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!url || hasAnimation === false || reducedMotion || !('IntersectionObserver' in window)) return;

    mode = /\.webm(?:$|[?#])/i.test(url) ? 'video' : 'lottie';

    const clearDestroyTimer = () => {
      if (destroyTimer) clearTimeout(destroyTimer);
      destroyTimer = undefined;
    };

    const destroyLottie = () => {
      if (!animation) return;
      animation.destroy();
      animation = undefined;
      container?.replaceChildren();
    };

    const stop = () => {
      startVersion += 1;
      pendingSlot?.cancel();
      pendingSlot = undefined;
      if (mode === 'video') video?.pause();
      if (mode === 'lottie') animation?.pause();
      releaseSlot?.();
      releaseSlot = undefined;

      // Keep a short grace period for tiny scroll reversals, then free the
      // canvas, renderer and animation object to avoid retaining GPU memory.
      if (mode === 'lottie' && animation) {
        clearDestroyTimer();
        destroyTimer = setTimeout(() => {
          if (!visible && !disposed) destroyLottie();
        }, 1800);
      }
    };

    const start = async () => {
      if (!visible || disposed || releaseSlot || pendingSlot || failed) return;
      clearDestroyTimer();
      const version = ++startVersion;
      const request = requestAnimationSlot();
      pendingSlot = request;
      const release = await request.promise;
      if (pendingSlot === request) pendingSlot = undefined;
      if (disposed || !visible || version !== startVersion) {
        release();
        return;
      }
      releaseSlot = release;

      try {
        if (mode === 'video') {
          if (!video) throw new Error('Video element unavailable');
          if (!video.src) video.src = url;
          await video.play();
          return;
        }

        if (!animation) {
          const bytes = await loadAnimationBytes(url);
          const isTgs = /\.tgs(?:$|[?#])/i.test(url) || (bytes[0] === 0x1f && bytes[1] === 0x8b);
          const decoded = isTgs ? gunzipSync(bytes) : bytes;
          const animationData = JSON.parse(new TextDecoder().decode(decoded));
          if (disposed || !visible || version !== startVersion || !container) {
            releaseSlot?.();
            releaseSlot = undefined;
            return;
          }
          animation = lottie.loadAnimation({
            container,
            renderer: 'canvas',
            loop: true,
            autoplay: false,
            animationData,
            rendererSettings: {
              preserveAspectRatio: 'xMidYMid meet',
              clearCanvas: true,
              dpr: Math.min(window.devicePixelRatio || 1, 1.25)
            }
          });
          // Render at the animation's authored frame rate. Subframe
          // interpolation burns CPU without inventing real source frames.
          animation.setSubframe(false);
        }
        if (visible && !disposed && version === startVersion) animation.play();
      } catch {
        if (!disposed && visible && version === startVersion) {
          failed = true;
          mode = 'image';
          releaseSlot?.();
          releaseSlot = undefined;
        }
      }
    };

    const observer = new IntersectionObserver((entries) => {
      const entry = entries[0];
      if (!entry || disposed) return;
      visible = entry.isIntersecting;
      if (visible) void start();
      else stop();
    }, { rootMargin: '64px 0px', threshold: 0.05 });

    void tick().then(() => {
      if (!disposed) {
        const target = mode === 'video' ? video : container;
        if (target) observer.observe(target);
      }
    });

    const onVisibilityChange = () => {
      if (document.hidden) stop();
      else if (visible) void start();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      disposed = true;
      visible = false;
      startVersion += 1;
      pendingSlot?.cancel();
      pendingSlot = undefined;
      clearDestroyTimer();
      observer.disconnect();
      document.removeEventListener('visibilitychange', onVisibilityChange);
      video?.pause();
      animation?.destroy();
      animation = undefined;
      releaseSlot?.();
      releaseSlot = undefined;
    };
  });
</script>

{#if mode === 'video' && animationUrl && !failed}
  <video bind:this={video} class={`${className} gift-media`} poster={image} loop muted playsinline preload="none" aria-label={alt}></video>
{:else if mode === 'lottie' && !failed}
  <div class={`${className} gift-media`} bind:this={container} role="img" aria-label={alt}></div>
{:else if image}
  <img class={className} src={image} alt={alt} loading="lazy" decoding="async"/>
{:else}
  <div class="gift-placeholder">🎁</div>
{/if}
