// Local AI Chat — main app logic.
// History persistence (IndexedDB) with a memory-only fallback when storage
// is unavailable. Memory features arrive in later phases.

import * as db from './db.js';
import { uuid, SCHEMA_VERSION } from './db.js';
import { initHistory, refreshSidebar, showUnavailable } from './history.js';
import { initSettings } from './settings.js';
import { initMemory, buildInjection, addSuggestions, refreshMemoryUI } from './memory.js';

// ── Helpers ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Minimal markdown -> HTML. Always escapes first, so any stored content
// (including <script>/<img onerror>) is rendered as inert text.
function renderMd(raw) {
  const escaped = esc(raw);

  let html = escaped.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const trimmed = code.replace(/\n$/, '');
    return `<pre><code>${trimmed}</code></pre>`;
  });

  const parts = html.split(/(<pre[\s\S]*?<\/pre>)/);
  html = parts.map((part, i) => {
    if (i % 2 === 1) return part; // inside a code block — leave as-is
    return part
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  }).join('');

  return html;
}

const nowIso = () => new Date().toISOString();

function deriveTitle(text) {
  const t = (text || '').trim().replace(/\s+/g, ' ');
  if (!t) return 'New chat';
  return t.length > 50 ? t.slice(0, 50) + '…' : t;
}

// ── State ─────────────────────────────────────────────────────────────────
const state = {
  messages: [],            // [{id, role, content, ts, ...}]
  files: [],               // [{name, content, size, mimeType}]
  streaming: false,
  conv: null,              // {id, title, createdAt, pinned, lastRoute} | null
  persistence: 'memory-only',
  historyAvailable: false,
  historyError: null,
};

// ── DOM ───────────────────────────────────────────────────────────────────
const $msgs   = document.getElementById('messages');
const $input  = document.getElementById('msg-input');
const $send   = document.getElementById('send-btn');
const $mic    = document.getElementById('mic-btn');
const $fInput = document.getElementById('file-input');
const $chips  = document.getElementById('file-chips');
const $model  = document.getElementById('model-select');
const $dropOvl = document.getElementById('drop-overlay');

const WELCOME_HTML = `
  <div class="welcome">
    <h2>Local AI Chat</h2>
    <p>Type a question, speak it, or drop a file.</p>
    <p>All requests stay on your local network.</p>
    <p class="hint">Shift+Enter for newline &nbsp;·&nbsp; Enter to send</p>
  </div>`;

// ── Model picker ──────────────────────────────────────────────────────────
const LABELS = {
  'auto':                'Auto (smart routing)',
  'local-fast':          'Local Fast',
  'local-quality':       'Local Quality',
  'private-docs':        'Private Docs',
  'local-fast-llama':    'Fast · Llama 3.1',
  'local-fast-gemma':    'Fast · Gemma 2',
  'local-quality-gemma': 'Quality · Gemma 4',
};

async function loadModels() {
  try {
    const r = await fetch('/v1/models');
    if (!r.ok) return;
    const { data = [] } = await r.json();
    const sorted = [
      ...data.filter(m => m.id === 'auto'),
      ...data.filter(m => m.id !== 'auto').sort((a, b) => a.id.localeCompare(b.id)),
    ];
    $model.innerHTML = sorted.map(m =>
      `<option value="${esc(m.id)}">${esc(LABELS[m.id] || m.id)}</option>`
    ).join('');
  } catch { /* keep default option */ }
}

// ── Rendering ─────────────────────────────────────────────────────────────
function clearWelcome() { document.querySelector('.welcome')?.remove(); }
function showWelcome()  { $msgs.innerHTML = WELCOME_HTML; }

function appendMsg(role, contentHtml, routeInfo) {
  clearWelcome();
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = contentHtml; // callers always pass renderMd()/esc() output
  wrap.appendChild(bubble);

  if (role === 'assistant' && routeInfo) wrap.appendChild(makeBadge(routeInfo));

  $msgs.appendChild(wrap);
  scrollDown();
  return bubble;
}

