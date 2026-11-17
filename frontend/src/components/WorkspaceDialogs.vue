<script setup>
import { computed, reactive, ref, watch, nextTick } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import P from '../domain/policy.js';
import REF from '../domain/reference-data.js';
import FactInput from './FactInput.vue';
const store = useWorkspaceStore(),
  { go, section, isConsult } = useNavigation(),
  dialog = ref(null);
const config = computed(() => store.ui.dialog);
const field = computed(() =>
  P.applicable(store.current, store.state.rules).find(
    (f) => f.id === config.value?.id
  )
);
const option = computed(() =>
  store.options.find((o) => o.id === config.value?.id)
);
const sample = computed(() =>
  !isConsult.value && ['visuals', 'outcomes'].includes(section.value)
    ? REF.samples.find(
        (s) =>
          s.id === config.value?.id &&
          s.domain === store.current.fields.dispute?.value
      )
    : null
);
const form = reactive({
  title: '',
  domain: 'traffic',
  value: '',
  reason: '',
  feedback: '',
  rules: {}
});
const fixed = ['dispute', 'role', 'phase'];
const title = computed(
  () =>
    ({
      new: '新建案件',
      rules: '设置案情收集规则',
      task: '记录实际办理反馈',
      field: config.value?.unknown
        ? `记录“${field.value?.label || ''}”暂不清楚`
        : `核对${field.value?.label || ''}`,
      route: `确认${option.value?.title || '路径'}`,
      reference: `参考案例 · ${sample.value?.issue || ''}`
    })[config.value?.kind] || ''
);
watch(
  config,
  async (value) => {
    if (!value) {
      dialog.value?.close();
      return;
    }
    Object.assign(form, {
      title: '',
      domain: 'traffic',
      value: store.current.fields[value.id]?.value || '',
      reason: store.current.fields[value.id]?.reason || '',
      feedback: '',
      rules: Object.fromEntries(
        P.fields.map((f) => [f.id, store.state.rules[f.id] || f.mode])
      )
    });
    await nextTick();
    if (store.ui.dialog === value && !dialog.value.open)
      dialog.value.showModal();
  },
  { flush: 'post' }
);
function close() {
  store.closeDialog();
}
function submit() {
  const value = config.value;
  if (!value || value.caseId !== store.current.id) {
    close();
    return;
  }
  let success = false;
  if (value.kind === 'new') {
    const created = store.createCase(form.title, form.domain);
    if (created) {
      close();
      go('chat');
    }
    return;
  }
  if (value.kind === 'field')
    success = store.changeFact(value.id, form.value, {
      unknown: !!value.unknown,
      reason: form.reason
    });
  if (value.kind === 'rules') {
    store.updateRules(form.rules);
    success = true;
  }
  if (value.kind === 'task')
    success = store.recordFeedback(value.index, form.feedback);
  if (value.kind === 'route') {
    success = store.confirmRoute(value.id);
    if (success) {
      close();
      go('workflow');
      store.notify('已保存你的处理意向，实际程序阶段保持不变。');
      return;
    }
  }
  if (success) close();
}
function backdrop(event) {
  if (event.target !== dialog.value) return;
  const rect = dialog.value.getBoundingClientRect();
  if (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  )
    close();
}
</script>
<template>
  <dialog
    ref="dialog"
    aria-labelledby="dialog-title"
    @cancel.prevent="close"
    @click="backdrop"
  >
    <template v-if="config">
      <div class="dialog-head">
        <h2 id="dialog-title">{{ title }}</h2>
        <button aria-label="关闭对话框" @click="close">✕</button>
      </div>
      <div class="dialog-body">
        <form
          v-if="config.kind === 'new'"
          id="new-form"
          class="stack"
          @submit.prevent="submit"
        >
          <label for="new-title">案件名称</label
          ><input
            id="new-title"
            v-model="form.title"
            required
            maxlength="100"
            placeholder="例如：借款未归还"
          /><label for="new-domain">纠纷类型</label
          ><select id="new-domain" v-model="form.domain">
            <option
              v-for="(label, value) in P.domains"
              :key="value"
              :value="value"
            >
              {{ label }}
            </option>
          </select>
          <p class="note">
            新案件从案情收集开始。必答信息处理并确认后，再选择维权路径。
          </p>
        </form>
        <form
          v-else-if="config.kind === 'field' && field"
          id="field-form"
          @submit.prevent="submit"
        >
          <p>{{ field.question }}</p>
          <template v-if="config.unknown"
            ><label for="unknown-reason">请说明原因或准备怎样核实</label
            ><textarea
              id="unknown-reason"
              v-model="form.reason"
              required
              maxlength="2000"
              placeholder="例如：目前没有取得材料，准备向相关人员了解"
            ></textarea></template
          ><FactInput
            v-else
            id="edit-value"
            v-model="form.value"
            :field="field"
          />
          <p class="note" style="margin-top: 14px">
            修改后会重新检查案情确认状态，原有路径选择保留在记录中。
          </p>
        </form>
        <template v-else-if="config.kind === 'rules'"
          ><p>
            设计演示中可以调整每项信息的进入条件。正式版由服务器配置并统一校验，普通案件用户不能修改规则。
          </p>
          <form id="rules-form" @submit.prevent="submit">
            <div v-for="item in P.fields" :key="item.id" class="rules-row">
              <span
                >{{ item.label
                }}<small class="muted"
                  >　{{ item.domain ? P.domains[item.domain] : '通用' }}</small
                ></span
              ><span v-if="fixed.includes(item.id)" class="pill"
                >必填且确认 · 流程基础字段</span
              ><select
                v-else
                v-model="form.rules[item.id]"
                :aria-label="`${item.label}的收集规则`"
              >
                <option
                  v-for="(label, value) in P.modes"
                  :key="value"
                  :value="value"
                >
                  {{ label }}
                </option>
              </select>
            </div>
            <p class="note" style="margin-top: 16px">
              “必填且确认”需要明确值。“必须回应”允许记录未知及原因。规则改变后，需要重新确认案情摘要。
            </p>
          </form></template
        >
        <template v-else-if="config.kind === 'route' && option"
          ><p>{{ option.description }}</p>
          <div class="notice">
            实际程序阶段继续保留为“{{
              P.phaseLabels[store.current.fields.phase.value]
            }}”。本次操作只保存你的处理意向。
          </div>
          <p>
            事实或程序阶段发生变化时，系统会请你重新确认。你仍可以随时查看法律、补充材料和切换处理意向。
          </p></template
        >
        <form
          v-else-if="config.kind === 'task'"
          id="task-form"
          @submit.prevent="submit"
        >
          <label for="task-feedback">请记录实际发生的情况</label
          ><textarea
            id="task-feedback"
            v-model="form.feedback"
            required
            maxlength="5000"
            placeholder="例如：已联系对方，对方希望下周继续协商"
          ></textarea>
          <p class="note">反馈只保存办理记录，不会自动将案件标为办结。</p>
        </form>
        <template v-else-if="config.kind === 'reference' && sample"
          ><div class="notice warn">
            模拟案例，无真实案号或原文链接，不能作为裁判引用。
          </div>
          <p>{{ sample.region }} · {{ sample.court }} · {{ sample.year }} 年</p>
          <h3>样例事实</h3>
          <p>{{ sample.facts }}</p>
          <h3>理由展示</h3>
          <p>{{ sample.reason }}</p>
          <h3>本案当前记录</h3>
          <p>{{ store.current.fields.story?.value || '待补充' }}</p>
          <h3>与本案需要核对的差异</h3>
          <p>{{ sample.check }}</p></template
        >
      </div>
      <div v-if="config.kind !== 'reference'" class="dialog-actions">
        <button
          v-if="
            config.kind === 'field' &&
            !config.unknown &&
            field?.mode === 'answered'
          "
          class="button"
          @click="store.openDialog('field', { id: field.id, unknown: true })"
        >
          暂不清楚
        </button>
        <button class="button" @click="close">
          {{ config.kind === 'route' ? '返回比较' : '取消' }}
        </button>
        <button
          v-if="config.kind === 'route'"
          class="button primary"
          @click="submit"
        >
          确认选择
        </button>
        <button
          v-else
          class="button primary"
          type="submit"
          :form="`${config.kind}-form`"
        >
          {{
            {
              new: '创建案件',
              rules: '应用本地演示规则',
              task: '保存实际反馈',
              field: config.unknown ? '记录未知及原因' : '保存并确认'
            }[config.kind]
          }}
        </button>
      </div>
    </template>
  </dialog>
</template>
