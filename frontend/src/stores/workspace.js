import { computed, reactive, ref, onScopeDispose } from 'vue';
import { defineStore } from 'pinia';
import P from '../domain/policy.js';
import { newCase, makeFact } from '../domain/seed.js';
import { routeName, routeTasks } from '../domain/presentation.js';
import { loadWorkspace, saveWorkspace } from '../services/workspaceStorage.js';

export const useWorkspaceStore = defineStore('workspace', () => {
  const state = reactive(loadWorkspace());
  const ui = reactive({
    menu: false,
    search: '',
    dialog: null,
    researchQuery: '',
    researchSearched: false
  });
  const toastMessage = ref('');
  let timer;
  onScopeDispose(() => clearTimeout(timer));
  const current = computed(
    () => state.cases.find((c) => c.id === state.activeId) || state.cases[0]
  );
  const readiness = computed(() => P.assess(current.value, state.rules));
  const options = computed(() => P.routeOptions(current.value));
  function notify(message) {
    clearTimeout(timer);
    toastMessage.value = message;
    timer = setTimeout(() => {
      toastMessage.value = '';
    }, 3600);
    timer.unref?.();
  }
  function persist() {
    try {
      saveWorkspace(state);
    } catch {
      notify('浏览器存储已满，本次改动仅保留在当前页面。');
    }
  }
  function audit(text) {
    current.value.audit.push({
      text,
      time: new Date().toLocaleString('zh-CN')
    });
  }
  function selectCase(id) {
    if (!state.cases.some((c) => c.id === id)) return false;
    if (state.activeId !== id) {
      state.activeId = id;
      persist();
    }
    ui.menu = false;
    ui.dialog = null;
    return true;
  }
  function createCase(title, domain = 'traffic') {
    const clean = title.trim();
    if (!clean || !Object.hasOwn(P.domains, domain)) return null;
    const item = newCase(clean, domain);
    state.cases.unshift(item);
    state.activeId = item.id;
    persist();
    return item;
  }
  function changeFact(id, value, { unknown = false, reason = '' } = {}) {
    const field = P.applicable(current.value, state.rules).find(
      (f) => f.id === id
    );
    if (!field) return false;
    const clean = String(value ?? '').trim();
    if (unknown) {
      if (field.mode !== 'answered' || !reason.trim()) {
        notify('本项需要明确内容或有效的未知原因。');
        return false;
      }
    } else if (
      !clean ||
      (field.options && !Object.hasOwn(field.options, clean)) ||
      (field.mode === 'known' && !P.known(makeFact(clean)))
    ) {
      notify('请填写明确内容，未知项不能计作已确认。');
      return false;
    }
    current.value.fields[id] = unknown
      ? { value: '', status: 'unknown', reason: reason.trim() }
      : makeFact(clean);
    current.value.intakeConfirmation = null;
    current.value.routeConfirmation = null;
    audit(`更新“${field.label}”，后续操作需重新核对案情。`);
    persist();
    return true;
  }
  function confirmIntake() {
    if (readiness.value.blockers.length) {
      notify('仍有必答信息未完成。');
      return false;
    }
    current.value.intakeConfirmation = {
      basis: readiness.value.signature,
      at: new Date().toISOString(),
      actor: 'local-user'
    };
    audit('用户确认本版案情摘要。');
    persist();
    return true;
  }
  function confirmRoute(id) {
    if (
      !readiness.value.canSelectRoute ||
      !options.value.some((o) => o.id === id)
    ) {
      notify('案情已变化，请重新确认。');
      return false;
    }
    current.value.route = id;
    current.value.routeConfirmation = {
      basis: readiness.value.signature,
      at: new Date().toISOString(),
      actor: 'local-user'
    };
    audit(
      `选择“${routeName(current.value)}”，实际程序阶段保持“${P.phaseLabels[current.value.fields.phase.value]}”。`
    );
    persist();
    return true;
  }
  function addStatement(value) {
    if (!value.trim()) return false;
    current.value.messages.push(value.trim());
    audit('保存一条补充陈述，尚未自动更新事实。');
    persist();
    notify('已保留补充陈述，请通过案情卡确认结构化内容。');
    return true;
  }
  function updateRules(input) {
    const rules = {};
    for (const field of P.fields) {
      const mode = input[field.id];
      if (
        !['dispute', 'role', 'phase'].includes(field.id) &&
        Object.hasOwn(P.modes, mode) &&
        mode !== field.mode
      )
        rules[field.id] = mode;
    }
    state.rules = rules;
    state.cases.forEach((c) => {
      c.intakeConfirmation = null;
      c.routeConfirmation = null;
    });
    persist();
    notify('已更新本地规则，请重新核对案情。');
  }
  function registerFiles(files) {
    for (const file of files)
      current.value.files.push({
        id: crypto.randomUUID(),
        name: file.name,
        size: file.size
      });
    persist();
    notify('已登记本地材料信息，尚未解析文件内容。');
  }
  function removeFile(id) {
    current.value.files = current.value.files.filter((f) => f.id !== id);
    persist();
  }
  function recordFeedback(index, value) {
    if (!readiness.value.routeConfirmed || !routeTasks(current.value)[index]) {
      notify('请重新确认当前处理路径。');
      return false;
    }
    if (!value.trim()) return false;
    const key = `${current.value.route}:${index}`;
    if (!current.value.tasks.includes(key)) current.value.tasks.push(key);
    audit(`用户反馈：${value.trim()}`);
    persist();
    return true;
  }
  function generateDraft(form) {
    if (!readiness.value.canDraft) {
      notify('当前不满足文书准备条件。');
      return false;
    }
    if (!form.approved || !form.wording.trim()) return false;
    const f = {
      plaintiff: form.plaintiff.trim(),
      defendant: form.defendant.trim(),
      wording: form.wording.trim()
    };
    current.value.draftForm = f;
    current.value.draft = {
      basis: readiness.value.signature,
      text: `原告：${f.plaintiff || '［姓名或名称待补充］'}\n被告：${f.defendant || '［姓名或名称待补充］'}\n身份及送达信息：［待补充并核对］\n\n诉讼请求\n${f.wording}\n\n事实与理由\n${current.value.fields.story?.value || '［待补充］'}\n\n法律依据与证据对应关系：［待检索、关联并核对］\n\n此致\n［法院名称待核实］\n\n具状人：［待签署］\n日期：［待填写］`
    };
    audit('用户确认正式表述，生成一份演示草稿。');
    persist();
    notify('已生成演示草稿，未知内容保留为待补充。');
    return true;
  }
  function draftMarkdown() {
    if (
      !readiness.value.canDraft ||
      current.value.draft?.basis !== readiness.value.signature
    ) {
      notify('请重新核对案情并生成草稿。');
      return null;
    }
    return `# 民事起诉状（本地演示草稿）\n\n${current.value.draft.text}\n\n本文件为界面演示输出，尚未填写法院原始模板。`;
  }
  function openDialog(kind, payload = {}) {
    ui.dialog = { kind, ...payload, caseId: current.value.id };
  }
  function closeDialog() {
    ui.dialog = null;
  }
  return {
    state,
    ui,
    current,
    readiness,
    options,
    toastMessage,
    notify,
    selectCase,
    createCase,
    changeFact,
    confirmIntake,
    confirmRoute,
    addStatement,
    updateRules,
    registerFiles,
    removeFile,
    recordFeedback,
    generateDraft,
    draftMarkdown,
    openDialog,
    closeDialog
  };
});
