import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
    setupBrowserModal,
    __browserModalTestHooks,
} from '../../backend/static/js/modules/browser-modal.js';
import { state } from '../../backend/static/js/modules/state.js?v=2';

const require = createRequire(import.meta.url);

function installBrowserModalDom() {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM(`
        <!doctype html>
        <body>
            <div id="browser-modal" class="modal">
                <button id="browser-close-btn"></button>
                <button id="browser-complete-btn"></button>
                <input id="browser-type-input">
                <button id="browser-type-send-btn"></button>
                <img id="browser-viewport" src="" alt="Browser Viewport">
                <div class="browser-status-overlay"></div>
            </div>
        </body>
    `, { url: 'http://localhost/' });

    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.Event = dom.window.Event;
    globalThis.HTMLElement = dom.window.HTMLElement;
    Object.defineProperty(globalThis, 'navigator', {
        value: dom.window.navigator,
        configurable: true,
    });

    return dom;
}

test('browser socket message parser ignores malformed payloads', () => {
    const { parseBrowserSocketMessage } = __browserModalTestHooks;

    assert.deepEqual(parseBrowserSocketMessage('{"type":"status","msg":"Completed"}'), {
        type: 'status',
        msg: 'Completed',
    });
    assert.equal(parseBrowserSocketMessage('not json'), null);
    assert.equal(parseBrowserSocketMessage('[{"type":"frame"}]'), null);
    assert.equal(parseBrowserSocketMessage('"frame"'), null);
    assert.equal(parseBrowserSocketMessage(null), null);
});

test('browser modal ignores malformed websocket messages and keeps handling frames', () => {
    installBrowserModalDom();
    const originalWebSocket = globalThis.WebSocket;
    const originalWarn = console.warn;
    const sockets = [];
    const warnings = [];

    class FakeWebSocket {
        static OPEN = 1;

        constructor(url) {
            this.url = url;
            this.readyState = FakeWebSocket.OPEN;
            sockets.push(this);
        }

        send() {}

        close() {
            this.closed = true;
        }
    }

    globalThis.WebSocket = FakeWebSocket;
    console.warn = (message) => warnings.push(String(message));

    try {
        setupBrowserModal();
        window.location.hash = '';
        state.openBrowserModal('session-1');
        const socket = sockets[0];
        assert.ok(socket);

        assert.doesNotThrow(() => {
            socket.onmessage({ data: 'not json' });
            socket.onmessage({ data: JSON.stringify({ type: 'frame', image: 'abc123' }) });
        });

        const img = document.getElementById('browser-viewport');
        const status = document.querySelector('.browser-status-overlay');
        assert.equal(img.style.display, 'block');
        assert.equal(img.getAttribute('src'), 'data:image/jpeg;base64,abc123');
        assert.equal(status.style.display, 'none');
        assert.equal(warnings.includes('Ignored malformed browser socket message.'), true);
    } finally {
        globalThis.WebSocket = originalWebSocket;
        console.warn = originalWarn;
    }
});
