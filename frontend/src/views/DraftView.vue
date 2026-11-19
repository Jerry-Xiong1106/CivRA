<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import { defaultWording } from '../domain/presentation.js';
import { downloadMarkdown } from '../services/download.js';
import { templateSources } from '../domain/template-sources.js';
import PageHeading from '../components/PageHeading.vue';
import LockedState from '../components/LockedState.vue';
const store = useWorkspaceStore(),
  { current: c, readiness: r } = storeToRefs(store);
const plaintiffInput = ref(null);
const templates = [
  { id: 'traffic', name: '机动车交通事故责任纠纷民事起诉状', description: '适用于交通事故责任及赔偿请求的草稿整理' },
  { id: 'loan', name: '民间借贷纠纷民事起诉状', description: '适用于借款、还款及本金余额等事项的草稿整理' },
  { id: 'sale', name: '买卖合同纠纷民事起诉状', description: '适用于货款、交付及合同履行等事项的草稿整理' }
].map((template) => ({ ...template, ...templateSources.find((source) => source.id === template.id) }));
const form = reactive({
  plaintiff: c.value.draftForm.plaintiff || '',
  defendant: c.value.draftForm.defendant || '',
  wording: c.value.draftForm.wording || defaultWording(c.value),
  approved: false
});
const stale = computed(
  () => c.value.draft && (store.apiMode ? c.value.draft.case_version !== c.value.server.snapshot.case_version : c.value.draft.basis !== r.value.signature)
);
const downloads = computed(() => store.artifacts.filter(a => a.output.output_id === c.value.draft?.output_id));
watch(
  () => r.value.signature,
  () => {
    form.approved = false;
  }
);
async function generate() {
  if (await store.generateDraft(form)) form.approved = false;
}
function download() {
  const text = store.draftMarkdown();
  if (text) downloadMarkdown(text, 'CivRA_起诉状演示草稿.md');
}
</script>
<template>
  <div class="classic-ui">
  <template v-if="!r.canDraft"
    ><PageHeading
      kicker="COMPLAINT DRAFT"
      title="起诉状草稿"
      sub="先查看法院模板。准备生成时，再核对案情与正式表述。"
    />
    <article v-for="template in templates" :key="template.id" class="cc-template-preview">
      <h3>{{ template.name }}</h3><p>{{ template.id === c.fields.dispute?.value ? '与当前记录的纠纷类型对应，适用性仍需核对' : '其他纠纷类型的模板' }}</p>
      <a :href="template.sourcePage" target="_blank" rel="noopener">模板来源：{{ template.sourceName }} · 公开示范文本 ↗</a>
      <a :href="template.sourceUrl" target="_blank" rel="noopener">查看法院原表 DOCX ↗</a>
    </article>
    <LockedState
      title="生成本案草稿前，需要核对这些信息"
      :reasons="r.draftReasons"
      :target="
        r.intakeConfirmed && (!r.routeConfirmed || c.route !== 'litigation')
          ? 'workflow'
          : 'review'
      "
    />
    <details v-if="c.draft" class="details">
      <summary>查看此前保存的草稿（历史版本，仅供回看）</summary>
      <article class="paper">{{ c.draft.text }}</article>
      <p>原始内容仍保留。确认当前案情与适用路径后，重新生成再导出。</p>
    </details></template
  >
  <template v-else>
    <PageHeading
      kicker="COMPLAINT DRAFT"
      title="起诉状草稿"
      sub="选择适用模板 → 补充文书信息 → 确认正式表述 → 预览导出。"
    />
    <div class="notice">
      {{ store.apiMode ? '已确认诉讼准备方向。后端会继续检查文书所需信息；此处不认定实际立案或开庭状态。' : '已满足进入条件　案情已确认 · 用户选择诉讼 · 当前尚未起诉' }}
    </div>
    <ol class="cu-draft-steps" aria-label="文书填写顺序">
      <li><span>1</span>选择适用模板</li>
      <li><span>2</span>核对信息与正式表述</li>
      <li><span>3</span>预览并下载草稿</li>
    </ol>
    <form @submit.prevent="generate">
      <section class="cu-template-section" aria-labelledby="template-heading">
        <div class="cu-section-label"><h2 id="template-heading">选择模板</h2><span>按已确认的纠纷类型匹配</span></div>
        <div class="cu-template-list">
          <article v-for="template in templates" :key="template.id" class="cu-template-card" :class="{ 'is-matched': template.id === c.fields.dispute?.value }">
            <span class="cu-document-icon" aria-hidden="true"><i></i><i></i><i></i></span>
            <div class="cu-template-info">
              <h3>{{ template.name }}</h3>
              <p>{{ template.description }}</p>
              <small>结构预览 · 未回填法院原表</small>
              <div class="cu-template-source">
                <span>模板来源：</span>
                <a :href="template.sourcePage" target="_blank" rel="noopener noreferrer">{{ template.sourceName }} · 诉讼文书示范文本 ↗</a>
                <a :href="template.sourceUrl" target="_blank" rel="noopener noreferrer">法院原表 DOCX ↗</a>
              </div>
            </div>
            <div class="cu-template-action">
              <span class="pill" :class="{ good: template.id === c.fields.dispute?.value }">{{ template.id === c.fields.dispute?.value ? '当前适用' : '与本案类型不符' }}</span>
              <button type="button" class="button small" :disabled="template.id !== c.fields.dispute?.value" @click="plaintiffInput?.focus()">填写此模板 →</button>
            </div>
          </article>
        </div>
        <p class="note cu-template-note">
          {{ store.apiMode ? '上方提供法院原表参考。当前 Agent 接入的是结构化草稿生成；官方表格帮选、确认后回填仍需后端开放接口。' : '来源对应仓库已收录的法院空白原表；当前仍为填写流程演示，生成内容尚未回填到法院原表。' }}
        </p>
      </section>
      <section class="cu-panel cu-draft-form">
      <div class="cu-panel-head"><h2>核对信息与正式表述</h2><span>先确认，再填入草稿</span></div>
      <div class="cu-panel-body">
      <div class="cu-party-fields">
        <label class="stack"
          >原告姓名或名称<input
            ref="plaintiffInput"
            v-model="form.plaintiff"
            type="text"
            maxlength="200"
            placeholder="可暂留空，草稿显示待补充" /></label
        ><label class="stack"
          >被告姓名或名称<input
            v-model="form.defendant"
            type="text"
            maxlength="200"
            placeholder="正式身份信息后续核对"
        /></label>
      </div>
      <div class="cu-wording">
        <h3>正式表述预览</h3>
        <p>下面只整理已确认的请求，不扩写尚未提供的责任结论或金额。</p>
        <textarea
          v-model="form.wording"
          aria-label="正式表述"
          required
          maxlength="5000"
        ></textarea
        ><label class="checkline"
          ><input v-model="form.approved" type="checkbox" required /><span
            >我已核对以上表述，允许将它用于准备草稿。待补充信息继续保留为占位符。</span
          ></label
        ><button class="button primary" type="submit" :disabled="store.busy">
          确认表述并生成草稿 →
        </button>
      </div>
      </div>
      </section>
    </form>
    <template v-if="c.draft"
      ><div v-if="stale" class="notice warn" style="margin-top: 20px">
        这份草稿依据的案情已变化，请重新生成后再导出。
      </div>
      <div class="cu-section-label cu-preview-heading"><h2>草稿预览</h2><span>未知信息继续保留为待补充字段</span></div>
      <article class="paper">
        <h2>民事起诉状</h2>
        {{ c.draft.text }}
      </article>
      <div class="footer-action">
        <span class="note">{{ store.apiMode ? '仅下载后端实际生成的文件；正式提交前请核对。' : '演示导出 Markdown，正式系统继续支持 Word / PDF' }}</span>
        <template v-if="store.apiMode"><button v-for="artifact in downloads" :key="artifact.id" class="button primary" :disabled="!!stale" @click="store.downloadArtifact(artifact.id)">下载 {{ artifact.format.toUpperCase() }} ↓</button><span v-if="!downloads.length" class="note">尚无可下载文件</span></template>
        <button v-else class="button primary" :disabled="!!stale" @click="download">
          下载演示草稿 ↓
        </button>
      </div></template
    >
  </template>
  </div>
</template>
