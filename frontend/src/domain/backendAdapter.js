import P from './policy.js';
export const isApiCase = (c) => c?.mode === 'api';
export const ROUTE_LABELS = { negotiation: '协商解决', mediation: '调解解决', litigation: '诉讼准备' };
const disputeNames = { traffic_accident: 'traffic', private_lending: 'loan', sales_contract: 'sale' };
const fieldNames = {
  claimant_name: '当事人姓名', defendant_name: '对方姓名或名称',
  accident_date: '事故发生日期', accident_location: '事故地点',
  liability_finding: '责任认定信息', insurance: '保险关系',
  loan_principal: '借款本金', outstanding_principal: '尚欠本金',
  repayment_history: '还款记录', medical_amount: '医疗金额', repair_amount: '维修金额',
  loan_date: '借款日期', loan_delivery: '款项交付情况', contract_details: '合同情况'
};
export function valueText(value) {
  if (value == null) return '待补充';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') return value.amount !== undefined ? value.amount + ' ' + (value.currency || '') : JSON.stringify(value);
  return String(value);
}
export function sourceUrl(url) {
  try { const u = new URL(url); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; }
}
export function sourceLocation(locator) {
  if (typeof locator === 'string') return locator;
  return [locator?.article, locator?.paragraph, locator?.page_start ? 'PDF 第 ' + locator.page_start + (locator.page_end && locator.page_end !== locator.page_start ? '–' + locator.page_end : '') + ' 页' : '', locator?.chunk_id].filter(Boolean).join(' · ');
}
export const outputData = (output) => output?.structured_data || {};
export function latestOutput(c, type, predicate = () => true) {
  // Inventory is sorted by output_id, NOT creation time. Use receipt history
  // for ordering, with case_version as the fallback for uncited saved outputs.
  const refs = (c.server?.submissions || []).flatMap(m => m.run?.final?.output_refs || []);
  return (c.server?.outputs || []).filter(o => o.output_type === type && predicate(o))
    .sort((a, b) => b.case_version - a.case_version || refs.lastIndexOf(b.output_id) - refs.lastIndexOf(a.output_id))[0];
}
export function panelForOutput(output) {
  if (output.output_id?.startsWith('check_evidence') || output.output_type === 'evidence_directory') return 'evidence';
  return { route_comparison: 'workflow', workflow: 'workflow', document: 'draft', analysis: 'outcomes', visualization: 'visuals', verification: 'results' }[output.output_type] || '';
}
export function panelsForFinal(final, outputs) {
  const panels = (final?.output_refs || []).map(id => outputs.find(o => o.output_id === id)).filter(Boolean).map(panelForOutput);
  if (final?.source_refs?.length) panels.push('research');
  return [...new Set(panels.filter(Boolean))];
}
export function currentWait(c) { return !c.server?.pending && c.server?.run?.status === 'waiting_user' ? c.server.run.wait : null; }
function replyText(input) {
  if (input.route) return '我选择「' + (ROUTE_LABELS[input.route] || input.route) + '」';
  if (input.text) return input.text;
  const reply = input.reply || {};
  const decision = { done: '这一步完成了。', revise: '这一步还需要修改。' }[reply.step_decision] || '';
  const body = reply.text || Object.values(reply.answers || {}).join('\n');
  return [decision, body].filter(Boolean).join('\n') || '（已回复）';
}
function waitText(wait) {
  if (wait.kind === 'step_confirmation') return wait.message || wait.summary || '请确认这一步是否完成。';
  const questions = (wait.questions || []).map(q => q.text + (q.reason ? '\n' + q.reason : '')).join('\n\n');
  return [wait.message, questions].filter(Boolean).join('\n\n');
}
export function transcript(view) {
  const turns = [];
  const outputs = view.outputs || [];
  const seenFinal = new Set(), seenWait = new Set();
  for (const message of view.messages || []) {
    turns.push({ id: message.request_id, role: 'user', text: replyText(message.input), at: message.created_at });
    const run = message.run;
    if (run?.wait && !seenWait.has(run.wait.wait_id)) {
      seenWait.add(run.wait.wait_id);
      turns.push({ id: run.wait.wait_id, role: 'assistant', text: waitText(run.wait),
        panel: run.wait.kind === 'step_confirmation' ? 'workflow' : '', wait: run.wait, at: message.created_at });
    }
    // A single resumed run can appear in several historical receipts.
    if (run?.final && !seenFinal.has(run.run_id)) {
      seenFinal.add(run.run_id);
      const panels = panelsForFinal(run.final, outputs);
      turns.push({ id: run.run_id + ':answer', role: 'assistant', text: run.final.answer,
        panels, panel: panels[0] || '', final: run.final, at: message.created_at });
    }
    if (message.status === 'failed' || ['failed', 'cancelled', 'budget_exhausted'].includes(run?.status)) {
      turns.push({ id: message.request_id + ':failure', role: 'assistant',
        text: ({ failed: '本次处理失败。', cancelled: '本次处理已停止。', budget_exhausted: '本次处理达到运行上限。' }[run?.status] || '本次提交处理失败。') + '已保存的记录仍可查看，可补充信息后继续。' });
    }
  }
  return turns;
}
export function applyConversation(c, view) {
  if (c.server?.conversationId && c.server.conversationId !== view.conversation_id) throw new Error('会话标识不匹配');
  c.server ||= {};
  if ((c.server.snapshot?.case_version || 0) > view.case.case_version) return false;
  Object.assign(c.server, { conversationId: view.conversation_id, caseId: view.case_id,
    snapshot: view.case, run: view.run, pending: view.pending, outputs: view.outputs || [],
    routeProgress: view.route_progress || null,
    submissions: view.messages || [], inventoryTruncated: view.inventory_truncated || false,
    loadedAt: new Date().toISOString() });
  c.title = view.title;
  c.route = view.case.selected_route;
  c.routeIntent = view.case.selected_route;
  // Never synthesize confirmations or court procedural state from a route choice.
  c.fields = { dispute: { value: disputeNames[view.case.dispute_type] || 'general', status: view.case.dispute_type ? 'confirmed' : 'unconfirmed' } };
  const map = { defendant_name: 'counterparty', accident_date: 'when', accident_location: 'where',
    liability_finding: 'liability', insurance: 'insurance', loan_principal: 'principal' };
  for (const fact of view.case.facts || []) {
    if (fact.superseded_by) continue;
    const id = map[fact.field];
    if (id) c.fields[id] = { value: valueText(fact.value), status: fact.confirmation_status };
  }
  c.files = (view.files || []).map(f => ({ id: f.file_id, version: f.file_version, name: f.file_name, size: f.size_bytes,
    uploaded: true, evidence: (view.case.evidence || []).filter(e => e.file_id === f.file_id && e.file_version === f.file_version) }));
  c.conversation = transcript(view);
  c.intakeConfirmation = null;
  c.routeConfirmation = null;
  c.draft = latestOutput(c, 'document') || null;
  return true;
}
export function backendReadiness(c) {
  const snapshot = c.server?.snapshot || { facts: [], case_version: 0 };
  const items = (snapshot.facts || []).filter(f => !f.superseded_by).map(f => ({
    id: f.fact_id, label: fieldNames[f.field] || f.field, backendField: f.field,
    fact: { value: valueText(f.value), status: f.confirmation_status }, source: f,
    satisfied: f.confirmation_status === 'confirmed', reason: f.confirmation_status === 'disputed' ? '有异议，需核对' : '尚待用户核对'
  }));
  const blockers = items.filter(f => !f.satisfied);
  const confirmed = items.length > 0 && !blockers.length;
  return { signature: 'server:' + snapshot.case_version, items, required: items, blockers,
    completed: items.length - blockers.length, intakeConfirmed: confirmed,
    routeConfirmed: !!snapshot.selected_route, canSelectRoute: true,
    // The backend validates generation; the frontend does not create its own prerequisite rules.
    canDraft: snapshot.selected_route === 'litigation',
    draftReasons: ['请选择诉讼准备；后端会核对生成草稿所需的信息。'] };
}
export function researchSources(c) {
  const sources = new Map();
  for (const message of c.server?.submissions || []) {
    for (const source of message.run?.final?.source_refs || []) {
      if (!['statute', 'judicial_interpretation', 'guideline'].includes(source.source_type)) continue;
      const key = source.source_id + ':' + source.source_version;
      sources.set(key, { ...source, key, href: sourceUrl(source.url), location: sourceLocation(source.locator),
        citation: (message.run.final.citations || []).filter(ref => ref.source_id === source.source_id).map(ref => ref.location).join('；') });
    }
  }
  return [...sources.values()];
}
export function evidenceStatus(c) {
  const outputs = (c.server?.outputs || []).filter(o => o.case_version === c.server?.snapshot?.case_version);
  const findings = outputs.flatMap(o => {
    const data = outputData(o);
    return [...(o.findings || []), ...(data.findings || []), ...(data.duplicates || []), ...(data.material_gaps || []), ...(data.missing_facts || [])];
  });
  const dedupe = [...new Map(findings.map(f => [f.finding_id || f.message, f])).values()];
  const duplicate = dedupe.filter(f => f.category === 'duplicate');
  const missing = dedupe.filter(f => ['missing_fact', 'missing_evidence', 'conflict', 'required_field'].includes(f.category));
  const evidence = c.server?.snapshot?.evidence || [];
  const duplicateIds = evidence.filter(e => e.duplicate_of);
  return { demo: false, backend: true,
    duplicate: duplicate.length ? duplicate.map(f => f.message).join('\n') : duplicateIds.length ? duplicateIds.map(e => e.file_name + '：已标记疑似重复，需核对。').join('\n') : '当前接口尚未返回可展示的重复检查明细。详细说明请查看对话；这不代表材料没有重复。',
    missing: missing.length ? missing.map(f => f.message).join('\n') : '当前接口尚未返回可展示的补证明细。可在对话中请求检查，并按助手追问补充或解释。' };
}
export function artifactList(c) {
  return (c.server?.outputs || []).flatMap(o => {
    const entries = Object.entries(outputData(o).artifacts || {});
    if (!entries.length && o.artifact_id) entries.push(['文件', o.artifact_id]);
    return entries.filter(([, id]) => typeof id === 'string').map(([format, id]) => ({
      id, format, output: o, stale: o.case_version !== c.server?.snapshot?.case_version
    }));
  });
}
export function knownFieldLabel(id) { return fieldNames[id] || P.fields.find(f => f.id === id)?.label || id; }
