<script setup lang="ts">
type Region = { x: number; y: number; width: number; height: number }
type Point = { x: number; y: number }
const props = defineProps<{ src?: string; stream?: MediaStream; fileName?: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const t = useVideoCopy()
const video = ref<HTMLVideoElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const ready = ref(false)
const dimensions = ref({ width: 0, height: 0 })
const error = ref('')
const regions = ref<Region[]>([])
const drawing = ref(false)
const draft = ref<Region | null>(null)
const preparing = ref(false)
const recording = ref(false)
const pausedRecording = ref(false)
const progress = ref(0)
const duration = ref(0)
const elapsed = ref(0)
const elapsedLabel = computed(() => Math.floor(elapsed.value / 60) + ':' + String(Math.floor(elapsed.value % 60)).padStart(2, '0'))
const includeAudio = ref(true)
const exportUrl = ref('')
const exportAvailable = ref(false)
const live = computed(() => Boolean(props.stream))
const busy = computed(() => preparing.value || recording.value)
const mediaStyle = computed(() => ({ maxWidth: dimensions.value.height ? dimensions.value.width / dimensions.value.height * 540 + 'px' : '100%' }))
const downloadName = computed(() => (props.fileName?.replace(/\.[^.]+$/, '') || 'camera') + '-redacted.webm')
let frameId = 0
let pointerId: number | null = null
let startPoint: Point | null = null
let recorder: MediaRecorder | null = null
let capture: MediaStream | null = null
let audioContext: AudioContext | null = null
let audioSource: MediaElementAudioSourceNode | null = null
let audioOutput: MediaStreamAudioDestinationNode | null = null
let mounted = true
let exportGeneration = 0
let cancelled = false
let chunks: BlobPart[] = []
let recordingBytes = 0
let removeSeekListeners: (() => void) | null = null
watch(busy, value => emit('busy', value), { immediate: true })

function invalidateOutput() {
  if (exportUrl.value) URL.revokeObjectURL(exportUrl.value)
  exportUrl.value = ''
}
function render() {
  const surface = canvas.value
  const source = video.value
  if (!surface || !source || source.readyState < 2) return
  const context = surface.getContext('2d', { alpha: false })
  if (!context) return
  context.drawImage(source, 0, 0, surface.width, surface.height)
  context.fillStyle = '#000'
  for (const region of regions.value) {
    const x = Math.floor(region.x * surface.width)
    const y = Math.floor(region.y * surface.height)
    context.fillRect(x, y, Math.ceil(region.width * surface.width) + 1, Math.ceil(region.height * surface.height) + 1)
  }
  if (draft.value) {
    const region = draft.value
    context.fillRect(region.x * surface.width, region.y * surface.height, region.width * surface.width, region.height * surface.height)
    context.strokeStyle = '#6497c6'
    context.lineWidth = Math.max(2, surface.width / 500)
    context.strokeRect(region.x * surface.width, region.y * surface.height, region.width * surface.width, region.height * surface.height)
  }
}
function animate() {
  render()
  frameId = requestAnimationFrame(animate)
}
function onMetadata() {
  const source = video.value
  const surface = canvas.value
  if (!source || !surface || !source.videoWidth || !source.videoHeight) return
  surface.width = source.videoWidth
  surface.height = source.videoHeight
  dimensions.value = { width: source.videoWidth, height: source.videoHeight }
  ready.value = true
  onTimeUpdate()
  render()
}
function point(event: PointerEvent): Point {
  const bounds = canvas.value!.getBoundingClientRect()
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  }
}
function onPointerDown(event: PointerEvent) {
  if (!drawing.value || busy.value || !ready.value || event.button !== 0) return
  if (!live.value) video.value?.pause()
  startPoint = point(event)
  pointerId = event.pointerId
  canvas.value?.setPointerCapture(event.pointerId)
  draft.value = { ...startPoint, width: 0, height: 0 }
}
function onPointerMove(event: PointerEvent) {
  if (!startPoint || pointerId !== event.pointerId) return
  const current = point(event)
  draft.value = {
    x: Math.min(startPoint.x, current.x), y: Math.min(startPoint.y, current.y),
    width: Math.abs(startPoint.x - current.x), height: Math.abs(startPoint.y - current.y),
  }
}
function onPointerUp(event: PointerEvent) {
  if (pointerId !== event.pointerId) return
  onPointerMove(event)
  if (draft.value && draft.value.width * canvas.value!.width >= 4 && draft.value.height * canvas.value!.height >= 4) {
    regions.value.push({ ...draft.value })
    invalidateOutput()
    drawing.value = false
  }
  cancelPointer()
}
function cancelPointer() {
  if (pointerId !== null && canvas.value?.hasPointerCapture(pointerId)) canvas.value.releasePointerCapture(pointerId)
  pointerId = null
  startPoint = null
  draft.value = null
}
function toggleDrawing() {
  cancelPointer()
  drawing.value = !drawing.value
}
function clearRegions(lastOnly = false) {
  if (busy.value) return
  if (lastOnly) regions.value.pop()
  else regions.value = []
  cancelPointer()
  drawing.value = false
  invalidateOutput()
}
function onTimeUpdate() {
  const source = video.value
  if (source) {
    elapsed.value = Number.isFinite(source.currentTime) ? Math.max(0, source.currentTime) : 0
    duration.value = Number.isFinite(source.duration) ? Math.max(0, source.duration) : 0
    if (duration.value > 0) progress.value = Math.min(100, Math.floor(source.currentTime / duration.value * 100))
  }
}
function onPlaybackError() {
  error.value = t.value.playbackError
  ready.value = false
  stopExport(true)
}
function cleanCapture() {
  capture?.getTracks().forEach(track => track.stop())
  capture = null
  if (audioOutput && audioSource) {
    try { audioSource.disconnect(audioOutput) } catch { /* Already disconnected. */ }
  }
  audioOutput = null
}
function stopExport(discard = false) {
  exportGeneration++
  cancelled ||= discard
  removeSeekListeners?.()
  removeSeekListeners = null
  if (recorder && recorder.state !== 'inactive') {
    recorder.stop()
  } else {
    cleanCapture()
    preparing.value = false
    recording.value = false
    pausedRecording.value = false
  }
  if (!live.value) video.value?.pause()
}
function seekToStart(source: HTMLVideoElement) {
  if (source.currentTime < 0.02) return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => { cleanup(); reject(new Error('seek timeout')) }, 10000)
    function cleanup() {
      window.clearTimeout(timer)
      source.removeEventListener('seeked', done)
      source.removeEventListener('error', fail)
      removeSeekListeners = null
    }
    function done() { cleanup(); resolve() }
    function fail() { cleanup(); reject(new Error('seek failed')) }
    removeSeekListeners = () => { cleanup(); reject(new Error('cancelled')) }
    source.addEventListener('seeked', done, { once: true })
    source.addEventListener('error', fail, { once: true })
    source.currentTime = 0
  })
}
async function startExport() {
  if (!ready.value || busy.value || !video.value || !canvas.value) return
  if (!exportAvailable.value) { error.value = t.value.exportUnavailable; return }
  const generation = ++exportGeneration
  const source = video.value
  error.value = ''
  preparing.value = true
  cancelled = false
  chunks = []
  recordingBytes = 0
  drawing.value = false
  cancelPointer()
  invalidateOutput()
  try {
    if (!live.value) {
      source.pause()
      source.playbackRate = 1
      await seekToStart(source)
      if (generation !== exportGeneration || !mounted) return
    }
    render()
    capture = canvas.value.captureStream(30)
    if (!live.value && includeAudio.value) {
      try {
        if (!audioContext) audioContext = new AudioContext()
        if (!audioSource) {
          audioSource = audioContext.createMediaElementSource(source)
          audioSource.connect(audioContext.destination)
        }
        await audioContext.resume()
        if (generation !== exportGeneration || !mounted) { cleanCapture(); return }
        audioOutput = audioContext.createMediaStreamDestination()
        audioSource.connect(audioOutput)
        for (const track of audioOutput.stream.getAudioTracks()) capture.addTrack(track)
      } catch { throw new Error('audio unavailable') }
    }
    const mimeType = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
      .find(type => MediaRecorder.isTypeSupported(type))
    if (!mimeType) throw new Error('recording unavailable')
    const activeRecorder = new MediaRecorder(capture, { mimeType, videoBitsPerSecond: 5_000_000 })
    recorder = activeRecorder
    activeRecorder.ondataavailable = event => {
      if (!event.data.size || cancelled) return
      recordingBytes += event.data.size
      if (recordingBytes > 512 * 1024 ** 2) { error.value = t.value.exportTooLarge; stopExport(true); return }
      chunks.push(event.data)
    }
    activeRecorder.onerror = () => { error.value = t.value.exportError; stopExport(true) }
    activeRecorder.onstop = () => {
      if (mounted && !cancelled && chunks.length) {
        const output = new Blob(chunks, { type: activeRecorder.mimeType })
        exportUrl.value = URL.createObjectURL(output)
      }
      chunks = []
      cleanCapture()
      recorder = null
      preparing.value = false
      recording.value = false
      pausedRecording.value = false
    }
    activeRecorder.start(250)
    recording.value = true
    preparing.value = false
    await source.play()
    onVisibilityChange()
  } catch (cause) {
    if (!mounted || generation !== exportGeneration) return
    error.value = cause instanceof Error && cause.message === 'audio unavailable' ? t.value.audioError : t.value.exportError
    stopExport(true)
  }
}
function onEnded() {
  render()
  onTimeUpdate()
  if (recording.value && !live.value) stopExport()
}
function onVisibilityChange() {
  if (!recording.value || !recorder) return
  if (document.hidden && recorder.state === 'recording') {
    recorder.pause()
    video.value?.pause()
    pausedRecording.value = true
  }
}
async function resumeExport() {
  if (!recorder || recorder.state !== 'paused' || !video.value) return
  try {
    await video.value.play()
    recorder.resume()
    pausedRecording.value = false
  } catch { error.value = t.value.exportError; stopExport(true) }
}
onMounted(async () => {
  exportAvailable.value = typeof MediaRecorder !== 'undefined' && typeof HTMLCanvasElement.prototype.captureStream === 'function'
  document.addEventListener('visibilitychange', onVisibilityChange)
  frameId = requestAnimationFrame(animate)
  if (props.stream && video.value) {
    video.value.srcObject = props.stream
    try { await video.value.play() } catch { error.value = t.value.playbackError }
  }
})
onBeforeUnmount(() => {
  mounted = false
  stopExport(true)
  cancelAnimationFrame(frameId)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  invalidateOutput()
  if (video.value) { video.value.pause(); video.value.srcObject = null }
  void audioContext?.close().catch(() => {})
})
</script>

