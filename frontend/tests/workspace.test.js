import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPinia, setActivePinia, disposePinia } from 'pinia';
import { useWorkspaceStore } from '../src/stores/workspace.js';
import {
  loadWorkspace,
  saveWorkspace,
  STORAGE_KEY
} from '../src/services/workspaceStorage.js';
import { seedWorkspace } from '../src/domain/seed.js';

function setup(t) {
  const pinia = createPinia();
  setActivePinia(pinia);
  t.after(() => disposePinia(pinia));
  return useWorkspaceStore();
}
function memory() {
  const data = new Map();
  return {
    getItem: (key) => data.get(key) || null,
    setItem: (key, value) => data.set(key, value)
  };
}
const form = {
  plaintiff: '甲',
  defendant: '乙',
  wording: '请求返还本金。',
  approved: true
};
test('未完成案情时，直接调用动作也无法跳过摘要和路径确认', (t) => {
  const store = setup(t);
  assert.equal(store.confirmIntake(), false);
  assert.equal(store.confirmRoute('litigation'), false);
  assert.equal(store.generateDraft(form), false);
  assert.equal(store.draftMarkdown(), null);
});
test('必答项完成之后仍需分别确认摘要与路径', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[1].id);
  store.changeFact('principal', '20,000元');
  assert.equal(store.readiness.blockers.length, 0);
  assert.equal(store.confirmRoute('litigation'), false);
  assert.equal(store.confirmIntake(), true);
  assert.equal(store.readiness.canDraft, false);
  assert.equal(store.confirmRoute('litigation'), true);
  assert.equal(store.generateDraft(form), true);
  assert.match(store.draftMarkdown(), /请求返还本金/);
});
test('更改事实使确认失效，历史草稿保留但不能导出', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[1].id);
  store.generateDraft(form);
  const original = store.current.draft.text;
  store.changeFact('principal', '12,000元');
  assert.equal(store.readiness.intakeConfirmed, false);
  assert.equal(store.readiness.routeConfirmed, false);
  assert.equal(store.draftMarkdown(), null);
  assert.equal(store.current.draft.text, original);
  store.confirmIntake();
  store.confirmRoute('litigation');
  assert.equal(store.draftMarkdown(), null);
  store.generateDraft(form);
  assert.ok(store.draftMarkdown());
});
test('未知还款必须有原因，不被转换为零；必填信息不允许未知', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[1].id);
  assert.equal(
    store.changeFact('has_repaid', '', { unknown: true, reason: '' }),
    false
  );
  assert.equal(
    store.changeFact('has_repaid', '', {
      unknown: true,
      reason: '待核实银行流水'
    }),
    true
  );
  assert.deepEqual(
    { ...store.current.fields.has_repaid },
    { value: '', status: 'unknown', reason: '待核实银行流水' }
  );
  assert.equal(
    store.changeFact('role', '', { unknown: true, reason: '不知道' }),
    false
  );
  assert.equal(store.changeFact('counterparty', '待补充'), false);
});
test('选择调解只更新意向，开庭阶段独立保留', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[2].id);
  assert.equal(store.confirmRoute('mediation'), true);
  assert.equal(store.current.fields.phase.value, 'hearing');
  assert.equal(store.readiness.canDraft, false);
});
test('材料只保存名称大小，不使已确认事实失效', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[1].id);
  store.registerFiles([{ name: '票据.pdf', size: 100, body: 'not stored' }]);
  assert.equal(store.readiness.routeConfirmed, true);
  const file = store.current.files[0];
  assert.equal(file.body, undefined);
  assert.deepEqual(Object.keys(file).sort(), ['id', 'name', 'size']);
  store.removeFile(file.id);
  assert.equal(store.current.files.length, 0);
});
test('调整规则要求所有案件重新确认，基础字段不可降级', (t) => {
  const store = setup(t);
  store.updateRules({
    story: 'answered',
    phase: 'optional',
    role: 'optional',
    dispute: 'optional'
  });
  assert.deepEqual({ ...store.state.rules }, { story: 'answered' });
  assert.ok(
    store.state.cases.every(
      (c) => !c.intakeConfirmation && !c.routeConfirmation
    )
  );
});
test('未核对表述不能生成；生成和反馈不会自动办结', (t) => {
  const store = setup(t);
  store.selectCase(store.state.cases[1].id);
  assert.equal(store.generateDraft({ ...form, approved: false }), false);
  assert.equal(store.recordFeedback(3, '已整理材料'), true);
  assert.equal(store.generateDraft(form), true);
  assert.equal(store.current.fields.phase.value, 'not_filed');
  assert.equal(store.recordFeedback(99, '无效事项'), false);
});
test('旧 HTML v2 工作区可原样往返，保留确认依据及额外字段', () => {
  const storage = memory(),
    original = seedWorkspace();
  original.cases[1].extraMetadata = { retained: true };
  saveWorkspace(original, storage);
  assert.ok(storage.getItem(STORAGE_KEY));
  assert.deepEqual(loadWorkspace(storage), original);
});
test('损坏的存储可回退，丢失 activeId 时选用现有案件', () => {
  const storage = memory();
  storage.setItem(STORAGE_KEY, '{bad');
  assert.equal(loadWorkspace(storage).cases.length, 3);
  const data = seedWorkspace();
  data.activeId = 'missing';
  saveWorkspace(data, storage);
  assert.equal(loadWorkspace(storage).activeId, data.cases[0].id);
});
