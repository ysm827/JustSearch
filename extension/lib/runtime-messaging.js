// 运行时消息工具:带超时的 sendMessage + 按需注入 content script。
// 仿 browser-control-bridge 的 src/background/runtimeMessaging.ts。

const CONTENT_SCRIPT_TIMEOUT_MS = 1000;

export async function sendTabMessageWithTimeout(tabId, message, timeoutMs = CONTENT_SCRIPT_TIMEOUT_MS) {
  let timeoutId;
  const timeout = new Promise((resolve) => {
    timeoutId = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    return await Promise.race([chrome.tabs.sendMessage(tabId, message), timeout]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

export async function isContentScriptReady(tabId) {
  try {
    const response = await sendTabMessageWithTimeout(tabId, { type: "CONTENT_PING" });
    return response?.ok === true;
  } catch {
    return false;
  }
}

const inflightInjections = new Map();

export async function ensureContentScript(tabId) {
  if (await isContentScriptReady(tabId)) return true;

  let inflight = inflightInjections.get(tabId);
  if (!inflight) {
    inflight = injectAndPing(tabId).finally(() => {
      inflightInjections.delete(tabId);
    });
    inflightInjections.set(tabId, inflight);
  }
  return inflight;
}

async function injectAndPing(tabId) {
  try {
    const result = await sendScript(tabId);
    if (!result) return false;
  } catch {
    return false;
  }
  return isContentScriptReady(tabId);
}

async function sendScript(tabId) {
  await chrome.scripting.executeScript({
    files: ["content/cursor-overlay.js"],
    injectImmediately: true,
    target: { tabId },
  });
  return true;
}
