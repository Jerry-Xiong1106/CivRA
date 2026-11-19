import { seedWorkspace } from '../domain/seed.js';
export const STORAGE_KEY = 'civra.guided-workflow.prototype.v2';
// The HTML and Vue clients share this schema. Browser origin rules still apply.
export function loadWorkspace(storage = globalThis.localStorage) {
  try {
    const data = JSON.parse(storage?.getItem(STORAGE_KEY));
    if (
      data?.version === 2 &&
      Array.isArray(data.cases) &&
      data.cases.length &&
      data.cases.every(
        (c) =>
          c.id &&
          c.fields &&
          Array.isArray(c.messages) &&
          Array.isArray(c.files)
      )
    ) {
      for (const c of data.cases) {
        c.audit ||= [];
        c.tasks ||= [];
        c.draftForm ||= {};
        // Transient browser locks must not survive a reload. Persisted pending
        // runs/outboxes are reconciled with the server, never blindly replayed.
        if (c.server) { c.server.sending = false; c.server.syncing = false; c.server.uploading = false; }
      }
      data.rules ||= {};
      if (data.consultation?.server) {
        data.consultation.server.sending = false;
        data.consultation.server.syncing = false;
        data.consultation.server.uploading = false;
      }
      if (!data.cases.some((c) => c.id === data.activeId))
        data.activeId = data.cases[0].id;
      return data;
    }
  } catch {
    /* Invalid or unavailable storage falls back to the same demo fixtures. */
  }
  return seedWorkspace();
}
export function saveWorkspace(workspace, storage = globalThis.localStorage) {
  if (!storage) return;
  storage.setItem(STORAGE_KEY, JSON.stringify(workspace));
}
