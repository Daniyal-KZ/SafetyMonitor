<script setup lang="ts">
import type { WhitelistEntry } from '~/shared/types/whitelist'
import WhitelistEntryDialog from '~/components/WhitelistEntryDialog.vue'

const t = useWhitelistCopy()
const { locale } = useLocale()
const { state, loading, error, load, update, remove, setEnabled, message } = useWhitelist()
const editor = ref<InstanceType<typeof WhitelistEntryDialog> | null>(null)
const confirmation = ref<HTMLDialogElement | null>(null)
const selected = ref<WhitelistEntry | null>(null)
const tab = ref<'person' | 'plate'>('person')
const search = ref('')
const onlyEnabled = ref(false)
const pendingId = ref('')
const actionError = ref('')
const globalPending = ref(false)
const entries = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  const compactQuery = query.replace(/[\s-]/g, '')
  return state.value.entries.filter(entry => entry.kind === tab.value && (!onlyEnabled.value || entry.enabled)
    && (!query || `${entry.label} ${entry.notes}`.toLocaleLowerCase().includes(query) || (entry.plate || '').toLocaleLowerCase().includes(compactQuery)))
})
const peopleCount = computed(() => state.value.entries.filter(entry => entry.kind === 'person').length)
const plateCount = computed(() => state.value.entries.filter(entry => entry.kind === 'plate').length)
onMounted(() => { void load() })
async function toggle(entry: WhitelistEntry, event: Event) {
  if (pendingId.value) return
  pendingId.value = entry.id; actionError.value = ''
  try { await update(entry.id, { enabled: !entry.enabled }) }
  catch (cause) { actionError.value = message(cause); (event.target as HTMLInputElement).checked = entry.enabled }
  finally { pendingId.value = '' }
}
async function toggleGlobal(event: Event) {
  const input = event.target as HTMLInputElement
  input.checked = state.value.enabled
  globalPending.value = true; actionError.value = ''
  try { await setEnabled(!state.value.enabled) }
  catch (cause) { actionError.value = message(cause) }
  finally { globalPending.value = false }
}
function askRemove(entry: WhitelistEntry) {
  selected.value = entry; actionError.value = ''; confirmation.value?.showModal()
}
async function confirmRemove() {
  if (!selected.value || pendingId.value) return
  pendingId.value = selected.value.id; actionError.value = ''
  try { await remove(selected.value.id); confirmation.value?.close(); selected.value = null }
  catch (cause) { actionError.value = message(cause) }
  finally { pendingId.value = '' }
}
function dateLabel(value: string) {
  return new Intl.DateTimeFormat({ ru: 'ru-RU', kk: 'kk-KZ', en: 'en-GB' }[locale.value], { day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(value))
}
</script>

<template>
  <div class="whitelist-page">
    <header class="page-header">
      <div><h1 class="page-heading">{{ t.title }}</h1><p class="page-description">{{ t.description }}</p></div>
      <button type="button" class="btn btn-primary" @click="editor?.open(tab)">{{ tab === 'person' ? t.addPerson : t.addVehicle }}</button>
    </header>
    <section class="app-panel">
      <div class="whitelist-tabs">
        <div class="tabs" role="tablist" :aria-label="t.entryTypes">
          <button id="people-tab" type="button" class="tab" :class="{ active: tab === 'person' }" role="tab" :aria-selected="tab === 'person'" aria-controls="entries-panel" @click="tab = 'person'">{{ t.people }} <span class="tab-count">{{ peopleCount }}</span></button>
          <button id="plates-tab" type="button" class="tab" :class="{ active: tab === 'plate' }" role="tab" :aria-selected="tab === 'plate'" aria-controls="entries-panel" @click="tab = 'plate'">{{ t.vehicles }} <span class="tab-count">{{ plateCount }}</span></button>
        </div>
        <label class="list-enabled"><input type="checkbox" :checked="state.enabled" :disabled="globalPending || loading" @change="toggleGlobal" /> {{ t.useList }}</label>
      </div>
      <div class="toolbar list-toolbar">
        <label class="search-field"><span class="sr-only">{{ t.search }}</span><input v-model="search" class="field" type="search" :placeholder="t.search" /></label>
        <label class="list-enabled"><input v-model="onlyEnabled" type="checkbox" /> {{ t.onlyEnabled }}</label>
        <button type="button" class="btn refresh-button" :disabled="loading" @click="load(true)">{{ t.refresh }}</button>
      </div>
      <p v-if="error || actionError" class="error-message list-error" role="alert">{{ error || actionError }}</p>
      <div id="entries-panel" class="table-wrap" role="tabpanel" :aria-labelledby="tab === 'person' ? 'people-tab' : 'plates-tab'" :aria-busy="loading">
        <table class="data-table whitelist-table">
          <thead><tr><th scope="col" class="status-column">{{ t.active }}</th><th scope="col">{{ tab === 'person' ? t.name : t.vehicle }}</th><th scope="col">{{ tab === 'person' ? t.photos : t.plate }}</th><th scope="col">{{ t.added }}</th><th scope="col"><span class="sr-only">{{ t.actions }}</span></th></tr></thead>
          <tbody>
            <tr v-for="entry in entries" :key="entry.id" :class="{ 'entry-disabled': !entry.enabled }">
              <td><input type="checkbox" :checked="entry.enabled" :disabled="!!pendingId" :aria-label="t.activeLabel(entry.label)" @change="toggle(entry, $event)" /></td>
              <td><button type="button" class="entry-title" @click="editor?.open(entry.kind, entry)">{{ entry.label }}</button><div v-if="entry.notes" class="entry-notes muted" :title="entry.notes">{{ entry.notes }}</div></td>
              <td><div v-if="entry.kind === 'person'" class="table-photos"><img v-for="(photo,index) in entry.photos.slice(0,3)" :key="photo.id" :src="photo.url" :alt="t.tablePhotoAlt(entry.label, index + 1)" loading="lazy" /><span v-if="entry.photos.length > 3" class="muted">+{{ entry.photos.length - 3 }}</span></div><div v-else class="vehicle-number"><span>{{ entry.plate }}</span><span class="country-label muted">{{ entry.country === 'OTHER' ? '—' : entry.country }}</span></div></td>
              <td class="date-column muted">{{ dateLabel(entry.createdAt) }}</td>
              <td><div class="row-actions"><button type="button" class="btn" :aria-label="t.editLabel(entry.label)" @click="editor?.open(entry.kind, entry)">{{ t.edit }}</button><button type="button" class="btn delete-btn" :disabled="!!pendingId" :aria-label="t.removeLabel(entry.label)" @click="askRemove(entry)">{{ t.remove }}</button></div></td>
            </tr>
          </tbody>
        </table>
        <div v-if="loading && !state.entries.length" class="empty-state" role="status">{{ t.loading }}</div>
        <div v-else-if="!entries.length" class="empty-state">{{ search || onlyEnabled ? t.noMatches : tab === 'person' ? t.noPeople : t.noVehicles }}</div>
      </div>
      <div class="list-footer muted">{{ t.entries }} {{ entries.length }}<span v-if="!state.enabled">{{ t.listDisabled }}</span></div>
    </section>
    <WhitelistEntryDialog ref="editor" />
    <dialog ref="confirmation" class="delete-dialog" aria-labelledby="delete-title" @cancel="pendingId ? $event.preventDefault() : undefined">
      <form @submit.prevent="confirmRemove">
        <div class="dialog-header"><h2 id="delete-title">{{ t.deleteTitle }}</h2></div>
        <div class="delete-body"><p>{{ selected?.label }}</p><p class="muted">{{ selected?.kind === 'person' ? t.deletePerson : t.deleteVehicle }}</p><p v-if="actionError" class="error-message" role="alert">{{ actionError }}</p></div>
        <div class="dialog-footer"><button type="button" class="btn" :disabled="!!pendingId" @click="confirmation?.close()">{{ t.cancel }}</button><button type="submit" class="btn btn-danger" :disabled="!!pendingId">{{ pendingId ? t.deleting : t.remove }}</button></div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.whitelist-tabs { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); padding: 0 16px; gap: 16px; }
