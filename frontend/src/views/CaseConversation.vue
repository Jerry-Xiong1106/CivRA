<script setup>
import { computed, ref, watch, nextTick, inject } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { PANEL_TITLES, ROUTE_GUIDES, routePreference } from '../domain/conversation.js';
import { evidenceReview } from '../domain/evidence-review.js';
import { evidenceStatus } from '../domain/backendAdapter.js';
import ReviewView from './ReviewView.vue';
import EvidenceView from './EvidenceView.vue';
import ResearchView from './ResearchView.vue';
import OutcomesView from './OutcomesView.vue';
import DraftView from './DraftView.vue';
import ResultsView from './ResultsView.vue';
import ConversationRoutes from '../components/ConversationRoutes.vue';
import { renderMarkdown } from '../services/markdown.js';
const store = useWorkspaceStore(), { go, route } = useNavigation();
const input = ref(''), feed = ref(null), composer = ref(null), panelBody = ref(null);
const openUpload = inject('openUpload');
const panel = computed(() => Object.hasOwn(PANEL_TITLES, route.query.panel) ? route.query.panel : '');
const panels = { review: ReviewView, workflow: ConversationRoutes, evidence: EvidenceView, research: ResearchView, outcomes: OutcomesView, draft: DraftView, results: ResultsView };
const turns = computed(() => store.current.conversation || []);
const direction = computed(() => ROUTE_GUIDES.find((r) => r.id === routePreference(store.current))?.title);
const review = computed(() => store.apiMode ? evidenceStatus(store.current) : evidenceReview(store.current, store.readiness.signature));
const shortcuts = [
  ['法律依据', '我想了解法律依据', '⌕', 'research'], ['处理路径', '我想比较三种处理路径', '↗', 'workflow'],
  ['证据材料', '帮我整理证据材料', '▤', 'evidence'], ['结果分析', '看看结果分析', '◷', 'outcomes'], ['起诉状草稿', '我想准备起诉状草稿', '▧', 'draft']
];
function summary(id) {
  if (id === 'workflow') return direction.value ? `当前准备方向：${direction.value} · 可随时调整` : '协商解决 / 调解解决 / 诉讼准备';
  if (id === 'evidence') return `${store.current.files.length} 份材料 · ${review.value.demo ? '有预设重复与缺失提醒' : '内容待核验'}`;
  if (id === 'review') return store.readiness.intakeConfirmed ? '已确认的案情 · 可继续修改' : `${store.readiness.blockers.length} 项待补充或确认 · 可以稍后处理`;
  if (id === 'research') return '条款摘要 · 官方来源 · 原文入口';
  if (id === 'draft') return '法院模板来源 · 核对正式表述 · 草稿预览';
  if (id === 'outcomes') return store.apiMode ? '成立前提 · 不利因素 · 后端分析结果' : '成立前提 · 不利因素 · 模拟参考案例';
  return '本案已有记录';
}
async function send(value = input.value, preferredPanel = '') {
  const caseId = store.current.id;
  if (preferredPanel) {
    go(preferredPanel);
    if (store.apiMode && (store.wait || store.busy || store.current.server?.outbox)) return;
  }
  const reply = await store.sendConversation(value);
  if (!reply) return;
  if (store.current.id !== caseId) return;
  if (input.value === value) input.value = '';
  if (reply.panel === 'research') {
    store.ui.researchQuery = value;
    store.ui.researchSearched = true;
  }
  if (!preferredPanel && reply.panel) go(reply.panel);
}
function onKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); send();
  }
}
const stepWait = computed(() => store.apiMode && store.wait?.kind === 'step_confirmation' ? store.wait : null);
async function decide(decision) {
  const note = decision === 'revise' ? input.value : '';
  if (decision === 'revise' && !note.trim()) { store.notify('请在输入框写下需要修改的地方，再点“还需要修改”。'); composer.value?.focus(); return; }
  const result = await store.decideStep(decision, note);
  if (result && note && input.value === note) input.value = '';
}
function closePanel() { go('chat'); nextTick(() => composer.value?.focus()); }
watch(() => turns.value.length, async () => { await nextTick(); feed.value?.scrollTo({ top: feed.value.scrollHeight, behavior: 'smooth' }); });
watch(panel, async () => { await nextTick(); if (panelBody.value) panelBody.value.scrollTop = 0; });
watch(() => store.current.id, () => store.refreshCase(), { immediate: true });
</script>
<template>
  <div class="cc-workspace" :class="{ 'with-panel': panel }">
    <section class="cc-conversation" aria-label="案件对话">
      <div class="cc-context-bar"><span><i></i>{{ direction || '先聊聊你的问题' }}</span><button @click="go('review')">案情记录 <span>{{ store.readiness.completed }}/{{ store.readiness.required.length }}</span> ↗</button></div>
      <div ref="feed" class="cc-feed">
        <div class="cc-feed-inner">
          <div class="cc-welcome"><span class="eyebrow">CIVRA · CASE CONVERSATION</span><h1>从你关心的问题开始。</h1><p>查依据、理材料、想下一步。需要的内容，就在对话里展开。</p></div>
          <div v-if="store.current.narrative" class="cc-user-bubble">{{ store.current.narrative }}</div>
          <div class="cc-assistant-turn">
            <div class="assistant-label"><i>C</i> CivRA <span>案件助手</span></div>
            <p>{{ store.current.narrative ? '我会沿着这件事继续帮你整理。' : '可以先说说发生了什么，也可以直接问法律问题或上传材料。' }}你可以选择先谈一谈、请第三方调解，或准备诉讼。暂不确定也没关系。</p>
            <div class="cc-inline-routes"><button v-for="item in ROUTE_GUIDES" :key="item.id" @click="send(`我想了解${item.title}`)"><strong>{{ item.title }}</strong><span>{{ item.lead }} ↗</span></button></div>
            <button v-if="store.current.files.length" class="cc-receipt" @click="go('evidence')"><span class="cc-receipt-icon">▤</span><span><strong>本案材料</strong><small>{{ summary('evidence') }}</small></span><span>↗</span></button>
          </div>
          <details v-if="store.current.messages.length" class="cc-previous"><summary>此前的补充记录 · {{ store.current.messages.length }} 条</summary><p v-for="(message, i) in store.current.messages" :key="i">{{ message }}</p></details>
          <template v-for="turn in turns" :key="turn.id">
            <div v-if="turn.role === 'user'" class="cc-user-bubble">{{ turn.text }}</div>
            <article v-else class="cc-assistant-turn">
              <div class="assistant-label"><i>C</i> CivRA <span>案件助手</span></div>
              <div v-if="store.apiMode" class="cc-markdown" v-html="renderMarkdown(turn.text)"></div>
              <p v-else>{{ turn.text }}</p>
              <p v-if="turn.final?.partial" class="note">这是本次运行停止前保留的部分结果，仍有事项未完成。</p>
              <p v-if="turn.final?.open_issues?.length" class="note">待处理：{{ turn.final.open_issues.join('；') }}</p>
              <button v-for="card in (turn.panels || (turn.panel ? [turn.panel] : []))" :key="card" class="cc-receipt" :class="{ active: panel === card }" @click="go(card)"><span class="cc-receipt-icon">{{ card === 'evidence' ? '▤' : '↗' }}</span><span><strong>{{ PANEL_TITLES[card] || '案情图谱' }}</strong><small>{{ summary(card) }}</small></span><span>展开 ↗</span></button>
            </article>
          </template>
          <div v-if="stepWait && !store.busy" class="cc-step-confirm" role="group" aria-label="确认当前步骤">
            <p><strong>这一步完成了吗？</strong>{{ stepWait.summary }}</p>
            <div>
              <button class="button primary" @click="decide('done')">这一步完成了，继续下一步</button>
              <button class="button" @click="decide('revise')">还需要修改</button>
            </div>
            <small>需要修改时，先在下方输入框写清楚要改什么，再点“还需要修改”。</small>
          </div>
          <article v-if="store.apiMode && (store.busy || store.current.server?.error || store.current.server?.outbox)" class="cc-assistant-turn" aria-live="polite">
            <p>{{ store.current.server?.error || store.current.server?.progress || (store.current.server?.outbox ? '上一条提交的结果尚未确认，请重试同一条提交或刷新。' : '正在同步案件记录…') }}</p>
            <button v-if="store.current.server?.outbox && !store.current.server?.sending" class="link" @click="store.retryMessage()">重试这一条提交</button>
            <button v-if="!store.current.server?.sending" class="link" @click="store.refreshCase()">刷新会话</button>
          </article>
        </div>
      </div>
      <div class="cc-compose-wrap">
        <div class="cc-shortcuts" aria-label="对话快捷提问"><button v-for="[label, text, icon, target] in shortcuts" :key="label" @click="send(text, target)"><span>{{ icon }}</span>{{ label }}</button></div>
        <form class="cc-composer" @submit.prevent="send()">
          <textarea ref="composer" v-model="input" aria-label="向 CivRA 发送消息" placeholder="告诉我你想解决什么，例如：我想先和对方协商，需要准备什么？" maxlength="10000" @keydown="onKey"></textarea>
          <div><button type="button" class="link" :disabled="store.busy" @click="openUpload">＋ 添加材料</button><span>{{ stepWait ? '直接发送会作为修改意见' : 'Enter 发送 · Shift + Enter 换行' }}</span><button class="cc-send" type="submit" :disabled="!input.trim() || store.busy || !!store.current.server?.outbox" aria-label="发送消息">↑</button></div>
        </form>
        <p class="cc-demo-note">{{ store.apiMode ? '后端案件 · 回答与成果由 Agent 返回，上传后需解析与核对' : '本地历史 / 演示记录 · 新建案件可使用后端，已有数据不会自动上传' }}</p>
      </div>
    </section>
    <aside v-if="panel" class="cc-work-panel" :aria-label="PANEL_TITLES[panel]" @keydown.esc="closePanel">
      <header><div><small>本案工作面板</small><h2>{{ PANEL_TITLES[panel] }}</h2></div><button aria-label="收起工作面板" @click="closePanel">×</button></header>
      <div ref="panelBody" class="cc-panel-body"><component :is="panels[panel]" :key="panel" /></div>
    </aside>
  </div>
</template>
