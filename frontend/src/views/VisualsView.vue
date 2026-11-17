<script setup>
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import P from '../domain/policy.js';
import { routeName } from '../domain/presentation.js';
import PageHeading from '../components/PageHeading.vue';
import ReferenceCases from '../components/ReferenceCases.vue';
const { current: c, readiness: r } = storeToRefs(useWorkspaceStore());
</script>
<template>
  <PageHeading
    kicker="CASE MAP"
    title="一条时间线，串起案件进展"
    sub="事实、所选路径与实际程序进度分别保留。这里展示当前本地记录。"
  />
  <div class="notice">
    <strong>实际程序阶段</strong>　{{
      P.phaseLabels[c.fields.phase?.value] || '待确认'
    }}<br /><strong>当前处理意向</strong>　{{ routeName(c) }}　{{
      !r.routeConfirmed && c.route ? '（需重新确认）' : ''
    }}
  </div>
  <div class="timeline">
    <div>
      <strong>{{ c.fields.when?.value || '发生时间待补充' }}</strong>
      <p>{{ c.fields.story?.value || '尚未记录主要经过' }}</p>
    </div>
    <div v-for="(entry, i) in c.audit" :key="i">
      <strong>{{ entry.time }}</strong>
      <p>{{ entry.text }}</p>
    </div>
    <div>
      <strong>目前记录</strong>
      <p>
        {{ c.files.length }} 份材料 ·
        {{ c.draft ? '已生成演示草稿' : '尚未生成草稿' }}
      </p>
    </div>
  </div>
  <ReferenceCases statistics />
</template>
