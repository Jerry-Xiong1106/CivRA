<script setup>
defineProps({ kicker: String, title: String, sub: String });
</script>
<template>
  <div class="page-title">
    <div class="row between">
      <div>
        <div class="eyebrow">{{ kicker }}</div>
        <h1 style="margin-top: 8px">{{ title }}</h1>
      </div>
      <slot />
    </div>
    <p>{{ sub }}</p>
  </div>
</template>
