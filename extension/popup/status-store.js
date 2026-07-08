// popup 与 background 共享的状态存储。
// background 通过 onStatusChange 把 WS 状态写进 chrome.storage.local,
// popup 订阅 storage 变化刷新 UI。

const STATUS_KEY = "JUSTSEARCH_BRIDGE_STATUS";

export async function publishStatus(status) {
  await chrome.storage.local.set({ [STATUS_KEY]: { ...status, ts: Date.now() } });
}

export async function readStatus() {
  const stored = await chrome.storage.local.get(STATUS_KEY);
  return stored[STATUS_KEY] ?? null;
}

export function subscribeStatus(callback) {
  const listener = (changes, areaName) => {
    if (areaName === "local" && changes[STATUS_KEY]) {
      callback(changes[STATUS_KEY].newValue ?? null);
    }
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

export { STATUS_KEY };
