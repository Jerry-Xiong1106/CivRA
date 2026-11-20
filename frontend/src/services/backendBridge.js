import { createCivraApi, ApiError } from './civraApi.js';
import { applyConversation, currentWait, panelsForFinal } from '../domain/backendAdapter.js';

// Capture the case object for each operation. Switching the visible case must
// never redirect an upload, resumed wait or late response into another case.
export function createBackendBridge({ persist, notify, client = createCivraApi(), delay = ms => new Promise(r => setTimeout(r, ms)) }) {
  const active = new Map(), creating = new Map();
  const busy = c => !!(active.has(c.id) || c.server?.pending);
  function mark(c, patch) { c.server ||= {}; Object.assign(c.server, patch); persist(); }
  async function ensure(c) {
    if (c.server?.conversationId) return c.server.conversationId;
    if (!creating.has(c.id)) {
      creating.set(c.id, client.create(c.title).then(view => {
        applyConversation(c, view); persist(); return view.conversation_id;
      }).finally(() => creating.delete(c.id)));
    }
    return creating.get(c.id);
  }
  async function read(c) {
    if (!c.server?.conversationId) return null;
    const view = await client.get(c.server.conversationId);
    applyConversation(c, view);
    const sent = c.server.outbox && view.messages.find(m => m.request_id === c.server.outbox.request_id);
    if (sent) {
      const attached = new Set((sent.input.attachments || []).map(f => f.file_id));
      c.server.pendingAttachments = (c.server.pendingAttachments || []).filter(f => !attached.has(f.file_id));
      c.server.outbox = null;
    }
    persist(); return view;
  }
  async function track(c, runId, signal) {
    let lastError;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (signal.aborted) return;
      try {
        c.server.cursors ||= {};
        await client.events(c.server.conversationId, runId, c.server.cursors[runId] || 0, signal, event => {
          const seq = event.data?.seq;
          if (seq && event.data.run_id === runId) {
            if (seq <= (c.server.cursors[runId] || 0)) return;
            c.server.cursors[runId] = seq;
          }
          if (event.type === 'step.started') c.server.progress = '正在整理与处理…';
          if (event.type === 'step.finished') c.server.progress = '已完成一个处理步骤…';
          if (event.type === 'run.waiting_user') c.server.progress = '请回答问题或确认这一步';
          persist();
        });
        const view = await read(c);
        if (!view?.pending) { mark(c, { error: '', progress: '' }); return; }
      } catch (error) {
        if (signal.aborted) return;
        lastError = error;
        try {
          const view = await read(c);
          if (!view?.pending) { mark(c, { error: '', progress: '' }); return; }
        } catch (readError) { lastError = readError; }
      }
      if (attempt < 3) await delay(Math.min(500 * 2 ** attempt, 2000));
    }
    throw lastError || new ApiError('连接已中断，后端可能仍在处理。请刷新会话查看结果。');
  }
  async function sync(c) {
    if (active.has(c.id) || !c.server?.conversationId) return;
    const controller = new AbortController(); active.set(c.id, controller);
    mark(c, { syncing: true });
    try {
      const view = await read(c);
      if (view?.pending && view.run?.run_id) await track(c, view.run.run_id, controller.signal);
      else if (view?.pending) {
        // A queued submission may not have a RunInfo yet. Its receipt owns run_id.
        const receipt = [...view.messages].reverse().find(m => ['queued', 'running'].includes(m.status));
        if (receipt?.run_id) await track(c, receipt.run_id, controller.signal);
      }
      mark(c, { error: '' });
    } catch (error) { mark(c, { error: error.message }); notify(error.message); }
    finally { active.delete(c.id); mark(c, { syncing: false }); }
  }
  async function submit(c, text, { stepDecision, attachments, retry = false } = {}) {
    if (busy(c) && !retry) { notify('当前请求仍在处理，请等待结果或刷新会话。'); return null; }
    if (active.has(c.id)) return null;
    if (c.server?.outbox && !retry) { notify('上一条提交尚未确认，请先重试该条请求，避免重复办理。'); return null; }
    const controller = new AbortController(); active.set(c.id, controller);
    mark(c, { sending: true, error: '', progress: '正在提交…' });
    let accepted = false;
    try {
      const id = await ensure(c);
      let payload = c.server.outbox;
      if (!retry) {
        const wait = currentWait(c);
        const common = { request_id: crypto.randomUUID(), expected_case_version: c.server.snapshot.case_version,
          attachments: attachments || c.server.pendingAttachments || [] };
        if (wait?.kind === 'step_confirmation') {
          // Typing while a step waits for confirmation means "revise, with this note".
          const decision = stepDecision || 'revise';
          payload = { ...common, wait_id: wait.wait_id, reply: { step_decision: decision, ...(text ? { text } : {}) } };
        } else if (wait) {
          payload = { ...common, wait_id: wait.wait_id, reply: { text } };
        } else {
          payload = { ...common, text: (!c.server.submissions?.length && c.caseTypeHint ? '创建案件时初步选择的纠纷类型：' + c.caseTypeHint + '，请根据后续案情核对。\n' : '') + text };
        }
        mark(c, { outbox: payload });
      }
      if (!payload) return null;
      const receipt = await client.send(id, payload);
      accepted = true;
      mark(c, { pending: ['queued', 'running'].includes(receipt.status), progress: '助手正在处理…', lastReceipt: receipt });
      // Read the authoritative receipt and persisted transcript instead of
      // inventing an optimistic assistant response.
      await read(c);
      if (c.server.pending) await track(c, receipt.run_id, controller.signal);
      const final = c.server.run?.final;
      return { accepted: true, panel: currentWait(c) ? '' : panelsForFinal(final, c.server.outputs)[0] || '' };
    } catch (error) {
      if ([401, 404, 409, 413, 415, 422].includes(error.status)) {
        mark(c, { outbox: null });
        if (error.status === 409) { try { await read(c); } catch { /* show original conflict */ } }
      }
      mark(c, { error: error.message, progress: '' });
      notify(error.message);
      return accepted ? { accepted: true, panel: '' } : null;
    } finally { active.delete(c.id); mark(c, { sending: false }); }
  }
  // The user picked a route entry in the interface; the Agent starts guiding that route.
  async function selectRoute(c, route) {
    if (active.has(c.id) || c.server?.outbox) { notify('当前请求仍在处理，请稍后再切换路线。'); return null; }
    const controller = new AbortController(); active.set(c.id, controller);
    mark(c, { sending: true, error: '', progress: '正在进入所选路线…' });
    try {
      const id = await ensure(c);
      const receipt = await client.selectRoute(id, { request_id: crypto.randomUUID(), route,
        expected_case_version: c.server.snapshot.case_version });
      mark(c, { pending: ['queued', 'running'].includes(receipt.status), progress: '助手正在引导当前步骤…', lastReceipt: receipt });
      await read(c);
      if (c.server.pending) await track(c, receipt.run_id, controller.signal);
      return { accepted: true };
    } catch (error) {
      if (error.status === 409) { try { await read(c); } catch { /* show original conflict */ } }
      mark(c, { error: error.message, progress: '' });
      notify(error.message);
      return null;
    } finally { active.delete(c.id); mark(c, { sending: false }); }
  }

  async function upload(c, files) {
    if (busy(c) || c.server?.outbox) { notify('请先完成或重试当前请求，再上传材料。'); return []; }
    active.set(c.id, new AbortController());
    mark(c, { uploading: true, error: '' });
    const saved = [];
    try {
      const id = await ensure(c);
      // Keep room for the server's 20-attachment maximum, including prior uploads.
      const pending = c.server.pendingAttachments || [];
      if (pending.length + files.length > 20) throw new ApiError('每次消息最多引用 20 份材料，请先处理已上传材料。', 422);
      for (const file of files) {
        const record = await client.upload(id, file);
        saved.push(record);
        c.server.pendingAttachments ||= [];
        c.server.pendingAttachments.push({ file_id: record.file_id, file_version: record.file_version });
        persist();
      }
    } catch (error) { mark(c, { error: error.message }); notify(error.message); }
    finally {
      try { await read(c); } catch { notify('上传回执已保留，材料列表暂未刷新，请稍后刷新。'); }
      active.delete(c.id); mark(c, { uploading: false });
    }
    return saved;
  }
  async function download(c, artifactId) {
    try { return await client.download(c.server.conversationId, artifactId); }
    catch (error) { notify(error.message); return null; }
  }
  return { submit, selectRoute, sync, upload, download, busy, retry: c => submit(c, '', { retry: true }),
    dispose: () => { for (const controller of active.values()) controller.abort(); active.clear(); } };
}
