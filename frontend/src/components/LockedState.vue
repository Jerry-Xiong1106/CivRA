<script setup>
import LockIcon from './LockIcon.vue';
import { useNavigation } from '../composables/navigation.js';
defineProps({
  title: String,
  reasons: Array,
  target: { type: String, default: 'chat' }
});
const { go } = useNavigation();
</script>
<template>
  <div class="empty-state">
    <div class="empty-symbol"><LockIcon /></div>
    <h2>{{ title }}</h2>
    <p>先确认本次输出所依据的信息。你仍可继续对话、查依据或整理材料。</p>
    <ul class="missing-list">
      <li v-for="reason in reasons" :key="reason">{{ reason }}</li>
    </ul>
    <button class="button primary" @click="go(target)">
      {{ target === 'workflow' ? '前往选择维权路径' : '前往补充并确认案情' }} →
    </button>
  </div>
</template>
