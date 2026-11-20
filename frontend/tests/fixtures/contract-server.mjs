// Browser integration fixture only. No real Agent, RAG, OCR or legal advice.
// Run manually: node tests/fixtures/contract-server.mjs
import http from 'node:http';
import { randomUUID } from 'node:crypto';
const conversations = new Map();
const clone = value => JSON.parse(JSON.stringify(value));
const routes = ['negotiation', 'mediation', 'litigation'];
const labels = ['协商解决', '调解解决', '诉讼准备'];
function send(res, body, status = 200) { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(body)); }
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const parts = url.pathname.split('/').filter(Boolean);
  let bytes = Buffer.alloc(0); for await (const chunk of req) bytes = Buffer.concat([bytes, chunk]);
  let body; try { body = bytes.length && req.headers['content-type']?.includes('application/json') ? JSON.parse(bytes.toString()) : {}; } catch { return send(res, {}, 422); }
  if (req.method === 'POST' && url.pathname === '/api/conversations') {
    const id = randomUUID(), cid = 'case_' + randomUUID();
    const view = { conversation_id: id, case_id: cid, title: body.title, created_at: new Date().toISOString(), pending: false, run: null, messages: [], files: [], outputs: [],
      case: { schema_version: '0.1', case_id: cid, case_version: 1, synthetic: true, dispute_type: null, jurisdiction: null, stage: 'consultation', selected_route: null, facts: [], claims: [], evidence: [], user_goals: [], statements: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString() } };
    conversations.set(id, view); return send(res, view, 201);
  }
  const state = conversations.get(parts[2]);
  if (!state) return send(res, { detail: 'not found' }, 404);
  if (req.method === 'GET' && parts.length === 3) return send(res, state);
  if (req.method === 'POST' && parts[3] === 'messages') {
    const prior = state.messages.find(m => m.request_id === body.request_id);
    if (prior) return send(res, prior, 202);
    if (body.expected_case_version !== state.case.case_version) return send(res, {}, 409);
    if (state.run?.status === 'waiting_user' && body.wait_id !== state.run.wait.wait_id) return send(res, {}, 409);
    const runId = body.wait_id ? state.run.run_id : 'run_' + randomUUID();
    const run = { run_id: runId, case_id: state.case_id, case_version: state.case.case_version, status: 'completed', wait: null, final: null, usage: {}, stale: false };
    if (!state.messages.length && !body.text?.includes('仅咨询法律')) {
      run.status = 'waiting_user'; run.wait = { wait_id: 'wait_' + randomUUID(), kind: 'clarification', questions: [{ question_id: 'amount', text: '【接口联调】请补充希望解决的问题；这是一条脚本追问。' }], options: [] };
    } else if (!state.case.selected_route && !body.reply?.route_choice && !body.text?.includes('仅咨询法律')) {
      run.status = 'waiting_user'; run.wait = { wait_id: 'wait_' + randomUUID(), kind: 'route_selection', questions: [], options: routes.map((route, i) => ({ route, label: labels[i] })), comparison: '【接口联调】请选择处理方向，验证 wait_id 与路线回复。' };
    } else {
      if (body.reply?.route_choice) { state.case.selected_route = body.reply.route_choice; state.case.stage = body.reply.route_choice; state.case.case_version++; state.case.dispute_type = 'traffic_accident'; }
      run.case_version = state.case.case_version;
      const source = { source_id: 'fixture:law', source_type: 'statute', title: '接口联调来源（虚构，不可引用）', source_version: 'fixture-1', locator: { article: '联调条款', page_start: 1 }, url: null };
      run.final = { answer: '【接口联调回包，非真实模型回答】已收到本次输入。此服务只验证页面与 HTTP 契约，不进行法律分析。', output_refs: [], source_refs: [source], citations: [{ source_id: source.source_id, location: '联调说明' }], open_issues: ['真实 Agent、RAG 和 OCR 仍需在业务后端运行。'], case_version: state.case.case_version, partial: false };
      if (state.case.selected_route) {
        const workflow = { output_id: 'fixture-workflow', output_version: 1, case_id: state.case_id, case_version: state.case.case_version, output_type: 'workflow', text: '接口联调步骤', structured_data: { route: state.case.selected_route, steps: [{ title: '核对联调材料', materials: ['测试文件'], contact: null }], findings: [] } };
        state.outputs = [workflow]; run.final.output_refs.push(workflow.output_id);
        if (state.case.selected_route === 'litigation' && body.text?.includes('生成起诉状')) {
          const doc = { output_id: 'fixture-document', output_version: 1, case_id: state.case_id, case_version: state.case.case_version, output_type: 'document', text: '接口联调草稿：仅验证预览和下载，不可用于提交。', structured_data: { artifacts: { md: 'fixture-artifact' } } };
          state.outputs.push(doc); run.final.output_refs.push(doc.output_id);
        }
      }
    }
    const row = { request_id: body.request_id, run_id: runId, status: 'completed', input: body, created_at: new Date().toISOString(), run: clone(run), error: null };
    state.run = run; state.messages.push(row); return send(res, row, 202);
  }
  if (req.method === 'GET' && parts[3] === 'events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return res.end('event: state\ndata: ' + JSON.stringify({ run: state.run, pending: false }) + '\n\n');
  }
  if (req.method === 'POST' && parts[3] === 'files') {
    const name = /filename="([^"]+)"/.exec(bytes.toString())?.[1] || '联调材料.txt';
    const file = { case_id: state.case_id, file_id: 'file_' + randomUUID().replaceAll('-', ''), file_version: '1', file_name: name, media_type: 'text/plain', document_type: 'material', content_sha256: '0'.repeat(64), size_bytes: bytes.length };
    state.files.push(file); return send(res, file, 201);
  }
  if (req.method === 'GET' && parts[3] === 'artifacts' && parts[4] === 'fixture-artifact') {
    res.writeHead(200, { 'Content-Type': 'text/markdown', 'Content-Disposition': "attachment; filename*=UTF-8''CivRA_fixture.md" });
    return res.end('# 接口联调文件\n仅验证下载，非法律文书。');
  }
  send(res, {}, 404);
});
server.listen(18992, '127.0.0.1', () => console.log('Contract fixture only: http://127.0.0.1:18992'));
