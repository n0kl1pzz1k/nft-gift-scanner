<script lang="ts">
  type NavId = 'orders' | 'auctions' | 'offers' | 'profile';
  type Props = { active: NavId; lang: 'ru' | 'en'; onNavigate: (id: NavId) => void };
  let { active, lang, onNavigate }: Props = $props();
  const items: {id: NavId; label: string; icon: string}[] = $derived([
    {id:'orders',label:lang === 'ru' ? 'Маркет' : 'Market',icon:'▦'},
    {id:'auctions',label:lang === 'ru' ? 'Аукционы' : 'Auctions',icon:'♢'},
    {id:'offers',label:lang === 'ru' ? 'Рынок' : 'Market pulse',icon:'◫'},
    {id:'profile',label:lang === 'ru' ? 'Профиль' : 'Profile',icon:'◉'},
  ]);
</script>
<nav class="mobile-nav" aria-label="Primary navigation">
  {#each items as item}
    <button class:active={active === item.id} onclick={() => onNavigate(item.id)} aria-current={active === item.id ? 'page' : undefined}>
      <span>{item.icon}</span><small>{item.label}</small>
    </button>
  {/each}
</nav>
