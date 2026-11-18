<script setup>
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import CaseConversation from './CaseConversation.vue';
import VisualsView from './VisualsView.vue';
const store = useWorkspaceStore(),
  { section } = useNavigation();
</script>
<template>
  <div v-if="section === 'visuals'" class="page"><VisualsView :key="store.current.id" /></div>
  <CaseConversation v-else :key="store.current.id" />
</template>
