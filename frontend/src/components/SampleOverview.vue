<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import P from '../domain/policy.js';
import REF from '../domain/reference-data.js';
import R from '../domain/reference-logic.js';
const store = useWorkspaceStore();
const filters = reactive({ region: 'all', year: 'all', court: 'all' });
const limit = ref(3);
const all = computed(() => store.apiMode ? [] : REF.samples.filter((sample) => sample.domain === store.current.fields.dispute?.value));
const samples = computed(() => R.filterCases(all.value, filters));
const rows = computed(() => R.summarize(samples.value, 'outcome'));
const controls = [
  { id: 'region', label: '地区', all: '全部示例地区', options: ['广州示例', '深圳示例'] },
  { id: 'year', label: '年份', all: '全部年份', options: ['2024', '2025', '2026'] },
  { id: 'court', label: '法院', all: '全部示例法院', options: ['示例法院甲', '示例法院乙'] }
];
watch(filters, () => { limit.value = 3; });
function reset() {
  Object.assign(filters, { region: 'all', year: 'all', court: 'all' });
  limit.value = 3;
}
</script>
<template>
  <section class="cu-sample-board" aria-label="参考案例样本统计">
    <div class="cu-sample-toolbar">
      <span>展示维度示例 · {{ P.domains[store.current.fields.dispute?.value] || '当前纠纷类型' }}</span>
      <label v-for="control in controls" :key="control.id">
        <select v-model="filters[control.id]" :aria-label="`参考案例${control.label}`">
          <option value="all">{{ control.all }}</option>
          <option v-for="option in control.options" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <button v-if="Object.values(filters).some(value => value !== 'all')" class="link" @click="reset">重置筛选</button>
    </div>
    <div class="cu-sample-columns">
      <section class="cu-panel">
        <div class="cu-panel-head"><h2>样本结果分布</h2><span class="pill">{{ store.apiMode ? '真实数据待接入' : '模拟数据' }}</span></div>
        <div class="cu-panel-body">
          <div class="cu-sample-total"><strong>{{ samples.length }}</strong><span>条当前筛选样本</span></div>
          <div v-for="row in rows" :key="row.label" class="cu-sample-bar">
            <span>{{ row.label }}</span><div><i :style="{ width: `${row.percent}%` }"></i></div><small>{{ row.count }}</small>
          </div>
          <p v-if="!samples.length" class="cu-sample-note">当前筛选没有匹配样本，请调整筛选。</p>
          <p class="cu-sample-note">真实样本来源、法院与裁判结果数据待接入。样本比例不表示本案胜诉概率。</p>
        </div>
      </section>
      <section class="cu-panel">
        <div class="cu-panel-head"><h2>相似案件展示方式</h2><span class="pill">{{ store.apiMode ? '类案检索待接入' : '模拟案例' }}</span></div>
        <div class="cu-panel-body cu-sample-list">
          <article v-for="(sample, index) in samples.slice(0, limit)" :key="sample.id" class="cu-sample-item">
            <h3>模拟案例 {{ String(index + 1).padStart(2, '0') }} · {{ sample.issue }}</h3>
            <p>{{ P.domains[sample.domain] }} · {{ sample.region }} · {{ sample.court }} · {{ sample.year }}年</p>
            <div class="row between wrap"><span class="pill">{{ sample.outcome }}</span><button class="link" @click="store.openDialog('reference', { id: sample.id })">查看理由与本案差异 ↗</button></div>
          </article>
          <p v-if="!samples.length" class="cu-sample-note">{{ all.length ? '当前筛选没有匹配样本，可以调整或重置筛选。' : '当前纠纷类型尚未配置参考案例样例。' }}</p>
          <div v-if="samples.length > limit" class="cu-sample-more"><span>已显示 {{ limit }} / {{ samples.length }} 条</span><button class="link" @click="limit += 3">查看更多案例 ↓</button></div>
        </div>
      </section>
    </div>
  </section>
</template>
