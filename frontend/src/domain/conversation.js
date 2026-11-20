export const PANEL_TITLES = {
  research: '法律依据', workflow: '处理路径', evidence: '证据材料',
  outcomes: '结果分析', draft: '起诉状草稿', review: '案情记录', results: '已有成果'
};
export const ROUTE_GUIDES = [
  { id: 'negotiation', title: '协商解决', lead: '先和对方谈一谈',
    description: '由你或代理人与对方沟通，先把诉求、依据和可接受的安排说清楚。',
    materials: ['双方关系、事情经过与主要争议', '支持金额或责任的关键材料', '沟通记录，以及希望约定的履行方式和时间'],
    output: '协商沟通提纲',
    note: '整理关键材料即可开始准备；不是要求先提交一套法院证据目录。对外发送前，由你核对内容。' },
  { id: 'mediation', title: '调解解决', lead: '请第三方协助沟通',
    description: '由调解组织、调解员或法院主持。需要核实适用渠道和双方参与意愿。',
    materials: ['身份、联系方式及纠纷说明', '主要诉求、争议焦点及相关材料', '已有沟通情况；已诉案件补充案号与程序信息'],
    output: '调解准备提纲',
    note: '立案前、诉讼中可能适用不同调解渠道。具体申请材料以承办组织要求为准；软件不会代替第三方主持调解。' },
  { id: 'litigation', title: '诉讼准备', lead: '准备通过法院解决',
    description: '核对当事人、诉讼请求、事实和依据，整理证据，再准备适用文书。',
    materials: ['原被告身份与联系信息、管辖待核实事项', '请求与金额明细、证据目录和对应关系', '按实际阶段准备起诉状或其他诉讼材料'],
    output: '起诉状草稿与材料核对',
    note: '起诉前就应开始整理材料。已开庭的案件保留当前阶段，不重新生成初次起诉状。' }
];

// Local preview dispatcher. Production replaces this with validated Agent events.
// User text only requests a view; it cannot confirm facts, choose a route or submit anything.
export function resolveConversationIntent(input) {
  const text = String(input || '').trim();
  if (/区别|一回事|一件事/.test(text) && /协商/.test(text) && /调解/.test(text))
    return { panel: 'workflow', text: '协商通常是双方自己谈；调解由第三方主持促成协议。两种方式都需要关键事实和材料，但不要求先完成一套诉讼卷宗。可以先比较，再决定准备方向。' };
  if (/图谱|时间线|可视化/.test(text))
    return { panel: 'visuals', text: '可以在案情图谱里查看时间线，以及单独标注的模拟参考样本。返回后可以继续这段对话。' };
  if (/起诉状|模板|草稿|文书/.test(text))
    return { panel: 'draft', text: '我把起诉状工作面板放在这里。可以先查看法院模板来源；生成草稿前，再核对必要案情、诉讼方向和正式表述。' };
  if (/证据|材料|票据|发票|重复|上传|扫描|OCR/i.test(text))
    return { panel: 'evidence', text: '材料可以随时整理，协商、调解和诉讼都能复用。这里保留重复与缺失提醒；当前预览只登记文件，真实文件尚未执行识别。' };
  if (/法律|法条|依据|规定|法发|开庭.*调解|调解.*开庭/.test(text))
    return { panel: 'research', text: '可以先了解法律依据，不需要先填完整个案件。展开卡片能查看来源和原文；当前仅展示内置的官方来源示例。' };
  if (/结果|胜诉|支持|风险|参考案例|相似案件/.test(text))
    return { panel: 'outcomes', text: '结果分析会区分支持条件、证据缺口和可能改变判断的因素。模拟参考案例只作展示，不能用于推算本案胜诉率。' };
  if (/协商|调解|诉讼|起诉|路径|怎么处理|怎么解决/.test(text))
    return { panel: 'workflow', text: '你可以比较协商解决、调解解决和诉讼准备，随时切换准备方向。三者没有必须依次完成的顺序；选一个方向也不会改变案件实际阶段。' };
  return { panel: 'review', text: '这段补充已经保存在对话中。请在案情记录里核对对应事实；预览不会把自由输入直接认定为已经确认的事实。也可以继续问法律问题或整理材料。' };
}

export function routePreference(c) {
  return c.routeIntent || c.route || '';
}

export function preparationText(c, id, notes = '') {
  const guide = ROUTE_GUIDES.find((r) => r.id === id);
  if (!guide || id === 'litigation') return '';
  const fact = (key) => c.fields[key]?.status === 'confirmed'
    ? c.fields[key].value : '［待补充并核对］';
  return `# ${guide.output}（本地草稿）\n\n案件：${c.title}\n\n## 目前记录\n对方及关系：${fact('counterparty')}\n事情经过：${fact('story')}\n希望解决：${fact('goal')}\n\n## 准备核对的材料\n${guide.materials.map((t) => `- ${t}`).join('\n')}\n\n## 本案已登记材料\n${c.files.length ? c.files.map((f) => `- ${f.name}（内容待核验）`).join('\n') : '［尚未登记，可后续补充］'}\n\n## 我的沟通想法\n${notes.trim() || '［待补充：期望安排、待询问事项、可接受的履行时间］'}\n\n## 需要另行确认\n${id === 'mediation' ? '调解机构、双方参与意愿、申请方式，以及协议形式和效力。' : '对方回应、是否达成合意，以及协议内容、履行方式和期限。'}\n\n本文件仅整理当前记录与准备事项，未发送给对方或调解机构，不代表双方已经达成协议。`;
}
