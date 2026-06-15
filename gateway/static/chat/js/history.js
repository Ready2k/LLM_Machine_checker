// Conversation sidebar: list, search, rename, pin, delete.
//
// All rendering uses textContent (never innerHTML) so stored conversation
// titles and contents can never execute as markup.

import * as db from './db.js';

let _opts = {};       // { getActiveId, onSelect, onNew, onActiveDeleted }
let _cache = [];      // last-loaded conversations
let _query = '';

const $ = (id) => document.getElementById(id);

export function initHistory(opts) {
  _opts = opts || {};

  const search = $('sb-search-input');
  if (search) {
    search.addEventListener('input', () => { _query = search.value.trim().toLowerCase(); render(); });
  }

  document.querySelectorAll('[data-action="new-chat"]').forEach(b =>
    b.addEventListener('click', () => { closeMobile(); _opts.onNew && _opts.onNew(); }));

  document.querySelectorAll('[data-action="toggle-sidebar"]').forEach(b =>
    b.addEventListener('click', toggleSidebar));

  const backdrop = $('sb-backdrop');
  if (backdrop) backdrop.addEventListener('click', closeMobile);
}

export function toggleSidebar() {
  const shell = $('shell');
  if (!shell) return;
  // Desktop uses .sb-collapsed; mobile uses .sb-open (CSS is media-gated).
  if (window.matchMedia('(max-width: 600px)').matches) {
    shell.classList.toggle('sb-open');
  } else {
    shell.classList.toggle('sb-collapsed');
  }
}

function closeMobile() {
  $('shell')?.classList.remove('sb-open');
}

// Shown when IndexedDB is unavailable (memory-only mode).
export function showUnavailable(msg) {
  const list = $('conv-list');
  if (!list) return;
  list.replaceChildren();
  const b = document.createElement('div');
  b.className = 'sb-banner';
  b.textContent = msg || 'History unavailable in this browser. Chat will work, but conversations will not be saved.';
  list.appendChild(b);
}

export async function refreshSidebar() {
  try {
    _cache = await db.listConversations();
  } catch {
    _cache = [];
  }
  render();
}

function matchesQuery(conv, q) {
  if (!q) return true;
  if ((conv.title || '').toLowerCase().includes(q)) return true;
  return (conv.messages || []).some(m => (m.content || '').toLowerCase().includes(q));
}

function render() {
  const list = $('conv-list');
  if (!list) return;

  const activeId = _opts.getActiveId ? _opts.getActiveId() : null;
  const items = _cache
    .filter(c => matchesQuery(c, _query))
    .sort((a, b) =>
      (Number(b.pinned) - Number(a.pinned)) ||
      (b.updatedAt || '').localeCompare(a.updatedAt || ''));

  list.replaceChildren();

  if (items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'sb-empty';
    empty.textContent = _query ? 'No matches.' : 'No conversations yet.';
    list.appendChild(empty);
    return;
  }

  for (const conv of items) list.appendChild(rowFor(conv, activeId));
}

function rowFor(conv, activeId) {
  const el = document.createElement('div');
  el.className = 'conv' + (conv.id === activeId ? ' active' : '') + (conv.pinned ? ' pinned' : '');

  const main = document.createElement('div');
  main.className = 'conv-main';
  main.addEventListener('click', () => { closeMobile(); _opts.onSelect && _opts.onSelect(conv.id); });

  const title = document.createElement('div');
  title.className = 'conv-title';
  title.textContent = conv.title || 'Untitled';

  const meta = document.createElement('div');
  meta.className = 'conv-meta';
  const time = document.createElement('span');
  time.textContent = relTime(conv.updatedAt);
  meta.appendChild(time);
  if (conv.lastRoute) {
    const chip = document.createElement('span');
    chip.className = 'conv-route';
    chip.textContent = conv.lastRoute;
    meta.appendChild(chip);
  }

  main.appendChild(title);
  main.appendChild(meta);
  el.appendChild(main);

  const actions = document.createElement('div');
  actions.className = 'conv-actions';
  actions.appendChild(actBtn(conv.pinned ? '★' : '☆', conv.pinned ? 'Unpin' : 'Pin', async () => {
    conv.pinned = !conv.pinned;
    await db.saveConversation(conv);
    refreshSidebar();
  }));
  actions.appendChild(actBtn('✎', 'Rename', () => startRename(conv, title, main)));
  actions.appendChild(actBtn('🗑', 'Delete', async () => {
    if (!confirm('Delete this conversation?')) return;
    await db.deleteConversation(conv.id);
    const active = _opts.getActiveId ? _opts.getActiveId() : null;
    if (conv.id === active && _opts.onActiveDeleted) _opts.onActiveDeleted();
    refreshSidebar();
  }));
  el.appendChild(actions);

  return el;
}

function actBtn(label, title, handler) {
  const b = document.createElement('button');
  b.className = 'conv-act';
  b.textContent = label;
  b.title = title;
  b.addEventListener('click', (e) => { e.stopPropagation(); handler(); });
  return b;
}

function startRename(conv, titleEl, main) {
  const input = document.createElement('input');
  input.className = 'conv-rename';
  input.value = conv.title || '';
  input.addEventListener('click', (e) => e.stopPropagation());
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const commit = async () => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    conv.title = v || conv.title || 'Untitled';
    await db.saveConversation(conv);
    refreshSidebar();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { done = true; refreshSidebar(); }
  });
  input.addEventListener('blur', commit);
}

function relTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const s = Math.floor((Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 604800) return Math.floor(s / 86400) + 'd ago';
  return new Date(iso).toLocaleDateString();
}
