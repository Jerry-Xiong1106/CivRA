<script setup>
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { displayFact } from '../domain/presentation.js';
import PageHeading from '../components/PageHeading.vue';
const store = useWorkspaceStore(),
  { go } = useNavigation();
async function confirm() {
  const id = store.current.id;
  if (await store.confirmIntake() && store.current.id === id) go('workflow');
}
</script>
<template>
  <PageHeading
    kicker="REVIEW & CONFIRM"
    title="这是我们整理的案情"
    sub="核对内容、保留未知项，再确认本版摘要。以后修改关键事实时会再次请你核对。"
  />
  <div v-if="store.readiness.blockers.length" class="notice warn">
    还有
    {{
      store.readiness.blockers.length
    }}
    项信息待补充或确认。法律咨询、材料整理和路径比较都可以继续；生成本案文书前再完成核对。
  </div>
  <div v-else class="notice">
    {{ store.apiMode ? '这里显示后端已记录的事实。' : '必答项已处理。' }}{{
      store.readiness.intakeConfirmed
        ? '本版摘要已经确认。'
        : '请确认以下内容与自己的陈述一致。'
    }}
  </div>
  <table class="fact-table">
    <thead>
      <tr>
        <th>收集项目</th>
        <th>目前记录</th>
        <th>操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="f in store.readiness.items" :key="f.id">
        <td>{{ f.label }}</td>
        <td>
          {{ store.apiMode ? f.fact.value : displayFact(f, f.fact) }}<br /><small
            :class="{ muted: f.satisfied }"
            >{{
              f.satisfied
                ? f.fact?.status === 'unknown'
                  ? '已记录未知，后续补充'
                  : '已确认或非必答项'
                : f.reason
            }}</small
          >
        </td>
        <td>
          <button class="link" @click="store.openDialog('field', { id: f.id })">
            核对 / 修改
          </button>
        </td>
      </tr>
    </tbody>
  </table>
  <div class="footer-action">
    <button class="button" @click="go('chat')">继续沟通</button
    ><button
      class="button primary"
      :disabled="store.apiMode ? store.busy || !store.readiness.items.length : !!store.readiness.blockers.length"
      @click="confirm"
    >
      {{
        store.readiness.intakeConfirmed
          ? '查看处理方向'
          : '确认案情记录'
      }}
      →
    </button>
  </div>
  <p class="note" style="text-align: right; margin-top: 12px">
    确认表示记录准确，不代表已经认定事实或满足法院全部要求。
  </p>
</template>
