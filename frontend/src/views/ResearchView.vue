<script setup>
import PageHeading from '../components/PageHeading.vue';
import LawLookup from '../components/LawLookup.vue';
import { useWorkspaceStore } from '../stores/workspace.js';
const store = useWorkspaceStore();
</script>
<template>
  <div class="classic-ui">
  <PageHeading
    kicker="LEGAL BASIS"
    title="法律依据"
    sub="可先了解法律，也可以围绕当前案件查询。阅读法律依据不受办事步骤限制。"
  ><span class="pill">{{ store.apiMode ? '后端返回的法律来源' : '内置法条样例' }}</span></PageHeading>
  <LawLookup />
  </div>
</template>
