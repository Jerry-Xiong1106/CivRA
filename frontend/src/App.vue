<script setup>
import { ref, provide, watch, nextTick } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from './stores/workspace.js';
import { useNavigation } from './composables/navigation.js';
import P from './domain/policy.js';
import { TABS, tabLocked } from './domain/presentation.js';
import WorkspaceSidebar from './components/WorkspaceSidebar.vue';
import WorkflowSteps from './components/WorkflowSteps.vue';
import WorkspaceDialogs from './components/WorkspaceDialogs.vue';
import LockIcon from './components/LockIcon.vue';
const store = useWorkspaceStore();
const { current: c, readiness: r } = storeToRefs(store);
const { route, isConsult, section, go } = useNavigation();
const upload = ref(null),
  content = ref(null);
provide('openUpload', () => upload.value?.click());
function register(event) {
  store.registerFiles(Array.from(event.target.files));
  event.target.value = '';
  go('evidence');
}
watch(
  () => route.fullPath,
  async () => {
    await nextTick();
    if (content.value) content.value.scrollTop = 0;
  }
);
</script>

<template>
  <div class="layout" :class="{ 'menu-open': store.ui.menu }">
    <button
      class="backdrop"
      aria-label="收起案件列表"
      @click="store.ui.menu = false"
    ></button>
    <WorkspaceSidebar />
    <main class="main">
      <header class="header">
        <div class="row grow">
          <button
            class="mobile-menu"
            aria-label="展开案件列表"
            @click="store.ui.menu = true"
          >
            ☰
          </button>
          <div class="grow">
            <div class="header-title">
              {{ isConsult ? '法律咨询' : c.title }}
            </div>
            <div class="header-sub">
              {{
                isConsult
                  ? '无需建案，可直接了解法律规定'
                  : `${P.phaseLabels[c.fields.phase?.value] || '程序阶段待确认'}　·　${r.intakeConfirmed ? '已确认案情摘要' : '正在整理案情'}`
              }}
            </div>
          </div>
        </div>
        <span class="pill">本地设计预览</span
        ><button
          class="button small"
          @click="go(isConsult ? 'chat' : 'review')"
        >
          {{ isConsult ? '进入案件办理' : '案情概览 ↗' }}
        </button>
      </header>
      <template v-if="!isConsult">
        <nav class="tabs" aria-label="案件功能">
          <button
            v-for="[id, title] in TABS"
            :key="id"
            class="tab"
            :class="{
              active: section === id || (id === 'chat' && section === 'review'),
              locked: tabLocked(id, r)
            }"
            :title="tabLocked(id, r) ? '点击查看进入条件' : undefined"
            @click="go(id)"
          >
            {{ title }}<LockIcon v-if="tabLocked(id, r)" />
          </button>
        </nav>
        <WorkflowSteps />
      </template>
      <div id="content" ref="content" class="content"><RouterView /></div>
    </main>
  </div>
  <input
    ref="upload"
    type="file"
    accept=".pdf,.png,.jpg,.jpeg,.docx,.txt"
    multiple
    hidden
    @change="register"
  />
  <WorkspaceDialogs />
  <div
    id="toast"
    role="status"
    aria-live="polite"
    :style="{ display: store.toastMessage ? 'block' : 'none' }"
  >
    {{ store.toastMessage }}
  </div>
</template>
