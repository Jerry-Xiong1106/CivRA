<script setup>
import { useWorkspaceStore } from '../stores/workspace.js';
import PageHeading from '../components/PageHeading.vue';
import LockedState from '../components/LockedState.vue';
import ReferenceCases from '../components/ReferenceCases.vue';
const store = useWorkspaceStore();
const cards = [
  ['需要证明什么', '展示各项请求成立所需核实的事实，以及目前的来源。'],
  ['哪些材料仍需补充', '展示证据缺口、冲突和对方可能提出的争议。'],
  ['哪些因素会改变分析', '说明结论依赖的前提，更新事实后重新分析。']
];
</script>
<template>
  <template v-if="!store.readiness.intakeConfirmed"
    ><PageHeading
      kicker="SCENARIOS"
      title="结果分析"
      sub="核对案情后，再围绕具体请求分析不同情形。" /><LockedState
      title="先确认分析所依据的案情"
      :reasons="['补充并确认案情摘要']"
  /></template>
  <template v-else
    ><PageHeading
      kicker="SCENARIOS"
      title="围绕事实，分析不同情形"
      sub="正式接入后由事实、材料与检索依据支撑分析。此处展示结果页结构。" />
    <div class="notice warn">
      这是界面结构样例，尚未对本案进行法律分析或预测结果。
    </div>
    <div class="card-grid">
      <article
        v-for="[title, description] in cards"
        :key="title"
        class="route-card"
      >
        <h3>{{ title }}</h3>
        <p>{{ description }}</p>
        <span class="pill">待后端分析结果</span>
      </article>
    </div>
    <ReferenceCases
  /></template>
</template>
