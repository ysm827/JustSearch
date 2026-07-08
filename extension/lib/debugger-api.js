// chrome.debugger 封装:attach / sendCommand / detach,按 tabId 串行。
// 仿 browser-control-bridge 的 src/background/debuggerApi.ts,简化。

const attachedTabs = new Set();
const tabQueues = new Map();   // tabId -> Promise<void>
const DEFAULT_CDP_TIMEOUT_MS = 10000;

export function registerDetachListener() {
  chrome.debugger.onDetach.addListener((source) => {
    if (typeof source.tabId === "number") attachedTabs.delete(source.tabId);
  });
}

export async function attachTab(tabId) {
  await withQueue(tabId, async () => {
    if (attachedTabs.has(tabId)) return;
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
    } catch (err) {
      // 重复 attach 会报 "Another debugger is already attached",吞掉。
      if (!isAnotherDebuggerError(err)) throw err;
    }
    attachedTabs.add(tabId);
  });
}

export async function detachTab(tabId) {
  await withQueue(tabId, async () => {
    try {
      await chrome.debugger.detach({ tabId });
    } finally {
      attachedTabs.delete(tabId);
    }
  });
}

export function isTabAttached(tabId) {
  return attachedTabs.has(tabId);
}

export async function detachAll() {
  const ids = [...attachedTabs];
  await Promise.allSettled(ids.map((t) => detachTab(t)));
}

// 执行任意 CDP 命令,带超时。
// 返回 chrome.debugger.sendCommand 的原始结果。
export async function executeCdp({ tabId, method, params = {}, timeoutMs }) {
  const timeout = normalizeTimeout(timeoutMs);
  await attachTab(tabId);
  let timeoutId;
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => {
      reject(new Error(`CDP command "${method}" timed out after ${timeout}ms`));
    }, timeout);
  });
  try {
    return await Promise.race([
      chrome.debugger.sendCommand({ tabId }, method, params),
      timeoutPromise,
    ]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

function normalizeTimeout(ms) {
  return typeof ms === "number" && Number.isFinite(ms) && ms > 0 ? ms : DEFAULT_CDP_TIMEOUT_MS;
}

function isAnotherDebuggerError(err) {
  return String(err?.message ?? err).includes("Another debugger is already attached");
}

async function withQueue(tabId, operation) {
  const previous = tabQueues.get(tabId) ?? Promise.resolve();
  let release = () => {};
  const current = new Promise((resolve) => { release = resolve; });
  const chained = previous.catch(() => {}).then(() => current);
  tabQueues.set(tabId, chained);
  try {
    await previous.catch(() => {});
    return await operation();
  } finally {
    release();
    if (tabQueues.get(tabId) === chained) tabQueues.delete(tabId);
  }
}
