<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
const store = useWorkspaceStore();
const matches = computed(() =>
  /调解|和解|开庭|执行/.test(store.ui.researchQuery)
);
function search() {
  store.ui.researchQuery = store.ui.researchQuery.trim();
  if (store.ui.researchQuery) store.ui.researchSearched = true;
}
function example() {
  store.ui.researchQuery = '开庭以后还能调解吗？';
  search();
}
</script>
<template>
  <div class="consult-box">
    <form @submit.prevent="search">
      <input
        v-model="store.ui.researchQuery"
        aria-label="法律咨询问题"
        placeholder="例如：开庭以后还能调解吗？"
        required
      /><button class="button primary">查询法律依据 →</button>
    </form>
  </div>
  <button class="button small" @click="example">
    试问：开庭以后还能调解吗？
  </button>
  <template v-if="store.ui.researchSearched"
    ><article v-if="matches" class="legal-card">
      <div class="row between">
        <h3>民事诉讼法 · 调解与执行和解</h3>
        <span class="pill good">官方来源样例</span>
      </div>
      <p>
        判决前仍可进行调解。二审调解与执行和解分别适用相应程序，所以“实际程序阶段”和“希望如何处理”应分别记录。
      </p>
      <small
        >相关条款　第145条、第179条、第241条<br />文件版本　2023年修正<br />来源　最高人民法院知识产权法庭</small
      >
      <p>
        <a
          target="_blank"
          rel="noopener"
          href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html"
          >前往法律原文，核对适用条款 ↗</a
        >
      </p>
    </article>
    <div v-else class="notice">
      本版仅内置“调解时点”的来源样例。正式接入后，此处调用现有 RAG
      并展示原文、来源版本和定位信息。本次没有生成模拟检索结论。
    </div></template
  >
  <div v-else class="panel">
    <h3>从问题到法律原文</h3>
    <p>
      输入问题 → 查看相关条款和解释 →
      打开原文核对出处。需要处理具体案件时，再进入案情收集。
    </p>
  </div>
</template>
