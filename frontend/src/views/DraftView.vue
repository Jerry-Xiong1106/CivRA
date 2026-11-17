<script setup>
import { computed, reactive, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import P from '../domain/policy.js';
import { defaultWording } from '../domain/presentation.js';
import { downloadMarkdown } from '../services/download.js';
import PageHeading from '../components/PageHeading.vue';
import LockedState from '../components/LockedState.vue';
const store = useWorkspaceStore(),
  { current: c, readiness: r } = storeToRefs(store);
const form = reactive({
  plaintiff: c.value.draftForm.plaintiff || '',
  defendant: c.value.draftForm.defendant || '',
  wording: c.value.draftForm.wording || defaultWording(c.value),
  approved: false
});
const stale = computed(
  () => c.value.draft && c.value.draft.basis !== r.value.signature
);
watch(
  () => r.value.signature,
  () => {
    form.approved = false;
  }
);
function generate() {
  if (store.generateDraft(form)) form.approved = false;
}
function download() {
  const text = store.draftMarkdown();
  if (text) downloadMarkdown(text, 'CivRA_起诉状演示草稿.md');
}
</script>
<template>
  <template v-if="!r.canDraft"
    ><PageHeading
      kicker="COMPLAINT DRAFT"
      title="起诉状草稿"
      sub="在确认案情并明确选择诉讼准备后，进入模板与文书核对。"
    /><LockedState
      title="暂未进入起诉状准备"
      :reasons="r.draftReasons"
      :target="
        r.intakeConfirmed && (!r.routeConfirmed || c.route !== 'litigation')
          ? 'workflow'
          : 'chat'
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
      title="先确认表述，再生成草稿"
      sub="选择适用模板 → 补充文书信息 → 确认正式表述 → 预览导出。"
    />
    <div class="notice">
      已满足进入条件　案情已确认 · 用户选择诉讼 · 当前尚未起诉
    </div>
    <form @submit.prevent="generate">
      <div class="template">
        <strong>模板选择</strong
        ><select aria-label="起诉状模板">
          <option :value="c.fields.dispute?.value">
            {{ P.domains[c.fields.dispute?.value] || '其他纠纷' }} ·
            起诉状结构预览
          </option>
        </select>
        <p class="note" style="margin-top: 10px">
          正式版接入仓库中的法院原表和模板版本，本原型仅预览填写流程。
        </p>
      </div>
      <div class="card-grid" style="grid-template-columns: 1fr 1fr">
        <label class="stack" style="font-size: 12px; gap: 8px"
          >原告姓名或名称<input
            v-model="form.plaintiff"
            type="text"
            maxlength="200"
            placeholder="可暂留空，草稿显示待补充" /></label
        ><label class="stack" style="font-size: 12px; gap: 8px"
          >被告姓名或名称<input
            v-model="form.defendant"
            type="text"
            maxlength="200"
            placeholder="正式身份信息后续核对"
        /></label>
      </div>
      <div class="panel">
        <h3>正式表述预览</h3>
        <p>下面只整理已确认的请求，不扩写尚未提供的责任结论或金额。</p>
        <textarea
          v-model="form.wording"
          aria-label="正式表述"
          style="margin-top: 15px"
          required
          maxlength="5000"
        ></textarea
        ><label class="checkline"
          ><input v-model="form.approved" type="checkbox" required /><span
            >我已核对以上表述，允许将它写入这份演示草稿。待补充信息继续保留为占位符。</span
          ></label
        ><button class="button primary" type="submit">
          确认表述并生成草稿 →
        </button>
      </div>
    </form>
    <template v-if="c.draft"
      ><div v-if="stale" class="notice warn" style="margin-top: 20px">
        这份草稿依据的案情已变化，请重新生成后再导出。
      </div>
      <article class="paper">
        <h2>民事起诉状</h2>
        {{ c.draft.text }}
      </article>
      <div class="footer-action">
        <span class="note">演示导出 Markdown，正式系统继续支持 Word / PDF</span
        ><button class="button primary" :disabled="!!stale" @click="download">
          下载演示草稿 ↓
        </button>
      </div></template
    >
  </template>
</template>