function makeBadge({ route, backend, model, reason }) {
  const el = document.createElement('div');
  el.className = 'badge';
  if (route)   el.innerHTML += `<span>route: ${esc(route)}</span>`;
  if (backend) el.innerHTML += `<span>backend: ${esc(backend)}</span>`;
  if (model)   el.innerHTML += `<span>model: ${esc(model)}</span>`;
  if (reason)  el.innerHTML += `<span class="reason">↳ ${esc(reason)}</span>`;
  return el;
}

const INTERRUPTED_HTML = ' <span style="color:var(--err)">[stream interrupted]</span>';

// Re-render a stored conversation from state.messages.
function renderConversation() {
  $msgs.replaceChildren();
  if (!state.messages.length) { showWelcome(); return; }
  for (const m of state.messages) renderStored(m);
  scrollDown();
}

function renderStored(m) {
  if (m.role === 'user') {
    appendMsg('user', renderMd(m.content));
    return;
  }
  if (m.role === 'assistant') {
    const wrap = document.createElement('div');
    wrap.className = 'msg assistant';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    // streaming:true at load time means the stream died with the tab.
    const interrupted = m.interrupted || m.streaming === true;
    bubble.innerHTML = renderMd(m.content || '') + (interrupted ? INTERRUPTED_HTML : '');
    wrap.appendChild(bubble);
    if (m.route || m.backend || m.model) {
      wrap.appendChild(makeBadge({ route: m.route, backend: m.backend, model: m.model }));
    }
    $msgs.appendChild(wrap);
  }
}

function addTypingDots() {
  clearWelcome();
  const wrap = document.createElement('div');
  wrap.className = 'msg assistant';
  wrap.id = '_typing';
  wrap.innerHTML = `
    <div class="bubble thinking">
      <div class="dots">
        <div class="dot"></div><div class="dot"></div><div class="dot"></div>
      </div>
      <div class="think-meta">
        <span class="think-label">Connecting…</span>
        <span class="think-note"></span>
      </div>
    </div>`;
  $msgs.appendChild(wrap);
  scrollDown();

  let base = 'Connecting';
  const $lbl = wrap.querySelector('.think-label');
  const $note = wrap.querySelector('.think-note');
  const t0 = Date.now();
  let tid = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $lbl.textContent = s > 0 ? `${base} (${s}s)` : `${base}…`;
    if (s === 12) $note.textContent = 'Quality models can take a moment…';
  }, 1000);

  return {
    el: wrap,
    setGenerating(modelName) {
      base = modelName ? `Generating · ${modelName}` : 'Generating';
      $note.textContent = '';
    },
    stop() { if (tid) { clearInterval(tid); tid = null; } },
  };
}

function scrollDown() { $msgs.scrollTop = $msgs.scrollHeight; }

// ── Persistence ─────────────────────────────────────────────────────────────
function newConversation() {
  state.conv = { id: uuid(), title: null, createdAt: nowIso(), pinned: false, lastRoute: null };
  state.messages = [];
}

function firstUserContent() {
  const m = state.messages.find(x => x.role === 'user');
  return m ? m.content : '';
}

async function persist() {
  if (state.persistence !== 'indexeddb' || !state.conv) return;
  const record = {
    schemaVersion: SCHEMA_VERSION,
    id: state.conv.id,
    title: state.conv.title || deriveTitle(firstUserContent()),
    createdAt: state.conv.createdAt,
    updatedAt: nowIso(),
    pinned: !!state.conv.pinned,
    lastRoute: state.conv.lastRoute || null,
    messages: state.messages,
  };
  state.conv.title = record.title;
  try {
    await db.saveConversation(record);
  } catch (e) {
    if (e && e.name === 'QuotaExceededError') {
      toast('History storage full — export and clear old chats.');
    } else {
      console.warn('persist failed', e);
    }
  }
}

let _persistTimer = null;
function persistDebounced() {
  if (state.persistence !== 'indexeddb') return;
  if (_persistTimer) clearTimeout(_persistTimer);
  _persistTimer = setTimeout(() => { _persistTimer = null; persist(); }, 500);
}

