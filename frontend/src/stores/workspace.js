import { computed, reactive, ref, onScopeDispose } from 'vue';
import { defineStore } from 'pinia';
import P from '../domain/policy.js';
import { newCase, makeFact } from '../domain/seed.js';
import { createDemoCases } from '../domain/demo.js';
import { routeName, routeTasks } from '../domain/presentation.js';
import { resolveConversationIntent, ROUTE_GUIDES, PANEL_TITLES, preparationText } from '../domain/conversation.js';
import { loadWorkspace, saveWorkspace } from '../services/workspaceStorage.js';
import { createBackendBridge } from '../services/backendBridge.js';
import { isApiCase, backendReadiness, currentWait, knownFieldLabel, researchSources, artifactList } from '../domain/backendAdapter.js';

export const useWorkspaceStore = defineStore('workspace', () => {
  const state = reactive(loadWorkspace());
  // Existing local records remain readable and explicit demos. New cases use
  // the API; no historical material or statement is uploaded automatically.
  state.consultation ||= { ...newCase('法律咨询', 'general'), mode: 'api' };
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
  const apiMode = computed(() => isApiCase(current.value));
  const readiness = computed(() => apiMode.value ? backendReadiness(current.value) : P.assess(current.value, state.rules));
  const options = computed(() => apiMode.value ? ROUTE_GUIDES.map(g => ({ ...g, title: g.title })) : P.routeOptions(current.value));
  const bridge = createBackendBridge({ persist, notify });
  onScopeDispose(() => bridge.dispose());
  const busy = computed(() => apiMode.value && !!(current.value.server?.sending || current.value.server?.syncing || current.value.server?.pending || current.value.server?.uploading));
  const wait = computed(() => currentWait(current.value));
  const sources = computed(() => researchSources(current.value));
  const artifacts = computed(() => artifactList(current.value));
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
  function createCase(title, domain = 'traffic', { mode = 'api' } = {}) {
    const clean = title.trim();
    if (!clean || !Object.hasOwn(P.domains, domain)) return null;
    const item = newCase(clean, domain);
    item.mode = mode;
    if (mode === 'api' && domain !== 'general') item.caseTypeHint = P.domains[domain];
    state.cases.unshift(item);
    state.activeId = item.id;
    persist();
    return item;
  }
  function loadDemoCases() {
    const demos = createDemoCases(state.rules);
    const added = demos.filter(
      (demo) => !state.cases.some((c) => c.demoScenario === demo.demoScenario)
    );
    state.cases.unshift(...added);
    const selected = state.cases.find((c) => c.demoScenario === 'full-traffic');
    selectCase(selected.id);
    ui.researchQuery = '开庭以后还能调解吗？';
    ui.researchSearched = true;
    persist();
    notify('已载入虚构演示案件；已有案件和修改继续保留。');
    return selected;
  }
  function changeFact(id, value, { unknown = false, reason = '' } = {}) {
    if (apiMode.value) {
      const field = readiness.value.items.find(f => f.id === id);
      return bridge.submit(current.value, '我核对记录“' + (field?.label || knownFieldLabel(id)) + '”：' + (unknown ? '目前不清楚，原因：' + reason : String(value)) + '。请据此更新记录，未知内容保留待补充。');
    }
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
    if (apiMode.value) return bridge.submit(current.value, '我已核对当前展示的案情记录：\n' + readiness.value.items.map(f => f.label + '：' + f.fact.value).join('\n') + '\n以上已提供的内容与我的陈述一致，尚未提供的信息不要补造。请记录本次确认。');
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
    if (apiMode.value) return bridge.submit(current.value, '', { routeChoice: id });
    if (
      !readiness.value.canSelectRoute ||
      !options.value.some((o) => o.id === id)
    ) {
      notify('案情已变化，请重新确认。');
      return false;
    }
    current.value.route = id;
    current.value.routeIntent = id;
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
    if (apiMode.value) return bridge.submit(current.value, value);
    if (!value.trim()) return false;
    current.value.messages.push(value.trim());
    audit('保存一条补充陈述，尚未自动更新事实。');
    persist();
    notify('已保留补充陈述，请通过案情卡确认结构化内容。');
    return true;
  }
  function appendTurn(role, text, panel = '') {
    current.value.conversation ||= [];
    current.value.conversation.push({ id: crypto.randomUUID(), role, text, panel, at: new Date().toISOString() });
  }
  function sendConversation(value) {
    const clean = String(value || '').trim();
    if (!clean) return null;
    if (apiMode.value) return bridge.submit(current.value, clean);
    const reply = resolveConversationIntent(clean);
    appendTurn('user', clean);
    appendTurn('assistant', reply.text, reply.panel);
    persist();
    return reply;
  }
  function addWorkCard(panel, text) {
    if (apiMode.value) return; // Real cards come only from server results.
    if (!PANEL_TITLES[panel]) return;
    appendTurn('assistant', text, panel);
    persist();
  }
  function setRouteIntent(id) {
    if (apiMode.value) return bridge.submit(current.value, '', { routeChoice: id });
    const guide = ROUTE_GUIDES.find((item) => item.id === id);
    if (!guide) return false;
    if (current.value.routeIntent === id) return true;
    current.value.routeIntent = id;
    if (current.value.route !== id) current.value.routeConfirmation = null;
    audit(`准备方向调整为“${guide.title}”，实际程序阶段未改变。`);
    appendTurn('assistant', `已记录：先按“${guide.title}”准备。共用案情和材料继续保留，你也可以随时调整方向。`, 'workflow');
    persist();
    return true;
  }
  function savePreparation(id, notes = '') {
    if (apiMode.value) return bridge.submit(current.value, '请根据当前案情整理' + (ROUTE_GUIDES.find(g => g.id === id)?.output || '准备事项') + '，说明所需材料、未知信息和下一步。我的补充：' + (notes.trim() || '暂无。'));
    const text = preparationText(current.value, id, notes);
    if (!text) return false;
    current.value.preparationNotes ||= {};
    current.value.preparationDrafts ||= {};
    current.value.preparationNotes[id] = notes;
    current.value.preparationDrafts[id] = { text, basis: readiness.value.signature, files: current.value.files.map((f) => f.id) };
    appendTurn('assistant', `已整理${ROUTE_GUIDES.find((item) => item.id === id).output}，未知信息继续留空。你可以在卡片中查看、修改输入并下载；尚未对外发送。`, 'workflow');
    persist();
    return true;
  }
  function updateRules(input) {
    if (apiMode.value) { notify('真实案件的收集与核验规则由后端管理，前端不能修改。'); return false; }
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
    if (apiMode.value) return bridge.upload(current.value, files);
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
    if (apiMode.value) { notify('当前后端没有删除材料接口，原文件继续保留。可在对话中说明哪些材料不应采用。'); return false; }
    current.value.files = current.value.files.filter((f) => f.id !== id);
    persist();
  }
  function recordFeedback(index, value) {
    if (apiMode.value) return bridge.submit(current.value, '实际办理情况补充：' + value);
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
    if (apiMode.value) {
      if (!form.approved || !form.wording.trim()) return false;
      return bridge.submit(current.value, '我希望生成起诉状草稿。\n原告：' + (form.plaintiff.trim() || '待补充') + '\n被告：' + (form.defendant.trim() || '待补充') + '\n我核对并同意采用的请求表述：\n' + form.wording.trim() + '\n请核对现有案情和证据，缺失信息继续标注待补充。请提供可下载的 Markdown、Word 和 PDF 草稿；没有完成的格式请说明。');
    }
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
    appendTurn('assistant', '已按你确认的正式表述生成一份演示草稿。可在这里预览与下载，未知信息保留为待补充。', 'draft');
    persist();
    notify('已生成演示草稿，未知内容保留为待补充。');
    return true;
  }
  function draftMarkdown() {
    if (apiMode.value) return null;
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
  function refreshCase(c = current.value) { return isApiCase(c) ? bridge.sync(c) : Promise.resolve(); }
  function retryMessage(c = current.value) { return bridge.retry(c); }
  async function downloadArtifact(id) {
    const result = await bridge.download(current.value, id);
    if (!result) return false;
    const url = URL.createObjectURL(result.blob), link = document.createElement('a');
    link.href = url; link.download = result.filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); return true;
  }
  function searchLegal(value, consultation = false) {
    const c = consultation ? state.consultation : current.value;
    if (!isApiCase(c)) return null;
    if (currentWait(c)) return bridge.submit(c, value);
    return bridge.submit(c, '仅咨询法律，请检索适用条款并说明原文出处、条款编号、来源版本与链接；无需推进具体案件办理。问题：' + value);
  }
  function replyRoute(id, c = current.value) { return bridge.submit(c, '', { routeChoice: id }); }
  return {
    state,
    apiMode, busy, wait, sources, artifacts, refreshCase, retryMessage, downloadArtifact, searchLegal, replyRoute,
    ui,
    current,
    readiness,
    options,
    toastMessage,
    notify,
    selectCase,
    createCase,
    loadDemoCases,
    changeFact,
    confirmIntake,
    confirmRoute,
    addStatement,
    sendConversation,
    addWorkCard,
    setRouteIntent,
    savePreparation,
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
