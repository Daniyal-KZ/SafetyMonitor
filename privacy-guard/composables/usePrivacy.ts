export type Locale = 'kk' | 'ru' | 'en'
export type MaskKey = 'faces' | 'plates' | 'cards' | 'documents' | 'digital' | 'voice' | 'profanity' | 'sensitive'
export type ContentKey = 'tobacco' | 'cannabis' | 'alcohol' | 'nudity'
export type VoiceMode = 'deep' | 'rough' | 'robotic'
export type Appearance = 'light' | 'dark' | 'system'

export const maskKeys: MaskKey[] = ['faces', 'plates', 'cards', 'documents', 'digital', 'voice', 'profanity', 'sensitive']
export const contentKeys: ContentKey[] = ['tobacco', 'cannabis', 'alcohol', 'nudity']

export type MaskSettings = Record<MaskKey, boolean> & {
  voiceMode: VoiceMode
  content: Record<ContentKey, boolean>
}

export const initialSettings: MaskSettings = {
  faces: true,
  plates: true,
  cards: false,
  documents: false,
  digital: false,
  voice: false,
  voiceMode: 'rough',
  profanity: false,
  sensitive: false,
  content: { tobacco: true, cannabis: true, alcohol: true, nudity: true },
}

function savePreference(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) }
  catch { /* Preferences still apply while browser storage is unavailable. */ }
}

export function useMaskSettings() {
  const settings = useState<MaskSettings>('privacy-settings', () => ({ ...initialSettings, content: { ...initialSettings.content } }))
  const loaded = useState('privacy-settings-loaded', () => false)

  onMounted(() => {
    if (!loaded.value) {
      try {
        const saved = JSON.parse(localStorage.getItem('privacy-guard:settings') || '{}') as Partial<MaskSettings>
        for (const key of maskKeys) {
          if (typeof saved[key] === 'boolean') settings.value[key] = saved[key]
        }
        if (saved.voiceMode === 'deep' || saved.voiceMode === 'rough' || saved.voiceMode === 'robotic') settings.value.voiceMode = saved.voiceMode
        for (const key of contentKeys) {
          if (typeof saved.content?.[key] === 'boolean') settings.value.content[key] = saved.content[key]
        }
      } catch { /* Keep valid defaults when a stored preference cannot be read. */ }
      loaded.value = true
    }
    watch(settings, value => savePreference('privacy-guard:settings', value), { deep: true })
  })

  return settings
}

export function useAppPreferences() {
  const preferences = useState<{ appearance: Appearance }>('privacy-app-preferences', () => ({ appearance: 'light' }))
  const loaded = useState('privacy-app-preferences-loaded', () => false)

  onMounted(() => {
    if (!loaded.value) {
      try {
        const saved = JSON.parse(localStorage.getItem('privacy-guard:preferences') || '{}') as { appearance?: Appearance }
        if (saved.appearance === 'light' || saved.appearance === 'dark' || saved.appearance === 'system') preferences.value.appearance = saved.appearance
      } catch { /* The default appearance remains available. */ }
      loaded.value = true
    }
    watch(preferences, value => savePreference('privacy-guard:preferences', value), { deep: true })
  })

  return preferences
}

export function useLocale() {
  const locale = useState<Locale>('privacy-locale', () => 'ru')
  const loaded = useState('privacy-locale-loaded', () => false)

  onMounted(() => {
    if (loaded.value) return
    try {
      const saved = localStorage.getItem('privacy-guard:locale')
      if (saved === 'kk' || saved === 'ru' || saved === 'en') locale.value = saved
    } catch { /* Use the default language when browser storage is unavailable. */ }
    loaded.value = true
  })

  function setLocale(value: Locale) {
    locale.value = value
    if (import.meta.client) {
      try { localStorage.setItem('privacy-guard:locale', value) }
      catch { /* Language changes do not require browser storage. */ }
    }
  }

  return { locale, setLocale }
}
