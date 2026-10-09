/**
 * Chats in IndexedDB instead of localStorage.
 *
 * localStorage holds about 10 MB per origin. By 8 October 2026 the chats
 * filled it (279 of them, 4.6M characters, half of it old tool results);
 * every save after that threw, the throw was swallowed, and a day of chats
 * lived only in the open window. IndexedDB's quota is a share of the disk.
 *
 * One record per chat, so a save writes only the chats that changed. Reads
 * are asynchronous, so main.tsx waits for `preloadChats()` before the store
 * is created and the store reads the result synchronously, as it always did.
 * Where IndexedDB is missing (the tests' jsdom) the store keeps using
 * localStorage.
 */
import type { Conversation } from '../types';

export const CHAT_DB_NAME = 'sage-chats';
const DB_VERSION = 1;
const CHATS = 'conversations';
const META = 'meta';
const META_KEY = 'state';

export interface PreloadedChats {
  conversations: Record<string, Conversation>;
  activeId: string | null;
}

let db: IDBDatabase | null = null;
let preloaded: PreloadedChats | null = null;

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export function openChatDb(factory: IDBFactory | undefined = globalThis.indexedDB): Promise<IDBDatabase | null> {
  if (!factory) return Promise.resolve(null);
  return new Promise((resolve) => {
    let req: IDBOpenDBRequest;
    try {
      req = factory.open(CHAT_DB_NAME, DB_VERSION);
    } catch {
      resolve(null);
      return;
    }
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(CHATS)) req.result.createObjectStore(CHATS);
      if (!req.result.objectStoreNames.contains(META)) req.result.createObjectStore(META);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
}

/** Open the database and read every chat, before the store is created. */
export async function preloadChats(factory?: IDBFactory): Promise<void> {
  db = await openChatDb(factory);
  if (!db) return;
  try {
    const tx = db.transaction([CHATS, META], 'readonly');
    const [keys, values, meta] = await Promise.all([
      request(tx.objectStore(CHATS).getAllKeys()),
      request(tx.objectStore(CHATS).getAll()),
      request(tx.objectStore(META).get(META_KEY)),
    ]);
    if (!keys.length) return;
    const conversations: Record<string, Conversation> = {};
    keys.forEach((key, i) => {
      conversations[String(key)] = values[i] as Conversation;
    });
    preloaded = {
      conversations,
      activeId: (meta as { activeId?: string | null } | undefined)?.activeId ?? null,
    };
  } catch {
    preloaded = null;
  }
}

/** Whether saves go to IndexedDB. */
export function chatDbReady(): boolean {
  return db !== null;
}

/** The chats read at start-up, handed over once. */
export function takePreloadedChats(): PreloadedChats | null {
  const out = preloaded;
  preloaded = null;
  return out;
}

/** Write the chats that changed, drop the ones that are gone. */
export async function writeChats(
  changed: Conversation[],
  removed: string[],
  activeId: string | null,
): Promise<void> {
  if (!db) throw new Error('chat database is not open');
  const tx = db.transaction([CHATS, META], 'readwrite');
  const chats = tx.objectStore(CHATS);
  for (const conversation of changed) chats.put(conversation, conversation.id);
  for (const id of removed) chats.delete(id);
  tx.objectStore(META).put({ version: 1, activeId }, META_KEY);
  await done(tx);
}

/** Forget the open database (tests). */
export function resetChatDbForTests(): void {
  db?.close();
  db = null;
  preloaded = null;
}