.tabs { border-bottom: 0; }
.tab-count { margin-left: 6px; color: var(--muted); font-size: 12px; }
.list-enabled { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; white-space: nowrap; }
.list-toolbar { padding: 12px 16px; border-bottom: 1px solid var(--line); gap: 12px; }
.search-field { width: min(320px, 100%); }
.refresh-button { margin-left: auto; }
.table-wrap { position: relative; overflow-x: auto; }
.whitelist-table { width: 100%; white-space: nowrap; }
.whitelist-table td:first-child { text-align: center; }
.whitelist-table .status-column { width: 70px; text-align: center; }
.entry-title { color: var(--text); background: none; border: 0; padding: 0; font-weight: 600; text-align: left; cursor: pointer; }
.entry-title:hover { color: var(--accent); text-decoration: underline; }
.entry-notes { max-width: 250px; overflow: hidden; text-overflow: ellipsis; font-size: 12px; margin-top: 3px; }
.entry-disabled .entry-title, .entry-disabled .vehicle-number { color: var(--muted); }
.table-photos { display: flex; gap: 5px; align-items: center; }
.table-photos img { width: 32px; height: 36px; object-fit: cover; border: 1px solid var(--line); border-radius: 3px; }
.vehicle-number { display: flex; align-items: center; gap: 10px; font-family: Consolas, monospace; font-size: 14px; letter-spacing: .5px; }
.country-label { font-size: 11px; font-family: inherit; }
.date-column { font-size: 12px; }
.row-actions { display: flex; justify-content: flex-end; gap: 6px; }
.row-actions .btn { padding: 5px 9px; font-size: 12px; min-height: 30px; }
.delete-btn { color: var(--muted); }
.delete-btn:hover { color: var(--danger); }
.list-error { margin: 12px 16px; }
.list-footer { display: flex; justify-content: space-between; border-top: 1px solid var(--line); padding: 10px 16px; font-size: 12px; }
.delete-dialog { width: min(400px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: 6px; color: var(--text); background: var(--panel); padding: 0; box-shadow: 0 12px 40px #0003; }
.delete-dialog::backdrop { background: #0006; }
.delete-body { padding: 18px; }
.delete-body p { margin: 0 0 8px; }
.delete-body p:last-child { margin-bottom: 0; }
@media (max-width: 640px) { .whitelist-tabs { flex-wrap: wrap; gap: 0; padding-bottom: 10px; } .tabs { width: 100%; } .list-toolbar { flex-wrap: wrap; } .search-field { width: 100%; } }
</style>
