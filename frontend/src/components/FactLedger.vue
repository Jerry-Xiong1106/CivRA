<script setup>
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { displayFact } from '../domain/presentation.js';
const store = useWorkspaceStore(),
  { go } = useNavigation();
</script>
<template>
  <aside class="ledger">
    <div class="ledger-head">
      <div class="row between">
        <h3>案情收集清单</h3>
        <button class="link" @click="store.openDialog('rules')">规则</button>
      </div>
      <div class="progress">
        <span
          :style="{
            width: `${store.readiness.required.length ? (store.readiness.completed / store.readiness.required.length) * 100 : 100}%`
          }"
        ></span>
      </div>
      <div class="row between ledger-count">
        <span
          >{{ store.readiness.completed }} /
          {{ store.readiness.required.length }} 项已处理</span
        ><span>{{ store.readiness.blockers.length }} 项待核对</span>
      </div>
    </div>
    <div class="facts">
      <div v-for="f in store.readiness.items" :key="f.id" class="fact">
        <span class="fact-icon" :class="{ warn: !f.satisfied }">{{
          f.satisfied ? '✓' : '○'
        }}</span>
        <div>
          <div class="fact-label">
            {{ f.label }} {{ f.mode === 'known' ? '*' : '' }}
          </div>
          <div class="fact-value">{{ displayFact(f, f.fact) }}</div>
          <small v-if="!f.satisfied" style="color: #ae905c">{{
            f.reason
          }}</small>
        </div>
        <button
          :aria-label="`编辑${f.label}`"
          @click="store.openDialog('field', { id: f.id })"
        >
          编辑
        </button>
      </div>
    </div>
    <div class="ledger-foot">
      <p>* 必须有明确内容并确认。其他必答项允许说明暂不清楚的原因。</p>
      <button
        class="button"
        :class="{ primary: !store.readiness.blockers.length }"
        @click="go('review')"
      >
        核对案情摘要 →
      </button>
    </div>
  </aside>
</template>
