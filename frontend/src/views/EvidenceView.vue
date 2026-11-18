<script setup>
import { computed, inject } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { evidenceReview } from '../domain/evidence-review.js';
import { evidenceStatus } from '../domain/backendAdapter.js';
import PageHeading from '../components/PageHeading.vue';
const store = useWorkspaceStore(),
  openUpload = inject('openUpload'),
  { go } = useNavigation();
const review = computed(() => store.apiMode ? evidenceStatus(store.current) : evidenceReview(store.current, store.readiness.signature));
</script>
<template>
  <PageHeading
    kicker="MATERIALS"
    title="先把材料集中到这里"
    sub="随时上传并补充。材料缺失会形成待办，上传本身不会改变案件程序阶段。"
  />
  <div class="dropzone">
    <h3>合同、票据、聊天记录、法院文书</h3>
    <p>支持选择 PDF、照片、Word 和文本文件</p>
    <button class="button primary" @click="openUpload">＋ 选择材料</button>
    <p class="note">{{ store.apiMode ? '原文件保存到本案后端。上传后由助手调用解析与检查，扫描件识别取决于服务端 OCR 配置。' : '原型只保存文件名称和大小，未运行 OCR 或重复票据识别。' }}</p>
  </div>
  <section class="classic-ui cu-evidence-review" aria-labelledby="evidence-review-title">
    <div class="cu-section-label"><h2 id="evidence-review-title">材料核对提醒</h2><span>{{ review.demo ? '模拟问题 · 等待人工核对' : store.apiMode ? '以本案返回的检查记录为准' : '检查入口已预留 · 尚未核验' }}</span></div>
    <div class="cu-warning-grid" aria-live="polite">
      <article class="cu-evidence-warning">
        <div class="cu-warning-heading"><span class="cu-warning-symbol" aria-hidden="true">!</span><h3>疑似重复</h3><span class="pill warn">{{ review.demo ? '模拟提醒' : '待核验' }}</span></div>
        <p>{{ review.duplicate }}</p>
        <a class="link" href="#case-materials">核对本案材料 ↓</a>
      </article>
      <article class="cu-evidence-warning is-missing">
        <div class="cu-warning-heading"><span class="cu-warning-symbol" aria-hidden="true">!</span><h3>缺失或待补充</h3><span class="pill warn">{{ review.demo ? '模拟提醒' : '待核验' }}</span></div>
        <p>{{ review.missing }}</p>
        <div class="row wrap"><button class="link" @click="openUpload">补充材料 ＋</button><button class="link" @click="go('chat')">说明缺失原因 ↗</button></div>
      </article>
    </div>
    <p class="note cu-review-note">{{ review.demo ? '以上来自虚构演示设定，未实际执行文件解析或 OCR。' : '没有检查结果不代表材料没有重复或缺失。' }} 上传或移除记录不会自动确认材料已通过核验。</p>
  </section>
  <div id="case-materials" class="panel">
    <p v-if="store.current.server?.inventoryTruncated" class="notice">后端目录超过本次返回上限，当前列表不完整；完整分页接口待接入。</p>
    <div class="row between">
      <h3>本案材料</h3>
      <span class="pill">{{ store.current.files.length }} 份</span>
    </div>
    <template v-if="store.current.files.length"
      ><div v-for="file in store.current.files" :key="file.id" class="file-row">
        <span class="file-icon">文件</span>
        <div class="grow">
          <strong>{{ file.name }}</strong
          ><small
            >{{ (file.size / 1024).toFixed(1) }} KB ·
            {{ file.uploaded ? '已上传后端' : file.synthetic ? '虚构材料登记' : '仅本地登记' }} ·
            {{ file.uploaded ? (file.evidence.length ? '已生成材料记录，仍需核对' : '待助手解析') : '待接入解析' }}</small
          >
        </div>
        <button class="link" @click="store.apiMode ? store.sendConversation('请核对材料“' + file.name + '”的用途、内容及是否重复。') : store.removeFile(file.id)">
          {{ store.apiMode ? '请求核对' : '移除记录' }}
        </button>
      </div></template
    >
    <p v-else>尚未登记材料。没有材料时，也可以先说明案情。</p>
  </div>
  <p v-if="store.apiMode && store.current.server?.pendingAttachments?.length" class="notice">有 {{ store.current.server.pendingAttachments.length }} 份材料等待随消息交给助手。<button class="link" :disabled="store.busy || store.wait?.kind === 'route_selection'" @click="store.sendConversation('请解析待处理材料并检查重复、冲突和缺失。')">继续解析与检查 →</button></p>
  <div class="notice warn" style="margin-top: 20px">
    {{
      store.current.fields.dispute?.value === 'traffic'
        ? '如主张医疗费，后续核对医疗票据与诊疗记录。缺少时可补充或解释。'
        : '后续按诉求核对材料及金额来源，缺少的内容单独列为补充事项。'
    }}这是材料整理提示。
  </div>
</template>