function newChat() {
  maybeAutoExtract();   // best-effort on the conversation being left
  // Active conversation is already persisted incrementally; just reset.
  state.conv = null;
  state.messages = [];
  clearFiles();
  showWelcome();
  refreshSidebar();
  $input.focus();
}

// ── Memory extraction (Phase 4) ───────────────────────────────────────────
async function extractFrom(conversationId, messages) {
  const payload = {
    messages: messages
      .filter(m => (m.role === 'user' || m.role === 'assistant') && m.content)
      .map(m => ({ role: m.role, content: m.content })),
  };
  if (payload.messages.length < 2) return 0;
  try {
    const r = await fetch('/chat/memory/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) return 0;
    const { facts = [] } = await r.json();
    if (!facts.length) return 0;
    return await addSuggestions(facts, conversationId);
  } catch {
    return 0;
  }
}

// Manual "Extract from current chat" (from the memory panel).
async function extractCurrent() {
  if (!state.conv || state.messages.filter(m => m.content).length < 2) {
    toast('Have a short conversation first, then extract.');
    return;
  }
  const added = await extractFrom(state.conv.id, state.messages);
  toast(added ? `Found ${added} new suggestion${added === 1 ? '' : 's'}.` : 'No new suggestions found.');
}

// Best-effort auto-extraction on leaving a conversation. Snapshots state
// synchronously so it's safe even though the caller resets state right after.
function maybeAutoExtract() {
  if (state.persistence !== 'indexeddb') return;
  const conv = state.conv;
  const messages = state.messages;
  if (!conv) return;
  const exchanges = messages.filter(m => m.role === 'assistant' && m.content).length;
  if (exchanges < 2) return;
  const convId = conv.id;
  const snapshot = messages.slice();
  (async () => {
    if (!(await db.getSetting('autoExtract', true))) return;
    const added = await extractFrom(convId, snapshot);
    if (added) refreshMemoryUI();
  })();
}

// Called after a bulk data change (import / clear). Refresh the sidebar and,
// if the active conversation no longer exists, reset to a new chat.
async function onDataChanged() {
  await refreshSidebar();
  if (state.conv?.id) {
    try {
      const still = await db.getConversation(state.conv.id);
      if (!still) newChat();
    } catch { /* ignore */ }
  }
}

async function resume(id) {
  if (state.streaming) return;
  if (state.conv?.id !== id) maybeAutoExtract();   // leaving the current chat
  const conv = await db.getConversation(id);
  if (!conv) { refreshSidebar(); return; }
  state.conv = {
    id: conv.id,
    title: conv.title,
    createdAt: conv.createdAt,
    pinned: conv.pinned,
    lastRoute: conv.lastRoute,
  };
  state.messages = conv.messages || [];
  clearFiles();
  renderConversation();
  refreshSidebar();
}

// ── Send ──────────────────────────────────────────────────────────────────
async function send() {
  if (state.streaming) return;
  const text = $input.value.trim();
  if (!text && state.files.length === 0) return;

  let userContent = text;
  if (state.files.length > 0) {
    const fileParts = state.files
      .map(f => `[File: ${f.name}]\n${f.content}`)
      .join('\n\n---\n\n');
    userContent = userContent ? `${userContent}\n\n${fileParts}` : fileParts;
  }

  // Attachment metadata only — never raw bytes, and not extracted text (Phase 1).
  const attachments = state.files.map(f => ({
    id: uuid(),
    name: f.name,
    mimeType: f.mimeType || '',
    size: f.size || 0,
    extractedTextStored: false,
    extractedText: null,
    summary: null,
  }));

  $input.value = '';
  autoResize();
  clearFiles();

  if (!state.conv) newConversation();

  const userMsg = { id: uuid(), role: 'user', content: userContent, ts: nowIso(), attachments };
  state.messages.push(userMsg);
  appendMsg('user', renderMd(userContent));

  if (!state.conv.title) state.conv.title = deriveTitle(userContent);

  // Persist the user message immediately (survives a mid-stream reload).
  await persist();
  refreshSidebar();

  // Build request body BEFORE adding the empty assistant placeholder.
  // Memory is injected as a system message that is re-derived per request and
  // never persisted into history.
  const convMessages = state.messages
    .filter(m => m.role === 'user' || (m.role === 'assistant' && m.content))
    .map(m => ({ role: m.role, content: m.content }));
  const memBlock = await buildInjection();
  const reqMessages = memBlock
    ? [{ role: 'system', content: memBlock }, ...convMessages]
    : convMessages;

  const thinking = addTypingDots();
  state.streaming = true;
  $send.disabled = true;

  const aMsg = { id: uuid(), role: 'assistant', content: '', ts: nowIso(), streaming: true, interrupted: false };
  let aMsgAdded = false;

  try {
    const resp = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: $model.value, messages: reqMessages, stream: true }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      const msg = (typeof err.detail === 'object' ? err.detail.message : err.detail) || resp.statusText;
      throw new Error(msg);
    }

    const routeInfo = {
      route:   resp.headers.get('x-ai-route'),
      backend: resp.headers.get('x-ai-backend'),
      model:   resp.headers.get('x-ai-model'),
      reason:  resp.headers.get('x-ai-auto-reason'),
    };

    thinking.setGenerating(routeInfo.model || routeInfo.route);
    thinking.stop();
    thinking.el.remove();

    // Register the assistant placeholder and persist it as streaming.
    state.messages.push(aMsg);
    aMsgAdded = true;
    persistDebounced();

    clearWelcome();
    const wrap = document.createElement('div');
    wrap.className = 'msg assistant';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    wrap.appendChild(bubble);
    $msgs.appendChild(wrap);
    scrollDown();

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', full = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (data === '[DONE]') continue;
          try {
            const token = JSON.parse(data)?.choices?.[0]?.delta?.content || '';
            if (token) {
              full += token;
              aMsg.content = full;
              bubble.innerHTML = renderMd(full);
              scrollDown();
              persistDebounced();
            }
          } catch { /* ignore malformed chunk */ }
        }
      }
    } catch (streamErr) {
      if (!full) throw streamErr;
      aMsg.interrupted = true;
      bubble.innerHTML = renderMd(full) + INTERRUPTED_HTML;
    }

    aMsg.streaming = false;
    aMsg.route = routeInfo.route;
    aMsg.backend = routeInfo.backend;
    aMsg.model = routeInfo.model;
    if (routeInfo.route || routeInfo.backend) wrap.appendChild(makeBadge(routeInfo));
    if (routeInfo.route) state.conv.lastRoute = routeInfo.route;

    await persist();
    refreshSidebar();

  } catch (err) {
    thinking.stop();
    document.getElementById('_typing')?.remove();
    if (aMsgAdded) {
      aMsg.streaming = false;
      aMsg.interrupted = true;
      await persist();
    }
    appendMsg('error', esc(`Error: ${err.message}`));
  } finally {
    state.streaming = false;
    $send.disabled = false;
    $input.focus();
  }
}

