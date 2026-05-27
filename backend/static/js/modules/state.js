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

export function setCurrentSessionId(id) {
    state.currentSessionId = id;
}

export function setSettings(newSettings) {
    state.settings = { ...state.settings, ...newSettings };
}

export function setIsProcessing(flag) {
    state.isProcessing = flag;
}

export function setAbortController(controller) {
    state.abortController = controller;
}

export function setLiveArtifactsMode(flag) {
    state.liveArtifactsMode = Boolean(flag);
}
