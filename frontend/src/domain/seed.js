import P from './policy.js';
const uid = () => crypto.randomUUID();
const clone = (value) => JSON.parse(JSON.stringify(value));
export const makeFact = (value, status = 'confirmed') => ({ value, status });
export function newCase(title, domain = 'traffic') {
  return {
    id: uid(),
    title,
    fields: { dispute: makeFact(domain) },
    messages: [],
    files: [],
    route: null,
    intakeConfirmation: null,
    routeConfirmation: null,
    draft: null,
    draftForm: {},
    tasks: [],
    audit: []
  };
}
export function seedWorkspace() {
  const traffic = newCase('交通事故 · 医疗费用赔偿');
  traffic.fields = {
    dispute: makeFact('traffic'),
    role: makeFact('claimant'),
    counterparty: makeFact('王某，另一方机动车驾驶人', 'unconfirmed'),
    story: makeFact(
      '11月5日在广州发生交通事故，我受伤后就诊，已经产生医疗费用。双方尚未就赔偿达成一致。'
    ),
    when: makeFact('2026年11月5日'),
    where: makeFact('广东省广州市'),
    phase: makeFact('not_filed'),
    liability: {
      value: '',
      status: 'unknown',
      reason: '暂未取得事故认定书，等待交警反馈'
    },
    insurance: {
      value: '',
      status: 'unknown',
      reason: '暂不清楚对方投保情况，准备向对方了解'
    }
  };
  traffic.narrative =
    '我在广州出了交通事故，11月5日受伤去医院看病。现在已经有一些医疗票据，但还没跟对方谈好赔偿，想先把事情理清楚。';
  const loan = newCase('借款未归还 · 诉讼准备', 'loan');
  loan.fields = {
    dispute: makeFact('loan'),
    role: makeFact('claimant'),
    counterparty: makeFact('李某，借款人'),
    story: makeFact('我向李某转账借款，约定还款期限已过，催要后尚未归还。'),
    when: makeFact('2026年3月'),
    where: makeFact('广东省深圳市'),
    phase: makeFact('not_filed'),
    goal: makeFact('要求归还实际尚欠借款本金'),
    principal: makeFact('30,000元'),
    has_repaid: makeFact('no')
  };
  loan.intakeConfirmation = {
    basis: P.basis(loan),
    at: '演示记录',
    actor: 'demo-user'
  };
  loan.route = 'litigation';
  loan.routeConfirmation = {
    basis: P.basis(loan),
    at: '演示记录',
    actor: 'demo-user'
  };
  const mediation = newCase('已开庭 · 调解意向核对');
  mediation.fields = clone(traffic.fields);
  mediation.fields.counterparty.status = 'confirmed';
  mediation.fields.phase = makeFact('hearing');
  mediation.fields.goal = makeFact('希望在保留诉讼进度的同时，了解调解渠道');
  mediation.intakeConfirmation = {
    basis: P.basis(mediation),
    at: '演示记录',
    actor: 'demo-user'
  };
  mediation.route = 'mediation';
  mediation.routeConfirmation = {
    basis: P.basis(mediation),
    at: '演示记录',
    actor: 'demo-user'
  };
  return {
    version: 2,
    activeId: traffic.id,
    cases: [traffic, loan, mediation],
    rules: {}
  };
}
