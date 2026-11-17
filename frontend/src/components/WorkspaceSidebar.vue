<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import P from '../domain/policy.js';
import { routeName } from '../domain/presentation.js';
const store = useWorkspaceStore();
const { isConsult, selectCase, consult } = useNavigation();
const cases = computed(() =>
  store.state.cases.filter((c) => c.title.includes(store.ui.search))
);
</script>
<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-symbol">C</span>
      <div><strong>CivRA</strong><small>CIVIL LAW ASSISTANT</small></div>
    </div>
    <button class="button new" @click="store.openDialog('new')">
      <span>＋</span> 新建案件
    </button>
    <button class="case" :class="{ active: isConsult }" @click="consult">
      <strong>⌕　只咨询法律</strong><small>查法条、看出处，随时开始</small>
    </button>
    <div class="search-filter">
      <span>⌕</span
      ><input
        v-model="store.ui.search"
        aria-label="搜索案件"
        placeholder="搜索案件"
      />
    </div>
    <div class="sidebar-group">
      <div class="sidebar-label">
        我的案件 <span class="muted">{{ store.state.cases.length }}</span>
      </div>
      <div id="case-list">
        <button
          v-for="c in cases"
          :key="c.id"
          class="case"
          :class="{ active: c.id === store.current.id && !isConsult }"
          @click="selectCase(c.id)"
        >
          <strong>{{ c.title }}</strong
          ><small
            ><span class="status-dot"></span
            >{{
              P.assess(c, store.state.rules).intakeConfirmed
                ? routeName(c)
                : '案情收集中'
            }}</small
          >
        </button>
        <p v-if="!cases.length" class="note">没有匹配的案件</p>
      </div>
    </div>
    <div class="sidebar-foot">
      <button class="button small" @click="store.openDialog('rules')">
        ⚙　调整收集规则
      </button>
      <div>本地交互原型 · v2<br />示例数据，尚未连接后端</div>
    </div>
    <div class="profile">
      <b>U</b>
      <div>
        我的工作台<br /><small class="muted">固定步骤 · 逐项确认</small>
      </div>
    </div>
  </aside>
</template>
