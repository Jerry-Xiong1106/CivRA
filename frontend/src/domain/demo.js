import P from './policy.js';
import { seedWorkspace, newCase, makeFact } from './seed.js';

const date = '2026-11-17T09:00:00+08:00';
const file = (name, size) => ({
  id: crypto.randomUUID(),
  name: `【模拟】${name}`,
  size,
  synthetic: true
});

function prepare(item, scenario, rules, route = 'litigation') {
  item.demoScenario = scenario;
  item.synthetic = true;
  item.audit = [
    {
      time: '2026/11/17 09:00',
      text: '演示预设：此案件及材料、反馈、草稿均为虚构，用于体验界面。'
    }
  ];
  const basis = P.basis(item, rules);
  item.intakeConfirmation = { basis, at: date, actor: 'demo-user' };
  item.route = route;
  item.routeConfirmation = { basis, at: date, actor: 'demo-user' };
  return item;
}

function withDraft(item, rules, plaintiff, defendant, wording) {
  item.draftForm = { plaintiff, defendant, wording };
  item.draft = {
    basis: P.basis(item, rules),
    text: `【虚构演示草稿，仅用于体验界面】\n\n原告：${plaintiff}\n被告：${defendant}\n身份及送达信息：［待核对］\n\n诉讼请求\n${wording}\n\n事实与理由\n${item.fields.story.value}\n\n证据与法律依据：［待检索、关联并核对］\n\n此致\n［法院名称待核实］\n\n具状人：［待签署］\n日期：［待填写］`
  };
  item.audit.push({
    time: '2026/11/17 09:15',
    text: '演示预设：用户已核对表述，生成一份可预览、下载的模拟草稿。'
  });
}

export function createDemoCases(rules = {}) {
  const [traffic, loan, mediation] = seedWorkspace().cases;
  traffic.title = '演示 · 交通事故全流程';
  traffic.fields.counterparty =
    makeFact('王某，另一方机动车驾驶人（虚构人物）');
  traffic.fields.goal = makeFact(
    '核对医疗费用 2,400 元及是否存在重复票据，再准备赔偿请求'
  );
  traffic.fields.story = makeFact(
    '虚构案情：11月5日在广州发生交通事故。张某就诊后取得两张医疗票据，金额分别为1,600元和800元；扫描PDF中另有一页重复出现的800元票据。双方尚未达成赔偿协议。'
  );
  traffic.fields.liability = makeFact(
    '演示材料中设有事故认定书，具体责任内容需要核对原件'
  );
  traffic.fields.insurance = makeFact(
    '演示设定为对方已投保交强险，具体保单与理赔情况需要核对'
  );
  traffic.narrative =
    '【虚构演示】我受伤后去医院就诊，票据上有1,600元和800元两笔费用，扫描件里还有重复页。我想先核对证据，再准备诉讼材料。';
  traffic.messages = [
    '【演示补充】同一张800元票据重复扫描了一次，不能仅凭文件数量相加。',
    '【演示补充】已经尝试协商，目前还没有收到赔付款。'
  ];
  traffic.files = [
    file('事故认定书.pdf', 184320),
    file('医疗票据_含重复扫描页.pdf', 983040),
    file('门诊诊疗记录.pdf', 368640),
    file('赔偿沟通记录.png', 245760),
    file('保险信息登记.txt', 2048)
  ];
  prepare(traffic, 'full-traffic', rules);
  traffic.tasks = ['litigation:3'];
  traffic.audit.push({
    time: '2026/11/17 09:10',
    text: '用户反馈：【模拟】已整理医疗材料；重复页、责任认定与保险信息仍待核对。'
  });
  traffic.demoAnalysis = {
    basis: P.basis(traffic, rules),
    files: traffic.files.map((f) => f.id),
    cards: [
      [
        '需要证明什么',
        '演示核对方向：事故经过、责任认定、就诊与事故的关联，以及每张票据对应的真实支出。预设两笔不同票据合计2,400元，重复扫描页不作为新增支出。'
      ],
      [
        '哪些材料仍需补充',
        '演示补证清单：核对事故认定书原件、完整诊疗记录、票据编号、是否已有垫付款，以及保险保单和理赔反馈。这里没有实际执行OCR或证据审查。'
      ],
      [
        '哪些因素会改变分析',
        '演示比较条件：若票据重复或已有支付，待核对金额会变化；若诊疗关联、责任比例或保险范围存在争议，需要重新核实。此处不预测胜诉率。'
      ]
    ]
  };
  withDraft(
    traffic,
    rules,
    '张某（虚构）',
    '王某（虚构）',
    '请求就与本次事故相关的医疗费用进行赔偿。演示金额合计2,400元；具体请求项目、责任、已付款项及金额仍需逐项核对。'
  );

  loan.title = '演示 · 借款追问与核对';
  loan.demoScenario = 'loan-intake';
  loan.synthetic = true;
  loan.fields.has_repaid = makeFact('yes');
  loan.fields.goal = makeFact('先核对还款性质，再确认实际尚欠本金');
  loan.narrative =
    '【虚构演示】我借出30,000元，对方后来转回5,000元，但我暂时不能确认这笔钱是还本金还是支付其他款项。';
  loan.intakeConfirmation = null;
  loan.route = null;
  loan.routeConfirmation = null;
  loan.files = [file('借据.pdf', 204800), file('转账记录.png', 409600)];
  loan.audit = [
    {
      time: '2026/11/17 09:00',
      text: '演示预设：等待用户核对已归还的本金，未知金额不自动按零处理。'
    }
  ];

  mediation.title = '演示 · 已开庭后申请调解';
  mediation.narrative =
    '【虚构演示】案件已经开过庭，还没有收到判决。我希望了解调解，并保留目前的诉讼进度。';
  mediation.files = [
    file('开庭通知.pdf', 122880),
    file('调解意向记录.txt', 1536)
  ];
  prepare(mediation, 'hearing-mediation', rules, 'mediation');
  mediation.tasks = ['mediation:2'];
  mediation.audit.push({
    time: '2026/11/17 09:10',
    text: '用户反馈：【模拟】已表达调解意向，尚未达成协议；实际阶段仍是已开庭、尚未判决。'
  });

  const sale = newCase('演示 · 买卖合同文书', 'sale');
  sale.fields = {
    dispute: makeFact('sale'),
    role: makeFact('claimant'),
    counterparty: makeFact('示例商家乙，与我订立买卖合同'),
    story: makeFact(
      '虚构案情：约定购买一件商品，已支付600元，约定交付日期已过，尚未收到商品。双方对继续交付或退还已付款项仍在沟通。'
    ),
    when: makeFact('2026年10月20日'),
    where: makeFact('广东省深圳市'),
    phase: makeFact('not_filed'),
    goal: makeFact('核对合同约定及履行情况，准备请求退还已付款项'),
    subject: makeFact('一件演示商品，约定价格1,200元'),
    performance: makeFact('演示记录显示已支付600元，尚未交付商品')
  };
  sale.files = [file('买卖合同.pdf', 204800), file('付款记录.png', 102400)];
  prepare(sale, 'sale-draft', rules);
  withDraft(
    sale,
    rules,
    '示例买方甲（虚构）',
    '示例商家乙（虚构）',
    '请求依法处理已付款项返还事项，演示金额600元。请求适用条件、合同约定及实际履行情况仍需核对。'
  );
  return [traffic, loan, mediation, sale];
}
