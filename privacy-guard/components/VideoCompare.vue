<script setup lang="ts">
const props = defineProps<{ src: string; fileName?: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
</script>

<template>
  <VideoRedactor :src="props.src" :file-name="props.fileName" @busy="emit('busy', $event)" />
</template>
