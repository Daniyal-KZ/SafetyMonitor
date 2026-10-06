<script setup lang="ts">
import { AdjustmentsHorizontalIcon, FilmIcon, ShieldCheckIcon, UserGroupIcon, VideoCameraIcon } from '@heroicons/vue/24/outline'

const t = useCopy()
const preferences = useAppPreferences()
const { locale } = useLocale()
const route = useRoute()
const navigation = computed(() => [
  { to: '/upload', text: t.value.navUpload, icon: FilmIcon },
  { to: '/camera', text: t.value.navCamera, icon: VideoCameraIcon },
  { to: '/whitelist', text: t.value.whitelist, icon: UserGroupIcon },
  { to: '/settings', text: t.value.navSettings, icon: AdjustmentsHorizontalIcon },
])

useHead(() => ({ htmlAttrs: { lang: locale.value }, title: `${navigation.value.find(item => item.to === route.path)?.text || 'Privacy Guard'} — Privacy Guard` }))

let scheme: MediaQueryList | undefined
let stopThemeWatch: (() => void) | undefined
function applyTheme() {
  const dark = preferences.value.appearance === 'dark' || (preferences.value.appearance === 'system' && scheme?.matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

onMounted(() => {
  scheme = window.matchMedia('(prefers-color-scheme: dark)')
  scheme.addEventListener('change', applyTheme)
  stopThemeWatch = watch(() => preferences.value.appearance, applyTheme, { immediate: true })
})
onBeforeUnmount(() => {
  scheme?.removeEventListener('change', applyTheme)
  stopThemeWatch?.()
})
watch(() => route.fullPath, () => {
  if (import.meta.client) window.scrollTo({ top: 0 })
})
</script>

<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <NuxtLink to="/upload" class="app-brand" aria-label="Privacy Guard">
        <ShieldCheckIcon class="brand-icon" aria-hidden="true" />
        <span>Privacy Guard</span>
      </NuxtLink>
      <nav class="app-nav" :aria-label="t.navLabel">
        <NuxtLink v-for="item in navigation" :key="item.to" :to="item.to" class="app-nav-link"
          :class="{ active: route.path === item.to }" :aria-current="route.path === item.to ? 'page' : undefined">
          <component :is="item.icon" class="nav-icon" aria-hidden="true" />
          <span>{{ item.text }}</span>
        </NuxtLink>
      </nav>
    </aside>
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>
