import { coerceBooleanSetting, state, setBridgeConnected } from './state.js?v=5';
import { authFetch } from './auth.js?v=1';
import { showToast } from './toast.js';

const DEFAULT_POLL_INTERVAL_MS = 5000;
const DEFAULT_WS_URL = 'ws://127.0.0.1:38975/justsearch';
const DEFAULT_DOWNLOAD_URL = '/api/extension/download';
const DOCKER_WEB_PORT = '8001';
const DOCKER_HOST_BRIDGE_PORT = '38978';

let pollTimer = null;
let lastKnownConnected = null;
let modalWired = false;
let settingsPanelWired = false;
let lastCheckedAt = null;
let lastMeta = null;

function bridgeMetaFromHealth(health) {
    const bridge = health && typeof health.bridge === 'object' ? health.bridge : {};
    const connected = Boolean(
        bridge.extension_connected ?? health?.browser ?? false
    );
    const versionStatus = String(bridge.extension_version_status || 'unknown').trim() || 'unknown';
    return {
        connected,
        wsUrl: String(bridge.ws_url || DEFAULT_WS_URL).trim() || DEFAULT_WS_URL,
        downloadUrl: String(bridge.download_url || DEFAULT_DOWNLOAD_URL).trim() || DEFAULT_DOWNLOAD_URL,
        installHint: String(bridge.install_hint || '').trim(),
        extensionName: bridge.extension_name ? String(bridge.extension_name).trim() : '',
        extensionVersion: bridge.extension_version ? String(bridge.extension_version).trim() : '',
        extensionInstanceId: bridge.extension_instance_id
            ? String(bridge.extension_instance_id).trim()
            : '',
        latestExtensionVersion: bridge.latest_extension_version
            ? String(bridge.latest_extension_version).trim()
            : '',
        extensionVersionStatus: versionStatus,
        updateAvailable: Boolean(bridge.update_available),
        isLatest: bridge.is_latest === null || bridge.is_latest === undefined
            ? null
            : Boolean(bridge.is_latest),
        checkedAt: Date.now(),
    };
}

function versionStatusLabel(status, latestVersion) {
    switch (status) {
        case 'latest':
            return '已是最新';
        case 'outdated':
            return latestVersion ? `可更新至 v${latestVersion}` : '需要更新';
        case 'newer':
            return '高于服务端包';
        case 'disconnected':
            return latestVersion ? `服务端最新 v${latestVersion}` : '未连接';
        default:
            return latestVersion ? `最新 v${latestVersion}` : '版本未知';
    }
}

export function normalizeBridgePollIntervalSec(value, fallback = 5) {
    if (value === 'manual' || value === 'off') return 0;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    if (parsed <= 0) return 0;
    if (parsed <= 3) return 3;
    if (parsed <= 5) return 5;
    return 15;
}

export function getBridgePreferences(settings = state.settings) {
    const source = settings && typeof settings === 'object' ? settings : {};
    return {
        requireBeforeSend: coerceBooleanSetting(source.bridge_require_before_send, true),
        showBanner: coerceBooleanSetting(source.bridge_show_banner, true),
        toastOnChange: coerceBooleanSetting(source.bridge_toast_on_change, true),
        pollIntervalSec: normalizeBridgePollIntervalSec(source.bridge_poll_interval_sec, 5),
    };
}

function formatCheckedAt(ts) {
    if (!ts) return '—';
    try {
        return new Date(ts).toLocaleString();
    } catch {
        return '—';
    }
}

function buildHostAccessTip(wsUrl) {
    const pagePort = String(window.location.port || '');
    let recommended = '';
    try {
        const asHttp = wsUrl.replace(/^ws/i, 'http').replace(/^wss/i, 'https');
        const parsed = new URL(asHttp);
        if (pagePort === DOCKER_WEB_PORT && (parsed.port === '38975' || parsed.port === '')) {
            recommended = `ws://127.0.0.1:${DOCKER_HOST_BRIDGE_PORT}${parsed.pathname || '/justsearch'}`;
            return `检测到页面运行在 Docker 映射端口 ${DOCKER_WEB_PORT}。扩展请连接 ${recommended}（宿主映射），而不是容器内的 38975。`;
        }
    } catch {
        // ignore parse errors
    }
    return '扩展弹窗中的地址须与上方「期望 WS 地址」一致。Docker 部署时注意宿主端口映射。';
}

