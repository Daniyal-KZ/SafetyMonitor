<script setup lang="ts">
const props = defineProps<{ compact?: boolean; disabled?: boolean }>()
const emit = defineEmits<{ uploaded: [file: File] }>()
const t = useVideoCopy()
const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const error = ref('')

function select(file?: File) {
  if (!file || props.disabled) return
  error.value = ''
  if (!file.size) error.value = t.value.emptyFile
  else if (file.size > 2 * 1024 ** 3) error.value = t.value.largeFile
  else if (!file.type.startsWith('video/') && !/\.(mp4|m4v|mov|webm|ogv|ogg|mkv|avi)$/i.test(file.name)) error.value = t.value.invalidFile
  else emit('uploaded', file)
}

function onInput(event: Event) {
  const target = event.target as HTMLInputElement
  select(target.files?.[0])
  target.value = ''
}

function onDrop(event: DragEvent) {
  dragging.value = false
  select(event.dataTransfer?.files?.[0])
}
</script>

<template>
  <div>
    <input ref="input" class="sr-only" type="file" accept="video/*,.mov,.mkv,.avi" :aria-label="t.pick" :disabled="props.disabled" @change="onInput" />
    <div v-if="props.compact" class="toolbar">
      <span class="muted">{{ t.local }}</span>
      <button type="button" class="btn" :disabled="props.disabled" @click="input?.click()">{{ t.replace }}</button>
    </div>
    <div v-else class="app-panel video-dropzone" :class="{ dragging }"
      @dragenter.prevent="dragging = true" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
      <p>{{ t.drop }}</p>
      <button type="button" class="btn btn-primary" :disabled="props.disabled" @click="input?.click()">{{ t.pick }}</button>
      <p class="muted">{{ t.formats }}</p>
    </div>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.video-dropzone { display: grid; justify-items: center; gap: 16px; padding: 40px 20px; border-style: dashed; }
.video-dropzone.dragging { border-color: var(--accent); background: var(--accent-soft); }
</style>
