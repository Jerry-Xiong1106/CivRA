<script setup>
import { useNavigation } from '../composables/navigation.js';
import PageHeading from '../components/PageHeading.vue';
import LawLookup from '../components/LawLookup.vue';
const { go } = useNavigation();
</script>
<template>
  <div class="page mode-page">
    <PageHeading
      kicker="LEGAL CONSULTATION"
      title="先了解法律，再决定下一步"
      sub="不需要先填写完整案情。需要办理具体案件时，可以随时切换。"
    /><LawLookup />
    <div class="panel">
      <div class="row between wrap">
        <div>
          <h3>准备处理自己的纠纷？</h3>
          <p>进入案件办理，逐步核对案情与路径。</p>
        </div>
        <button class="button" @click="go('chat')">进入案件办理 →</button>
      </div>
    </div>
  </div>
</template>
