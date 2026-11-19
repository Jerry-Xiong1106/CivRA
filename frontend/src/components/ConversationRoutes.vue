<script setup>
import { computed, ref, watch } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { ROUTE_GUIDES, routePreference } from '../domain/conversation.js';
import { downloadMarkdown } from '../services/download.js';
import P from '../domain/policy.js';
import { latestOutput, outputData } from '../domain/backendAdapter.js';
const store = useWorkspaceStore(), { go } = useNavigation();
const selected = computed(() => routePreference(store.current));
const guide = computed(() => ROUTE_GUIDES.find((r) => r.id === selected.value));
const notes = ref('');
watch(selected, () => { notes.value = store.current.preparationNotes?.[selected.value] || ''; }, { immediate: true });
const saved = computed(() => store.current.preparationDrafts?.[selected.value]);
const workflow = computed(() => latestOutput(store.current, 'workflow', o => outputData(o).route === selected.value));
const preparationItems = computed(() => store.apiMode && workflow.value ? outputData(workflow.value).steps.map(s => s.title + (s.contact ? '；办理方：' + s.contact : '') + (s.materials?.length ? '；材料：' + s.materials.join('、') : '')) : guide.value?.materials || []);
const stale = computed(() => saved.value && (saved.value.basis !== store.readiness.signature || JSON.stringify(saved.value.files) !== JSON.stringify(store.current.files.map((f) => f.id))));
function generate() {
  store.savePreparation(selected.value, notes.value);
}
function download() {
  if (!saved.value || stale.value) return;
  downloadMarkdown(saved.value.text, `CivRA_${guide.value.output}.md`);
}
</script>
<template>
  <section class="cc-routes">
    <div class="cc-panel-intro"><span class="eyebrow">YOUR NEXT MOVE</span><h2>这件事，你想怎样推进？</h2><p>先比较，再决定。三条路径可以调整，材料和案情会继续保留。</p></div>
    <div class="cc-route-list">
      <article v-for="(item, i) in ROUTE_GUIDES" :key="item.id" :class="{ selected: selected === item.id }">
        <span class="cc-route-number">0{{ i + 1 }}</span>
        <div><small>{{ item.lead }}</small><h3>{{ item.title }}</h3><p>{{ item.description }}</p>
        <button class="button small" :aria-pressed="selected === item.id" :disabled="store.busy || (store.wait?.kind === 'route_selection' && !store.wait.options.some(o => o.route === item.id))" @click="store.setRouteIntent(item.id)">{{ selected === item.id ? '当前准备方向 ✓' : store.apiMode && store.wait?.kind !== 'route_selection' ? '请助手评估这个方向' : '按这个方向准备' }}</button></div>
      </article>
    </div>
    <p class="cc-stage-note">实际阶段：{{ P.phaseLabels[store.current.fields.phase?.value] || '待确认' }}。准备方向只表示你的意向，不代表已申请、立案或达成协议。</p>
    <section v-if="guide" class="cc-preparation">
      <h3>{{ guide.title }}，先准备这些</h3>
      <ul><li v-for="item in preparationItems" :key="item">{{ item }}</li></ul>
      <p v-if="store.apiMode">{{ workflow ? (workflow.case_version === store.current.server.snapshot.case_version ? '以上步骤由后端针对本案返回。' : '步骤依据的案情版本已变化，请重新整理。') : '以上为通用准备提示；本案具体步骤以助手返回为准。' }}</p>
      <p>{{ guide.note }}</p>
      <div class="cc-action-row"><button class="link" @click="go('evidence')">查看共用材料 →</button><button class="link" @click="go('review')">核对案情 →</button></div>
      <template v-if="selected !== 'litigation'">
        <label class="cc-note-label" for="preparation-notes">这次希望怎样谈？</label>
        <textarea id="preparation-notes" v-model="notes" maxlength="5000" placeholder="例如：希望先核对费用，再商量付款时间；未确定的条件可以留空。"></textarea>
        <button class="button primary" :disabled="store.busy" @click="generate">整理{{ guide.output }}</button>
        <div v-if="saved" class="cc-saved-draft"><p v-if="stale" class="notice warn">案情已经变化，请重新整理提纲。</p><pre>{{ saved.text }}</pre><button class="button small" :disabled="stale" @click="download">下载提纲 ↓</button></div>
      </template>
      <template v-else>
        <div v-if="store.apiMode" class="notice">请核对本案记录。草稿生成所需的信息由后端检查，缺失时助手会追问。</div>
        <div v-else-if="!store.readiness.intakeConfirmed" class="notice">可以先了解模板和材料。生成针对本案的起诉状前，请核对案情记录。</div>
        <button v-else-if="!store.readiness.routeConfirmed || store.current.route !== 'litigation'" class="button" @click="store.confirmRoute('litigation')">确认使用已核对案情准备诉讼文书</button>
        <button class="button primary" @click="go('draft')">查看起诉状模板与草稿 →</button>
      </template>
    </section>
    <details class="cc-route-explainer"><summary>协商、调解和诉讼，有什么区别？</summary>
      <p>协商是双方自行沟通；调解有第三方主持。两者均需了解事实和诉求，关键材料用于说明问题、核对金额。这里的材料清单是准备提示，具体提交要求由受理机构确定。</p>
      <p>调解并不限于开庭前。人民调解协议与法院调解书的效力、申请执行条件也不同，不能把“已达成协议”直接标为“可强制执行”。</p>
      <a href="https://zjw.beijing.gov.cn/bjjs/xxgk/543346069/543346066/436333825/index.shtml" target="_blank" rel="noopener">人民调解法 · 第2、17、29、31、33条 ↗</a>
      <a href="https://www.court.gov.cn/fabu/xiangqing/22752.html" target="_blank" rel="noopener">人民法院特邀调解规定 · 第11条 ↗</a>
    </details>
  </section>
</template>
