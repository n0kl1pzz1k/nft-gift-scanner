<script lang="ts">
  type NavId = 'orders' | 'create' | 'offers' | 'profile';
  type Props = { active: NavId; lang: 'ru' | 'en'; onNavigate: (id: NavId) => void };
  let { active, lang, onNavigate }: Props = $props();
  const items = $derived<{id: NavId; label: string; icon: string}[]>([
    {id:'orders',label:lang === 'ru' ? 'Маркет' : 'Market',icon:'▦'},
    {id:'create',label:lang === 'ru' ? 'Инвентарь' : 'Inventory',icon:'◈'},
    {id:'offers',label:lang === 'ru' ? 'Активность' : 'Activity',icon:'↗'},
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
