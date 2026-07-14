/**
 * Regression: starting a new chat while a stream is in-flight must not let
 * late SSE re-bind currentSessionId, or the next message appends to history.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '../..');

function installBrowserGlobals() {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/' });
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.localStorage = dom.window.localStorage;
    globalThis.sessionStorage = dom.window.sessionStorage;
    globalThis.location = dom.window.location;
    window.markdownit = () => ({
        render: (value) => String(value || ''),
        utils: { escapeHtml: (value) => String(value || '') },
    });
    window.DOMPurify = { sanitize: (value) => String(value || '') };
    window.hljs = { getLanguage: () => false };
    return dom;
}

function stateModuleUrl() {
    return pathToFileURL(path.join(root, 'backend/static/js/modules/state.js')).href + '?v=4';
}

function apiModuleUrl() {
    return pathToFileURL(path.join(root, 'backend/static/js/modules/api.js')).href + '?v=9';
}

test('chatEpoch bump isolates abandoned stream session rebinding', async () => {
    installBrowserGlobals();
    const {
        state,
        setCurrentSessionId,
        bumpChatEpoch,
        isChatEpochCurrent,
        abortActiveStream,
        setAbortController,
        setIsProcessing,
    } = await import(stateModuleUrl());

    setCurrentSessionId('old-session');
    setIsProcessing(true);
    const controller = new AbortController();
    setAbortController(controller);

    const streamEpoch = state.chatEpoch;
    assert.equal(isChatEpochCurrent(streamEpoch), true);

    // Simulate "new chat": abort + bump + clear session
    abortActiveStream();
    bumpChatEpoch();
    setCurrentSessionId(null);

    assert.equal(controller.signal.aborted, true);
    assert.equal(state.isProcessing, false);
    assert.equal(state.currentSessionId, null);
    assert.equal(isChatEpochCurrent(streamEpoch), false);

    // Late SSE answer from old stream must not reclaim session when guarded.
    if (isChatEpochCurrent(streamEpoch)) {
        setCurrentSessionId('old-session');
    }
    assert.equal(state.currentSessionId, null);
});

test('streamChat freezes explicit sessionId in request body', async () => {
    installBrowserGlobals();
    const { state, setCurrentSessionId } = await import(stateModuleUrl());
    const { streamChat } = await import(apiModuleUrl());

    setCurrentSessionId('live-session');

    let postedBody = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_url, options = {}) => {
        postedBody = JSON.parse(options.body);
        // Mid-flight: user switched to new chat and cleared session.
        setCurrentSessionId(null);
        return {
            ok: true,
            body: {
                getReader() {
                    return {
                        async read() {
                            return { done: true, value: undefined };
                        },
                    };
                },
            },
        };
    };

    try {
        await streamChat('hello', {
            sessionId: 'live-session',
            model: 'm',
            providerId: 'p',
            liveArtifactsMode: false,
            onLog() {},
            onAnswerChunk() {},
            onAnswer() {},
            onSources() {},
            onStats() {},
            onError() {},
            onDone() {},
            onMeta() {},
        });
    } finally {
        globalThis.fetch = originalFetch;
    }

    assert.ok(postedBody, 'expected chat request body');
    assert.equal(postedBody.session_id, 'live-session');
    assert.equal(state.currentSessionId, null);
});

test('streamChat with null sessionId starts a new conversation', async () => {
    installBrowserGlobals();
    const { setCurrentSessionId } = await import(stateModuleUrl());
    const { streamChat } = await import(apiModuleUrl());

    setCurrentSessionId('should-not-be-used');

    let postedBody = null;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_url, options = {}) => {
        postedBody = JSON.parse(options.body);
        return {
            ok: true,
            body: {
                getReader() {
                    return {
                        async read() {
                            return { done: true, value: undefined };
                        },
                    };
                },
            },
        };
    };

    try {
        await streamChat('new topic', {
            sessionId: null,
            onDone() {},
        });
    } finally {
        globalThis.fetch = originalFetch;
    }

    assert.equal(postedBody.session_id, null);
});
