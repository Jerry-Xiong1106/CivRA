const phaseLabels = {
  not_filed: '尚未起诉',
  filed: '已立案，尚未开庭',
  hearing: '已开庭，尚未判决',
  judgment: '已收到判决，效力待核实',
  appeal: '二审中',
  effective: '裁判已生效',
  execution: '执行中',
  closed: '已处理完毕'
};
const domains = {
  traffic: '机动车交通事故',
  loan: '民间借贷',
  sale: '买卖合同',
  general: '其他／待进一步分类'
};
const roles = {
  claimant: '提出请求的一方',
  respondent: '被要求承担责任的一方',
  other: '其他参与人'
};
const fields = [
  {
    id: 'dispute',
    label: '纠纷类型',
    mode: 'known',
    kind: 'select',
    options: domains,
    question: '这起纠纷主要涉及什么？',
    group: '基本案情'
  },
  {
    id: 'role',
    label: '我的角色',
    mode: 'known',
    kind: 'select',
    options: roles,
    question: '你在纠纷中是什么角色？',
    group: '基本案情'
  },
  {
    id: 'counterparty',
    label: '对方及双方关系',
    mode: 'known',
    question: '对方是谁，与你是什么关系？初期可填写称呼或身份描述。',
    group: '基本案情'
  },
  {
    id: 'story',
    label: '主要经过',
    mode: 'known',
    kind: 'textarea',
    question: '请描述发生了什么，以及双方现在争议在哪里。',
    group: '基本案情'
  },
  {
    id: 'when',
    label: '发生时间',
    mode: 'answered',
    question: '事情大约何时发生？不清楚时可以记录原因。',
    group: '基本案情'
  },
  {
    id: 'where',
    label: '相关地点',
    mode: 'answered',
    question: '事件发生或合同履行的地点在哪里？这里暂不作管辖判断。',
    group: '基本案情'
  },
  {
    id: 'phase',
    label: '实际程序阶段',
    mode: 'known',
    kind: 'select',
    options: phaseLabels,
    question: '案件是否已起诉、开庭或进入执行？',
    group: '基本案情'
  },
  {
    id: 'goal',
    label: '希望解决什么',
    mode: 'known',
    question: '你希望对方做什么，或者希望解决哪一个问题？',
    group: '基本案情'
  },
  {
    id: 'liability',
    label: '责任认定情况',
    mode: 'answered',
    domain: 'traffic',
    question: '是否取得事故责任认定书？没有取得或不知道时请说明。',
    group: '交通事故补充'
  },
  {
    id: 'insurance',
    label: '保险情况',
    mode: 'answered',
    domain: 'traffic',
    question: '是否了解交强险、商业险或理赔情况？暂不清楚可以记录。',
    group: '交通事故补充'
  },
  {
    id: 'principal',
    label: '本笔借款本金',
    mode: 'answered',
    domain: 'loan',
    question: '实际借出了多少钱？请与利息分开。',
    group: '借贷补充'
  },
  {
    id: 'has_repaid',
    label: '是否发生还款',
    mode: 'answered',
    domain: 'loan',
    kind: 'select',
    options: { yes: '有还款', no: '没有还款' },
    question: '对方是否还过款？未知不能按零元处理。',
    group: '借贷补充'
  },
  {
    id: 'repaid_principal',
    label: '已归还的本金',
    mode: 'answered',
    domain: 'loan',
    condition: 'has_repaid',
    question: '已还款中，明确属于本金的金额是多少？无法区分时请记录。',
    group: '借贷补充'
  },
  {
    id: 'subject',
    label: '交易标的与约定',
    mode: 'answered',
    domain: 'sale',
    question: '买卖什么、约定怎么交付和付款？',
    group: '合同补充'
  },
  {
    id: 'performance',
    label: '付款与交付情况',
    mode: 'answered',
    domain: 'sale',
    question: '已经付款或交付了哪些，争议是什么？',
    group: '合同补充'
  }
];
const modes = {
  known: '必填且确认',
  answered: '必须回应，可暂不清楚',
  optional: '建议补充'
};
const unknownText =
  /^(未知|不知道|不清楚|暂不清楚|待补充|待确认|未确认|不详|unknown|n\/a)$/i;
const known = (f) =>
  Boolean(
    f &&
      String(f.value ?? '').trim() &&
      !unknownText.test(String(f.value).trim()) &&
      f.status === 'confirmed'
  );
const answered = (f) =>
  known(f) || Boolean(f?.status === 'unknown' && String(f.reason || '').trim());