<template>
  <section class="redactor">
    <div class="toolbar region-toolbar">
      <button type="button" class="btn" :class="{ 'btn-primary': drawing }" :disabled="!ready || busy" :aria-pressed="drawing" @click="toggleDrawing">{{ drawing ? t.cancelDrawing : t.addRegion }}</button>
      <button type="button" class="btn" :disabled="!regions.length || busy" @click="clearRegions(true)">{{ t.undo }}</button>
      <button type="button" class="btn" :disabled="!regions.length || busy" @click="clearRegions()">{{ t.clear }}</button>
      <span class="muted">{{ t.regionCount }}: {{ regions.length }}</span>
    </div>
    <p v-if="drawing" class="selection-hint" role="status">{{ t.drawHint }}</p>
    <div class="video-pair">
      <div class="app-panel video-panel">
        <h2 class="panel-heading">{{ t.original }}</h2>
        <div class="video-stage">
          <video ref="video" :src="props.src" :style="mediaStyle" :controls="!live && !busy" :muted="live" playsinline preload="metadata"
            :aria-label="t.original" @loadedmetadata="onMetadata" @loadeddata="render" @seeked="render" @timeupdate="onTimeUpdate" @durationchange="onTimeUpdate" @ended="onEnded" @error="onPlaybackError" />
        </div>
      </div>
      <div class="app-panel video-panel">
        <h2 class="panel-heading">{{ t.result }}</h2>
        <div class="video-stage">
          <canvas ref="canvas" :style="mediaStyle" :aria-label="t.result" :class="{ selecting: drawing }"
            @pointerdown.prevent="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="cancelPointer" />
        </div>
      </div>
    </div>
    <div class="app-panel export-panel">
      <div class="toolbar">
        <button v-if="!busy" type="button" class="btn btn-primary" :disabled="!ready || !exportAvailable" @click="startExport">{{ live ? t.cameraRecord : t.export }}</button>
        <button v-else type="button" class="btn" @click="stopExport(!live)">{{ live ? t.cameraRecordStop : t.cancelExport }}</button>
        <label v-if="!live" class="audio-option"><input v-model="includeAudio" type="checkbox" :disabled="busy" @change="invalidateOutput" />{{ t.audio }}</label>
        <a v-if="exportUrl" class="btn btn-primary" :href="exportUrl" :download="downloadName">{{ t.download }}</a>
        <span v-if="busy" class="status-text" role="status">{{ preparing ? t.preparing : t.recording }}<template v-if="!live && !preparing">: {{ duration ? progress + '%' : elapsedLabel }}</template></span>
      </div>
      <p v-if="!live" class="muted export-hint">{{ t.exportHint }}</p>
      <div v-if="pausedRecording" class="toolbar">
        <span class="muted">{{ t.exportBackground }}</span>
        <button type="button" class="btn" @click="resumeExport">{{ t.resume }}</button>
      </div>
      <p v-if="!exportAvailable" class="muted export-hint">{{ t.exportUnavailable }}</p>
    </div>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.redactor { display: grid; gap: 14px; }
.region-toolbar { justify-content: flex-start; }
.selection-hint { color: var(--accent); margin: 0; }
.video-pair { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
.video-panel { overflow: hidden; min-width: 0; padding: 0; }
.video-panel .panel-heading { padding: 12px 14px; margin: 0; border-bottom: 1px solid var(--line); }
.video-stage { display: grid; align-items: center; justify-items: center; min-height: 180px; background: #16191d; }
video, canvas { display: block; width: 100%; height: auto; margin: auto; }
canvas.selecting { cursor: crosshair; touch-action: none; }
.export-panel { padding: 14px; }
.audio-option { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; }
.export-hint { margin: 10px 0 0; font-size: 12px; }
@media (max-width: 900px) { .video-pair { grid-template-columns: minmax(0, 1fr); } }
</style>

