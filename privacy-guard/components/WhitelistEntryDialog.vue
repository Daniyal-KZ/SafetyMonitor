<script setup lang="ts">
import type { WhitelistEntry } from '~/shared/types/whitelist'
const emit = defineEmits<{ saved: [] }>()
const t = useWhitelistCopy()
const { state, createPerson, createPlate, update, addPhotos, removePhoto, message } = useWhitelist()
const dialog = ref<HTMLDialogElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const id = ref<string | null>(null)
const kind = ref<'person' | 'plate'>('person')
const label = ref('')
const notes = ref('')
const plate = ref('')
const country = ref('KZ')
const enabled = ref(true)
const busy = ref(false)
const error = ref('')
const files = ref<File[]>([])
const previews = ref<string[]>([])
const existing = computed(() => state.value.entries.find(entry => entry.id === id.value))
function clearFiles() {
  previews.value.forEach(url => URL.revokeObjectURL(url))
  previews.value = []; files.value = []
}
function close() {
  if (busy.value) return
  dialog.value?.close(); clearFiles(); error.value = ''
}
async function open(selectedKind: 'person' | 'plate', entry?: WhitelistEntry) {
  clearFiles(); error.value = ''
  id.value = entry?.id || null; kind.value = selectedKind
  label.value = entry?.label || ''; notes.value = entry?.notes || ''
  plate.value = entry?.plate || ''; country.value = entry?.country || 'KZ'
  enabled.value = entry?.enabled ?? true
  dialog.value?.showModal()
  await nextTick(); nameInput.value?.focus()
}
defineExpose({ open })
function checkPhotos(selected: File[], maximum = 5): string {
  if (!selected.length) return t.value.requirePhoto
  if (selected.length > maximum) return t.value.photoLimit(maximum)
  if (selected.some(file => !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) return t.value.imageTypes
  if (selected.some(file => file.size > 10 * 1024 * 1024)) return t.value.imageSize
  return ''
}
async function choosePhotos(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files || []); input.value = ''
  if (!selected.length) return
  error.value = checkPhotos(selected, 5 - (existing.value?.photos.length || 0))
  if (error.value) return
  if (id.value) {
    busy.value = true
    try { await addPhotos(id.value, selected) }
    catch (cause) { error.value = message(cause) }
    finally { busy.value = false }
  } else {
    clearFiles(); files.value = selected
    previews.value = selected.map(file => URL.createObjectURL(file))
  }
}
async function deletePhoto(photoId: string) {
  if (!id.value || busy.value) return
  busy.value = true; error.value = ''
  try { await removePhoto(id.value, photoId) }
  catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function submit() {
  if (busy.value) return
  error.value = ''
  if (!id.value && kind.value === 'person') {
    error.value = checkPhotos(files.value)
    if (error.value) return
  }
  busy.value = true
  try {
    if (id.value) await update(id.value, { label: label.value.trim(), notes: notes.value.trim(), enabled: enabled.value,
      ...(kind.value === 'plate' ? { plate: plate.value, country: country.value as 'KZ' | 'RU' | 'OTHER' } : {}) })
    else if (kind.value === 'person') await createPerson(label.value, notes.value, files.value)
    else await createPlate(label.value, notes.value, plate.value, country.value)
    busy.value = false; close(); emit('saved')
  } catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
function backdropClick(event: MouseEvent) {
  if (!dialog.value || event.target !== dialog.value) return
  const rect = dialog.value.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close()
}
onBeforeUnmount(clearFiles)
</script>

<template>
  <dialog ref="dialog" class="record-dialog" aria-labelledby="entry-dialog-title" @cancel.prevent="close" @click="backdropClick">
    <form @submit.prevent="submit">
      <div class="dialog-header"><h2 id="entry-dialog-title">{{ id ? t.editTitle : kind === 'person' ? t.addPerson : t.addVehicle }}</h2><button type="button" class="icon-btn" :aria-label="t.close" :disabled="busy" @click="close">×</button></div>
      <fieldset class="entry-fields" :disabled="busy">
        <label class="field-label" for="entry-name">{{ kind === 'person' ? t.name : t.label }}</label>
        <input id="entry-name" ref="nameInput" v-model="label" class="field" type="text" required maxlength="80" autocomplete="off" :placeholder="kind === 'person' ? t.personPlaceholder : t.vehiclePlaceholder" />
        <div v-if="kind === 'plate'" class="plate-fields">
          <div><label class="field-label" for="entry-plate">{{ t.registration }}</label><input id="entry-plate" v-model="plate" class="field plate-input" required minlength="4" maxlength="20" autocomplete="off" placeholder="123 ABC 02" /></div>
          <div><label class="field-label" for="entry-country">{{ t.country }}</label><select id="entry-country" v-model="country" class="field"><option value="KZ">{{ t.kazakhstan }}</option><option value="RU">{{ t.russia }}</option><option value="OTHER">{{ t.otherCountry }}</option></select></div>
        </div>
        <template v-else>
          <div class="photo-heading"><span class="field-label">{{ t.photos }}</span><span class="muted photo-count">{{ existing?.photos.length || previews.length }} / 5</span></div>
          <div v-if="existing?.photos.length || previews.length" class="photo-list">
            <div v-for="(photo, index) in existing?.photos || []" :key="photo.id" class="photo-item"><img :src="photo.url" :alt="t.photoAlt(index + 1, label)" /><button type="button" class="photo-remove" :disabled="(existing?.photos.length || 0) <= 1" :aria-label="t.removePhotoLabel(index + 1)" @click="deletePhoto(photo.id)">×</button></div>
            <div v-for="(url, index) in previews" :key="url" class="photo-item"><img :src="url" :alt="t.selectedPhotoAlt(index + 1)" /></div>
          </div>
          <label v-if="(existing?.photos.length || 0) < 5" class="photo-picker btn">{{ id ? t.addPhotos : previews.length ? t.replacePhotos : t.pickPhotos }}<input type="file" accept="image/jpeg,image/png,image/webp" multiple :disabled="busy" @change="choosePhotos" /></label>
          <p class="photo-note muted">{{ t.photoFormats }}</p>
        </template>
        <label class="field-label" for="entry-notes">{{ t.notes }}</label><textarea id="entry-notes" v-model="notes" class="field notes-field" maxlength="500" rows="3" />
        <label v-if="id" class="enabled-field"><input v-model="enabled" type="checkbox" /> {{ t.entryEnabled }}</label>
      </fieldset>
      <div v-if="error" class="error-message dialog-error" role="alert">{{ error }}</div>
      <div class="dialog-footer"><button type="button" class="btn" :disabled="busy" @click="close">{{ t.cancel }}</button><button type="submit" class="btn btn-primary" :disabled="busy">{{ busy ? t.saving : t.save }}</button></div>
    </form>
  </dialog>
</template>

<style scoped>
.record-dialog { padding: 0; width: min(560px, calc(100vw - 32px)); max-height: calc(100dvh - 40px); border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--text); box-shadow: 0 12px 40px #0003; }
.record-dialog::backdrop { background: #0006; }
.entry-fields { margin: 0; padding: 20px; border: 0; display: grid; gap: 8px; }
.entry-fields > .field-label:not(:first-child) { margin-top: 8px; }
.plate-fields { display: grid; grid-template-columns: 1fr 150px; gap: 12px; margin-top: 8px; }
.plate-input { text-transform: uppercase; font-family: Consolas, monospace; letter-spacing: 1px; }
.notes-field { resize: vertical; min-height: 72px; }
.enabled-field { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.photo-heading { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }
.photo-count, .photo-note { font-size: 12px; }
.photo-note { margin: 0; }
.photo-list { display: flex; flex-wrap: wrap; gap: 8px; }
.photo-item { position: relative; width: 88px; height: 104px; border: 1px solid var(--line); border-radius: 3px; overflow: hidden; background: var(--bg); }
.photo-item img { width: 100%; height: 100%; object-fit: cover; }
.photo-remove { position: absolute; top: 3px; right: 3px; width: 22px; height: 22px; border: 1px solid #fff9; border-radius: 3px; background: #242b34d9; color: white; font-size: 17px; line-height: 18px; }
.photo-remove:disabled { display: none; }
.photo-picker { width: fit-content; position: relative; overflow: hidden; cursor: pointer; }
.photo-picker input { position: absolute; inset: 0; width: 100%; opacity: 0; cursor: pointer; }
.photo-picker:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.dialog-error { margin: 0 20px 16px; }
@media (max-width: 440px) { .plate-fields { grid-template-columns: 1fr; } }
</style>
