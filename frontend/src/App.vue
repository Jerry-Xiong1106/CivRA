<script setup>
import { ref, provide, watch, nextTick } from 'vue';
import { storeToRefs } from 'pinia';
import { useWorkspaceStore } from './stores/workspace.js';
import { useNavigation } from './composables/navigation.js';
import P from './domain/policy.js';
import { TABS } from './domain/presentation.js';
import WorkspaceSidebar from './components/WorkspaceSidebar.vue';
import WorkspaceDialogs from './components/WorkspaceDialogs.vue';
const store = useWorkspaceStore();
const { current: c, readiness: r } = storeToRefs(store);
const { route, isConsult, section, go } = useNavigation();
const upload = ref(null),
  content = ref(null);
provide('openUpload', () => upload.value?.click());
async function register(event) {
  const files = Array.from(event.target.files);
  if (!files.length) return;
  const caseId = store.current.id, apiMode = store.apiMode;
  const result = await store.registerFiles(files);
  event.target.value = '';
  if (store.current.id !== caseId) return;
  if (apiMode) {
    go('evidence');
    if (result?.length && store.wait?.kind !== 'step_confirmation') await store.sendConversation('请解析我刚上传的材料，整理内容并检查重复、冲突和缺失；不确定的信息请让我核对。');
    else if (result?.length) store.notify('材料已上传，确认或修改当前步骤后，助手会继续使用这些材料。');
    return;
  }
  store.addWorkCard('evidence', `已登记 ${files.length} 份材料，可以在这里查看并补充。当前预览尚未解析文件内容。`);
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
  <div class="layout conversation-workspace" :class="{ 'menu-open': store.ui.menu }">
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
        <span class="pill">{{
          isConsult || store.apiMode ? '后端对话' : c.synthetic ? '虚构案件演示' : '本地历史预览'
        }}</span
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
              active: section === id
            }"
            @click="go(id)"
          >
            {{ title }}
          </button>
        </nav>
      </template>
      <div id="content" ref="content" class="content" :class="{ 'is-conversation': !isConsult && section === 'chat' }"><RouterView /></div>
    </main>
  </div>
  <input
    ref="upload"
    type="file"
    accept=".pdf,.png,.jpg,.jpeg,.webp,.docx,.txt"
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
