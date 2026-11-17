<script setup>
import { computed } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import P from '../domain/policy.js';
import { routeName, routeTasks } from '../domain/presentation.js';
import PageHeading from '../components/PageHeading.vue';
import LockedState from '../components/LockedState.vue';
const store = useWorkspaceStore(),
  { current: c, readiness: r } = storeToRefs(store),
  { go } = useNavigation();
const reasons = computed(() => [
  ...r.value.blockers.map((f) => `${f.label} · ${f.reason}`),
  '确认本版案情摘要'
]);
</script>
<template>
  <template v-if="!r.canSelectRoute"
    ><PageHeading
      kicker="CHOOSE A PATH"
      title="维权路径"
      sub="先确认案情，再由你选择当前想处理的事项。" /><LockedState
      title="先完成案情核对"
      :reasons="reasons"
  /></template>
  <template v-else>
    <PageHeading
      kicker="CHOOSE A PATH"
      title="下一步，你希望怎样处理？"
      sub="路径由你选择。你可以返回调整，已经保存的事实和材料会继续保留。"
    />
    <div class="notice">
      <strong>实际程序阶段　{{ P.phaseLabels[c.fields.phase.value] }}</strong
      >　{{ c.route ? `当前处理意向　${routeName(c)}` : '尚未选择处理意向'
      }}<br />{{
        ['filed', 'hearing', 'appeal'].includes(c.fields.phase.value)
          ? '此阶段仍可了解调解。选择调解后，系统继续保留原来的诉讼阶段。'
          : '选择路径只记录办理意向，不代表已经向法院或调解组织提交申请。'
      }}
    </div>
    <div v-if="store.options.length" class="card-grid">
      <article
        v-for="(option, i) in store.options"
        :key="option.id"
        class="route-card"
        :class="{ selected: r.routeConfirmed && c.route === option.id }"
      >
        <div class="row between">
          <span class="route-number">0{{ i + 1 }}</span
          ><span
            v-if="r.routeConfirmed && c.route === option.id"
            class="pill good"
            >已选择</span
          >
        </div>
        <h3>{{ option.title }}</h3>
        <p>{{ option.description }}</p>
        <button
          class="button"
          :class="{ primary: !(c.route === option.id && r.routeConfirmed) }"
          @click="store.openDialog('route', { id: option.id })"
        >
          {{
            c.route === option.id && r.routeConfirmed
              ? '重新确认此路径'
              : '选择此路径'
          }}
          →
        </button>
      </article>
    </div>
    <div v-else class="panel">
      <h3>案件已处理完毕</h3>
      <p>可以查看历史成果和记录。有新的程序进展时，请先更新实际阶段。</p>
    </div>
    <div v-if="r.routeConfirmed" class="panel">
      <div class="row between">
        <h3>{{ routeName(c) }} · 当前待办</h3>
        <span class="pill good">由你确认</span>
      </div>
      <div v-for="(task, i) in routeTasks(c)" :key="i" class="task-row">
        <span>0{{ i + 1 }}</span
        ><span>{{ task.text }}</span
        ><button
          class="button small"
          @click="
            task.view ? go(task.view) : store.openDialog('task', { index: i })
          "
        >
          {{
            task.view
              ? '前往处理'
              : c.tasks.includes(`${c.route}:${i}`)
                ? '已记录反馈'
                : '记录实际反馈'
          }}
        </button>
      </div>
    </div>
    <details class="details">
      <summary>为什么开庭后仍有调解选项？</summary>
      <p>
        民事诉讼法第145条规定了判决前的调解。二审和执行阶段需分别对应适用程序，执行中显示“执行和解”。
      </p>
      <a
        href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html"
        target="_blank"
        rel="noopener"
        >查看最高人民法院发布的民事诉讼法 ↗</a
      >
    </details>
  </template>
</template>
