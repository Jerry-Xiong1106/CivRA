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
const stepInfo = {
  evidence: {
    description: '核对主体、金额和材料，确认哪些信息已经知道、哪些仍需补充。',
    actor: '由你核实本案信息',
    materials: '与请求有关的合同、票据、记录及已有说明。',
    condition: '按当前请求逐项核对材料，缺少的内容补充或解释。',
    link: '前往证据材料'
  },
  research: {
    description: '围绕当前请求查阅法律依据，核实办理渠道及其适用条件。',
    actor: '相关受理机构与具体要求需结合本案核验',
    materials: '已确认的案情摘要、当前请求和待核实的问题。',
    condition: '结合实际程序阶段核验办理条件，不把选择路径视为已经提交申请。',
    link: '前往法律依据'
  },
  draft: {
    description: '按纠纷类型核对模板，先确认正式表述，再生成并核对草稿。',
    actor: '由你核对当事人信息与拟填写内容',
    materials: '当事人信息、诉讼请求、事实经过及对应材料。',
    condition: '满足起诉状工作台的进入条件，未知信息继续保留为待补充。',
    link: '前往起诉状草稿'
  },
  feedback: {
    description: '保留沟通与办理结果。未达成一致或情况变化时，可重新比较可用路径。',
    actor: '由你记录实际发生的情况',
    materials: '沟通记录、办理反馈及需要后续核对的内容。',
    condition: '反馈只保存实际记录，不会自动将案件标为办结。',
    link: '前往结果分析'
  }
};
const steps = computed(() => routeTasks(c.value).map((task) => ({
  ...task, ...stepInfo[task.view || 'feedback']
})));
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
    <div v-if="r.routeConfirmed" class="classic-ui cu-workflow-columns">
      <section class="cu-panel cu-route-plan">
        <div class="cu-panel-head"><h2>{{ routeName(c) }} · 准备步骤</h2><span>由你确认</span></div>
        <ol class="cu-route-steps">
          <li v-for="(task, i) in steps" :key="`${c.route}:${i}`">
            <span class="cu-route-node" aria-hidden="true">{{ i + 1 }}</span>
            <div class="cu-route-copy">
              <div class="cu-route-step-head">
                <h3>{{ task.text }}</h3>
                <button class="link" @click="store.openDialog('task', { index: i })">{{ c.tasks.includes(`${c.route}:${i}`) ? '已记录反馈' : '记录反馈' }}</button>
              </div>
              <p>{{ task.description }}</p>
              <p class="cu-route-actor">办理方 · {{ task.actor }}</p>
              <details class="cu-step-details">
                <summary>材料、条件、依据与期限</summary>
                <dl>
                  <div><dt>材料</dt><dd>{{ task.materials }}</dd></div>
                  <div><dt>条件</dt><dd>{{ task.condition }}</dd></div>
                  <div><dt>依据</dt><dd>需结合本案核对适用条款及原文出处。<button class="link" @click="go('research')">查看法律依据 ↗</button></dd></div>
                  <div><dt>期限</dt><dd>向实际受理机构核验具体期限，本页不自动推定。</dd></div>
                </dl>
              </details>
              <button class="link cu-step-link" @click="go(task.view || 'outcomes')">{{ task.link }} ↗</button>
            </div>
          </li>
        </ol>
      </section>
      <aside class="cu-route-aside">
        <section class="cu-panel">
          <div class="cu-panel-head"><h2>出发前需要核验</h2></div>
          <ul class="cu-check-list">
            <li>材料是否完整，费用是否对应本案</li>
            <li>办理机构、受理条件与具体期限</li>
            <li>所在地及案件情况对应的管辖信息</li>
          </ul>
        </section>
        <section class="cu-route-reminder">
          <h3>情况变化后，仍可调整路径</h3>
          <p>协商无果或收到新反馈时，可以重新比较当前可用的路径。事实或程序阶段有变化时，先更新案情并重新确认。</p>
          <p class="note">选择路径和记录反馈都不会自动改变实际程序阶段。</p>
        </section>
      </aside>
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
