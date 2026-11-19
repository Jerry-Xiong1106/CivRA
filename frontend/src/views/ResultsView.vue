<script setup>
import { computed } from 'vue';
import { useWorkspaceStore } from '../stores/workspace.js';
import { useNavigation } from '../composables/navigation.js';
import { routeName } from '../domain/presentation.js';
import PageHeading from '../components/PageHeading.vue';
const store = useWorkspaceStore(),
  { go } = useNavigation();
const rows = computed(() => {
  const c = store.current,
    r = store.readiness;
  return [
    ['案情摘要', r.intakeConfirmed ? '本版已确认' : '仍需核对', 'review'],
    ['材料记录', `${c.files.length} 份本地登记`, 'evidence'],
    [
      '处理意向',
      r.routeConfirmed ? routeName(c) : '尚未确认当前路径',
      'workflow'
    ],
    [
      '办理反馈',
      `${c.audit.filter((a) => a.text.startsWith('用户反馈')).length} 条实际反馈`,
      'visuals'
    ],
    ...(c.draft
      ? [
          [
            '起诉状演示草稿',
            c.draft.basis === r.signature
              ? '可返回核对'
              : '案情已变化，需要更新',
            'draft'
          ]
        ]
      : [])
  ];
});
</script>
<template>
  <PageHeading
    kicker="REVIEW OUTPUTS"
    title="核对本案成果"
    sub="整理已保存的内容和实际反馈，再决定下一步。成果生成不会自动标记案件办结。"
  />
  <div class="panel">
    <h3>可查看的记录</h3>
    <template v-if="store.apiMode"><div v-for="output in store.current.server?.outputs || []" :key="output.output_id + ':' + output.output_version" class="task-row"><span>{{ output.output_type }}</span><span>{{ output.text || '结构化成果' }} · 案情版本 {{ output.case_version }}</span></div><div v-for="artifact in store.artifacts" :key="artifact.id" class="task-row"><span>{{ artifact.format.toUpperCase() }}</span><span>{{ artifact.stale ? '历史案情版本，请核对后使用' : '当前案情版本' }}</span><button class="button small" @click="store.downloadArtifact(artifact.id)">下载</button></div><p v-if="!store.current.server?.outputs?.length">尚未保存后端成果。</p></template>
    <div v-for="[label, detail, view] in (store.apiMode ? [] : rows)" :key="label" class="task-row">
      <span>{{ label }}</span
      ><span>{{ detail }}</span
      ><button class="button small" @click="go(view)">查看</button>
    </div>
  </div>
  <div class="notice" style="margin-top: 20px">
    协商、调解和诉讼都可以在这里回看各自的记录。是否达成协议、提交材料、立案或开庭，以实际发生并由你记录的信息为准。
  </div>
</template>
