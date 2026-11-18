<script setup>
import { ref, computed, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import P from '../domain/policy.js';
import { routeName } from '../domain/presentation.js';
import PageHeading from '../components/PageHeading.vue';
import SampleOverview from '../components/SampleOverview.vue';
import { latestOutput, outputData, valueText, knownFieldLabel } from '../domain/backendAdapter.js';
const store = useWorkspaceStore();
const { current: c, readiness: r } = storeToRefs(store);
const activeView = ref('current');
const timelineOutput = computed(() => latestOutput(c.value, 'visualization', o => outputData(o).chart_type === 'timeline'));
const timeline = computed(() => timelineOutput.value ? outputData(timelineOutput.value).records.map(f => ({ id: f.id, date: f.date, title: knownFieldLabel(f.field), text: f.confirmation_status === 'confirmed' ? '用户已确认的日期记录' : '尚待核对的日期记录' })) : (c.value.server?.snapshot?.facts || []).filter(f => f.value_type === 'date' && !f.superseded_by).map(f => ({ id: f.fact_id, date: valueText(f.value), title: knownFieldLabel(f.field), text: f.confirmation_status === 'confirmed' ? '用户已确认' : '待确认' })));
const links = computed(() => (c.value.server?.snapshot?.claims || []).map(claim => ({ id: claim.claim_id, title: claim.description, text: (claim.evidence_ids || []).map(id => c.value.server.snapshot.evidence.find(e => e.evidence_id === id)?.file_name || id).join('、') || '尚未关联证据' })));
watch(() => c.value.id, () => store.refreshCase(), { immediate: true });
</script>
<template>
  <div class="classic-ui">
  <PageHeading
    kicker="CASE MAP"
    title="案情图谱"
    sub="沿时间看经过，沿证据看主张。当前案件与参考样本分别查看。"
  ><span class="pill">{{ activeView === 'samples' ? (store.apiMode ? '真实类案待接入' : '模拟样本') : '当前案件' }}</span></PageHeading>
  <div class="cu-view-switch" role="group" aria-label="案情图谱展示方式">
    <button :class="{ active: activeView === 'current' }" :aria-pressed="activeView === 'current'" @click="activeView = 'current'">当前案件</button>
    <button :class="{ active: activeView === 'samples' }" :aria-pressed="activeView === 'samples'" @click="activeView = 'samples'">{{ P.domains[c.fields.dispute?.value] || '参考案例' }}样本</button>
  </div>
  <div v-show="activeView === 'current'">
  <div class="cu-case-status">
    <div><span>实际程序阶段</span><strong>{{
      P.phaseLabels[c.fields.phase?.value] || '待确认'
    }}</strong></div><div><span>当前处理意向</span><strong>{{ routeName(c) }}　{{
      !r.routeConfirmed && c.route ? '（需重新确认）' : ''
    }}</strong></div>
  </div>
  <section class="cu-panel">
    <div class="cu-panel-head"><h2>案件时间线</h2><button v-if="store.apiMode" class="link" :disabled="store.busy" @click="store.sendConversation('请按现有记录生成本案时间线与证据关系，不补造日期或关联。')">整理本案图谱 ↗</button><span v-else>当前案件的事实与操作记录</span></div>
    <ol class="cu-timeline">
    <template v-if="store.apiMode">
      <li v-for="entry in timeline" :key="entry.id"><div class="cu-timeline-date">{{ entry.date }}</div><span class="cu-timeline-node" aria-hidden="true"></span><div class="cu-timeline-copy"><h3>{{ entry.title }}</h3><p>{{ entry.text }}</p></div></li>
      <li v-if="!timeline.length"><div class="cu-timeline-date">待补充</div><span class="cu-timeline-node" aria-hidden="true"></span><div class="cu-timeline-copy"><p>后端尚未记录可展示的日期事实，可继续对话补充。</p></div></li>
      <li v-for="entry in links" :key="entry.id"><div class="cu-timeline-date">主张与证据</div><span class="cu-timeline-node" aria-hidden="true"></span><div class="cu-timeline-copy"><h3>{{ entry.title }}</h3><p>{{ entry.text }}；关联不表示已获司法认定。</p></div></li>
    </template>
    <li v-else>
      <div class="cu-timeline-date">{{ c.fields.when?.value || '时间待补充' }}</div>
      <span class="cu-timeline-node" aria-hidden="true"></span>
      <div class="cu-timeline-copy"><h3>事件经过</h3><p>{{ c.fields.story?.value || '尚未记录主要经过' }}</p></div>
    </li>
    <li v-for="(entry, i) in c.audit" :key="i">
      <div class="cu-timeline-date">{{ entry.time }}</div>
      <span class="cu-timeline-node" aria-hidden="true"></span>
      <div class="cu-timeline-copy"><p>{{ entry.text }}</p></div>
    </li>
    <li class="is-current">
      <div class="cu-timeline-date">目前记录</div>
      <span class="cu-timeline-node" aria-hidden="true"></span>
      <div class="cu-timeline-copy"><h3>当前案件资料</h3><p>
        {{ c.files.length }} 份材料 ·
        {{ c.draft ? (store.apiMode ? '已保存后端草稿' : '已生成演示草稿') : '尚未生成草稿' }}
      </p></div>
    </li>
    </ol>
  </section>
  </div>
  <SampleOverview v-show="activeView === 'samples'" />
  </div>
</template>
