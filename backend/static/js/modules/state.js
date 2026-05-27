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

function coerceBooleanSetting(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off', ''].includes(normalized)) return false;
    }
    return Boolean(value);
}

export function setCurrentSessionId(id) {
    state.currentSessionId = id;
}

export function setSettings(newSettings) {
    state.settings = { ...state.settings, ...newSettings };
    if (
        newSettings
        && Object.prototype.hasOwnProperty.call(newSettings, 'live_artifacts_mode')
    ) {
        state.liveArtifactsMode = coerceBooleanSetting(newSettings.live_artifacts_mode);
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