export async function fetchBridgeStatus() {
    try {
        const res = await authFetch('/api/health');
        if (!res.ok) {
            const meta = {
                connected: false,
                wsUrl: state.bridgeWsUrl || DEFAULT_WS_URL,
                downloadUrl: state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL,
                installHint: '',
                extensionName: '',
                extensionVersion: '',
                extensionInstanceId: '',
                latestExtensionVersion: lastMeta?.latestExtensionVersion || '',
                extensionVersionStatus: 'unknown',
                updateAvailable: false,
                isLatest: null,
                checkedAt: Date.now(),
            };
            setBridgeConnected(false);
            lastCheckedAt = meta.checkedAt;
            lastMeta = meta;
            updateBridgeStatusUI(meta);
            return meta;
        }
        const health = await res.json();
        const meta = bridgeMetaFromHealth(health);
        setBridgeConnected(meta.connected);
        lastCheckedAt = meta.checkedAt;
        lastMeta = meta;
        updateBridgeStatusUI(meta);
        return meta;
    } catch (error) {
        console.warn('[bridge] health check failed', error);
        const meta = {
            connected: false,
            wsUrl: state.bridgeWsUrl || DEFAULT_WS_URL,
            downloadUrl: state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL,
            installHint: '',
            extensionName: '',
            extensionVersion: '',
            extensionInstanceId: '',
            latestExtensionVersion: lastMeta?.latestExtensionVersion || '',
            extensionVersionStatus: 'unknown',
            updateAvailable: false,
            isLatest: null,
            checkedAt: Date.now(),
        };
        setBridgeConnected(false);
        lastCheckedAt = meta.checkedAt;
        lastMeta = meta;
        updateBridgeStatusUI(meta);
        return meta;
    }
}

function setDotState(el, connected) {
    if (!el) return;
    if (connected === null || connected === undefined) {
        el.dataset.state = 'unknown';
        return;
    }
    el.dataset.state = connected ? 'connected' : 'disconnected';
}

