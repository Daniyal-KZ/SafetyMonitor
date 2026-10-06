<script setup lang="ts">
const t = useVideoCopy()
const stream = shallowRef<MediaStream | null>(null)
const pending = ref(false)
const error = ref('')
const devices = ref<MediaDeviceInfo[]>([])
const selectedDevice = ref('')
const busy = ref(false)
let generation = 0
let disposed = false
const active = computed(() => Boolean(stream.value))

async function refreshDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return
  try {
    const available = await navigator.mediaDevices.enumerateDevices()
    if (!disposed) devices.value = available.filter(device => device.kind === 'videoinput')
  } catch { /* Device selection is optional; the default camera remains available. */ }
}
async function start() {
  if (pending.value || active.value) return
  const request = ++generation
  error.value = ''
  if (!navigator.mediaDevices?.getUserMedia) { error.value = t.value.cameraUnavailable; return }
  pending.value = true
  try {
    const connected = await navigator.mediaDevices.getUserMedia({
      video: selectedDevice.value ? { deviceId: { exact: selectedDevice.value } } : { facingMode: 'user' },
      audio: false,
    })
    if (disposed || request !== generation) {
      connected.getTracks().forEach(track => track.stop())
      return
    }
    stream.value = connected
    connected.getVideoTracks()[0]?.addEventListener('ended', stop, { once: true })
    await refreshDevices()
  } catch (cause) {
    if (disposed || request !== generation) return
    const name = cause instanceof DOMException ? cause.name : ''
    error.value = name === 'NotAllowedError' || name === 'SecurityError' ? t.value.cameraDenied
      : name === 'NotFoundError' || name === 'OverconstrainedError' ? t.value.cameraMissing : t.value.cameraError
    stream.value = null
  } finally {
    if (request === generation) pending.value = false
  }
}
function stop() {
  generation++
  stream.value?.getTracks().forEach(track => track.stop())
  stream.value = null
  pending.value = false
  busy.value = false
}
onMounted(() => {
  void refreshDevices()
  navigator.mediaDevices?.addEventListener('devicechange', refreshDevices)
})
onBeforeUnmount(() => {
  disposed = true
  stop()
  navigator.mediaDevices?.removeEventListener('devicechange', refreshDevices)
})
</script>

<template>
  <section class="camera-workspace">
    <div class="app-panel camera-toolbar">
      <div class="toolbar">
        <label class="camera-device">
          <span class="field-label">{{ t.cameraSelect }}</span>
          <select v-model="selectedDevice" class="field" :disabled="active || pending">
            <option value="">{{ t.cameraDefault }}</option>
            <option v-for="(device, index) in devices" :key="device.deviceId || index" :value="device.deviceId">{{ device.label || t.cameraName + ' ' + (index + 1) }}</option>
          </select>
        </label>
        <button type="button" class="btn" :class="{ 'btn-primary': !active && !pending }" :disabled="busy" @click="active || pending ? stop() : start()">
          {{ pending ? t.cameraPending : active ? t.cameraStop : t.cameraStart }}
        </button>
        <span class="status-text" role="status">{{ active ? t.cameraOn : t.cameraOff }}</span>
      </div>
    </div>
    <VideoRedactor v-if="stream" :key="stream.id" :stream="stream" @busy="busy = $event" />
    <div v-else class="app-panel empty-state camera-empty">{{ t.cameraOff }}</div>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.camera-workspace { display: grid; gap: 16px; }
.camera-toolbar { padding: 14px; }
.camera-device { display: flex; align-items: center; gap: 10px; }
.camera-device .field { width: min(280px, 60vw); }
.camera-empty { min-height: 300px; display: grid; place-items: center; background: #16191d; color: #bcc2ca; }
@media (max-width: 520px) { .camera-device { align-items: stretch; flex-direction: column; width: 100%; } .camera-device .field { width: 100%; } }
</style>
