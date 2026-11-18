<script setup>
import { computed, nextTick, reactive, ref } from 'vue';
import P from '../domain/policy.js';
import { createDemoCases } from '../domain/demo.js';
import { templateSources } from '../domain/template-sources.js';
import REF from '../domain/reference-data.js';
import { downloadMarkdown } from '../services/download.js';

function freshCase() {
  const item = createDemoCases()[0];
  delete item.fields.phase;
  item.intakeConfirmation = null;
  item.routeConfirmation = null;
  item.route = null;
  item.draft = null;
  return item;
}
const caseData = reactive(freshCase());
const readiness = computed(() => P.assess(caseData));
const routeOptions = computed(() => P.routeOptions(caseData));
const routeTitle = computed(() => routeOptions.value.find(option => option.id === caseData.route)?.title);
const source = templateSources.find(item => item.id === 'traffic');
const samples = REF.samples.filter(item => item.domain === 'traffic').slice(0, 3);
const panel = ref('evidence');
const input = ref('');
const stream = ref(null);
const mobileMenu = ref(false);
const approved = ref(false);
const draftStep = ref('template');
const draftText = ref('');
const initialWording = '请求就与本次事故相关的医疗费用进行赔偿。演示金额合计2,400元；责任比例、已付款项及具体请求金额仍待核对。';
const wording = ref(initialWording);
const titleMap = { facts: '案情摘要', evidence: '证据核对', research: '法律依据', workflow: '维权路径', outcomes: '结果分析', draft: '起诉状草稿', visuals: '案情图谱' };
const intro = [
  { role: 'user', text: '我想处理交通事故的赔偿。医疗票据是扫描 PDF，里面好像有重复页，应该先做什么？' },
  { role: 'assistant', text: '先把重复票据核对清楚。我为你打开了证据核对面板，列出这份演示材料中的重复和补充提醒。\n\n继续选择处理路径前，还需要确认：这件事目前是否已经起诉或开庭？', panel: 'evidence', actions: ['not_filed', 'hearing', 'research'] }
];
const messages = ref(intro.map(item => ({ ...item })));
const actionLabels = { not_filed: '尚未起诉', hearing: '已经开庭', confirm: '确认这份案情摘要', evidence: '先核对证据', research: '先查法律依据', workflow: '比较处理路径', draft: '准备起诉状', outcomes: '看看分析思路', visuals: '看看案情图谱', facts: '查看案情摘要' };
const prompts = ['扫描件有重复票据', '开庭后还能调解吗？', '我想准备起诉状', '看看结果分析', '查看案情图谱'];
function append(role, text, extra = {}) {
  messages.value.push({ role, text, ...extra });
  nextTick(() => stream.value?.scrollTo({ top: stream.value.scrollHeight, behavior: 'smooth' }));
}
function reply(text, view, actions = []) {
  if (view) panel.value = view;
  append('assistant', text, { panel: view, actions });
}
function setPhase(value, echo = true) {
  if (echo) append('user', value === 'hearing' ? '这件事已经开庭了。' : '还没有起诉。');
  caseData.fields.phase = { value, status: 'confirmed' };
  caseData.intakeConfirmation = null;
  caseData.routeConfirmation = null;
  caseData.route = null;
  approved.value = false;
  draftText.value = '';
  draftStep.value = 'template';
  reply(`已记录“${P.phaseLabels[value]}”。我把这份虚构案件的预置资料整理成了摘要，请先核对。确认之前，我不会替你选择处理路径。`, 'facts', ['confirm', 'evidence']);
}
function confirmFacts() {
  if (readiness.value.blockers.length) {
    reply('还缺少当前程序阶段，请先告诉我是否已经起诉或开庭。', 'facts', ['not_filed', 'hearing']);
    return;
  }
  caseData.intakeConfirmation = { basis: P.basis(caseData), actor: 'demo-user' };
  append('user', '我已核对并确认这份演示案情摘要。');
  reply('案情摘要已确认。现在可以比较处理路径，我已经打开选项。请你明确选择；我不会自动替你决定。', 'workflow');
}
function requestPanel(view) {
  if (['workflow', 'draft', 'outcomes'].includes(view) && !readiness.value.intakeConfirmed) {
    reply('这一步需要先确认案情。我为你打开摘要；还有未回答的信息时，请先补充，再点击确认。', 'facts', readiness.value.blockers.length ? ['not_filed', 'hearing'] : ['confirm']);
    return;
  }
  if (view === 'draft' && !readiness.value.canDraft) {
    reply(caseData.fields.phase?.value === 'hearing'
      ? '当前已经开庭，这个起诉状工作台不适用于重新准备首次起诉状。可以继续核对材料、了解调解，实际程序阶段会保留。'
      : '准备起诉状前，需要你明确选择诉讼准备。我已打开路径面板，请先核对并选择。', 'workflow');
    return;
  }
  const text = {
    facts: '案情摘要已打开。这里区分已记录的事实和仍待核对的信息。',
    evidence: '我先打开证据核对面板，提醒你核对重复扫描页及待补充材料。这里展示的是虚构示例，未实际运行 OCR。',
    research: '我已打开法律依据，展示“调解时点”的官方来源样例。你可以打开原文核对适用条款。',
    workflow: '我把当前阶段可用的路径放在右侧，请比较后明确选择。',
    outcomes: '我已打开分析思路，分别列出需要证明的事实、材料缺口和会改变分析的条件，并保留参考案例。',
    draft: '进入条件已满足。我已打开适用模板，先看来源，再核对正式表述；确认后才生成草稿。',
    visuals: '我把本次对话中的案情、确认和路径选择串成时间线，便于回看。'
  };
  reply(text[view], view, view === 'evidence' ? ['facts', 'research'] : []);
}
function selectRoute(id) {
  if (!readiness.value.canSelectRoute || !routeOptions.value.some(option => option.id === id)) return;
  caseData.route = id;
  caseData.routeConfirmation = { basis: P.basis(caseData), actor: 'demo-user' };
  append('user', `我确认选择“${routeTitle.value}”。`);
  if (readiness.value.canDraft) {
    reply('已记录你的诉讼准备意向，实际阶段仍是尚未起诉。我为你打开适用模板；接下来请核对表述。', 'draft');
  } else {
    reply(`已记录“${routeTitle.value}”，实际程序阶段仍是“${P.phaseLabels[caseData.fields.phase.value]}”。下一步可以先整理材料与沟通要点。`, 'workflow', ['evidence', 'research', 'outcomes']);
  }
}
function act(action) {
  if (['not_filed', 'hearing'].includes(action)) return setPhase(action);
  if (action === 'confirm') return confirmFacts();
  append('user', actionLabels[action]);
  requestPanel(action);
}
function send(text = input.value) {
  const clean = text.trim();
  if (!clean) return;
  input.value = '';
  append('user', clean);
  if (/已经开庭|已开庭/.test(clean)) return setPhase('hearing', false);
  if (/没有起诉|尚未起诉|还没起诉/.test(clean)) return setPhase('not_filed', false);
  const view = /起诉状|模板|文书/.test(clean) ? 'draft'
    : /重复|票据|材料|证据/.test(clean) ? 'evidence'
    : /法条|法律|依据|调解/.test(clean) ? 'research'
    : /分析|结果|支持/.test(clean) ? 'outcomes'
    : /图谱|时间线/.test(clean) ? 'visuals'
    : /路径|起诉|协商/.test(clean) ? 'workflow'
    : 'facts';
  requestPanel(view);
}
function composerKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
function generate() {
  if (!readiness.value.canDraft || !approved.value || !wording.value.trim()) return;
  draftText.value = `民事起诉状（虚构演示草稿）\n\n原告：［待补充］\n被告：［待核对］\n\n诉讼请求\n${wording.value.trim()}\n\n事实与理由\n${caseData.fields.story.value}\n\n法律依据与证据：［待检索并核对］\n法院、签署与日期：［待补充］`;
  approved.value = false;
  draftStep.value = 'preview';
  append('user', '我已核对正式表述，确认生成演示草稿。');
  reply('演示草稿已生成，未知信息仍保留为待补充。请核对后再下载；这一步没有向法院提交文书。', 'draft', ['outcomes', 'visuals']);
}
function restart() {
  Object.keys(caseData).forEach(key => delete caseData[key]);
  Object.assign(caseData, freshCase());
  messages.value = intro.map(item => ({ ...item }));
  panel.value = 'evidence';
  draftStep.value = 'template';
  draftText.value = '';
  approved.value = false;
  input.value = '';
  mobileMenu.value = false;
  wording.value = initialWording;
}
</script>
<template>
  <div class="ag-layout" :class="{ 'has-panel': panel, 'menu-open': mobileMenu }">
    <aside class="ag-sidebar">
      <a class="ag-brand" href="/agent-preview.html"><i>C</i><span>CivRA<small>CIVIL LAW ASSISTANT</small></span></a>
      <button class="ag-new" @click="restart">＋　重新体验演示</button>
      <span class="ag-side-label">我的案件</span>
      <button class="ag-case" @click="mobileMenu = false"><strong>交通事故 · 医疗费用核对</strong><small>虚构案件 · 对话式办理</small></button>
      <div class="ag-progress"><span class="ag-side-label">本案进度</span>
        <p :class="{ done: !readiness.blockers.length }"><i>{{ readiness.blockers.length ? '1' : '✓' }}</i>收集案情</p>
        <p :class="{ done: readiness.intakeConfirmed }"><i>{{ readiness.intakeConfirmed ? '✓' : '2' }}</i>你确认案情摘要</p>
        <p :class="{ done: readiness.routeConfirmed }"><i>{{ readiness.routeConfirmed ? '✓' : '3' }}</i>你选择处理路径</p>
        <p :class="{ done: draftText }"><i>{{ draftText ? '✓' : '4' }}</i>核对并生成成果</p>
      </div>
      <div class="ag-side-foot"><a href="/">↗ 对比固定流程版</a><p>本地交互演示<br>独立于原工作区，不保存本次演示</p></div>
    </aside>
    <button v-if="mobileMenu" class="ag-backdrop" aria-label="关闭案件列表" @click="mobileMenu = false"></button>
    <main class="ag-main">
      <header class="ag-header"><button class="ag-menu" aria-label="展开案件列表" @click="mobileMenu = !mobileMenu">☰</button><div><strong>交通事故 · 医疗费用核对</strong><small>让对话引导下一步，需要的面板随时出现</small></div><span class="ag-badge">对话式 Agent 示例</span></header>
      <div class="ag-status"><span class="ag-status-dot"></span>{{ readiness.intakeConfirmed ? (readiness.routeConfirmed ? `已选择 ${routeTitle}` : '案情已确认 · 等你选择路径') : '正在整理案情 · 关键步骤由你确认' }}<button v-if="!panel" @click="panel = 'facts'">查看案情 ↗</button></div>
      <div ref="stream" class="ag-stream" aria-live="polite">
        <div class="ag-conversation-label">今天 · 虚构演示案件</div>
        <article v-for="(message, index) in messages" :key="index" class="ag-message" :class="message.role">
          <div v-if="message.role === 'assistant'" class="ag-agent-label"><i>C</i>CivRA <span>下一步建议</span></div>
          <p>{{ message.text }}</p>
          <button v-if="message.panel" class="ag-panel-receipt" @click="panel = message.panel"><span>▤</span><div><strong>{{ titleMap[message.panel] }}</strong><small>在本案面板中查看与核对</small></div><span>↗</span></button>
          <div v-if="message.actions?.length && index === messages.length - 1" class="ag-message-actions"><button v-for="action in message.actions" :key="action" @click="act(action)">{{ actionLabels[action] }} →</button></div>
        </article>
      </div>
      <div class="ag-composer-area"><div class="ag-prompts"><button v-for="prompt in prompts" :key="prompt" @click="send(prompt)">{{ prompt }}</button></div>
        <form class="ag-composer" @submit.prevent="send()"><textarea v-model="input" aria-label="告诉 CivRA 你想做什么" placeholder="直接告诉我你想做什么，例如：我想准备起诉状…" rows="2" @keydown="composerKey"></textarea><div><span>Enter 发送 · Shift + Enter 换行</span><button type="submit" :disabled="!input.trim()" aria-label="发送消息">↑</button></div></form>
        <p class="ag-demo-note">预置虚构资料 · 回复与面板调度为本地模拟，未连接真实 Agent 或 OCR</p>
      </div>
    </main>
    <aside v-if="panel" class="ag-panel" :aria-label="titleMap[panel]">
      <div class="ag-panel-header"><div><small>本案工作面板</small><h2>{{ titleMap[panel] }}</h2></div><button aria-label="收起工作面板" @click="panel = ''">×</button></div>
      <div :key="`${panel}:${draftStep}:${caseData.fields.phase?.value || 'unset'}`" class="ag-panel-body">
        <template v-if="panel === 'facts'">
          <p class="ag-panel-intro">请核对预置的虚构案件信息。未知信息不会自动补成事实。</p>
          <dl class="ag-facts"><div v-for="field in readiness.items.filter(item => item.mode !== 'optional')" :key="field.id"><dt>{{ field.label }}</dt><dd>{{ field.fact?.status === 'unknown' ? `暂不清楚 · ${field.fact.reason}` : (field.options?.[field.fact?.value] || field.fact?.value || '待你补充') }}</dd></div></dl>
          <div v-if="readiness.blockers.length" class="ag-warning"><strong>还差：{{ readiness.blockers.map(item => item.label).join('、') }}</strong><div class="ag-actions"><button @click="setPhase('not_filed')">尚未起诉</button><button @click="setPhase('hearing')">已经开庭</button></div></div>
          <button v-else class="ag-primary ag-full" @click="confirmFacts">{{ readiness.intakeConfirmed ? '再次确认并查看路径' : '确认这份案情摘要 →' }}</button>
        </template>
        <template v-else-if="panel === 'evidence'">
          <p class="ag-panel-intro">先核对提示，再整理本次请求的材料。</p>
          <article class="ag-warning"><div class="ag-row"><strong>疑似重复扫描页</strong><span class="ag-badge amber">模拟提醒</span></div><p>医疗票据 PDF 中预设了一页重复出现的 800 元票据。请核对票号和原件，避免重复计入金额。</p><small>定位示例：医疗票据_含重复扫描页.pdf</small></article>
          <article class="ag-warning subdued"><strong>材料仍待补充或解释</strong><p>保险保单原件、理赔反馈及是否有垫付款仍待核对。文件名称本身不能证明材料完整。</p></article>
          <h3 class="ag-section-title">演示材料 · {{ caseData.files.length }} 份</h3>
          <div v-for="file in caseData.files" :key="file.id" class="ag-file"><span>▤</span><div><strong>{{ file.name }}</strong><small>虚构材料记录 · 未执行 OCR</small></div></div>
          <button class="ag-secondary ag-full" @click="act('facts')">核对案情与所需材料 →</button>
        </template>
        <template v-else-if="panel === 'research'">
          <p class="ag-panel-intro">从解释回到法律原文，核对条款与适用条件。</p>
          <article class="ag-card"><span class="ag-badge">官方来源样例</span><h3>民事诉讼法 · 调解与执行和解</h3><p>判决前的调解、二审调解和执行和解，应分别核对适用程序。</p><dl class="ag-mini-meta"><dt>条款</dt><dd>第145条、第179条、第241条</dd><dt>版本</dt><dd>2023年修正</dd><dt>来源</dt><dd>最高人民法院知识产权法庭</dd></dl><a class="ag-text-link" href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html" target="_blank" rel="noopener noreferrer">查看官方原文 ↗</a></article>
          <p class="ag-small-note">此处展示已有的“调解时点”来源样例。其他问题的实时检索需接入 RAG。</p>
          <button class="ag-secondary ag-full" @click="act('facts')">回到我的案情 →</button>
        </template>
        <template v-else-if="panel === 'workflow'">
          <p class="ag-panel-intro">实际阶段：{{ P.phaseLabels[caseData.fields.phase?.value] || '待确认' }}。选择路径仅记录处理意向。</p>
          <template v-if="readiness.canSelectRoute"><article v-for="option in routeOptions" :key="option.id" class="ag-card" :class="{ selected: readiness.routeConfirmed && caseData.route === option.id }"><div class="ag-row"><h3>{{ option.title }}</h3><span v-if="readiness.routeConfirmed && caseData.route === option.id" class="ag-badge">已选择</span></div><p>{{ option.description }}</p><button class="ag-secondary ag-full" @click="selectRoute(option.id)">我选择{{ option.title }} →</button></article></template>
          <div v-else class="ag-warning">请先确认案情摘要。<button class="ag-text-link" @click="act('facts')">前往核对 →</button></div>
        </template>
        <template v-else-if="panel === 'outcomes'">
          <template v-if="readiness.intakeConfirmed"><p class="ag-panel-intro">以下为模拟分析结构，不是对本案的结果预测。</p><article v-for="card in caseData.demoAnalysis.cards" :key="card[0]" class="ag-card"><h3>{{ card[0] }}</h3><p>{{ card[1] }}</p></article><h3 class="ag-section-title">参考案例 · 模拟样本</h3><details v-for="sample in samples" :key="sample.id" class="ag-reference"><summary>{{ sample.issue }}</summary><p>{{ sample.reason }}</p><strong>与本案核对</strong><p>{{ sample.check }}</p></details></template>
          <div v-else class="ag-warning">案情发生变化，请重新确认后查看分析。<button class="ag-text-link" @click="act('facts')">核对案情 →</button></div>
        </template>
        <template v-else-if="panel === 'draft'">
          <template v-if="readiness.canDraft">
            <div class="ag-draft-steps"><span :class="{ active: draftStep === 'template' }">1 模板</span><span :class="{ active: draftStep === 'wording' }">2 确认表述</span><span :class="{ active: draftStep === 'preview' }">3 草稿</span></div>
            <article v-if="draftStep === 'template'" class="ag-card selected"><span class="ag-badge">适用当前纠纷类型</span><h3>{{ source.name }}</h3><p>先核对模板来源，再准备拟填写内容。</p><a class="ag-text-link" :href="source.sourcePage" target="_blank" rel="noopener noreferrer">来源：{{ source.sourceName }} ↗</a><a class="ag-text-link" :href="source.sourceUrl" target="_blank" rel="noopener noreferrer">法院原表 DOCX ↗</a><p class="ag-small-note">本示例生成结构草稿，尚未回填官方原表。</p><button class="ag-primary ag-full" @click="draftStep = 'wording'">使用此模板，核对表述 →</button></article>
            <form v-if="draftStep === 'wording'" @submit.prevent="generate"><h3 class="ag-section-title">拟填写的正式表述</h3><p class="ag-panel-intro">请修改或确认；不确定的责任和金额继续待核对。</p><textarea v-model="wording" aria-label="拟填写的正式表述" rows="7" required></textarea><label class="ag-confirm"><input v-model="approved" type="checkbox" required>我已核对表述，确认生成这份演示草稿。</label><button class="ag-primary ag-full" type="submit">确认并生成草稿 →</button><button class="ag-text-link" type="button" @click="draftStep = 'template'">返回模板来源</button></form>
            <template v-if="draftStep === 'preview'"><pre class="ag-paper">{{ draftText }}</pre><button class="ag-primary ag-full" @click="downloadMarkdown(draftText, 'CivRA_Agent交互演示草稿.md')">下载演示草稿 ↓</button><button class="ag-text-link" @click="draftStep = 'wording'">返回修改表述</button></template>
          </template>
          <div v-else class="ag-warning">当前条件已变化，请重新核对案情与路径。<button class="ag-text-link" @click="requestPanel('draft')">查看下一步 →</button></div>
        </template>
        <template v-else-if="panel === 'visuals'">
          <p class="ag-panel-intro">本次演示的案件记录，沿时间回看。</p>
          <ol class="ag-timeline"><li><small>2026年11月5日</small><h3>交通事故与就诊</h3><p>广州发生事故，演示医疗票据为1,600元及800元，另有重复扫描页。</p></li><li><small>当前资料</small><h3>5 份演示材料</h3><p>重复票据、保险材料及已付款项仍需核对。</p></li><li><small>当前阶段</small><h3>{{ P.phaseLabels[caseData.fields.phase?.value] || '等待你确认' }}</h3><p>{{ readiness.intakeConfirmed ? '用户已确认案情摘要。' : '案情摘要尚待用户确认。' }}</p></li><li v-if="readiness.routeConfirmed"><small>处理意向</small><h3>{{ routeTitle }}</h3><p>由用户明确选择，实际程序阶段保持不变。</p></li><li v-if="draftText"><small>当前成果</small><h3>演示草稿已生成</h3><p>已确认拟填写文字，未知字段保留待补充。</p></li></ol>
          <details class="ag-reference"><summary>参考案例 · 模拟样本</summary><div v-for="sample in samples" :key="sample.id"><h3>{{ sample.issue }}</h3><p>{{ sample.facts }}</p><p>与本案核对：{{ sample.check }}</p></div></details>
        </template>
      </div>
    </aside>
  </div>
</template>