function applicable(c, overrides = {}) {
  return fields
    .filter(
      (f) =>
        (!f.domain || f.domain === c.fields.dispute?.value) &&
        (!f.condition || c.fields[f.condition]?.value === 'yes')
    )
    .map((f) => ({ ...f, mode: overrides[f.id] || f.mode }));
}
function basis(c, overrides = {}) {
  return JSON.stringify({ rules: overrides, fields: c.fields });
}
function assess(c, overrides = {}) {
  const items = applicable(c, overrides).map((f) => {
    const value = c.fields[f.id];
    const satisfied =
      f.mode === 'optional' ||
      (f.mode === 'known' ? known(value) : answered(value));
    return {
      ...f,
      satisfied,
      fact: value,
      reason: satisfied
        ? ''
        : value?.status === 'disputed'
          ? '有异议，需核实'
          : value?.status === 'unknown'
            ? '本项需要明确内容'
            : value?.value
              ? '已记录，待你确认'
              : '尚未填写'
    };
  });
  const blockers = items.filter((f) => !f.satisfied);
  const required = items.filter((f) => f.mode !== 'optional');
  const signature = basis(c, overrides);
  const intakeConfirmed =
    blockers.length === 0 && c.intakeConfirmation?.basis === signature;
  const routeConfirmed =
    intakeConfirmed &&
    c.routeConfirmation?.basis === signature &&
    Boolean(c.route);
  const draftReasons = [];
  if (!intakeConfirmed) draftReasons.push('先补齐必答项，并确认本版案情摘要');
  if (!routeConfirmed || c.route !== 'litigation')
    draftReasons.push('由你明确选择并确认诉讼路径');
  if (!['traffic', 'loan', 'sale'].includes(c.fields.dispute?.value))
    draftReasons.push('本轮文书模板先支持交通事故、民间借贷和买卖合同');
  if (c.fields.role?.value !== 'claimant')
    draftReasons.push('本轮起诉状工作台仅支持提出请求的一方');
  if (c.fields.phase?.value !== 'not_filed')
    draftReasons.push(
      '本轮起诉状工作台仅支持尚未起诉的案件，其他阶段需选择适用文书'
    );
  return {
    items,
    blockers,
    required,
    completed: required.filter((f) => f.satisfied).length,
    signature,
    intakeConfirmed,
    routeConfirmed,
    canSelectRoute: intakeConfirmed,
    canDraft: draftReasons.length === 0,
    draftReasons
  };
}
function routeOptions(c) {
  const phase = c.fields.phase?.value;
  if (phase === 'closed') return [];
  if (phase === 'execution')
    return [
      {
        id: 'negotiation',
        title: '执行和解',
        description: '记录协商意向和履行情况，执行进度独立保留。'
      },
      {
        id: 'litigation',
        title: '继续执行事项',
        description: '整理执行中的问题和材料，不新建起诉状。'
      }
    ];
  if (phase === 'effective')
    return [
      {
        id: 'negotiation',
        title: '沟通履行',
        description: '核对生效文书和实际履行情况。'
      },
      {
        id: 'litigation',
        title: '核实执行条件',
        description: '先查明文书效力和履行期限，再确认适用事项。'
      }
    ];
  if (phase === 'judgment')
    return [
      {
        id: 'negotiation',
        title: '沟通解决',
        description: '保留判决信息，并记录双方处理意向。'
      },
      {
        id: 'litigation',
        title: '核实判决后程序',
        description: '先确认文书、送达及效力，后续事项另行核实。'
      }
    ];
  return [
    {
      id: 'negotiation',
      title: '协商解决',
      description: '整理沟通要点、请求和材料，由你与对方沟通。'
    },
    {
      id: 'mediation',
      title:
        phase === 'not_filed'
          ? '了解调解渠道'
          : phase === 'appeal'
            ? '二审中申请调解'
            : '诉讼中申请调解',
      description: '核实双方意愿和当前适用渠道，记录实际反馈。'
    },
    {
      id: 'litigation',
      title: phase === 'not_filed' ? '诉讼准备' : '继续处理诉讼',
      description:
        phase === 'not_filed'
          ? '核对诉讼请求、材料和管辖，再准备起诉状草稿。'
          : '围绕当前程序整理证据、争议和待办事项。'
    }
  ];
}
export default {
  fields,
  modes,
  domains,
  roles,
  phaseLabels,
  known,
  answered,
  applicable,
  basis,
  assess,
  routeOptions
};
