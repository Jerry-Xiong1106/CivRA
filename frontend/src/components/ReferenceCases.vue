<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import P from '../domain/policy.js';
import REF from '../domain/reference-data.js';
import R from '../domain/reference-logic.js';
const props = defineProps({ statistics: Boolean }),
  store = useWorkspaceStore();
const filters = reactive({ region: 'all', court: 'all', year: 'all' }),
  limit = ref(6);
const all = computed(() =>
  REF.samples.filter((s) => s.domain === store.current.fields.dispute?.value)
);
const samples = computed(() =>
  props.statistics ? R.filterCases(all.value, filters) : all.value.slice(0, 3)
);
const rows = computed(() => R.summarize(samples.value, 'outcome'));
const controls = [
  {
    id: 'region',
    label: '地区',
    all: '全部地区',
    options: ['广州示例', '深圳示例']
  },
  {
    id: 'court',
    label: '法院',
    all: '全部法院',
    options: ['示例法院甲', '示例法院乙']
  },
  {
    id: 'year',
    label: '年份',
    all: '全部年份',
    options: ['2024', '2025', '2026']
  }
];
watch(filters, () => {
  limit.value = 6;
});
function reset() {
  Object.assign(filters, { region: 'all', court: 'all', year: 'all' });
  limit.value = 6;
}
</script>
<template>
  <component
    :is="statistics ? 'details' : 'section'"
    class="panel case-reference-section"
  >
    <summary v-if="statistics">
      参考案例与样本统计 <span class="note">展开查看</span>
    </summary>
    <h3 v-else>参考案例</h3>
    <div :class="{ 'reference-body': statistics }">
      <p class="reference-note">
        以下为模拟案例，用于演示参考方式，真实案例检索待接入。案例差异需结合本案核对，样本比例不表示胜诉概率。
      </p>
      <template v-if="statistics"
        ><div class="reference-filters">
          <label v-for="control in controls" :key="control.id"
            >{{ control.label
            }}<select
              v-model="filters[control.id]"
              :aria-label="`参考案例${control.label}`"
            >
              <option value="all">{{ control.all }}</option>
              <option
                v-for="value in control.options"
                :key="value"
                :value="value"
              >
                {{ value }}
              </option>
            </select></label
          >
        </div>
        <div class="row between wrap">
          <p class="reference-note">
            {{ P.domains[store.current.fields.dispute?.value] || '当前类型' }} ·
            当前筛选 {{ samples.length }} 条
          </p>
          <button class="link" @click="reset">重置筛选</button>
        </div>
        <div v-if="samples.length" class="reference-chart">
          <h3>模拟样本结果分布</h3>
          <div v-for="row in rows" :key="row.label" class="reference-bar">
            <div class="row between">
              <span>{{ row.label }}</span
              ><span>{{ row.count }} 条 · {{ row.percent.toFixed(1) }}%</span>
            </div>
            <div class="reference-track">
              <span :style="{ width: `${row.percent}%` }"></span>
            </div>
          </div>
        </div>
      </template>
      <template v-if="samples.length"
        ><div class="reference-cards">
          <article
            v-for="sample in samples.slice(0, statistics ? limit : 3)"
            :key="sample.id"
            class="reference-card"
          >
            <div class="row between wrap">
              <span class="pill warn">模拟案例</span
              ><small>{{ sample.id }}</small>
            </div>
            <h3>{{ sample.issue }}</h3>
            <p>{{ sample.facts }}</p>
            <div class="reference-meta">
              {{ sample.region }} · {{ sample.court }} · {{ sample.year }} 年
            </div>
            <button
              class="link"
              @click="store.openDialog('reference', { id: sample.id })"
            >
              查看理由与本案差异 ↗
            </button>
          </article>
        </div>
        <div v-if="statistics && samples.length > limit" class="footer-action">
          <button class="button small" @click="limit += 6">查看更多案例</button>
        </div></template
      >
      <div v-else class="reference-empty">
        {{
          all.length
            ? '当前筛选没有匹配样本，可以调整或重置筛选。'
            : '当前纠纷类型尚未配置参考案例样例。'
        }}
      </div>
    </div>
  </component>
</template>
