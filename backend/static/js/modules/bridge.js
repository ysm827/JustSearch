import { state, setBridgeConnected } from './state.js?v=3';
import { authFetch } from './auth.js?v=1';
import { showToast } from './toast.js';

const POLL_INTERVAL_MS = 5000;
const DEFAULT_WS_URL = 'ws://127.0.0.1:38975/justsearch';
const DEFAULT_DOWNLOAD_URL = '/api/extension/download';

let pollTimer = null;
let lastKnownConnected = null;
let modalWired = false;

function bridgeMetaFromHealth(health) {
    const bridge = health && typeof health.bridge === 'object' ? health.bridge : {};
    const connected = Boolean(
        bridge.extension_connected ?? health?.browser ?? false
    );
    return {
        connected,
        wsUrl: String(bridge.ws_url || DEFAULT_WS_URL).trim() || DEFAULT_WS_URL,
        downloadUrl: String(bridge.download_url || DEFAULT_DOWNLOAD_URL).trim() || DEFAULT_DOWNLOAD_URL,
        installHint: String(bridge.install_hint || '').trim(),
    };
}

export async function fetchBridgeStatus() {
    try {
        const res = await authFetch('/api/health');
        if (!res.ok) {
            setBridgeConnected(false);
            updateBridgeStatusUI();
            return { connected: false, wsUrl: DEFAULT_WS_URL, downloadUrl: DEFAULT_DOWNLOAD_URL };
        }
        const health = await res.json();
        const meta = bridgeMetaFromHealth(health);
        setBridgeConnected(meta.connected);
        updateBridgeStatusUI(meta);
        return meta;
    } catch (error) {
        console.warn('[bridge] health check failed', error);
        setBridgeConnected(false);
        updateBridgeStatusUI();
        return { connected: false, wsUrl: DEFAULT_WS_URL, downloadUrl: DEFAULT_DOWNLOAD_URL };
    }
}

export function updateBridgeStatusUI(meta = null) {
    const connected = meta ? Boolean(meta.connected) : Boolean(state.bridgeConnected);
    const wsUrl = meta?.wsUrl || state.bridgeWsUrl || DEFAULT_WS_URL;
    const downloadUrl = meta?.downloadUrl || state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL;

    if (meta) {
        state.bridgeWsUrl = wsUrl;
        state.bridgeDownloadUrl = downloadUrl;
    }

    const statusBtn = document.getElementById('bridge-status-btn');
    const statusLabel = document.getElementById('bridge-status-label');
    const banner = document.getElementById('bridge-status-banner');
    const modalStatus = document.getElementById('bridge-modal-status');
    const wsInput = document.getElementById('bridge-ws-url');

    if (statusBtn) {
        statusBtn.dataset.state = connected ? 'connected' : 'disconnected';
        statusBtn.setAttribute(
            'aria-label',
            connected ? '浏览器桥接已连接' : '浏览器桥接未连接，点击查看安装说明'
        );
        statusBtn.title = connected
            ? '浏览器桥接已连接'
            : '扩展未连接 — 点击查看安装说明';
    }
    if (statusLabel) {
        statusLabel.textContent = connected ? '桥接已连接' : '扩展未连接';
    }
    if (banner) {
        banner.hidden = connected;
        banner.setAttribute('aria-hidden', connected ? 'true' : 'false');
    }
    if (modalStatus) {
        modalStatus.dataset.state = connected ? 'connected' : 'disconnected';
        modalStatus.textContent = connected
            ? '状态：已连接，可以开始搜索'
            : '状态：未连接，请按下方步骤安装扩展';
    }
    if (wsInput && wsUrl) {
        wsInput.value = wsUrl;
    }

    const downloadLink = document.getElementById('bridge-download-btn');
    if (downloadLink) {
        downloadLink.href = downloadUrl;
    }

    if (lastKnownConnected === false && connected) {
        showToast('浏览器桥接已连接', 'success');
    }
    lastKnownConnected = connected;
}

