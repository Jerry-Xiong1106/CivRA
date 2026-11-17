<script setup>
import { inject } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import PageHeading from '../components/PageHeading.vue';
const store = useWorkspaceStore(),
  openUpload = inject('openUpload');
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
    <p class="note">原型只保存文件名称和大小，未运行 OCR 或重复票据识别。</p>
  </div>
  <div class="panel">
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
            >{{ (file.size / 1024).toFixed(1) }} KB · 仅本地登记 ·
            待接入解析</small
          >
        </div>
        <button class="link" @click="store.removeFile(file.id)">
          移除记录
        </button>
      </div></template
    >
    <p v-else>尚未登记材料。没有材料时，也可以先说明案情。</p>
  </div>
  <div class="notice warn" style="margin-top: 20px">
    {{
      store.current.fields.dispute?.value === 'traffic'
        ? '如主张医疗费，后续核对医疗票据与诊疗记录。缺少时可补充或解释。'
        : '后续按诉求核对材料及金额来源，缺少的内容单独列为补充事项。'
    }}这是材料整理提示。
  </div>
</template>
