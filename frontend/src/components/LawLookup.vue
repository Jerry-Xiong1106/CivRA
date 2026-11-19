<script setup>
import { computed, watch } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { researchSources, currentWait } from '../domain/backendAdapter.js';
import { renderMarkdown } from '../services/markdown.js';
const store = useWorkspaceStore();
const { isConsult } = useNavigation();
const apiMode = computed(() => isConsult.value || store.apiMode);
const active = computed(() => isConsult.value ? store.state.consultation : store.current);
const sources = computed(() => researchSources(active.value));
const answer = computed(() => active.value.server?.run?.final?.answer);
const busy = computed(() => !!(active.value.server?.sending || active.value.server?.pending || active.value.server?.syncing));
const waiting = computed(() => currentWait(active.value));
watch(() => active.value.id, () => store.refreshCase(active.value), { immediate: true });
const matches = computed(() =>
  /调解|和解|开庭|执行/.test(store.ui.researchQuery)
);
async function search() {
  store.ui.researchQuery = store.ui.researchQuery.trim();
  if (store.ui.researchQuery) {
    store.ui.researchSearched = true;
    if (apiMode.value) await store.searchLegal(store.ui.researchQuery, isConsult.value);
  }
}
function example() {
  store.ui.researchQuery = '开庭以后还能调解吗？';
  search();
}
</script>
<template>
  <div class="classic-ui law-lookup">
  <div class="cu-search">
    <form @submit.prevent="search">
      <input
        v-model="store.ui.researchQuery"
        type="text"
        aria-label="法律咨询问题"
        placeholder="例如：开庭以后还能调解吗？"
        required
      /><button class="button primary" :disabled="busy || !!active.server?.outbox">查询法律依据 →</button>
    </form>
  </div>
  <div class="cu-search-hint"><span>试着问</span>
  <button class="link" @click="example">
    试问：开庭以后还能调解吗？
  </button></div>
  <template v-if="apiMode">
    <p v-if="active.server?.error" class="notice warn">{{ active.server.error }} <button class="link" @click="store.refreshCase(active)">刷新会话</button></p>
    <p v-if="active.server?.outbox && !busy" class="notice"><button class="link" @click="store.retryMessage(active)">重试上一条查询</button></p>
    <p v-if="busy" class="notice" role="status">正在检索与整理，请稍候…</p>
    <article v-if="waiting" class="cu-source-card"><h3>助手需要你补充或确认</h3><p v-for="question in waiting.questions || []" :key="question.question_id">{{ question.text }}</p><p v-if="waiting.kind === 'step_confirmation'">{{ waiting.summary }}</p><p class="note">{{ waiting.kind === 'step_confirmation' ? '请在对话中确认这一步是否完成。' : '可在上面的输入框填写回答，继续本次咨询。' }}</p></article>
    <article v-if="answer" class="cu-source-card"><h3>助手返回的说明</h3><div class="cc-markdown" v-html="renderMarkdown(answer)"></div></article>
    <article v-for="source in sources" :key="source.key" class="cu-source-card">
      <div class="row between wrap"><h3>{{ source.title }}</h3><span class="pill good">{{ source.location }}</span></div>
      <p class="cu-source-excerpt">{{ source.citation ? '回答中的引用位置：' + source.citation : '该来源由后端随回答返回；请打开原文核对适用条件。' }}</p>
      <dl class="cu-source-meta">
        <div><dt>来源编号</dt><dd>{{ source.source_id }}</dd></div>
        <div><dt>文件版本</dt><dd>{{ source.source_version }}</dd></div>
        <div><dt>原文定位</dt><dd>{{ source.location }}</dd></div>
      </dl>
      <div class="cu-source-footer"><span>来源元数据 · 当前接口不提供检索原文全文</span><a v-if="source.href" :href="source.href" target="_blank" rel="noopener noreferrer">前往法律原文 ↗</a><span v-else>后端未提供原文链接</span></div>
    </article>
    <div v-if="!sources.length && !busy" class="cu-panel cu-empty"><h3>从问题到法律原文</h3><p>{{ answer ? '本次回答没有返回结构化法律来源。请继续要求说明出处，不能将无来源回答视为已核验依据。' : '输入问题后，展示后端返回的条款编号、文件版本和原文入口。' }}</p></div>
  </template>
  <template v-else-if="store.ui.researchSearched"
    ><article v-if="matches" class="cu-source-card">
      <div class="row between wrap">
        <h3>民事诉讼法 · 调解与执行和解</h3>
        <span class="pill good">第145条 · 第179条 · 第241条</span>
      </div>
      <p class="cu-source-excerpt">
        判决前仍可进行调解。二审调解与执行和解分别适用相应程序，所以“实际程序阶段”和“希望如何处理”应分别记录。
      </p>
      <dl class="cu-source-meta">
        <div><dt>文件版本</dt><dd>2023年修正</dd></div>
        <div><dt>原文来源</dt><dd>最高人民法院知识产权法庭</dd></div>
        <div><dt>展示类型</dt><dd>官方来源样例 · 条款摘要</dd></div>
      </dl>
      <div class="cu-source-footer">
        <span>阅读完整条款，核对适用条件</span>
        <a
          target="_blank"
          rel="noopener"
          href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html"
          >前往法律原文，核对适用条款 ↗</a
        >
      </div>
    </article>
    <div v-else class="notice">
      本版仅内置“调解时点”的来源样例。正式接入后，此处调用现有 RAG
      并展示原文、来源版本和定位信息。本次没有生成模拟检索结论。
    </div></template
  >
  <div v-else class="cu-panel cu-empty">
    <h3>从问题到法律原文</h3>
    <p>
      输入问题 → 查看相关条款和解释 →
      打开原文核对出处。需要处理具体案件时，再进入案情收集。
    </p>
  </div>
  </div>
</template>
