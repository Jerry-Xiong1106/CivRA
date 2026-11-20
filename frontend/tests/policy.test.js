import test from 'node:test';
import assert from 'node:assert/strict';
import P from '../src/domain/policy.js';
function complete(domain = 'traffic') {
  const c = {
    fields: { dispute: { value: domain, status: 'confirmed' } },
    route: null
  };
  for (const f of P.applicable(c))
    c.fields[f.id] = {
      value:
        f.id === 'dispute'
          ? domain
          : f.id === 'phase'
            ? 'not_filed'
            : f.id === 'role'
              ? 'claimant'
              : f.id === 'has_repaid'
                ? 'no'
                : '明确内容',
      status: 'confirmed'
    };
  return c;
}
function confirm(c) {
  c.intakeConfirmation = { basis: P.basis(c) };
  return c;
}
function litigate(c) {
  confirm(c);
  c.route = 'litigation';
  c.routeConfirmation = { basis: P.basis(c) };
  return c;
}
test('missing facts block route and drafting even with a forged stale confirmation', () => {
  const c = complete();
  delete c.fields.goal;
  c.intakeConfirmation = { basis: P.basis(c) };
  assert.equal(P.assess(c).canSelectRoute, false);
  assert.equal(P.assess(c).canDraft, false);
});
test('all fields filled still require an explicit summary confirmation', () => {
  const c = complete();
  assert.equal(P.assess(c).blockers.length, 0);
  assert.equal(P.assess(c).canSelectRoute, false);
  confirm(c);
  assert.equal(P.assess(c).canSelectRoute, true);
});
test('unknown with reason satisfies answered field, never a known field', () => {
  const c = complete();
  c.fields.insurance = { value: '', status: 'unknown', reason: '正在查询' };
  assert.equal(P.assess(c).blockers.length, 0);
  c.fields.goal = { value: '', status: 'unknown', reason: '没有确定' };
  assert.ok(P.assess(c).blockers.some((x) => x.id === 'goal'));
});
test('unconfirmed, disputed and placeholder strings do not satisfy mandatory facts', () => {
  for (const fact of [
    { value: '明确内容', status: 'unconfirmed' },
    { value: '明确内容', status: 'disputed' },
    { value: '待补充', status: 'confirmed' }
  ]) {
    const c = complete();
    c.fields.goal = fact;
    assert.ok(P.assess(c).blockers.some((x) => x.id === 'goal'));
  }
});
test('repayment amount becomes required only when loan repayment is recorded', () => {
  const c = complete('loan');
  assert.equal(P.assess(c).blockers.length, 0);
  c.fields.has_repaid.value = 'yes';
  assert.ok(P.assess(c).blockers.some((x) => x.id === 'repaid_principal'));
});
test('confirmed litigation is required for drafting', () => {
  const c = confirm(complete());
  assert.equal(P.assess(c).canDraft, false);
  c.route = 'litigation';
  assert.equal(P.assess(c).canDraft, false);
  litigate(c);
  assert.equal(P.assess(c).canDraft, true);
  c.route = 'mediation';
  assert.equal(P.assess(c).canDraft, false);
});
test('changing a relevant fact invalidates the summary and route confirmation', () => {
  const c = litigate(complete());
  c.fields.goal.value = '新的请求';
  assert.equal(P.assess(c).intakeConfirmed, false);
  assert.equal(P.assess(c).routeConfirmed, false);
});
test('mediation is available after hearing and does not mutate the procedure phase', () => {
  const c = confirm(complete());
  c.fields.phase.value = 'hearing';
  assert.ok(P.routeOptions(c).some((x) => x.id === 'mediation'));
  assert.equal(c.fields.phase.value, 'hearing');
  litigate(c);
  assert.equal(P.assess(c).canDraft, false);
});
test('execution has settlement option and no fresh complaint entry', () => {
  const c = complete();
  c.fields.phase.value = 'execution';
  litigate(c);
  assert.ok(P.routeOptions(c).some((x) => x.title === '执行和解'));
  assert.equal(
    P.routeOptions(c).some((x) => x.id === 'mediation'),
    false
  );
  assert.equal(P.assess(c).canDraft, false);
});
test('rule changes invalidate prior confirmations and permit domain-specific levels', () => {
  const c = confirm(complete());
  assert.equal(P.assess(c, { insurance: 'known' }).intakeConfirmed, false);
  delete c.fields.insurance;
  assert.equal(P.assess(c, { insurance: 'optional' }).blockers.length, 0);
});
test('respondents have no claimant complaint draft in this prototype scope', () => {
  const c = complete();
  c.fields.role.value = 'respondent';
  litigate(c);
  assert.equal(P.assess(c).canDraft, false);
});
test('an unconfigured dispute can choose a route but cannot use an unrelated template', () => {
  const c = litigate(complete('general'));
  assert.equal(P.assess(c).canSelectRoute, true);
  assert.equal(P.assess(c).canDraft, false);
});
