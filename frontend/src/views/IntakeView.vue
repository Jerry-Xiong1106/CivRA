<script setup>
import { computed, ref, watch, inject } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import P from '../domain/policy.js';
import PageHeading from '../components/PageHeading.vue';
import FactInput from '../components/FactInput.vue';
import FactLedger from '../components/FactLedger.vue';
const store = useWorkspaceStore(),
  { current: c, readiness: r } = storeToRefs(store),
  { go } = useNavigation();
const openUpload = inject('openUpload'),
  answer = ref(''),
  statement = ref('');
const q = computed(
  () => r.value.blockers.find((f) => !f.fact?.value) || r.value.blockers[0]
);
watch(
  () => [q.value?.id, q.value?.fact?.value],
  () => {
    answer.value = q.value?.fact?.value || '';
  },
  { immediate: true }
);
function confirm() {
  if (q.value && store.changeFact(q.value.id, answer.value))
    store.notify('已确认，继续处理下一项。');
}
function send() {
  if (store.addStatement(statement.value)) statement.value = '';
}
</script>
<template>
  <PageHeading
    kicker="CASE INTAKE"
    title="先把案情说清楚"
    sub="你可以对话补充，也可以逐项填写。确认案情后，即可选择维权路径。"
    ><span class="pill good">步骤 01 / 05</span></PageHeading
  >
  <div class="intake-grid">
    <section class="conversation">
      <div class="conversation-intro">
        {{
          c.narrative
            ? '以下为示例案情，可修改后体验完整流程'
            : '先说明基本情况，上传材料也可以从这里开始'
        }}
      </div>
      <div v-if="c.narrative" class="message user-message">
        {{ c.narrative }}
      </div>
      <div class="assistant-label"><i>C</i> CivRA 案件助手</div>
      <div class="message">
        {{
          r.intakeConfirmed
            ? '这一版案情已经由你确认。你可以查看法律依据，或进入维权路径选择。'
            : r.blockers.length
              ? `案情已保存。进入路径选择前，还有 ${r.blockers.length} 项信息需要补充或确认。我们先处理下面这一项。`
              : '必答项已处理。请再核对一次案情摘要，然后由你确认进入下一步。'
        }}
      </div>
      <section v-if="q" class="question-card">
        <div class="question-head">
          <span>当前待办 · {{ q.group }}</span
          ><span>{{ P.modes[q.mode] }}</span>
        </div>
        <form class="question-body" @submit.prevent="confirm">
          <h3>{{ q.label }}</h3>
          <p>{{ q.question }}</p>
          <FactInput v-model="answer" :field="q" />
          <div class="question-actions">
            <button class="button primary" type="submit">
              {{ q.fact?.value ? '确认这项信息' : '保存并确认' }} →</button
            ><button
              v-if="q.mode === 'answered'"
              class="button"
              type="button"
              @click="store.openDialog('field', { id: q.id, unknown: true })"
            >
              暂不清楚
            </button>
          </div>
        </form>
      </section>
      <div v-else class="panel">
        <h3>
          {{ r.intakeConfirmed ? '案情核对已完成' : '可以核对案情摘要了' }}
        </h3>
        <p>
          已明确记录
          {{ r.completed }} 项必答信息。暂不清楚的内容会继续保留为待补充。
        </p>
        <button
          class="button primary"
          style="margin-top: 18px"
          @click="go(r.intakeConfirmed ? 'workflow' : 'review')"
        >
          {{ r.intakeConfirmed ? '进入维权路径' : '核对案情摘要' }} →
        </button>
      </div>
      <div v-if="c.messages.length" class="message-history">
        <div v-for="(message, i) in c.messages.slice(-3)" :key="i">
          <small class="muted">补充记录</small>
          <p>{{ message }}</p>
        </div>
      </div>
      <form class="composer" @submit.prevent="send">
        <textarea
          v-model="statement"
          aria-label="补充案情"
          placeholder="还有其他情况？可以继续补充，或直接编辑右侧案情卡。"
        ></textarea>
        <div class="composer-footer">
          <button type="button" class="link" @click="openUpload">
            ＋ 上传材料</button
          ><span>补充内容会保留为待核对记录</span
          ><button class="button primary small" type="submit">发送 ↑</button>
        </div>
      </form>
      <p class="review-note">
        演示采用固定字段追问。正式接入后由 AI 提取候选信息，再由你确认。
      </p>
    </section>
    <FactLedger />
  </div>
</template>
