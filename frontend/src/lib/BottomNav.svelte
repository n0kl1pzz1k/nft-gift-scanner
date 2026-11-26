<script lang="ts">
  type NavId = 'orders' | 'auctions' | 'offers' | 'profile';
  type Props = { active: NavId; lang: 'ru' | 'en'; onNavigate: (id: NavId) => void };
  let { active, lang, onNavigate }: Props = $props();
  const items: {id: NavId; label: string}[] = $derived([
    {id:'orders',label:lang === 'ru' ? 'Маркет' : 'Market'},
    {id:'auctions',label:lang === 'ru' ? 'Аукционы' : 'Auctions'},
    {id:'offers',label:lang === 'ru' ? 'Активность' : 'Activity'},
    {id:'profile',label:lang === 'ru' ? 'Профиль' : 'Profile'},
  ]);
</script>
<nav class="mobile-nav" aria-label="Primary navigation">
  {#each items as item}
    <button type="button" class:active={active === item.id} onclick={() => onNavigate(item.id)} aria-current={active === item.id ? 'page' : undefined}>
      <span class="nav-icon" aria-hidden="true">
        {#if item.id === 'orders'}
          <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="6" height="6" rx="1.2"/><rect x="14" y="4" width="6" height="6" rx="1.2"/><rect x="4" y="14" width="6" height="6" rx="1.2"/><rect x="14" y="14" width="6" height="6" rx="1.2"/></svg>
        {:else if item.id === 'auctions'}
          <svg viewBox="0 0 24 24" fill="none"><path d="m12 3 8 9-8 9-8-9 8-9Z"/></svg>
        {:else if item.id === 'offers'}
          <svg viewBox="0 0 24 24" fill="none"><path d="M4 19V5m0 14h16M8 15v-4m4 4V7m4 8v-6"/></svg>
        {:else}
          <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c.7-3.6 3.3-5.5 7.5-5.5s6.8 1.9 7.5 5.5"/></svg>
        {/if}
      </span>
      <small>{item.label}</small>
    </button>
  {/each}
</nav>
