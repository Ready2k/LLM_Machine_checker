// IndexedDB wrapper for Local AI Chat — conversations, memories, settings.
//
// Notes for the LAN deployment:
//  - The gateway is served over plain HTTP (http://gatewayname:8080), which is
//    NOT a secure context. crypto.randomUUID() is therefore unavailable; uuid()
//    falls back to crypto.getRandomValues. IndexedDB itself works fine over HTTP.
//  - All chat data is per-device. The gateway stores nothing about users.

const DB_NAME = 'localai-chat';
const DB_VERSION = 1;
export const SCHEMA_VERSION = 1;

let _db = null;
let _available = false;

// ── ID generation (secure-context-safe) ────────────────────────────────────
export function uuid() {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      const b = crypto.getRandomValues(new Uint8Array(16));
      b[6] = (b[6] & 0x0f) | 0x40; // version 4
      b[8] = (b[8] & 0x3f) | 0x80; // variant
      const h = [...b].map(x => x.toString(16).padStart(2, '0'));
      return `${h[0]}${h[1]}${h[2]}${h[3]}-${h[4]}${h[5]}-${h[6]}${h[7]}-${h[8]}${h[9]}-${h[10]}${h[11]}${h[12]}${h[13]}${h[14]}${h[15]}`;
    }
  } catch { /* fall through */ }
  return 'id-' + Date.now().toString(16) + '-' + Math.random().toString(16).slice(2, 10);
}

export function isAvailable() { return _available; }

// ── Open / upgrade ──────────────────────────────────────────────────────────
export function openDB() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined' || !indexedDB) {
      return reject(new Error('IndexedDB not available'));
    }
    let req;
    try {
      req = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (e) {
      return reject(e);
    }
    req.onupgradeneeded = (ev) => {
      const db = ev.target.result;
      if (!db.objectStoreNames.contains('conversations')) {
        const s = db.createObjectStore('conversations', { keyPath: 'id' });
        s.createIndex('by_updated', 'updatedAt');
      }
      if (!db.objectStoreNames.contains('memories')) {
        const s = db.createObjectStore('memories', { keyPath: 'id' });
        s.createIndex('by_created', 'createdAt');
      }
      if (!db.objectStoreNames.contains('settings')) {
        db.createObjectStore('settings', { keyPath: 'key' });
      }
    };
    req.onsuccess = () => {
      _db = req.result;
      _available = true;
      // If another tab triggers a version change, close so it isn't blocked.
      _db.onversionchange = () => { try { _db.close(); } catch {} _db = null; _available = false; };
      resolve(_db);
    };
    req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    req.onblocked = () => reject(new Error('IndexedDB blocked by another tab'));
  });
}

// ── Internals ───────────────────────────────────────────────────────────────
function store(name, mode) {
  if (!_db) throw new Error('DB not open');
  return _db.transaction(name, mode).objectStore(name);
}

function asPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// ── Conversations ───────────────────────────────────────────────────────────
export function saveConversation(conv) { return asPromise(store('conversations', 'readwrite').put(conv)); }
export function getConversation(id)     { return asPromise(store('conversations', 'readonly').get(id)); }
export function deleteConversation(id)  { return asPromise(store('conversations', 'readwrite').delete(id)); }

export async function listConversations() {
  const all = await asPromise(store('conversations', 'readonly').getAll());
  // newest-first; callers may re-sort pinned to the top
  return all.sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''));
}

// ── Memories (stores created now; used from Phase 3) ────────────────────────
export function saveMemory(mem)    { return asPromise(store('memories', 'readwrite').put(mem)); }
export function deleteMemory(id)   { return asPromise(store('memories', 'readwrite').delete(id)); }
export async function listMemories() {
  const all = await asPromise(store('memories', 'readonly').getAll());
  return all.sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));
}

// Settings
export async function getSetting(key, fallback) {
  const row = await asPromise(store('settings', 'readonly').get(key));
  return row ? row.value : fallback;
}
export function setSetting(key, value) { return asPromise(store('settings', 'readwrite').put({ key, value })); }
