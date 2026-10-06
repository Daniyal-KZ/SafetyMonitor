import type { WhitelistEntry, WhitelistState } from '~/shared/types/whitelist'

export function useWhitelist() {
  const state = useState<WhitelistState>('whitelist-data', () => ({ enabled: false, entries: [] }))
  const loading = useState('whitelist-loading', () => false)
  const error = useState('whitelist-error', () => '')
  const loaded = useState('whitelist-loaded', () => false)

  function message(cause: unknown): string {
    const failure = cause as { data?: { data?: { message?: string }; message?: string; statusMessage?: string } }
    return failure?.data?.data?.message || failure?.data?.statusMessage || failure?.data?.message || 'Не удалось сохранить изменения. Проверьте соединение.'
  }
  async function load(force = false) {
    if (loading.value || (loaded.value && !force)) return
    loading.value = true
    error.value = ''
    try { state.value = await $fetch<WhitelistState>('/api/whitelist'); loaded.value = true }
    catch (cause) { error.value = message(cause) }
    finally { loading.value = false }
  }
  async function save(path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: FormData | Record<string, unknown>) {
    state.value = await $fetch<WhitelistState>(path, { method, body })
    loaded.value = true
    error.value = ''
    return state.value
  }
  function createPerson(label: string, notes: string, photos: File[]) {
    const body = new FormData()
    body.append('label', label.trim()); body.append('notes', notes.trim())
    photos.forEach(file => body.append('photos', file))
    return save('/api/whitelist/people', 'POST', body)
  }
  function createPlate(label: string, notes: string, plate: string, country: string) {
    return save('/api/whitelist/plates', 'POST', { label: label.trim(), notes: notes.trim(), plate, country })
  }
  function update(id: string, changes: Partial<Pick<WhitelistEntry, 'label' | 'notes' | 'enabled' | 'plate' | 'country'>>) {
    return save(`/api/whitelist/${encodeURIComponent(id)}`, 'PATCH', changes)
  }
  function remove(id: string) { return save(`/api/whitelist/${encodeURIComponent(id)}`, 'DELETE') }
  function addPhotos(id: string, photos: File[]) {
    const body = new FormData()
    photos.forEach(file => body.append('photos', file))
    return save(`/api/whitelist/${encodeURIComponent(id)}/photos`, 'POST', body)
  }
  function removePhoto(id: string, photoId: string) {
    return save(`/api/whitelist/${encodeURIComponent(id)}/photos/${encodeURIComponent(photoId)}`, 'DELETE')
  }
  function setEnabled(enabled: boolean) { return save('/api/whitelist/settings', 'PATCH', { enabled }) }
  return { state, loading, loaded, error, load, createPerson, createPlate, update, remove, addPhotos, removePhoto, setEnabled, message }
}
