<script setup lang="ts">
const t = useVideoCopy()
</script>

<template>
  <div>
    <div class="page-header">
      <h1 class="page-heading">{{ t.cameraTitle }}</h1>
      <p class="page-description">{{ t.cameraDesc }}</p>
    </div>
    <StreamViewer />
  </div>
</template>

<style scoped>
.page-header { margin-bottom: 20px; }
</style>
