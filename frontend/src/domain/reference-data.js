const domainLabels = {
  traffic: '机动车交通事故',
  loan: '民间借贷',
  sale: '买卖合同'
};
const scenarios = {
  traffic: [
    {
      issue: '医疗费用与治疗经过',
      facts:
        '虚构样本设定中，诊疗记录与费用票据能对应到同一治疗经过，且已核对垫付款。',
      reason:
        '演示裁判理由段落：在该虚构样本设定下，对应费用被计入样本结果。实际案件仍需核对责任、关联性与金额。',
      check: '本案是否具备对应诊疗记录，票据是否重复，已付款项是否核清？',
      focus: ['story', 'liability', 'insurance']
    },
    {
      issue: '维修费用与责任认定',
      facts:
        '虚构样本设定中，维修项目有部分争议，责任材料与维修清单仍需分别核对。',
      reason:
        '演示裁判理由段落：该样本仅对设定中能够对应的维修项目作处理。它不表示本案会得到相同结果。',
      check: '本案责任认定与维修明细是否已经确认，争议项目具体是什么？',
      focus: ['liability', 'story']
    },
    {
      issue: '保险赔付与材料缺口',
      facts: '虚构样本设定中，保险赔付记录和剩余请求金额尚未核清。',
      reason:
        '演示裁判理由段落：该样本保留金额与材料缺口，结果分类为信息不全。',
      check: '本案投保、理赔和已付款项是否明确？',
      focus: ['insurance', 'goal']
    }
  ],
  loan: [
    {
      issue: '借款交付与本金返还',
      facts: '虚构样本设定中，借款关系、交付记录和本金金额有对应材料。',
      reason:
        '演示裁判理由段落：该样本围绕已设定的交付与本金事实组织结果展示，不构成本案结论。',
      check: '本案借款交付、本金及到期情况是否有材料对应？',
      focus: ['principal', 'story', 'when']
    },
    {
      issue: '已还款项的性质',
      facts: '虚构样本设定中，双方对部分转账属于本金还是其他款项存在分歧。',
      reason:
        '演示裁判理由段落：该样本只展示能够对应的款项，其他条目保留争议。',
      check: '本案是否发生还款，已还本金与其他款项能否区分？',
      focus: ['has_repaid', 'repaid_principal']
    },
    {
      issue: '利息约定与证据缺口',
      facts: '虚构样本设定中，利息约定的内容和来源尚未核实。',
      reason:
        '演示裁判理由段落：该样本需要补充原始约定后再分析，不用假定利率补齐结论。',
      check: '本案有无利息约定，原始材料和适用条件是否可核实？',
      focus: ['story', 'principal']
    }
  ],
  sale: [
    {
      issue: '付款与交付义务',
      facts: '虚构样本设定中，合同约定与付款、交付记录能够对应。',
      reason:
        '演示裁判理由段落：该样本按设定的履行情况组织结果展示，真实案件应独立审查。',
      check: '本案付款、交付和合同约定能否逐项对应？',
      focus: ['subject', 'performance']
    },
    {
      issue: '质量争议与处理方式',
      facts: '虚构样本设定中，部分质量问题有记录，部分项目仍存在争议。',
      reason:
        '演示裁判理由段落：该样本按不同项目展示差异化处理，不能直接作为本案请求结论。',
      check: '本案质量问题、通知和处理约定是否明确？',
      focus: ['performance', 'goal']
    },
    {
      issue: '合同内容与履行缺口',
      facts: '虚构样本设定中，交易约定和实际履行记录不完整。',
      reason: '演示裁判理由段落：该样本以补充材料为待办，保留无法确认的内容。',
      check: '本案缺少合同、付款凭证还是交付记录？',
      focus: ['subject', 'performance']
    }
  ]
};
const samples = Object.entries(domainLabels).flatMap(([domain, label]) =>
  Array.from({ length: domain === 'traffic' ? 24 : 6 }, (_, i) => {
    const scenario = scenarios[domain][i % 3],
      region = i % 2 ? '深圳示例' : '广州示例',
      court = i % 2 ? '示例法院乙' : '示例法院甲';
    return {
      id: `DEMO-${domain.toUpperCase()}-${String(i + 1).padStart(2, '0')}`,
      synthetic: true,
      domain,
      domainLabel: label,
      title: `${scenario.issue} · 模拟样本 ${String(i + 1).padStart(2, '0')}`,
      region,
      court,
      year: 2024 + (Math.floor(i / 2) % 3),
      outcome: ['请求获支持', '部分支持', '信息不全'][i % 3],
      ...scenario,
      caseNumber: null,
      sourceUrl: null,
      sourceRefs: [],
      sourceLabel: '本地界面演示数据，非真实裁判文书'
    };
  })
);
export default { samples, domainLabels };