function wireBridgeModalOnce() {
    if (modalWired) return;
    const modal = document.getElementById('bridge-install-modal');
    if (!modal) return;
    modalWired = true;

    const closeBtns = modal.querySelectorAll('[data-bridge-close]');
    closeBtns.forEach((btn) => {
        btn.addEventListener('click', () => closeBridgeInstallModal());
    });
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeBridgeInstallModal();
    });

    const recheckBtn = document.getElementById('bridge-recheck-btn');
    if (recheckBtn) {
        recheckBtn.addEventListener('click', async () => {
            recheckBtn.disabled = true;
            try {
                const meta = await fetchBridgeStatus();
                if (meta.connected) {
                    showToast('扩展已连接', 'success');
                    closeBridgeInstallModal();
                } else {
                    showToast('仍未检测到扩展连接，请确认扩展已加载且状态为绿色', 'warning', 4500);
                }
            } finally {
                recheckBtn.disabled = false;
            }
        });
    }

    const copyWsBtn = document.getElementById('bridge-copy-ws-btn');
    if (copyWsBtn) {
        copyWsBtn.addEventListener('click', async () => {
            const value = document.getElementById('bridge-ws-url')?.value || DEFAULT_WS_URL;
            try {
                await navigator.clipboard.writeText(value);
                showToast('已复制 WebSocket 地址', 'success');
            } catch {
                showToast('复制失败，请手动选择地址', 'error');
            }
        });
    }

    const copyExtPathBtn = document.getElementById('bridge-copy-extensions-url-btn');
    if (copyExtPathBtn) {
        copyExtPathBtn.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText('chrome://extensions');
                showToast('已复制 chrome://extensions', 'success');
            } catch {
                showToast('复制失败，请手动输入 chrome://extensions', 'error');
            }
        });
    }

    const downloadBtn = document.getElementById('bridge-download-btn');
    if (downloadBtn && !downloadBtn.dataset.downloadWired) {
        downloadBtn.dataset.downloadWired = '1';
        downloadBtn.addEventListener('click', async (event) => {
            // Prefer authenticated fetch so remote sessions with Bearer token still work.
            event.preventDefault();
            const href = downloadBtn.getAttribute('href') || state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL;
            downloadBtn.setAttribute('aria-busy', 'true');
            try {
                const res = await authFetch(href);
                if (!res.ok) {
                    throw new Error(`download failed: ${res.status}`);
                }
                const blob = await res.blob();
                const objectUrl = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = objectUrl;
                anchor.download = 'justsearch-bridge.zip';
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
                URL.revokeObjectURL(objectUrl);
                showToast('扩展包已开始下载', 'success');
            } catch (error) {
                console.error('[bridge] download failed', error);
                showToast('下载失败，请检查网络或认证后重试', 'error');
            } finally {
                downloadBtn.removeAttribute('aria-busy');
            }
        });
    }
}

export function openBridgeInstallModal() {
    wireBridgeModalOnce();
    const modal = document.getElementById('bridge-install-modal');
    if (!modal) return;
    updateBridgeStatusUI();
    modal.classList.add('active');
    const recheckBtn = document.getElementById('bridge-recheck-btn');
    requestAnimationFrame(() => recheckBtn?.focus());
}

export function closeBridgeInstallModal() {
    const modal = document.getElementById('bridge-install-modal');
    if (!modal) return;
    modal.classList.remove('active');
}

/**
 * Ensure the extension bridge is connected before starting a search.
 * Returns true when ready; opens the install modal and returns false otherwise.
 */
export async function ensureBridgeConnected({ forceRefresh = true } = {}) {
    let connected = state.bridgeConnected;
    if (forceRefresh || connected === null || connected === undefined) {
        const meta = await fetchBridgeStatus();
        connected = meta.connected;
    }
    if (connected) return true;
    openBridgeInstallModal();
    return false;
}

export function warnIfBridgeDisconnected(contextLabel = '') {
    if (state.bridgeConnected !== false) return;
    const suffix = contextLabel ? `（${contextLabel}）` : '';
    showToast(
        `搜索依赖 Chrome 扩展桥接，当前未连接${suffix}。点击「扩展未连接」查看安装说明。`,
        'warning',
        4500
    );
}

export function startBridgeStatusPolling() {
    wireBridgeModalOnce();

    const statusBtn = document.getElementById('bridge-status-btn');
    if (statusBtn && !statusBtn.dataset.wired) {
        statusBtn.dataset.wired = '1';
        statusBtn.addEventListener('click', () => {
            if (state.bridgeConnected) {
                showToast('浏览器桥接已连接', 'info');
            } else {
                openBridgeInstallModal();
            }
        });
    }

    const bannerAction = document.getElementById('bridge-banner-action');
    if (bannerAction && !bannerAction.dataset.wired) {
        bannerAction.dataset.wired = '1';
        bannerAction.addEventListener('click', () => openBridgeInstallModal());
    }

    fetchBridgeStatus();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
        if (document.hidden) return;
        fetchBridgeStatus();
    }, POLL_INTERVAL_MS);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) fetchBridgeStatus();
    });
}

export function isBridgeRequiredError(detail) {
    if (!detail) return false;
    if (typeof detail === 'object') {
        return detail.code === 'BRIDGE_REQUIRED'
            || /扩展未连接|桥接不可用|BRIDGE_REQUIRED/i.test(String(detail.message || ''));
    }
    return /扩展未连接|桥接不可用|BRIDGE_REQUIRED|JustSearch Bridge/i.test(String(detail));
}
