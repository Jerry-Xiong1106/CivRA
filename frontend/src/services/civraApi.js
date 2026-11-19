// Current develop HTTP contract. The development proxy holds the workspace
// credential on the server; model/embedding secrets never enter this bundle.
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = 'ApiError'; this.status = status; }
}
const errors = {
  401: '后端访问令牌缺失或无效，请检查服务端连接配置。',
  404: '后端没有找到这份会话或文件，请核对连接的工作区。',
  409: '案件或等待状态已经变化，已重新读取，请核对后再操作。',
  413: '文件为空或超过 20 MiB，请重新选择。',
  415: '文件格式不支持，请使用 PDF、TXT、PNG、JPEG、WebP 或 DOCX。',
  422: '后端未接受本次输入，请核对内容。',
  503: '后端暂时不可用或队列已满，可以重试本次提交。'
};
export async function* readSse(body) {
  const reader = body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  function parse(block) {
    const event = { type: 'message', id: '', data: '' };
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue;
      const at = line.indexOf(':');
      const key = at < 0 ? line : line.slice(0, at);
      const value = at < 0 ? '' : line.slice(at + 1).replace(/^ /, '');
      if (key === 'data') data.push(value);
      if (key === 'event') event.type = value;
      if (key === 'id') event.id = value;
    }
    if (!data.length) return null;
    event.data = JSON.parse(data.join('\n'));
    return event;
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const event = parse(block);
        if (event) yield event;
      }
      if (done) break;
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
export function createCivraApi({ base = '/api', fetchImpl = globalThis.fetch, timeout = 30000 } = {}) {
  const path = (id, suffix = '') => '/conversations/' + encodeURIComponent(id) + suffix;
  async function request(endpoint, { json, signal, streaming = false, ...options } = {}) {
    const timed = AbortSignal.timeout(streaming ? 350000 : timeout);
    let response;
    try {
      response = await fetchImpl(base.replace(/\/$/, '') + endpoint, {
        ...options, signal: signal ? AbortSignal.any([signal, timed]) : timed,
        headers: { ...(json ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
        ...(json ? { body: JSON.stringify(json) } : {})
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ApiError('无法连接后端。请检查 API 服务；已提交的请求可能仍在处理，请刷新或重试同一条请求。');
    }
    if (!response.ok) throw new ApiError(errors[response.status] || '后端请求失败（' + response.status + '），请稍后重试。', response.status);
    return response;
  }
  return {
    async create(title) { return (await request('/conversations', { method: 'POST', json: { title } })).json(); },
    async get(id) { return (await request(path(id))).json(); },
    async send(id, payload) { return (await request(path(id, '/messages'), { method: 'POST', json: payload })).json(); },
    async selectRoute(id, payload) { return (await request(path(id, '/route'), { method: 'POST', json: payload })).json(); },
    async upload(id, file) {
      const types = { pdf: 'application/pdf', txt: 'text/plain', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
      const type = types[file.name.split('.').pop().toLowerCase()];
      if (!type) throw new ApiError(errors[415], 415);
      if (!file.size || file.size > 20 * 1024 * 1024) throw new ApiError(errors[413], 413);
      const form = new FormData();
      form.append('file', new Blob([file], { type }), file.name);
      return (await request(path(id, '/files'), { method: 'POST', body: form })).json();
    },
    async events(id, run, cursor, signal, onEvent) {
      const response = await request(path(id, '/events?run_id=' + encodeURIComponent(run) + '&after_seq=' + cursor), { signal, streaming: true, headers: { Accept: 'text/event-stream' } });
      if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body) throw new ApiError('后端未返回运行事件流，请刷新会话。');
      for await (const event of readSse(response.body)) await onEvent(event);
    },
    async download(id, artifactId) {
      const response = await request(path(id, '/artifacts/' + encodeURIComponent(artifactId)));
      let filename = 'CivRA_成果';
      const match = /filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('content-disposition') || '');
      if (match) { try { filename = decodeURIComponent(match[1]); } catch { /* keep safe fallback */ } }
      filename = filename.replace(/[\\/]/g, '_');
      return { blob: await response.blob(), filename };
    }
  };
}
