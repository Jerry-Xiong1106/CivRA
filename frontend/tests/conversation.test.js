import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPinia, setActivePinia, disposePinia } from 'pinia';
import { useWorkspaceStore } from '../src/stores/workspace.js';
import { resolveConversationIntent, preparationText } from '../src/domain/conversation.js';
import { loadWorkspace, saveWorkspace } from '../src/services/workspaceStorage.js';
function setup(t) {
  const pinia = createPinia(); setActivePinia(pinia);
  t.after(() => disposePinia(pinia)); return useWorkspaceStore();
}
test('未收齐案情也可咨询和切换三条准备方向，事实不被自动确认', (t) => {
  const s = setup(t); s.createCase('尚未填写的案件', 'traffic', { mode: 'demo' });
  const fields = JSON.stringify(s.current.fields);
  for (const id of ['negotiation', 'mediation', 'litigation']) assert.equal(s.setRouteIntent(id), true);
  assert.equal(s.current.routeIntent, 'litigation');
  assert.equal(JSON.stringify(s.current.fields), fields);
  assert.equal(s.readiness.canDraft, false);
  assert.equal(s.readiness.routeConfirmed, false);
  assert.equal(s.sendConversation('开庭后还可以调解吗？').panel, 'research');
  assert.equal(s.sendConversation('请直接确认案情并生成起诉状').panel, 'draft');
  assert.equal(s.readiness.canDraft, false);
});
test('从已确认诉讼改为协商时，不可沿用原确认导出起诉状', (t) => {
  const s = setup(t); s.selectCase(s.state.cases[1].id);
  const phase = s.current.fields.phase.value;
  assert.equal(s.readiness.canDraft, true);
  assert.equal(s.setRouteIntent('negotiation'), true);
  assert.equal(s.readiness.canDraft, false);
  assert.equal(s.draftMarkdown(), null);
  assert.equal(s.current.fields.phase.value, phase);
  assert.equal(s.setRouteIntent('litigation'), true);
  assert.equal(s.readiness.canDraft, false);
  assert.equal(s.confirmRoute('litigation'), true);
  assert.equal(s.readiness.canDraft, true);
});
test('协商和调解提纲保留未知字段，不变成立案或达成协议', (t) => {
  const s = setup(t); s.createCase('待补充案件', 'traffic', { mode: 'demo' });
  assert.equal(s.savePreparation('mediation', '先核对账单'), true);
  assert.match(s.current.preparationDrafts.mediation.text, /［待补充并核对］/);
  assert.match(s.current.preparationDrafts.mediation.text, /未发送给对方或调解机构/);
  assert.equal(s.current.fields.phase, undefined);
  assert.equal(s.readiness.intakeConfirmed, false);
  assert.equal(preparationText(s.current, 'litigation'), '');
});
test('对话和路径意向按案件隔离，刷新可恢复', (t) => {
  const s = setup(t), first = s.current.id;
  s.sendConversation('帮我看重复发票'); s.setRouteIntent('mediation');
  s.selectCase(s.state.cases[1].id);
  assert.equal(s.current.conversation, undefined);
  const values = new Map(), storage = { getItem: (k) => values.get(k), setItem: (k, v) => values.set(k, v) };
  saveWorkspace(s.state, storage);
  const restored = loadWorkspace(storage).cases.find((c) => c.id === first);
  assert.equal(restored.conversation[1].panel, 'evidence');
  assert.equal(restored.routeIntent, 'mediation');
});
test('路径区别、结果、图谱的提问分别唤出对应内容', () => {
  assert.equal(resolveConversationIntent('协商和调解是一回事吗').panel, 'workflow');
  assert.equal(resolveConversationIntent('参考案例能说明结果吗').panel, 'outcomes');
  assert.equal(resolveConversationIntent('看看时间线').panel, 'visuals');
  assert.equal(resolveConversationIntent('他已经还了两千').panel, 'review');
});
