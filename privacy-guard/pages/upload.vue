<script setup lang="ts">
const t = useVideoCopy()
const fileName = ref('')
const videoUrl = ref('')
const busy = ref(false)

function onUploaded(file: File) {
  if (busy.value) return
  const oldUrl = videoUrl.value
  videoUrl.value = URL.createObjectURL(file)
  fileName.value = file.name
  if (oldUrl) nextTick(() => URL.revokeObjectURL(oldUrl))
}
onBeforeUnmount(() => { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value) })
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-heading">{{ t.uploadTitle }}</h1>
      <p class="page-description">{{ t.uploadDesc }}</p>
    </div>
    <VideoUploader :compact="Boolean(videoUrl)" :disabled="busy" @uploaded="onUploaded" />
    <div v-if="videoUrl" class="video-file-workspace">
      <p class="video-filename" :title="fileName">{{ fileName }}</p>
      <VideoCompare :key="videoUrl" :src="videoUrl" :file-name="fileName" @busy="busy = $event" />
    </div>
  </div>
</template>

<style scoped>
.page-header { margin-bottom: 20px; }
.video-file-workspace { margin-top: 18px; }
.video-filename { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 12px; font-weight: 500; }
</style>
