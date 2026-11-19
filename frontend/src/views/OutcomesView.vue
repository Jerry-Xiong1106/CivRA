<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import PageHeading from '../components/PageHeading.vue';
import ReferenceCases from '../components/ReferenceCases.vue';
import { latestOutput, outputData } from '../domain/backendAdapter.js';
const store = useWorkspaceStore();
const placeholders = [
  ['需要证明什么', '展示各项请求成立所需核实的事实，以及目前的来源。'],
  ['哪些材料仍需补充', '展示证据缺口、冲突和对方可能提出的争议。'],
  ['哪些因素会改变分析', '说明结论依赖的前提，更新事实后重新分析。']
];
const demo = computed(() => {
  const c = store.current,
    result = c.demoAnalysis;
  return c.synthetic && store.readiness.intakeConfirmed &&
    result?.basis === store.readiness.signature &&
    JSON.stringify(result.files) === JSON.stringify(c.files.map((f) => f.id))
    ? result
    : null;
});
const backendOutput = computed(() => latestOutput(store.current, 'analysis'));
const cards = computed(() => {
  if (!store.apiMode) return demo.value?.cards || placeholders;
  const data = outputData(backendOutput.value);
  const scenarios = (data.claims || []).flatMap(claim => claim.scenarios || []);
  return scenarios.length ? scenarios.map(s => [s.statement || s.kind, (s.origin === 'template' ? '通用条件框架，尚未应用于本案。' : '候选分析，仍需核对。') + (s.conditions || []).join('；')]) : placeholders;
});
</script>
<template>
  <div class="classic-ui">
  <PageHeading
      kicker="SCENARIOS"
      title="结果分析"
      :sub="store.apiMode ? '根据后端返回的事实、材料与条件分析展示；具体分析也可在对话中查看。' : '正式接入后由事实、材料与检索依据支撑分析。此处展示结果页结构。'" />
    <div class="notice warn">
      {{
        store.apiMode ? (backendOutput ? (backendOutput.case_version === store.current.server.snapshot.case_version ? '以下为后端保存的分析，仍需核对依据及条件。' : '以下分析依据的是历史案情版本，需要重新分析。') : '当前默认 Agent 尚未接入结构化结果分析工具，以下保留分析位置，不表示已经完成本案分析。') : demo
          ? '以下内容为虚构案件的预设演示，用于体验分析页面；未运行真实 Agent、证据审查或结果预测。'
          : '这是界面结构样例，尚未对本案进行法律分析或预测结果。'
      }}
    </div>
    <p v-if="!store.readiness.intakeConfirmed" class="notice">可以先了解分析会考虑什么。你的案情仍待核对，下面不是针对本案作出的结论。</p>
    <div class="cu-section-label"><h2>不同情形下的分析</h2><span>围绕事实、证据与成立前提</span></div>
    <div class="card-grid cu-outcome-grid">
      <article
        v-for="([title, description], index) in cards"
        :key="title"
        class="route-card"
      >
        <div class="cu-scenario-index">情形 {{ String(index + 1).padStart(2, '0') }}</div>
        <h3>{{ title }}</h3>
        <p>{{ description }}</p>
        <span class="pill">{{ store.apiMode && backendOutput ? '后端保存的结果' : demo ? '预设模拟内容' : '待后端分析结果' }}</span>
      </article>
    </div>
    <ReferenceCases />
  </div>
</template>