export function updateBridgeStatusUI(meta = null) {
    const prefs = getBridgePreferences();
    const resolved = meta || lastMeta;
    const connected = resolved
        ? Boolean(resolved.connected)
        : state.bridgeConnected;
    const wsUrl = resolved?.wsUrl || state.bridgeWsUrl || DEFAULT_WS_URL;
    const downloadUrl = resolved?.downloadUrl || state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL;
    const installHint = resolved?.installHint || '';
    const extensionName = resolved?.extensionName || '';
    const extensionVersion = resolved?.extensionVersion || '';
    const extensionInstanceId = resolved?.extensionInstanceId || '';
    const latestExtensionVersion = resolved?.latestExtensionVersion || '';
    const extensionVersionStatus = resolved?.extensionVersionStatus || 'unknown';
    const updateAvailable = Boolean(resolved?.updateAvailable);
    const checkedAt = resolved?.checkedAt || lastCheckedAt;

    if (resolved) {
        state.bridgeWsUrl = wsUrl;
        state.bridgeDownloadUrl = downloadUrl;
        state.bridgeLastCheckedAt = checkedAt || null;
        state.bridgeExtensionVersion = extensionVersion || null;
        state.bridgeExtensionName = extensionName || null;
        state.bridgeLatestExtensionVersion = latestExtensionVersion || null;
        state.bridgeUpdateAvailable = updateAvailable;
    }

    const statusBtn = document.getElementById('bridge-status-btn');
    const statusLabel = document.getElementById('bridge-status-label');
    const banner = document.getElementById('bridge-status-banner');
    const modalStatus = document.getElementById('bridge-modal-status');
    const wsInput = document.getElementById('bridge-ws-url');

    if (statusBtn) {
        setDotState(statusBtn, connected);
        statusBtn.setAttribute(
            'aria-label',
            connected ? '浏览器桥接已连接' : '浏览器桥接未连接，点击查看安装说明'
        );
        statusBtn.title = connected
            ? '浏览器桥接已连接'
            : '扩展未连接 — 点击查看安装说明';
    }
    if (statusLabel) {
        if (connected === null || connected === undefined) {
            statusLabel.textContent = '桥接检测中';
        } else if (connected && updateAvailable) {
            statusLabel.textContent = extensionVersion
                ? `桥接可更新 · v${extensionVersion}`
                : '桥接可更新';
        } else if (connected) {
            statusLabel.textContent = extensionVersion
                ? `桥接已连接 · v${extensionVersion}`
                : '桥接已连接';
        } else {
            statusLabel.textContent = '扩展未连接';
        }
    }
    if (banner) {
        const showBanner = !connected && prefs.showBanner && connected !== null && connected !== undefined;
        banner.hidden = !showBanner;
        banner.setAttribute('aria-hidden', showBanner ? 'false' : 'true');
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

    // Settings panel fields
    const settingsDot = document.getElementById('settings-bridge-status-dot');
    const tabDot = document.getElementById('settings-bridge-tab-dot');
    const settingsHero = document.getElementById('settings-bridge-hero');
    const settingsBadge = document.getElementById('settings-bridge-status-badge');
    setDotState(settingsDot, connected);
    setDotState(tabDot, connected);
    if (settingsHero) setDotState(settingsHero, connected);
    if (settingsBadge) {
        settingsBadge.classList.remove('is-saved', 'is-pending', 'is-saving', 'is-invalid', 'is-error');
        if (connected === null || connected === undefined) {
            settingsBadge.dataset.state = 'unknown';
            settingsBadge.textContent = '检测中';
            settingsBadge.classList.add('is-pending');
        } else if (!connected) {
            settingsBadge.dataset.state = 'disconnected';
            settingsBadge.textContent = '未连接';
            settingsBadge.classList.add('is-invalid');
        } else if (updateAvailable) {
            settingsBadge.dataset.state = 'outdated';
            settingsBadge.textContent = '可更新';
            settingsBadge.classList.add('is-invalid');
        } else {
            settingsBadge.dataset.state = 'connected';
            settingsBadge.textContent = '已连接';
            settingsBadge.classList.add('is-saved');
        }
    }
    if (settingsHero) {
        if (updateAvailable) settingsHero.dataset.state = 'outdated';
        else setDotState(settingsHero, connected);
    }

    const settingsTitle = document.getElementById('settings-bridge-status-title');
    const settingsSubtitle = document.getElementById('settings-bridge-status-subtitle');
    if (settingsTitle) {
        if (connected === null || connected === undefined) {
            settingsTitle.textContent = '检测中…';
        } else if (!connected) {
            settingsTitle.textContent = '扩展未连接';
        } else if (updateAvailable) {
            settingsTitle.textContent = '扩展已连接 · 有新版本';
        } else {
            settingsTitle.textContent = '扩展已连接';
        }
    }
    if (settingsSubtitle) {
        if (connected && updateAvailable && extensionVersion && latestExtensionVersion) {
            settingsSubtitle.textContent = `当前 v${extensionVersion}，服务端最新 v${latestExtensionVersion}。请重新加载扩展或下载安装包。`;
        } else if (connected && extensionVersion) {
            const name = extensionName || 'JustSearch Bridge';
            const latestNote = latestExtensionVersion && extensionVersionStatus === 'latest'
                ? ' · 已是最新'
                : '';
            settingsSubtitle.textContent = `${name} v${extensionVersion}${latestNote} · 搜索与读网页经本机 Chrome 执行`;
        } else if (connected) {
            settingsSubtitle.textContent = '搜索与读网页将通过本机 Chrome 扩展执行（等待扩展上报版本…）';
        } else if (connected === null || connected === undefined) {
            settingsSubtitle.textContent = '正在查询桥接状态';
        } else {
            const latestNote = latestExtensionVersion ? `（服务端最新 v${latestExtensionVersion}）` : '';
            settingsSubtitle.textContent = `安装并连接 JustSearch Bridge 后才能搜索${latestNote}`;
        }
    }

    const settingsName = document.getElementById('settings-bridge-extension-name');
    if (settingsName) {
        settingsName.textContent = connected
            ? (extensionName || 'JustSearch Bridge')
            : '—';
    }
    const settingsVersion = document.getElementById('settings-bridge-extension-version');
    if (settingsVersion) {
        settingsVersion.textContent = connected
            ? (extensionVersion ? `v${extensionVersion}` : '等待上报…')
            : '—';
        settingsVersion.classList.toggle('is-pending', Boolean(connected && !extensionVersion));
        settingsVersion.classList.toggle('is-ready', Boolean(connected && extensionVersion && !updateAvailable));
        settingsVersion.classList.toggle('is-outdated', Boolean(connected && updateAvailable));
    }

    const settingsLatest = document.getElementById('settings-bridge-latest-version');
    if (settingsLatest) {
        settingsLatest.textContent = latestExtensionVersion ? `v${latestExtensionVersion}` : '—';
    }

    const settingsVersionStatus = document.getElementById('settings-bridge-version-status');
    if (settingsVersionStatus) {
        settingsVersionStatus.dataset.state = extensionVersionStatus || 'unknown';
        settingsVersionStatus.classList.remove('is-saved', 'is-pending', 'is-saving', 'is-invalid', 'is-error');
        if (extensionVersionStatus === 'latest') {
            settingsVersionStatus.classList.add('is-saved');
        } else if (extensionVersionStatus === 'outdated') {
            settingsVersionStatus.classList.add('is-invalid');
        } else if (extensionVersionStatus === 'newer') {
            settingsVersionStatus.classList.add('is-pending');
        }
        settingsVersionStatus.textContent = versionStatusLabel(
            extensionVersionStatus,
            latestExtensionVersion
        );
    }

    const updateCallout = document.getElementById('settings-bridge-update-callout');
    if (updateCallout) {
        const showUpdate = Boolean(connected && updateAvailable);
        updateCallout.hidden = !showUpdate;
        updateCallout.setAttribute('aria-hidden', showUpdate ? 'false' : 'true');
        const updateText = document.getElementById('settings-bridge-update-text');
        if (updateText && showUpdate) {
            updateText.textContent = `当前 ${extensionVersion ? `v${extensionVersion}` : '未知'}，最新 ${latestExtensionVersion ? `v${latestExtensionVersion}` : '未知'}。请下载最新包后在 chrome://extensions 中重新加载。`;
        }
    }

    const settingsInstance = document.getElementById('settings-bridge-instance-id');
    if (settingsInstance) {
        settingsInstance.textContent = connected
            ? (extensionInstanceId || '—')
            : '—';
    }

    const settingsWs = document.getElementById('settings-bridge-ws-url');
    if (settingsWs) settingsWs.textContent = wsUrl;

    const settingsLast = document.getElementById('settings-bridge-last-checked');
    if (settingsLast) settingsLast.textContent = formatCheckedAt(checkedAt);

    if (modalStatus) {
        if (connected && updateAvailable && extensionVersion && latestExtensionVersion) {
            modalStatus.dataset.state = 'disconnected';
            modalStatus.textContent = `状态：已连接，但扩展可更新（v${extensionVersion} → v${latestExtensionVersion}）`;
        } else if (connected && extensionVersion) {
            modalStatus.dataset.state = 'connected';
            modalStatus.textContent = `状态：已连接 · 扩展 v${extensionVersion}${extensionVersionStatus === 'latest' ? ' · 最新' : ''}`;
        }
    }

    const settingsHint = document.getElementById('settings-bridge-install-hint');
    if (settingsHint) {
        settingsHint.textContent = installHint
            || 'Chrome → chrome://extensions → 开发者模式 → 加载已解压扩展';
    }

    const settingsTip = document.getElementById('settings-bridge-host-tip');
    if (settingsTip) settingsTip.textContent = buildHostAccessTip(wsUrl);

    if (prefs.toastOnChange) {
        if (lastKnownConnected === false && connected) {
            if (updateAvailable) {
                showToast(
                    latestExtensionVersion
                        ? `桥接已连接，扩展可更新至 v${latestExtensionVersion}`
                        : '桥接已连接，扩展有可用更新',
                    'warning',
                    4500
                );
            } else {
                showToast('浏览器桥接已连接', 'success');
            }
        } else if (lastKnownConnected === true && connected === false) {
            showToast('浏览器桥接已断开', 'warning');
        }
    }
    if (connected === true || connected === false) {
        lastKnownConnected = connected;
    }
}

export async function downloadBridgeExtensionPackage() {
    const href = state.bridgeDownloadUrl || DEFAULT_DOWNLOAD_URL;
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
}

async function copyText(value, successMessage) {
    try {
        await navigator.clipboard.writeText(value);
        showToast(successMessage, 'success');
    } catch {
        showToast('复制失败，请手动选择文本', 'error');
    }
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
            const value = document.getElementById('bridge-ws-url')?.value || state.bridgeWsUrl || DEFAULT_WS_URL;
            await copyText(value, '已复制 WebSocket 地址');
        });
    }

    const copyExtPathBtn = document.getElementById('bridge-copy-extensions-url-btn');
    if (copyExtPathBtn) {
        copyExtPathBtn.addEventListener('click', async () => {
            await copyText('chrome://extensions', '已复制 chrome://extensions');
        });
    }

    const downloadBtn = document.getElementById('bridge-download-btn');
    if (downloadBtn && !downloadBtn.dataset.downloadWired) {
        downloadBtn.dataset.downloadWired = '1';
        downloadBtn.addEventListener('click', async (event) => {
            event.preventDefault();
            downloadBtn.setAttribute('aria-busy', 'true');
            try {
                await downloadBridgeExtensionPackage();
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

export function wireBridgeSettingsPanel() {
    if (settingsPanelWired) return;
    const panel = document.getElementById('tab-bridge');
    if (!panel) return;
    settingsPanelWired = true;

    const recheckBtn = document.getElementById('settings-bridge-recheck-btn');
    if (recheckBtn) {
        recheckBtn.addEventListener('click', async () => {
            recheckBtn.disabled = true;
            try {
                const meta = await fetchBridgeStatus();
                showToast(
                    meta.connected ? '扩展已连接' : '仍未检测到扩展连接',
                    meta.connected ? 'success' : 'warning',
                    4000
                );
            } finally {
                recheckBtn.disabled = false;
            }
        });
    }

    const downloadHandler = async (btn) => {
        if (!btn) return;
        btn.disabled = true;
        try {
            await downloadBridgeExtensionPackage();
            showToast('扩展包已开始下载', 'success');
        } catch (error) {
            console.error('[bridge] settings download failed', error);
            showToast('下载失败，请检查网络或认证后重试', 'error');
        } finally {
            btn.disabled = false;
        }
    };

    const downloadBtn = document.getElementById('settings-bridge-download-btn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', () => downloadHandler(downloadBtn));
    }
    const updateDownloadBtn = document.getElementById('settings-bridge-update-download-btn');
    if (updateDownloadBtn) {
        updateDownloadBtn.addEventListener('click', () => downloadHandler(updateDownloadBtn));
    }

    const installBtn = document.getElementById('settings-bridge-install-btn');
    if (installBtn) {
        installBtn.addEventListener('click', () => openBridgeInstallModal());
    }

    const copyWsBtn = document.getElementById('settings-bridge-copy-ws-btn');
    if (copyWsBtn) {
        copyWsBtn.addEventListener('click', async () => {
            const value = document.getElementById('settings-bridge-ws-url')?.textContent?.trim()
                || state.bridgeWsUrl
                || DEFAULT_WS_URL;
            await copyText(value, '已复制 WebSocket 地址');
        });
    }

    const copyExtBtn = document.getElementById('settings-bridge-copy-extensions-btn');
    if (copyExtBtn) {
        copyExtBtn.addEventListener('click', async () => {
            await copyText('chrome://extensions', '已复制 chrome://extensions');
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
 * When "require before send" is disabled, always returns true (debug mode).
 */
export async function ensureBridgeConnected({ forceRefresh = true } = {}) {
    const prefs = getBridgePreferences();
    if (!prefs.requireBeforeSend) {
        if (forceRefresh) {
            fetchBridgeStatus().catch(() => {});
        }
        return true;
    }

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
        `搜索依赖 Chrome 扩展桥接，当前未连接${suffix}。可在设置 → 浏览器桥接中查看详情。`,
        'warning',
        4500
    );
}

export function restartBridgeStatusPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    const prefs = getBridgePreferences();
    const intervalMs = prefs.pollIntervalSec > 0
        ? prefs.pollIntervalSec * 1000
        : 0;
    if (intervalMs <= 0) return;
    pollTimer = setInterval(() => {
        if (document.hidden) return;
        fetchBridgeStatus();
    }, intervalMs);
}

export function startBridgeStatusPolling() {
    wireBridgeModalOnce();
    wireBridgeSettingsPanel();

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
    restartBridgeStatusPolling();

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) fetchBridgeStatus();
    });
}

/** Call after settings save so poll interval / banner prefs apply immediately. */
export function applyBridgePreferencesFromSettings() {
    updateBridgeStatusUI(lastMeta);
    restartBridgeStatusPolling();
}

export function isBridgeRequiredError(detail) {
    if (!detail) return false;
    if (typeof detail === 'object') {
        return detail.code === 'BRIDGE_REQUIRED'
            || /扩展未连接|桥接不可用|BRIDGE_REQUIRED/i.test(String(detail.message || ''));
    }
    return /扩展未连接|桥接不可用|BRIDGE_REQUIRED|JustSearch Bridge/i.test(String(detail));
}
