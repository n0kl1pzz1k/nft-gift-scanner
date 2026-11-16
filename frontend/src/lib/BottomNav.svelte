<script lang="ts">
  type NavId = 'orders' | 'create' | 'offers' | 'profile';
  type Props = { active: NavId; onNavigate: (id: NavId) => void };
  let { active, onNavigate }: Props = $props();
  const items: {id: NavId; label: string; icon: string}[] = [
    {id:'orders',label:'Market',icon:'▦'},
    {id:'create',label:'Inventory',icon:'◈'},
    {id:'offers',label:'Activity',icon:'↗'},
    {id:'profile',label:'Profile',icon:'◉'},
  ];
</script>
<nav class="mobile-nav" aria-label="Primary navigation">
  {#each items as item}
    <button class:active={active === item.id} onclick={() => onNavigate(item.id)} aria-current={active === item.id ? 'page' : undefined}>
      <span>{item.icon}</span><small>{item.label}</small>
    </button>
  {/each}
</nav>
