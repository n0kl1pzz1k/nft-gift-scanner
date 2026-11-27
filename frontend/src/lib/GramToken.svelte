<script lang="ts">
  type Props = { size?: number; class?: string; title?: string };
  let { size = 20, class: className = '', title = 'Gram (GRAM)' }: Props = $props();
</script>

<svg class={className} width={size} height={size} viewBox="0 0 320 320" role="img" aria-label={title} focusable="false">
  <rect width="320" height="320" rx="0" fill="#30A1F5" />
  <path d="M99 85H221C229 85 236 89 241 96L257 145C260 152 258 158 252 164L168 247C163 252 157 252 152 247L66 163C60 157 59 151 63 144L94 95C96 89 102 85 109 85Z" fill="#fff" />
  <path d="M184 109C187 109 189 111 190 115L199 139L222 148C227 150 227 156 222 158L199 167L190 190C188 195 181 195 179 190L170 167L147 158C142 156 142 150 147 148L170 139L179 115C180 111 182 109 184 109Z" fill="#30A1F5" />
</svg>
