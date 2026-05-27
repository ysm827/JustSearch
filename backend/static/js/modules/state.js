export const state = {
    currentSessionId: null,
    settings: {},
    isProcessing: false,
    abortController: null,
    liveArtifactsMode: false,
    openBrowserModal: null,
    connectionStatus: 'connected', // connected | disconnected | reconnecting
    lastActivityTime: Date.now(),
};

const BOOLEAN_SETTING_DEFAULTS = {
    interactive_search: true,
    live_artifacts_mode: false,
};

export function coerceBooleanSetting(value, fallback = false) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off', ''].includes(normalized)) return false;
    }
    if (value === undefined || value === null) return fallback;
    return Boolean(value);
}

function normalizeBooleanSettings(settings) {
    if (!settings || typeof settings !== 'object') return {};
    const normalized = { ...settings };
    Object.entries(BOOLEAN_SETTING_DEFAULTS).forEach(([key, fallback]) => {
        if (Object.prototype.hasOwnProperty.call(normalized, key)) {
            normalized[key] = coerceBooleanSetting(normalized[key], fallback);
        }
    });
    return normalized;
}

export function setCurrentSessionId(id) {
    state.currentSessionId = id;
}

export function setSettings(newSettings) {
    const normalizedSettings = normalizeBooleanSettings(newSettings);
    state.settings = { ...state.settings, ...normalizedSettings };
    if (
        normalizedSettings
        && Object.prototype.hasOwnProperty.call(normalizedSettings, 'live_artifacts_mode')
    ) {
        state.liveArtifactsMode = coerceBooleanSetting(normalizedSettings.live_artifacts_mode);
    }
}

export function setIsProcessing(flag) {
    state.isProcessing = flag;
}

export function setAbortController(controller) {
    state.abortController = controller;
}

export function setLiveArtifactsMode(flag) {
    state.liveArtifactsMode = coerceBooleanSetting(flag);
}