// ── File handling ─────────────────────────────────────────────────────────
const TEXT_EXTS = new Set([
  'txt', 'md', 'py', 'js', 'ts', 'jsx', 'tsx', 'json', 'yaml', 'yml',
  'csv', 'html', 'css', 'sh', 'bash', 'zsh', 'sql', 'xml', 'toml', 'ini', 'env',
  'c', 'cpp', 'h', 'rs', 'go', 'java', 'rb', 'php', 'swift', 'kt',
]);

async function processFile(file) {
  if (file.size > 5 * 1024 * 1024) {
    alert(`${file.name}: too large (max 5 MB)`);
    return null;
  }
  const ext = (file.name.split('.').pop() || '').toLowerCase();

  if (TEXT_EXTS.has(ext)) {
    return { name: file.name, content: await file.text(), size: file.size, mimeType: file.type || 'text/plain' };
  }

  if (ext === 'pdf' || ext === 'docx') {
    const form = new FormData();
    form.append('file', file);
    const r = await fetch('/chat/extract', { method: 'POST', body: form });
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      alert(`${file.name}: ${e.detail || r.statusText}`);
      return null;
    }
    const { text, chars } = await r.json();
    console.log(`Extracted ${chars} chars from ${file.name}`);
    return { name: file.name, content: text, size: file.size, mimeType: file.type || 'application/octet-stream' };
  }

  alert(`${file.name}: unsupported type (.${ext})\nAccepted: text files, .pdf, .docx`);
  return null;
}

