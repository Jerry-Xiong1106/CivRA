import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, createCivraApi, readSse } from '../src/services/civraApi.js';
import { createBackendBridge } from '../src/services/backendBridge.js';
import { applyConversation, backendReadiness, currentWait, evidenceStatus, researchSources, artifactList, latestOutput, transcript, sourceUrl } from '../src/domain/backendAdapter.js';
const copy = x => JSON.parse(JSON.stringify(x));
const snapshot = () => ({ case_id: 'case-one', case_version: 1, synthetic: false, dispute_type: null,
  jurisdiction: null, stage: 'consultation', selected_route: null, facts: [], claims: [], evidence: [], statements: [], user_goals: [] });
const initial = () => ({ conversation_id: 'conversation-one', case_id: 'case-one', title: '案件', pending: false, run: null, messages: [], case: snapshot(), files: [], outputs: [] });
const record = () => ({ id: crypto.randomUUID(), title: '案件', mode: 'api', fields: {}, files: [], messages: [], audit: [], draftForm: {} });
const finish = (id, version, answer = '已处理') => ({ run_id: id, status: 'completed', case_version: version,
  final: { answer, output_refs: [], source_refs: [], citations: [], case_version: version }, wait: null });
function setup() {
  let state = initial(), calls = [], events = 0;
  const client = {
    async create() { return copy(state); },
    async get() { return copy(state); },
    async send(id, body) {
      calls.push(copy(body));
      const prior = state.messages.find(m => m.request_id === body.request_id);
      if (prior) return { ...prior, run_id: prior.run.run_id };
      const run = finish('run-' + state.messages.length, state.case.case_version);
      state.run = run;
      const row = { request_id: body.request_id, input: copy(body), run, run_id: run.run_id, status: 'completed', created_at: '2026-11-18' };
      state.messages.push(row);
      return copy(row);
    },
    async selectRoute(id, body) {
      calls.push({ route_request: copy(body) });
      state.case = { ...state.case, stage: body.route, selected_route: body.route, case_version: state.case.case_version + 1 };
      const run = { run_id: 'run-route-' + state.messages.length, status: 'waiting_user', case_version: state.case.case_version,
        wait: { wait_id: 'w-step', kind: 'step_confirmation', step_id: 'prepare', summary: '诉求已整理', message: '## 诉求清单\n- 修车费8000元' } };
      state.run = run;
      state.route_progress = { route: body.route, current_step: 'prepare', progress_version: 0, steps: [] };
      const row = { request_id: body.request_id, input: copy(body), run, run_id: run.run_id, status: 'waiting_user', created_at: '2026-11-19' };
      state.messages.push(row);
      return copy(row);
    },
    async events() { events++; },
    async upload() { throw new Error('not configured'); }
  };
  const notices = [];
  const bridge = createBackendBridge({ client, persist() {}, notify: x => notices.push(x), delay: async () => {} });
  return { client, bridge, calls, notices, get state() { return state; }, set state(v) { state = v; }, get events() { return events; } };
}
test('new user messages use only the HTTP contract, not local stage/facts/tools', async () => {
  const s = setup(), c = record();
  const result = await s.bridge.submit(c, '我想咨询');
  assert.equal(result.accepted, true);
  assert.deepEqual(Object.keys(s.calls[0]).sort(), ['attachments', 'expected_case_version', 'request_id', 'text']);
  assert.equal(c.conversation[1].text, '已处理');
  assert.equal(c.server.outbox, null);
  assert.equal(c.fields.phase, undefined);
});
test('clarification resumes the exact wait with free text instead of starting a new run', async () => {
  const s = setup(), c = record();
  s.state.run = { run_id: 'waiting', status: 'waiting_user', wait: { wait_id: 'w-1', kind: 'clarification', questions: [{ question_id: 'q1', text: '金额？' }] } };
  applyConversation(c, copy(s.state));
  await s.bridge.submit(c, '三千元');
  assert.equal(s.calls[0].wait_id, 'w-1');
  assert.deepEqual(s.calls[0].reply, { text: '三千元' });
  assert.equal(s.calls[0].text, undefined);
});
test('route entries call the route endpoint and the server decides the stage', async () => {
  for (const route of ['negotiation', 'mediation', 'litigation']) {
    const s = setup(), c = record();
    await s.bridge.selectRoute(c, route);
    assert.equal(s.calls[0].route_request.route, route);
    assert.equal(s.calls[0].text, undefined);
    assert.equal(c.server.snapshot.selected_route, route);
    assert.equal(c.server.routeProgress.route, route);
    const wait = currentWait(c);
    assert.equal(wait.kind, 'step_confirmation');
    const turns = transcript(copy(s.state));
    assert.equal(turns.at(-2).text, '我选择「' + { negotiation: '协商解决', mediation: '调解解决', litigation: '诉讼准备' }[route] + '」');
    assert.match(turns.at(-1).text, /修车费8000元/);
  }
});
test('step confirmation sends step_decision; typing while it waits means revise with a note', async () => {
  const s = setup(), c = record();
  await s.bridge.selectRoute(c, 'negotiation');
  await s.bridge.submit(c, '', { stepDecision: 'done' });
  assert.deepEqual(s.calls[1].reply, { step_decision: 'done' });
  assert.equal(s.calls[1].wait_id, 'w-step');
  const t = setup(), d = record();
  await t.bridge.selectRoute(d, 'negotiation');
  await t.bridge.submit(d, '再加上拖车费');
  assert.deepEqual(t.calls[1].reply, { step_decision: 'revise', text: '再加上拖车费' });
});
test('the user-selected case type is included once as context, not forged structured facts', async () => {
  const s = setup(), c = record();
  c.caseTypeHint = '民间借贷';
  await s.bridge.submit(c, '帮我整理');
  assert.match(s.calls[0].text, /初步选择的纠纷类型：民间借贷/);
  await s.bridge.submit(c, '继续');
  assert.equal(s.calls[1].text, '继续');
  assert.equal(s.calls[0].facts, undefined);
});
test('ambiguous POST failure persists an identical idempotent outbox across retry', async () => {
  const s = setup(), c = record();
  const send = s.client.send;
  let fail = true;
  s.client.send = async (...args) => { const receipt = await send(...args); if (fail) { fail = false; throw new ApiError('断线'); } return receipt; };
  assert.equal(await s.bridge.submit(c, '请处理'), null);
  const persisted = copy(c);
  assert.ok(persisted.server.outbox);
  assert.equal(await s.bridge.submit(c, '新请求'), null);
  await s.bridge.retry(persisted);
  assert.deepEqual(s.calls[0], s.calls[1]);
  assert.equal(s.state.messages.length, 1);
  assert.equal(persisted.server.outbox, null);
});
test('409 refreshes authoritative state and never silently replays an old confirmation', async () => {
  const s = setup(), c = record();
  s.client.send = async () => { s.state.case.case_version = 2; throw new ApiError('冲突', 409); };
  assert.equal(await s.bridge.submit(c, '旧确认'), null);
  assert.equal(c.server.snapshot.case_version, 2);
  assert.equal(c.server.outbox, null);
  assert.match(c.server.error, /冲突/);
});
test('SSE reconnect uses cursor; stream updates stay with the originating case', async () => {
  const s = setup(), c = record(), other = record();
  const send = s.client.send;
  s.client.send = async (...args) => { const r = await send(...args); s.state.pending = true; s.state.run.status = 'running'; return { ...r, status: 'running' }; };
  const cursors = [];
  s.client.events = async (id, run, cursor, signal, onEvent) => {
    cursors.push(cursor);
    if (!cursor) { onEvent({ type: 'step.started', data: { seq: 5, run_id: run } }); throw new Error('stream disconnected'); }
    s.state.pending = false; s.state.run.status = 'completed'; onEvent({ type: 'state', data: { pending: false, run: s.state.run } });
  };
  await s.bridge.submit(c, '执行');
  assert.deepEqual(cursors, [0, 5]);
  assert.equal(c.server.pending, false);
  assert.equal(other.server, undefined);
});
test('refresh restores a waiting run without sending a message or replaying model execution', async () => {
  const s = setup(), c = record();
  s.state.run = { run_id: 'r', status: 'waiting_user', wait: { wait_id: 'w', kind: 'material_request', questions: [] } };
  c.server = { conversationId: s.state.conversation_id };
  await s.bridge.sync(c);
  assert.equal(currentWait(c).wait_id, 'w');
  assert.equal(s.calls.length, 0);
});
test('source cards retain exact identity/version/page and reject executable links', () => {
  const c = record(), view = initial();
  const source = { source_id: 'law:680', source_version: 'v1', title: '来源', source_type: 'statute', locator: { article: '第680条', page_start: 26 }, url: 'javascript:alert(1)' };
  const run = finish('r', 1); run.final.source_refs = [source]; run.final.citations = [{ source_id: source.source_id, location: '第二段' }];
  view.messages = [{ request_id: 'm', input: { text: '问题' }, run }];
  applyConversation(c, view);
  const [card] = researchSources(c);
  assert.match(card.location, /第680条.*26/); assert.equal(card.source_version, 'v1');
  assert.equal(card.href, ''); assert.equal(card.citation, '第二段');
  assert.equal(sourceUrl('https://example.com/law'), 'https://example.com/law');
});
test('fact confirmations and dispute statuses come from backend; route is never a procedural phase', () => {
  const c = record(), view = initial();
  view.case.stage = 'mediation'; view.case.selected_route = 'mediation';
  view.case.facts = [{ fact_id: 'f1', field: 'medical_amount', value: { amount: '12.00', currency: 'CNY' }, confirmation_status: 'disputed' }];
  applyConversation(c, view);
  assert.equal(c.fields.phase, undefined); assert.equal(c.intakeConfirmation, null);
  assert.equal(backendReadiness(c).intakeConfirmed, false);
  assert.equal(backendReadiness(c).items[0].fact.value, '12.00 CNY');
});
test('no duplicate or missing-evidence verdict is manufactured when HTTP lacks detailed checks', () => {
  const c = record(); applyConversation(c, initial());
  assert.match(evidenceStatus(c).duplicate, /不代表材料没有重复/);
  c.server.outputs = [{ output_type: 'workflow', case_version: 1, structured_data: { findings: [{ finding_id: 'd', category: 'duplicate', message: '可能重复' }] } }];
  assert.equal(evidenceStatus(c).duplicate, '可能重复');
  c.server.snapshot.case_version = 2;
  assert.match(evidenceStatus(c).duplicate, /尚未返回/);
});
test('outputs use reference order, not lexical inventory order; exports preserve stale version flags', () => {
  const c = record(), view = initial();
  const older = { output_id: 'z-old', output_type: 'document', case_version: 1, structured_data: { artifacts: { docx: 'a1', pdf: 'a2' } } };
  const newer = { output_id: 'a-new', output_type: 'document', case_version: 2, structured_data: { artifacts: { md: 'a3' } } };
  view.case.case_version = 2; view.outputs = [newer, older]; applyConversation(c, view);
  assert.equal(latestOutput(c, 'document').output_id, newer.output_id);
  assert.equal(artifactList(c).filter(a => a.stale).length, 2);
  assert.equal(artifactList(c).length, 3);
});
test('a resumed run does not duplicate the final answer; all output panels are retained', () => {
  const view = initial(), run = finish('r', 1);
  run.final.output_refs = ['o1', 'o2'];
  view.outputs = [{ output_id: 'o1', output_type: 'document' }, { output_id: 'o2', output_type: 'visualization' }];
  view.messages = ['a', 'b'].map(request_id => ({ request_id, input: { text: '内容' }, run }));
  const answers = transcript(view).filter(t => t.final);
  assert.equal(answers.length, 1);
  assert.deepEqual(answers[0].panels, ['draft', 'visuals']);
});
test('SSE handles UTF-8 split bytes, CRLF, comments, multiline data and final state without an ID', async () => {
  const bytes = new TextEncoder().encode(': heartbeat\r\n\r\nid: r:1\r\nevent: step.finished\r\ndata: {"text":\r\ndata: "中文"}\r\n\r\nevent: state\ndata: {"pending":false}\n\n');
  const body = new ReadableStream({ start(controller) { for (const b of bytes) controller.enqueue(new Uint8Array([b])); controller.close(); } });
  const events = []; for await (const event of readSse(body)) events.push(event);
  assert.equal(events[0].data.text, '中文'); assert.equal(events[0].id, 'r:1');
  assert.equal(events[1].type, 'state'); assert.equal(events[1].id, '');
});
test('multipart upload and artifact download use the existing URLs without token query parameters', async () => {
  const calls = [];
  const api = createCivraApi({ fetchImpl: async (url, init) => {
    calls.push({ url, init });
    return url.endsWith('/files') ? Response.json({ file_id: 'file_a', file_version: '1' }) :
      new Response('docx bytes', { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''CivRA.docx" } });
  } });
  await api.upload('conversation-one', new File(['material'], '票据.txt', { type: '' }));
  assert.equal(calls[0].url, '/api/conversations/conversation-one/files');
  assert.equal(calls[0].init.body.get('file').type, 'text/plain');
  const result = await api.download('conversation-one', 'artifact-one');
  assert.equal(result.filename, 'CivRA.docx');
  assert.equal(await result.blob.text(), 'docx bytes');
  await assert.rejects(api.upload('c', new File([], 'empty.pdf')), { status: 413 });
  await assert.rejects(api.upload('c', new File(['x'], 'bad.exe')), { status: 415 });
});
test('HTTP authentication errors do not become simulated answers', async () => {
  const api = createCivraApi({ fetchImpl: async () => new Response('', { status: 401 }) });
  await assert.rejects(api.create('案件'), { status: 401 });
});
