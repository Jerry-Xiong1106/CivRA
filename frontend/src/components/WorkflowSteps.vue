<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
const store = useWorkspaceStore(),
  { section, go } = useNavigation();
const steps = [
  ['chat', '收集案情'],
  ['review', '确认摘要'],
  ['workflow', '选择路径'],
  ['workflow', '办理事项'],
  ['results', '核对成果']
];
const active = computed(() =>
  section.value === 'review'
    ? 1
    : !store.readiness.intakeConfirmed
      ? 0
      : !store.readiness.routeConfirmed
        ? 2
        : section.value === 'results'
          ? 4
          : 3
);
const done = computed(() => [
  store.readiness.blockers.length === 0,
  store.readiness.intakeConfirmed,
  store.readiness.routeConfirmed,
  false,
  false
]);
</script>
<template>
  <div class="steps" aria-label="案件办理步骤">
    <template v-for="([view, label], i) in steps" :key="label"
      ><div v-if="i" class="step-line"></div>
      <button
        class="step"
        :class="{ active: i === active, done: i !== active && done[i] }"
        :aria-current="i === active ? 'step' : 'false'"
        @click="go(view)"
      >
        <i>{{ done[i] && i !== active ? '✓' : i + 1 }}</i
        >{{ label }}
      </button></template
    >
  </div>
</template>
