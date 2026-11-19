import P from './policy.js';
export const TABS = [
  ['chat', '案情沟通'],
  ['visuals', '案情图谱']
];
export const displayFact = (field, fact) =>
  fact?.status === 'unknown'
    ? `暂不清楚 · ${fact.reason}`
    : field.options?.[fact?.value] || fact?.value || '待补充';
export function routeName(c) {
  if (c.routeIntent) return { negotiation: '协商解决', mediation: '调解解决', litigation: '诉讼准备' }[c.routeIntent] || '尚未选择';
  return (
    P.routeOptions(c).find((r) => r.id === c.route)?.title ||
    { mediation: '调解', negotiation: '协商', litigation: '诉讼事项' }[
      c.route
    ] ||
    '尚未选择'
  );
}
export function tabLocked(id, r) {
  return (
    (id === 'workflow' && !r.canSelectRoute) ||
    (id === 'draft' && !r.canDraft) ||
    (id === 'outcomes' && !r.intakeConfirmed)
  );
}
export function routeTasks(c) {
  return c.route === 'litigation' && c.fields.phase?.value === 'not_filed'
    ? [
        { text: '核对请求、金额与材料', view: 'evidence' },
        { text: '查阅法律依据和适用条件', view: 'research' },
        { text: '选择模板，核对起诉状草稿', view: 'draft' },
        { text: '记录实际办理结果' }
      ]
    : [
        { text: '整理本次沟通的请求与材料', view: 'evidence' },
        { text: '核实适用渠道和办理条件', view: 'research' },
        { text: '记录实际沟通或办理反馈' }
      ];
}
export function defaultWording(c) {
  return `请求依法处理以下事项：${c.fields.goal?.value?.replace(/[。.!！]+$/, '') || '［待补充］'}。具体请求项目、金额及依据仍需逐项核对。`;
}
