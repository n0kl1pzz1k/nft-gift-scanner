<script lang="ts">
  type Props = { size?: number; class?: string; title?: string };
  let { size = 20, class: className = '', title = 'Gram (GRAM)' }: Props = $props();
</script>

<svg class={className} width={size} height={size} viewBox="0 0 100 100" role="img" aria-label={title} focusable="false">
  <path d="M50 2 98 50 50 98 2 50Z" fill="#30A1F5" />
  <path d="M50 20 57.8 42.2 80 50 57.8 57.8 50 80 42.2 57.8 20 50 42.2 42.2Z" fill="#fff" />
</svg>
