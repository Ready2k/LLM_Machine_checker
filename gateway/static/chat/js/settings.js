// Settings & backup modal: export/import, clear history/memory, storage status.

import * as db from './db.js';

let _opts = {};   // { getPersistence, onDataChanged }

const $ = (id) => document.getElementById(id);

export function initSettings(opts) {
  _opts = opts || {};

  document.querySelectorAll('[data-action="open-settings"]').forEach(b =>
    b.addEventListener('click', open));

  $('settings-close')?.addEventListener('click', close);
  $('settings-backdrop')?.addEventListener('click', close);

  $('btn-export')?.addEventListener('click', doExport);
  $('btn-import')?.addEventListener('click', () => $('import-file').click());
  $('import-file')?.addEventListener('change', doImport);
  $('btn-clear-history')?.addEventListener('click', clearHistory);
  $('btn-clear-memory')?.addEventListener('click', clearMemory);
}

function open() {
  const persistence = _opts.getPersistence ? _opts.getPersistence() : 'unknown';
  const status = $('settings-status');
  if (status) {
    status.textContent = persistence === 'indexeddb'
      ? 'Storage: saved on this device (IndexedDB).'
      : 'Storage: memory-only — conversations are not being saved in this browser.';
  }
  setSummary('');
  $('settings-modal')?.classList.add('open');
}

function close() {
  $('settings-modal')?.classList.remove('open');
}

function setSummary(text, isError) {
  const el = $('settings-summary');
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? 'var(--err)' : 'var(--dim)';
}

async function doExport() {
  try {
    const data = await db.exportAll();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const date = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `localai-chat-backup-${date}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    const n = (data.conversations?.length || 0);
    setSummary(`Exported ${n} conversation${n === 1 ? '' : 's'}.`);
  } catch (e) {
    setSummary(`Export failed: ${e.message}`, true);
  }
}

async function doImport(ev) {
  const file = ev.target.files?.[0];
  ev.target.value = ''; // allow re-importing the same file later
  if (!file) return;

  const mode = $('import-mode')?.value === 'replace' ? 'replace' : 'merge';
  if (mode === 'replace' && !confirm('Replace mode will delete all current history and memory first. Continue?')) {
    return;
  }

  try {
    const text = await file.text();
    let payload;
    try { payload = JSON.parse(text); }
    catch { throw new Error('File is not valid JSON.'); }

    const summary = await db.importAll(payload, mode);
    setSummary(`Imported ${summary.imported}, skipped ${summary.skipped}, failed ${summary.failed}.`);
    _opts.onDataChanged && _opts.onDataChanged();
  } catch (e) {
    setSummary(`Import failed: ${e.message}`, true);
  }
}

async function clearHistory() {
  if (!confirm('Delete ALL conversations on this device? This cannot be undone.')) return;
  try {
    await db.clearConversations();
    setSummary('All conversations cleared.');
    _opts.onDataChanged && _opts.onDataChanged();
  } catch (e) {
    setSummary(`Clear failed: ${e.message}`, true);
  }
}

async function clearMemory() {
  if (!confirm('Delete ALL stored memory on this device? This cannot be undone.')) return;
  try {
    await db.clearMemories();
    setSummary('All memory cleared.');
    _opts.onDataChanged && _opts.onDataChanged();
  } catch (e) {
    setSummary(`Clear failed: ${e.message}`, true);
  }
}
