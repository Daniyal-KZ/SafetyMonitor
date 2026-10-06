<script setup lang="ts">
import { contentKeys, type Locale, type MaskKey, type VoiceMode } from '~/composables/usePrivacy'
import { languages } from '~/composables/useCopy'

const t = useCopy()
const settings = useMaskSettings()
const preferences = useAppPreferences()
const { locale, setLocale } = useLocale()
const { state: whitelist, loading, error, load, setEnabled, message } = useWhitelist()
const savingWhitelist = ref(false)
const options = computed<{ key: MaskKey; label: string }[]>(() => [
  { key: 'faces', label: t.value.faces },
  { key: 'plates', label: t.value.plates },
  { key: 'cards', label: t.value.cards },
  { key: 'documents', label: t.value.documents },
  { key: 'digital', label: t.value.digital },
  { key: 'voice', label: t.value.voice },
  { key: 'profanity', label: t.value.profanity },
  { key: 'sensitive', label: t.value.sensitive },
])
const voiceModes = computed<{ key: VoiceMode; label: string }[]>(() => [
  { key: 'deep', label: t.value.voiceDeep },
  { key: 'rough', label: t.value.voiceRough },
  { key: 'robotic', label: t.value.voiceRobotic },
])
onMounted(async () => { await load() })

function changeLocale(event: Event) {
  setLocale((event.target as HTMLSelectElement).value as Locale)
}
async function changeWhitelist(event: Event) {
  const input = event.target as HTMLInputElement
  savingWhitelist.value = true
  try { await setEnabled(input.checked) }
  catch (cause) { error.value = message(cause); input.checked = whitelist.value.enabled }
  finally { savingWhitelist.value = false }
}
</script>

<template>
  <div class="settings-page">
    <header class="page-header">
      <div>
        <h1 class="page-heading">{{ t.settingsTitle }}</h1>
        <p class="page-description">{{ t.settingsDesc }}</p>
      </div>
    </header>

    <div class="settings-grid">
      <section class="app-panel" :aria-label="t.interfaceTitle">
        <h2 class="panel-heading">{{ t.interfaceTitle }}</h2>
        <div class="setting-row">
          <label for="interface-language" class="setting-name">{{ t.language }}</label>
          <select id="interface-language" class="field setting-select" :value="locale" @change="changeLocale">
            <option v-for="language in languages" :key="language.code" :value="language.code">{{ language.label }}</option>
          </select>
        </div>
        <div class="setting-row">
          <label for="interface-appearance" class="setting-name">{{ t.appearance }}</label>
          <select id="interface-appearance" v-model="preferences.appearance" class="field setting-select">
            <option value="light">{{ t.themeLight }}</option>
            <option value="dark">{{ t.themeDark }}</option>
            <option value="system">{{ t.themeSystem }}</option>
          </select>
        </div>
      </section>

      <section class="app-panel" :aria-label="t.classesTitle">
        <div class="panel-heading"><h2>{{ t.classesTitle }}</h2><span class="status-text">{{ t.processingDisconnected }}</span></div>
        <template v-for="option in options" :key="option.key">
          <label class="setting-row">
            <span class="setting-name">{{ option.label }}</span>
            <input v-model="settings[option.key]" type="checkbox" :aria-label="option.label" />
          </label>
          <div v-if="option.key === 'voice' && settings.voice" class="setting-row">
            <label for="voice-mode" class="setting-name">{{ t.voiceModeLabel }}</label>
            <select id="voice-mode" v-model="settings.voiceMode" class="field setting-select">
              <option v-for="mode in voiceModes" :key="mode.key" :value="mode.key">{{ mode.label }}</option>
            </select>
          </div>
          <div v-if="option.key === 'sensitive' && settings.sensitive" class="settings-subgroup">
            <label v-for="key in contentKeys" :key="key" class="setting-check">
              <input v-model="settings.content[key]" type="checkbox" />
              <span>{{ t[key] }}</span>
            </label>
          </div>
        </template>
      </section>

      <section class="app-panel" :aria-label="t.whitelist">
        <h2 class="panel-heading">{{ t.whitelist }}</h2>
        <label class="setting-row">
          <span class="setting-name">{{ t.whitelistEnabled }}</span>
          <input type="checkbox" :checked="whitelist.enabled" :disabled="loading || savingWhitelist" @change="changeWhitelist" />
        </label>
        <div class="setting-row">
          <span class="setting-detail">{{ t.whitelistDesc }}</span>
          <NuxtLink to="/whitelist" class="btn">{{ t.manageWhitelist }}</NuxtLink>
        </div>
      </section>
    </div>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
    <p class="settings-footer">{{ t.preferencesSaved }}</p>
  </div>
</template>
