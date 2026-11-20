import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPinia, setActivePinia, disposePinia } from 'pinia';
import P from '../src/domain/policy.js';
import { createDemoCases } from '../src/domain/demo.js';
import { useWorkspaceStore } from '../src/stores/workspace.js';

test('完整演示具备可用草稿，借款场景保留追问，开庭场景保留阶段限制', () => {
  const [traffic, loan, mediation, sale] = createDemoCases();
  assert.equal(P.assess(traffic).canDraft, true);
  assert.equal(traffic.draft.basis, P.basis(traffic));
  assert.equal(traffic.demoAnalysis.basis, P.basis(traffic));
  assert.equal(traffic.files.length, 5);
  assert.deepEqual(
    P.assess(loan).blockers.map((f) => f.id),
    ['repaid_principal']
  );
  assert.equal(P.assess(loan).canSelectRoute, false);
  assert.equal(mediation.fields.phase.value, 'hearing');
  assert.equal(P.assess(mediation).canDraft, false);
  assert.equal(P.assess(sale).canDraft, true);
  assert.ok(
    [traffic, loan, mediation, sale].every(
      (c) => c.synthetic && c.demoScenario && c.files.every((f) => f.synthetic)
    )
  );
});

test('重复载入不覆盖原案件、既有演示修改或重复添加，草稿仍受版本门槛保护', (t) => {
  const pinia = createPinia();
  setActivePinia(pinia);
  t.after(() => disposePinia(pinia));
  const store = useWorkspaceStore();
  const original = JSON.stringify(store.state.cases);
  const originalCount = store.state.cases.length;
  store.loadDemoCases();
  assert.equal(store.state.cases.length, originalCount + 4);
  assert.equal(JSON.stringify(store.state.cases.slice(4)), original);
  assert.match(store.draftMarkdown(), /虚构演示草稿/);
  store.changeFact('goal', '演示用户修改后的请求');
  const activeId = store.current.id;
  store.loadDemoCases();
  assert.equal(store.current.id, activeId);
  assert.equal(store.current.fields.goal.value, '演示用户修改后的请求');
  assert.equal(store.state.cases.length, originalCount + 4);
  assert.equal(store.readiness.canDraft, false);
  assert.equal(store.draftMarkdown(), null);
});
