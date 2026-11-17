<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import IntakeView from './IntakeView.vue';
import ReviewView from './ReviewView.vue';
import WorkflowView from './WorkflowView.vue';
import EvidenceView from './EvidenceView.vue';
import ResearchView from './ResearchView.vue';
import OutcomesView from './OutcomesView.vue';
import DraftView from './DraftView.vue';
import VisualsView from './VisualsView.vue';
import ResultsView from './ResultsView.vue';
const store = useWorkspaceStore(),
  { section } = useNavigation();
const views = {
  chat: IntakeView,
  review: ReviewView,
  workflow: WorkflowView,
  evidence: EvidenceView,
  research: ResearchView,
  outcomes: OutcomesView,
  draft: DraftView,
  visuals: VisualsView,
  results: ResultsView
};
const view = computed(() => views[section.value] || IntakeView);
</script>
<template>
  <div class="page">
    <component :is="view" :key="`${store.current.id}:${section}`" />
  </div>
</template>