async function addFiles(list) {
  for (const f of list) {
    const result = await processFile(f);
    if (result) { state.files.push(result); renderChips(); }
  }
}

function renderChips() {
  $chips.innerHTML = state.files.map((f, i) => `
    <div class="chip">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      ${esc(f.name)}
      <button data-idx="${i}" title="Remove">×</button>
    </div>
  `).join('');
}

function removeFile(i) {
  state.files.splice(i, 1);
  renderChips();
}

function clearFiles() {
  state.files = [];
  $chips.innerHTML = '';
  $fInput.value = '';
}

// ── Toast ─────────────────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg) {
  let el = document.getElementById('_toast');
  if (!el) {
    el = document.createElement('div');
    el.id = '_toast';
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);
}

// ── Voice input ─────────────────────────────────────────────────────────────
function initVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    $mic.title = 'Voice not supported in this browser (try Chrome or Safari)';
    $mic.style.opacity = '.35';
    $mic.disabled = true;
    return;
  }
  const recog = new SR();
  recog.continuous = false;
  recog.interimResults = true;
  recog.lang = 'en-US';

  let listening = false;
  recog.onstart = () => { listening = true;  $mic.classList.add('listening'); $mic.title = 'Listening… click to stop'; };
  recog.onend   = () => { listening = false; $mic.classList.remove('listening'); $mic.title = 'Voice input'; };
  recog.onerror = (e) => { console.warn('speech error', e.error); listening = false; $mic.classList.remove('listening'); };
  recog.onresult = (e) => {
    $input.value = Array.from(e.results).map(r => r[0].transcript).join('');
    autoResize();
  };
  $mic.addEventListener('click', () => listening ? recog.stop() : recog.start());
}

// ── Textarea auto-resize ────────────────────────────────────────────────────
function autoResize() {
  $input.style.height = 'auto';
  $input.style.height = Math.min($input.scrollHeight, 220) + 'px';
}

// ── Drag-and-drop ───────────────────────────────────────────────────────────
function initDragDrop() {
  let dragN = 0;
  document.addEventListener('dragenter', e => { e.preventDefault(); dragN++; $dropOvl.classList.add('on'); });
  document.addEventListener('dragleave', () => { if (--dragN <= 0) { dragN = 0; $dropOvl.classList.remove('on'); } });
  document.addEventListener('dragover', e => e.preventDefault());
  document.addEventListener('drop', e => {
    e.preventDefault(); dragN = 0; $dropOvl.classList.remove('on');
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
  });
}

// ── Init ──────────────────────────────────────────────────────────────────
async function init() {
  loadModels();
  initVoice();
  initDragDrop();

  $input.addEventListener('input', autoResize);
  $input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $send.addEventListener('click', send);
  $fInput.addEventListener('change', () => addFiles($fInput.files));
  $chips.addEventListener('click', e => {
    const btn = e.target.closest('button[data-idx]');
    if (btn) removeFile(Number(btn.dataset.idx));
  });

  initHistory({
    getActiveId: () => state.conv?.id || null,
    onSelect: resume,
    onNew: newChat,
    onActiveDeleted: newChat,
  });

  initSettings({
    getPersistence: () => state.persistence,
    onDataChanged: onDataChanged,
  });

  initMemory({
    onExtractRequest: extractCurrent,
    onJumpToConversation: resume,
  });

  try {
    await db.openDB();
    state.persistence = 'indexeddb';
    state.historyAvailable = true;
    await refreshSidebar();
  } catch (e) {
    state.persistence = 'memory-only';
    state.historyAvailable = false;
    state.historyError = String(e);
    showUnavailable();
    toast('History unavailable — chat works, but conversations won’t be saved.');
  }

  $input.focus();
}

init();
