export const state = {
    currentSessionId: null,
    settings: {},
    isProcessing: false,
    abortController: null,
    openBrowserModal: null
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