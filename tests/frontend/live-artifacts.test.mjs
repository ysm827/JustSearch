import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
    getInlineLiveArtifact,
    renderLiveArtifactsForMessage,
    __liveArtifactsTestHooks,
} from '../../backend/static/js/modules/live-artifacts.js';

const {
    buildSrcdoc,
    extractInlineLiveArtifact,
    extractLiveArtifactInteraction,
    extractLiveArtifacts,
    handleArtifactFrameMessage,
    injectPreviewSecurityPolicy,
    linkArtifactCitationsInHtml,
    normalizePreviewDiagnostic,
    parseLiveArtifactInteractionSpec,
} = __liveArtifactsTestHooks;

const require = createRequire(import.meta.url);

function installBrowserGlobals(html = '<!doctype html><body></body>') {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM(html, { url: 'http://localhost/' });

    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.Event = dom.window.Event;
    globalThis.Element = dom.window.Element;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLSelectElement = dom.window.HTMLSelectElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.NodeFilter = dom.window.NodeFilter;
    globalThis.history = dom.window.history;
    globalThis.location = dom.window.location;
    if (!dom.window.HTMLElement.prototype.scrollTo) {
        dom.window.HTMLElement.prototype.scrollTo = function scrollTo(options = {}) {
            if (typeof options === 'object' && options !== null && Number.isFinite(options.top)) {
                this.scrollTop = options.top;
            }
        };
    }
    Object.defineProperty(globalThis, 'navigator', {
        value: dom.window.navigator,
        configurable: true,
    });
    window.markdownit = () => ({
        render: value => String(value || ''),
        utils: { escapeHtml: value => String(value || '') },
    });
    window.DOMPurify = { sanitize: value => String(value || '') };
    window.hljs = { getLanguage: () => false };
    window.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} });

    return dom;
}

test('complete HTML blocks are treated as self-contained Live Artifacts', () => {
    const markdown = [
        '```css',
        'body { background: red; }',
        '```',
        '```html',
        '<!DOCTYPE html>',
        '<html>',
        '<head><title>Live Artifacts Prompt</title><style>body { color: #111; }</style></head>',
        '<body><main>Ready</main><script>window.ready = true;</script></body>',
        '</html>',
        '```',
        '```js',
        'window.extra = true;',
        '```',
    ].join('\n');

    const artifacts = extractLiveArtifacts(markdown, 'message-a');

    assert.equal(artifacts.length, 1);
    assert.equal(artifacts[0].title, 'Live Artifacts Prompt');
    assert.equal(artifacts[0].language, 'html');
    assert.deepEqual(artifacts[0].supportBlockIndices, []);
    assert.match(artifacts[0].code, /<!DOCTYPE html>/i);
    assert.doesNotMatch(artifacts[0].code, /window\.extra/);
    assert.doesNotMatch(artifacts[0].code, /background:\s*red/);
});

test('artifact titles strip hostile markup without DOM parsing', () => {
    installBrowserGlobals();
    const originalCreateElement = document.createElement.bind(document);
    let createElementCalls = 0;
    document.createElement = () => {
        createElementCalls += 1;
        throw new Error('title extraction should not parse DOM nodes');
    };

    try {
        const markdown = [
            '```html',
            '<!doctype html>',
            '<html>',
            '<head><title><img src=x onerror=alert(1)>Safe &amp; Sound &#x1F4A1;</title></head>',
            '<body>Ready</body>',
            '</html>',
            '```',
        ].join('\n');
        const artifacts = extractLiveArtifacts(markdown, 'message-hostile-title');

        assert.equal(artifacts.length, 1);
        assert.equal(artifacts[0].title, 'Safe & Sound 💡');
        assert.equal(createElementCalls, 0);
    } finally {
        document.createElement = originalCreateElement;
    }
});

test('AMC-style raw inline HTML fragments render as inline Live Artifact frames', () => {
    const html = '<section style="display:grid"><strong>Inline Artifact</strong></section>';
    const artifact = extractInlineLiveArtifact(html, 'message-inline', false);

    assert.ok(artifact);
    assert.equal(artifact.inline, true);
    assert.equal(artifact.language, 'html');
    assert.match(artifact.srcdoc, /Inline Artifact/);
    assert.match(artifact.srcdoc, /justsearch-live-artifacts/);
});

test('inline Live Artifact fragments can include scoped style blocks', () => {
    const html = '<style>.metric{color:#0f766e}</style><section class="metric"><strong>Styled</strong></section>';
    const artifact = extractInlineLiveArtifact(html, 'message-style', false);

    assert.ok(artifact);
    assert.equal(artifact.language, 'html');
    assert.match(artifact.srcdoc, /\.metric/);
    assert.equal(
        extractInlineLiveArtifact('<section><script>alert(1)</script></section>', 'message-script', false),
        null,
    );
});

test('streaming open HTML fences keep the pending artifact preview path', () => {
    const artifact = extractInlineLiveArtifact(
        '```html\n<section style="display:grid"><strong>Partial',
        'message-open-fence',
        true,
    );

    assert.ok(artifact);
    assert.equal(artifact.isStreaming, true);
    assert.match(artifact.streamHtml, /Partial/);
    assert.match(artifact.srcdoc, /data-amc-stream-preview-root/);
});

test('Live Artifact srcdoc uses AMC-style CSP and preview diagnostics', () => {
    const srcdoc = buildSrcdoc('<section><img src="https://example.invalid/missing.png"></section>', 'html');

    assert.match(srcdoc, /http-equiv="Content-Security-Policy"/);
    assert.match(srcdoc, /default-src 'none'/);
    assert.match(srcdoc, /frame-src 'none'/);
    assert.match(srcdoc, /form-action 'none'/);
    assert.match(srcdoc, /event: 'diagnostic'/);
    assert.match(srcdoc, /resource-error/);
    assert.match(srcdoc, /runtime-error/);
    assert.match(srcdoc, /csp-violation/);
});

test('Live Artifact CSP injection preserves existing document heads', () => {
    const srcdoc = injectPreviewSecurityPolicy('<!doctype html><html><head><title>Demo</title></head><body>Ready</body></html>');

    assert.match(srcdoc, /<head><meta http-equiv="Content-Security-Policy"/);
    assert.match(srcdoc, /<title>Demo<\/title>/);
});

test('Live Artifact preview diagnostics are normalized before display', () => {
    assert.deepEqual(
        normalizePreviewDiagnostic({
            type: 'resource-error',
            tagName: 'img',
            url: ' https://example.invalid/missing.png ',
        }),
        {
            type: 'resource-error',
            tagName: 'img',
            url: 'https://example.invalid/missing.png',
        },
    );
    assert.deepEqual(
        normalizePreviewDiagnostic({
            type: 'runtime-error',
            message: 'Boom',
            line: 12,
            column: 3,
        }),
        {
            type: 'runtime-error',
            message: 'Boom',
            line: 12,
            column: 3,
        },
    );
    assert.equal(normalizePreviewDiagnostic({ type: 'unknown' }), null);
});

test('quick Live Artifacts button toggles AMC-style active prompt state', async () => {
    const originalSetTimeout = globalThis.setTimeout;
    const originalFetch = globalThis.fetch;
    let savedBody = null;
    globalThis.setTimeout = (callback) => {
        if (typeof callback === 'function') callback();
        return 0;
    };
    try {
        const dom = installBrowserGlobals(`
            <!doctype html>
            <body>
                <button id="quick-live-artifacts-btn" aria-label="加载 Live Artifacts 提示并保存设置" aria-pressed="false">
                    <span>Live Artifacts</span>
                </button>
                <button id="send-btn"></button>
                <textarea id="user-input"></textarea>
                <div id="chat-container"></div>
                <section id="hero-section"></section>
            </body>
        `);
        const { state, setLiveArtifactsMode } = await import('../../backend/static/js/modules/state.js?v=2');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=26');
        const button = document.getElementById('quick-live-artifacts-btn');

        state.settings = { search_engine: 'searxng', interactive_search: true };
        setLiveArtifactsMode(false);
        globalThis.fetch = async (_input, init) => {
            savedBody = JSON.parse(init.body);
            return new Response(JSON.stringify({ settings: savedBody }), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
            });
        };
        setupChatHandler({
            chatContainer: document.getElementById('chat-container'),
            userInput: document.getElementById('user-input'),
            sendBtn: document.getElementById('send-btn'),
            heroSection: document.getElementById('hero-section'),
            newChatBtn: document.createElement('button'),
        }, () => {});

        assert.equal(button.textContent.trim(), 'Live Artifacts');
        assert.equal(button.getAttribute('aria-label'), '加载 Live Artifacts 提示并保存设置');
        assert.equal(button.getAttribute('aria-pressed'), 'false');

        button.click();

        assert.equal(state.liveArtifactsMode, true);
        assert.equal(button.classList.contains('active'), true);
        assert.equal(button.getAttribute('aria-pressed'), 'true');
        assert.equal(button.getAttribute('aria-label'), 'Live Artifacts 提示已激活。点击移除。');
        assert.equal(button.title, 'Live Artifacts 提示已激活。点击移除。');
        assert.equal(dom.window.document.body.textContent.includes('Canvas'), false);
        await Promise.resolve();
        await Promise.resolve();
        assert.equal(savedBody.live_artifacts_mode, true);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
        globalThis.fetch = originalFetch;
    }
});

test('string false setting does not enable Live Artifacts mode', async () => {
    installBrowserGlobals();
    const { state, setLiveArtifactsMode, setSettings } = await import('../../backend/static/js/modules/state.js?v=2');

    setLiveArtifactsMode(true);
    setSettings({ live_artifacts_mode: 'false' });
    assert.equal(state.liveArtifactsMode, false);

    setSettings({ live_artifacts_mode: 'true' });
    assert.equal(state.liveArtifactsMode, true);

    setLiveArtifactsMode('false');
    assert.equal(state.liveArtifactsMode, false);
});

test('quick interactive search button coerces string false before toggling', async () => {
    const originalSetTimeout = globalThis.setTimeout;
    const originalFetch = globalThis.fetch;
    let savedBody = null;
    globalThis.setTimeout = (callback) => {
        if (typeof callback === 'function') callback();
        return 0;
    };

    try {
        installBrowserGlobals(`
            <!doctype html>
            <body>
                <button id="quick-interactive-btn" class="quick-toggle-btn active"></button>
                <input id="interactive-search-input" type="checkbox" checked>
                <button id="send-btn"></button>
                <textarea id="user-input"></textarea>
                <div id="chat-container"></div>
                <section id="hero-section"></section>
            </body>
        `);
        const { state, setSettings } = await import('../../backend/static/js/modules/state.js?v=2');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=26');
        const button = document.getElementById('quick-interactive-btn');
        const checkbox = document.getElementById('interactive-search-input');

        setSettings({ search_engine: 'searxng', interactive_search: 'false' });
        globalThis.fetch = async (_input, init) => {
            savedBody = JSON.parse(init.body);
            return new Response(JSON.stringify({ settings: savedBody }), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
            });
        };

        setupChatHandler({
            chatContainer: document.getElementById('chat-container'),
            userInput: document.getElementById('user-input'),
            sendBtn: document.getElementById('send-btn'),
            heroSection: document.getElementById('hero-section'),
            newChatBtn: document.createElement('button'),
        }, () => {});

        assert.equal(button.classList.contains('active'), false);

        button.click();

        await Promise.resolve();
        assert.equal(state.settings.interactive_search, true);
        assert.equal(savedBody.interactive_search, true);
        assert.equal(button.classList.contains('active'), true);
        assert.equal(checkbox.checked, true);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
        globalThis.fetch = originalFetch;
    }
});

test('inline Live Artifacts expose cited search sources outside the iframe', () => {
    installBrowserGlobals('<!doctype html><body><div id="message"></div></body>');
    const container = document.getElementById('message');

    const artifacts = renderLiveArtifactsForMessage(
        container,
        '<section style="display:block;width:100%"><p>结论来自 [2] 和 [4]。</p></section>',
        {
            messageId: 'message-sources',
            sources: [
                { id: 1, title: 'Uncited', url: 'https://one.example' },
                { id: 2, title: 'Official report', url: 'https://two.example/report' },
                { id: 4, title: 'Unsafe source', url: 'javascript:alert(1)' },
            ],
        },
    );

    assert.equal(artifacts.length, 1);
    const strip = container.querySelector('.live-artifact-source-strip');
    const chips = Array.from(container.querySelectorAll('.live-artifact-source-chip'));

    assert.ok(strip);
    assert.equal(chips.length, 2);
    assert.equal(chips[0].getAttribute('href'), 'https://two.example/report');
    assert.equal(chips[0].getAttribute('target'), '_blank');
    assert.equal(chips[1].tagName, 'SPAN');
    assert.equal(chips[1].hasAttribute('href'), false);
    assert.equal(strip.textContent.includes('Uncited'), false);
});

test('inline Live Artifact citations inside the iframe become safe clickable source links', () => {
    installBrowserGlobals('<!doctype html><body><div id="message"></div></body>');
    const container = document.getElementById('message');

    renderLiveArtifactsForMessage(
        container,
        '<section><p>官网 [2]，危险来源 [4]。</p><code>[2]</code><a href="https://already.example">[2]</a></section>',
        {
            messageId: 'message-iframe-citations',
            sources: [
                { id: 2, title: 'Official report', url: 'https://two.example/report' },
                { id: 4, title: 'Unsafe source', url: 'javascript:alert(1)' },
            ],
        },
    );

    const frame = container.querySelector('.live-artifact-inline-iframe');
    assert.ok(frame);
    assert.match(frame.getAttribute('sandbox'), /allow-popups/);
    assert.match(frame.getAttribute('sandbox'), /allow-popups-to-escape-sandbox/);
    assert.match(frame.srcdoc, /data-live-artifact-source-url="https:\/\/two\.example\/report"/);
    assert.match(frame.srcdoc, /window\.open\(url, '_blank'\)/);
    assert.match(frame.srcdoc, /opened\.opener = null/);
    assert.match(frame.srcdoc, /event\.preventDefault\(\);\s*openSourceUrl\(href\);/);
    assert.match(frame.srcdoc, /event: 'open-source'/);
    assert.match(frame.srcdoc, /sourceLink\.tagName === 'A'/);
    assert.doesNotMatch(frame.srcdoc, /data-live-artifact-source-url="javascript:/);
    assert.match(frame.srcdoc, /<code>\[2\]<\/code>/);
    assert.match(frame.srcdoc, /<a href="https:\/\/already\.example">\[2\]<\/a>/);
});

test('saved HTML answers with sources render citation links instead of inline artifact frames', async () => {
    installBrowserGlobals(`
        <!doctype html>
        <body>
            <div id="chat-container"></div>
            <section id="hero-section"></section>
        </body>
    `);

    const { elements, appendMessage } = await import('../../backend/static/js/modules/ui.js?v=20');
    Object.assign(elements, {
        chatContainer: document.getElementById('chat-container'),
        heroSection: document.getElementById('hero-section'),
    });

    appendMessage(
        'assistant',
        '<div style="display:block;width:100%"><h2>LinuxDo 是什么？</h2><p>来源给出的官网是 linux.do/。[2]</p></div>',
        null,
        [{ id: 2, title: 'Linux Do', url: 'linux.do/' }],
        null,
        1,
    );

    const body = document.querySelector('.message-answer-body');
    const citation = body.querySelector('.citation-link');
    const reference = body.querySelector('li#ref-2 a');

    assert.equal(body.querySelector('.live-artifact-inline-iframe'), null);
    assert.ok(citation);
    assert.equal(citation.textContent.trim(), '2');
    assert.equal(citation.getAttribute('href'), 'https://linux.do/');
    assert.equal(reference.getAttribute('href'), 'https://linux.do/');
});

test('saved rich HTML table answers link citation tags in place', async () => {
    installBrowserGlobals(`
        <!doctype html>
        <body>
            <div id="chat-container"></div>
            <section id="hero-section"></section>
        </body>
    `);

    const { elements, appendMessage } = await import('../../backend/static/js/modules/ui.js?v=20');
    Object.assign(elements, {
        chatContainer: document.getElementById('chat-container'),
        heroSection: document.getElementById('hero-section'),
    });

    appendMessage(
        'assistant',
        [
            '<div style="display:block;width:100%">',
            '<h2>LinuxDo 是什么？</h2>',
            '<table><tbody>',
            '<tr><td>官网</td><td>来源给出的官网是 linux.do/。[2]</td></tr>',
            '<tr><td>社区定位</td><td>面向技术交流、开源和分享的用户。[2][4]</td></tr>',
            '</tbody></table>',
            '</div>',
        ].join(''),
        null,
        [
            { id: 2, title: 'Linux Do', url: 'linux.do/' },
            { id: 4, title: 'Community article', url: 'https://four.example/path' },
        ],
        null,
        1,
    );

    const body = document.querySelector('.message-answer-body');
    const tableCitation = body.querySelector('table tr:first-child td:nth-child(2) .citation-link');
    const adjacentCitations = body.querySelectorAll('table tr:nth-child(2) td:nth-child(2) .citation-link');

    assert.equal(body.querySelector('.live-artifact-inline-iframe'), null);
    assert.ok(tableCitation);
    assert.equal(tableCitation.textContent.trim(), '2');
    assert.equal(tableCitation.getAttribute('href'), 'https://linux.do/');
    assert.equal(adjacentCitations.length, 2);
    assert.equal(adjacentCitations[0].getAttribute('href'), 'https://linux.do/');
    assert.equal(adjacentCitations[1].getAttribute('href'), 'https://four.example/path');
});

test('Live Artifact citation linker skips unsafe urls and existing links', () => {
    installBrowserGlobals();

    const html = linkArtifactCitationsInHtml(
        '<section>来源 [2, 4] <code>[2]</code><a href="https://existing.example">[2]</a></section>',
        [
            { id: 2, title: 'Safe source', url: 'https://safe.example/path' },
            { id: 4, title: 'Unsafe source', url: 'javascript:alert(1)' },
        ],
    );
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.live-artifact-citation-link');
    assert.ok(citation);
    assert.equal(citation.textContent, '2');
    assert.equal(citation.getAttribute('href'), 'https://safe.example/path');
    assert.equal(citation.getAttribute('target'), '_blank');
    assert.equal(citation.getAttribute('data-live-artifact-source-url'), 'https://safe.example/path');
    assert.equal(container.textContent.includes('[4]'), true);
    assert.equal(container.querySelector('code').textContent, '[2]');
    assert.equal(container.querySelector('a[href="https://existing.example"]').textContent, '[2]');
    assert.equal(container.innerHTML.includes('javascript:alert'), false);
});

test('Live Artifact citation linker normalizes bare-domain source urls', () => {
    installBrowserGlobals();

    const html = linkArtifactCitationsInHtml('<section>官网 [2]</section>', [
        { id: 2, title: 'Linux Do', url: 'linux.do/' },
    ]);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.live-artifact-citation-link');
    assert.ok(citation);
    assert.equal(citation.getAttribute('href'), 'https://linux.do/');
    assert.equal(citation.getAttribute('data-live-artifact-source-url'), 'https://linux.do/');
    assert.equal(citation.getAttribute('target'), '_blank');
});

test('Live Artifact frame messages require a registered preview iframe source', () => {
    installBrowserGlobals('<!doctype html><body><div id="message"></div><textarea id="user-input"></textarea></body>');
    const container = document.getElementById('message');
    const opened = [];
    const originalOpen = window.open;
    window.open = (...args) => {
        opened.push(args);
        return { opener: window };
    };

    try {
        renderLiveArtifactsForMessage(
            container,
            '<section><p>可信预览</p></section>',
            { messageId: 'trusted-frame-message' },
        );
        const frame = container.querySelector('.live-artifact-inline-iframe');
        assert.ok(frame);

        handleArtifactFrameMessage({
            source: window,
            data: {
                channel: 'justsearch-live-artifacts',
                event: 'open-source',
                url: 'trusted.example/source',
            },
        });
        assert.equal(opened.length, 0);

        handleArtifactFrameMessage({
            source: frame.contentWindow,
            data: {
                channel: 'justsearch-live-artifacts',
                event: 'open-source',
                url: 'trusted.example/source',
            },
        });
        assert.equal(opened.length, 1);
        assert.equal(opened[0][0], 'https://trusted.example/source');

        const input = document.getElementById('user-input');
        handleArtifactFrameMessage({
            source: window,
            data: {
                channel: 'justsearch-live-artifacts',
                event: 'followup',
                payload: { instruction: '伪造请求', state: { selected: 'A' } },
            },
        });
        assert.equal(input.value, '');

        handleArtifactFrameMessage({
            source: frame.contentWindow,
            data: {
                channel: 'justsearch-live-artifacts',
                event: 'followup',
                payload: { instruction: '可信请求', state: { selected: 'B' } },
            },
        });
        assert.match(input.value, /可信请求/);
        assert.match(input.value, /"selected": "B"/);
    } finally {
        window.open = originalOpen;
    }
});

test('streamChat sends live_artifacts_mode without the old Canvas request field', async () => {
    installBrowserGlobals();
    const { state } = await import('../../backend/static/js/modules/state.js?v=2');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=4');
    let capturedBody = null;
    let doneCalled = false;

    state.currentSessionId = 'session-live-artifacts';
    state.settings = {
        default_provider_id: 'provider-a',
        search_engine: 'searxng',
        max_results: 10,
        max_iterations: 3,
        interactive_search: true,
        max_concurrent_pages: 4,
    };
    globalThis.fetch = async (_input, init) => {
        capturedBody = JSON.parse(init.body);
        const encoder = new TextEncoder();
        return new Response(new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode('data: [DONE]\n\n'));
                controller.close();
            },
        }), { status: 200 });
    };

    await streamChat('生成一个可交互图表', {
        model: 'model-a',
        providerId: 'provider-a',
        liveArtifactsMode: true,
        onDone: () => { doneCalled = true; },
    });

    assert.equal(doneCalled, true);
    assert.equal(capturedBody.live_artifacts_mode, true);
    assert.equal(capturedBody.max_concurrent_pages, 4);
    assert.equal(Object.prototype.hasOwnProperty.call(capturedBody, 'canvas_mode'), false);
});

test('streamChat processes trailing SSE event when stream closes without blank delimiter', async () => {
    installBrowserGlobals();
    const { state } = await import('../../backend/static/js/modules/state.js?v=2');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=4');
    let answer = null;
    let doneCalled = false;

    state.currentSessionId = 'session-trailing-sse';
    state.settings = {
        default_provider_id: 'provider-a',
        search_engine: 'searxng',
        max_results: 10,
        max_iterations: 3,
        interactive_search: true,
    };
    globalThis.fetch = async () => {
        const encoder = new TextEncoder();
        return new Response(new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(
                    'data: {"type":"answer","content":"final answer","session_id":"session-trailing-sse","sources":[{"id":2,"title":"Linux Do","url":"linux.do/"}]}',
                ));
                controller.close();
            },
        }), { status: 200 });
    };

    await streamChat('hello', {
        model: 'model-a',
        providerId: 'provider-a',
        onAnswer: (content, sessionId, sources) => { answer = { content, sessionId, sources }; },
        onDone: () => { doneCalled = true; },
    });

    assert.deepEqual(answer, {
        content: 'final answer',
        sessionId: 'session-trailing-sse',
        sources: [{ id: 2, title: 'Linux Do', url: 'linux.do/' }],
    });
    assert.equal(doneCalled, true);
});

test('streamChat does not retry a non-idempotent chat request after response starts', async () => {
    installBrowserGlobals();
    const { state } = await import('../../backend/static/js/modules/state.js?v=2');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=4');
    const originalSetTimeout = globalThis.setTimeout;
    const originalConsoleError = console.error;
    let fetchCalls = 0;
    const chunks = [];

    state.currentSessionId = 'session-midstream-error';
    state.settings = {
        default_provider_id: 'provider-a',
        search_engine: 'searxng',
        max_results: 10,
        max_iterations: 3,
        interactive_search: true,
    };

    globalThis.setTimeout = (callback) => {
        if (typeof callback === 'function') callback();
        return 0;
    };
    console.error = () => {};
    try {
        globalThis.fetch = async () => {
            fetchCalls += 1;
            const encoder = new TextEncoder();
            let sent = false;
            return new Response(new ReadableStream({
                pull(controller) {
                    if (!sent) {
                        sent = true;
                        controller.enqueue(encoder.encode(
                            'data: {"type":"answer_chunk","content":"partial"}\n\n',
                        ));
                        return;
                    }
                    controller.error(new Error('socket closed'));
                },
            }), { status: 200 });
        };

        await assert.rejects(
            streamChat('hello', {
                model: 'model-a',
                providerId: 'provider-a',
                onAnswerChunk: chunk => chunks.push(chunk),
            }),
            /socket closed/,
        );

        assert.equal(fetchCalls, 1);
        assert.deepEqual(chunks, ['partial']);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
        console.error = originalConsoleError;
    }
});

test('streaming inline HTML keeps a stable pending preview frame', () => {
    const html = '<section style="display:grid"><strong>Partial';
    const artifact = extractInlineLiveArtifact(html, 'message-stream', true);

    assert.ok(artifact);
    assert.equal(artifact.isStreaming, true);
    assert.equal(artifact.streamHtml, html);
    assert.match(artifact.srcdoc, /data-amc-stream-preview-root/);
    assert.match(artifact.srcdoc, /stream-render/);
    assert.match(artifact.srcdoc, /sanitizeStreamDocument/);
    assert.doesNotMatch(artifact.srcdoc, /Partial/);
});

test('public inline Live Artifact probe matches AMC raw HTML fragments', () => {
    const artifact = getInlineLiveArtifact(
        '<div style="display:block;width:100%">Ready</div>',
        'message-public',
        false,
    );

    assert.ok(artifact);
    assert.equal(artifact.key, 'message-public:inline-0');
});

test('AMC Live Artifact interaction JSON is parsed into a schema form spec', () => {
    const spec = parseLiveArtifactInteractionSpec(JSON.stringify({
        version: 1,
        title: '论文写作参数',
        instruction: '根据这些论文参数继续写作。',
        submitLabel: '开始写作',
        schema: {
            type: 'object',
            required: ['topic'],
            properties: {
                topic: { type: 'string', title: '论文主题' },
                style: { type: 'string', enum: ['APA', 'MLA'], default: 'APA' },
                outline: { type: 'boolean', default: true },
                words: { type: 'integer', default: 2000, minimum: 100, maximum: 5000 },
            },
        },
    }));

    assert.ok(spec);
    assert.equal(spec.title, '论文写作参数');
    assert.equal(spec.schema.properties.topic.type, 'string');
    assert.deepEqual(spec.schema.required, ['topic']);
});

test('AMC Live Artifact interaction fence is detected while streaming', () => {
    const interaction = extractLiveArtifactInteraction(
        '```amc-live-artifact-interaction\n{"instruction":"Collect","schema":{',
        true,
    );

    assert.deepEqual(interaction, { pending: true });
});

test('legacy HTML fragments still merge same-message CSS and JavaScript support blocks', () => {
    const markdown = [
        '```css',
        '.stage { color: blue; }',
        '```',
        '```html title="Fragment Demo"',
        '<section class="stage"><button id="go">Go</button></section>',
        '```',
        '```javascript',
        'document.getElementById("go").textContent = "Done";',
        '```',
    ].join('\n');

    const artifacts = extractLiveArtifacts(markdown, 'message-b');

    assert.equal(artifacts.length, 1);
    assert.equal(artifacts[0].title, 'Fragment Demo');
    assert.deepEqual(artifacts[0].supportBlockIndices, [0, 2]);
    assert.match(artifacts[0].code, /<style>[\s\S]*\.stage/);
    assert.match(artifacts[0].code, /<script>[\s\S]*getElementById/);
});

test('explicit artifact metadata can declare HTML renderability', () => {
    const markdown = [
        '```artifact type="text/html" title="Metric Card" filename="metric-card.html"',
        '<section><h1>Metric</h1></section>',
        '```',
    ].join('\n');

    const artifacts = extractLiveArtifacts(markdown, 'message-c');

    assert.equal(artifacts.length, 1);
    assert.equal(artifacts[0].title, 'Metric Card');
    assert.equal(artifacts[0].fileName, 'metric-card.html');
    assert.equal(artifacts[0].renderable, true);
    assert.match(artifacts[0].srcdoc, /<section><h1>Metric<\/h1><\/section>/);
});

test('streaming chat re-renders citations when sources arrive after answer chunks', async () => {
    const originalFetch = globalThis.fetch;
    installBrowserGlobals(`
        <!doctype html>
        <body>
            <select id="model-select">
                <option value="model-a" data-provider-id="provider-a">Model A</option>
            </select>
            <button id="send-btn"><span class="material-symbols-rounded">send</span></button>
            <textarea id="user-input"></textarea>
            <div id="chat-container"></div>
            <section id="hero-section"></section>
            <button id="new-chat-btn"></button>
        </body>
    `);

    try {
        const { state, setCurrentSessionId, setLiveArtifactsMode } = await import('../../backend/static/js/modules/state.js?v=2');
        const { elements } = await import('../../backend/static/js/modules/ui.js?v=20');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=26');
        const encoder = new TextEncoder();
        const events = [
            { type: 'meta', session_id: 'late-sources-session' },
            { type: 'answer_chunk', content: '官网 [2]' },
            { type: 'sources', content: [{ id: 2, title: 'Linux Do', url: 'linux.do/' }] },
            { type: 'answer', content: '官网 [2]', session_id: 'late-sources-session' },
        ]
            .map(event => `data: ${JSON.stringify(event)}\n\n`)
            .join('') + 'data: [DONE]\n\n';

        setCurrentSessionId(null);
        setLiveArtifactsMode(false);
        state.settings = {
            default_provider_id: 'provider-a',
            search_engine: 'searxng',
            max_results: 10,
            max_iterations: 3,
            interactive_search: true,
            max_concurrent_pages: 4,
        };

        globalThis.fetch = async (input) => {
            const url = String(input);
            if (url === '/api/chat') {
                return new Response(new ReadableStream({
                    start(controller) {
                        controller.enqueue(encoder.encode(events));
                        controller.close();
                    },
                }), { status: 200 });
            }
            if (url === '/api/history' || url === '/api/history/groups') {
                return new Response(JSON.stringify([]), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        };

        const testElements = {
            chatContainer: document.getElementById('chat-container'),
            userInput: document.getElementById('user-input'),
            sendBtn: document.getElementById('send-btn'),
            heroSection: document.getElementById('hero-section'),
            newChatBtn: document.getElementById('new-chat-btn'),
        };
        Object.assign(elements, testElements);
        setupChatHandler(testElements, () => {});

        const input = document.getElementById('user-input');
        input.value = 'linuxdo是什么';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('send-btn').click();

        for (let i = 0; i < 20; i += 1) {
            const citation = document.querySelector('.message-answer-body .citation-link');
            if (citation) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }

        const citation = document.querySelector('.message-answer-body .citation-link');
        const reference = document.querySelector('.message-answer-body li#ref-2 a');
        assert.ok(citation);
        assert.equal(citation.textContent.trim(), '2');
        assert.equal(citation.getAttribute('href'), 'https://linux.do/');
        assert.equal(reference.getAttribute('href'), 'https://linux.do/');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('streaming raw HTML answer exits inline artifact mode when sources arrive', async () => {
    const originalFetch = globalThis.fetch;
    installBrowserGlobals(`
        <!doctype html>
        <body>
            <select id="model-select">
                <option value="model-a" data-provider-id="provider-a">Model A</option>
            </select>
            <button id="send-btn"><span class="material-symbols-rounded">send</span></button>
            <textarea id="user-input"></textarea>
            <div id="chat-container"></div>
            <section id="hero-section"></section>
            <button id="new-chat-btn"></button>
        </body>
    `);

    try {
        const { state, setCurrentSessionId, setLiveArtifactsMode } = await import('../../backend/static/js/modules/state.js?v=2');
        const { elements } = await import('../../backend/static/js/modules/ui.js?v=20');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=26');
        const encoder = new TextEncoder();
        const htmlAnswer = '<div style="display:block;width:100%"><h2>LinuxDo 是什么？</h2><p>来源给出的官网是 linux.do/。[2]</p></div>';
        const events = [
            { type: 'meta', session_id: 'late-html-sources-session' },
            { type: 'answer_chunk', content: htmlAnswer },
            { type: 'sources', content: [{ id: 2, title: 'Linux Do', url: 'linux.do/' }] },
            { type: 'answer', content: htmlAnswer, session_id: 'late-html-sources-session' },
        ]
            .map(event => `data: ${JSON.stringify(event)}\n\n`)
            .join('') + 'data: [DONE]\n\n';

        setCurrentSessionId(null);
        setLiveArtifactsMode(false);
        state.settings = {
            default_provider_id: 'provider-a',
            search_engine: 'searxng',
            max_results: 10,
            max_iterations: 3,
            interactive_search: true,
            max_concurrent_pages: 4,
        };

        globalThis.fetch = async (input) => {
            const url = String(input);
            if (url === '/api/chat') {
                return new Response(new ReadableStream({
                    start(controller) {
                        controller.enqueue(encoder.encode(events));
                        controller.close();
                    },
                }), { status: 200 });
            }
            if (url === '/api/history' || url === '/api/history/groups') {
                return new Response(JSON.stringify([]), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        };

        const testElements = {
            chatContainer: document.getElementById('chat-container'),
            userInput: document.getElementById('user-input'),
            sendBtn: document.getElementById('send-btn'),
            heroSection: document.getElementById('hero-section'),
            newChatBtn: document.getElementById('new-chat-btn'),
        };
        Object.assign(elements, testElements);
        setupChatHandler(testElements, () => {});

        const input = document.getElementById('user-input');
        input.value = 'linuxdo是什么';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('send-btn').click();

        for (let i = 0; i < 20; i += 1) {
            const citation = document.querySelector('.message-answer-body .citation-link');
            if (citation) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }

        const body = document.querySelector('.message-answer-body');
        const citation = body.querySelector('.citation-link');
        const reference = body.querySelector('li#ref-2 a');
        assert.equal(body.querySelector('.live-artifact-inline-iframe'), null);
        assert.ok(citation);
        assert.equal(citation.textContent.trim(), '2');
        assert.equal(citation.getAttribute('href'), 'https://linux.do/');
        assert.equal(reference.getAttribute('href'), 'https://linux.do/');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('streaming raw HTML answer links citations from final answer sources', async () => {
    const originalFetch = globalThis.fetch;
    installBrowserGlobals(`
        <!doctype html>
        <body>
            <select id="model-select">
                <option value="model-a" data-provider-id="provider-a">Model A</option>
            </select>
            <button id="send-btn"><span class="material-symbols-rounded">send</span></button>
            <textarea id="user-input"></textarea>
            <div id="chat-container"></div>
            <section id="hero-section"></section>
            <button id="new-chat-btn"></button>
        </body>
    `);

    try {
        const { state, setCurrentSessionId, setLiveArtifactsMode } = await import('../../backend/static/js/modules/state.js?v=2');
        const { elements } = await import('../../backend/static/js/modules/ui.js?v=20');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=26');
        const encoder = new TextEncoder();
        const htmlAnswer = '<div style="display:block;width:100%"><h2>LinuxDo 是什么？</h2><p>来源给出的官网是 linux.do/。[2]</p></div>';
        const events = [
            { type: 'meta', session_id: 'final-html-sources-session' },
            { type: 'answer_chunk', content: htmlAnswer },
            {
                type: 'answer',
                content: htmlAnswer,
                session_id: 'final-html-sources-session',
                sources: [{ id: 2, title: 'Linux Do', url: 'linux.do/' }],
            },
        ]
            .map(event => `data: ${JSON.stringify(event)}\n\n`)
            .join('') + 'data: [DONE]\n\n';

        setCurrentSessionId(null);
        setLiveArtifactsMode(false);
        state.settings = {
            default_provider_id: 'provider-a',
            search_engine: 'searxng',
            max_results: 10,
            max_iterations: 3,
            interactive_search: true,
            max_concurrent_pages: 4,
        };

        globalThis.fetch = async (input) => {
            const url = String(input);
            if (url === '/api/chat') {
                return new Response(new ReadableStream({
                    start(controller) {
                        controller.enqueue(encoder.encode(events));
                        controller.close();
                    },
                }), { status: 200 });
            }
            if (url === '/api/history' || url === '/api/history/groups') {
                return new Response(JSON.stringify([]), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        };

        const testElements = {
            chatContainer: document.getElementById('chat-container'),
            userInput: document.getElementById('user-input'),
            sendBtn: document.getElementById('send-btn'),
            heroSection: document.getElementById('hero-section'),
            newChatBtn: document.getElementById('new-chat-btn'),
        };
        Object.assign(elements, testElements);
        setupChatHandler(testElements, () => {});

        const input = document.getElementById('user-input');
        input.value = 'linuxdo是什么';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('send-btn').click();

        for (let i = 0; i < 20; i += 1) {
            const citation = document.querySelector('.message-answer-body .citation-link');
            if (citation) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }

        const body = document.querySelector('.message-answer-body');
        const citation = body.querySelector('.citation-link');
        const reference = body.querySelector('li#ref-2 a');
        assert.equal(body.querySelector('.live-artifact-inline-iframe'), null);
        assert.ok(citation);
        assert.equal(citation.textContent.trim(), '2');
        assert.equal(citation.getAttribute('href'), 'https://linux.do/');
        assert.equal(reference.getAttribute('href'), 'https://linux.do/');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('citation rendering resolves sparse source ids instead of array positions', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=7');

    const html = renderWithCitations('Sparse citation [4]', [
        { id: 2, title: 'Second', url: 'https://two.example' },
        { id: 4, title: 'Fourth', url: 'https://four.example' },
    ]);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.citation-link');
    assert.ok(citation);
    assert.equal(citation.textContent.trim(), '4');
    assert.equal(citation.getAttribute('href'), 'https://four.example');
    assert.equal(container.querySelector('li#ref-4 a').textContent, 'Fourth');
});

test('citation rendering normalizes bare-domain source urls', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=7');

    const html = renderWithCitations('官网 [2]', [
        { id: 2, title: 'Linux Do', url: 'linux.do/' },
    ]);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.citation-link');
    const reference = container.querySelector('li#ref-2 a');

    assert.ok(citation);
    assert.equal(citation.textContent.trim(), '2');
    assert.equal(citation.getAttribute('href'), 'https://linux.do/');
    assert.equal(citation.getAttribute('target'), '_blank');
    assert.equal(reference.getAttribute('href'), 'https://linux.do/');
    assert.equal(reference.getAttribute('target'), '_blank');
});

test('citation rendering links citations from embedded reference markdown when sources are absent', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=7');

    const html = renderWithCitations([
        '官网 来源给出的官网是 linux.do/。[2]',
        '',
        '---',
        '### 参考资料',
        '[2] [Linux Do](linux.do/)  ',
    ].join('\n'), []);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.citation-link');
    const reference = container.querySelector('li#ref-2 a');

    assert.ok(citation);
    assert.equal(citation.textContent.trim(), '2');
    assert.equal(citation.getAttribute('href'), 'https://linux.do/');
    assert.equal(citation.getAttribute('target'), '_blank');
    assert.equal(reference.getAttribute('href'), 'https://linux.do/');
    assert.equal(reference.textContent, 'Linux Do');
});

test('citation rendering neutralizes non-http source urls', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=7');

    const html = renderWithCitations('Unsafe citation [1]', [
        { id: 1, title: 'Unsafe', url: 'javascript:alert(1)' },
    ]);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citation = container.querySelector('.citation-link');
    const reference = container.querySelector('li#ref-1 a');

    assert.equal(citation.getAttribute('href'), '#');
    assert.equal(citation.hasAttribute('target'), false);
    assert.equal(reference.getAttribute('href'), '#');
    assert.equal(reference.hasAttribute('target'), false);
    assert.equal(container.querySelector('.citation-favicon'), null);
});

test('citation rendering tolerates malformed source payloads', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=7');

    assert.doesNotThrow(() => renderWithCitations('No sources [1]', { id: 1, url: 'https://bad.example' }));
    assert.doesNotThrow(() => renderWithCitations(null, [{ id: 1, url: 'https://safe.example' }]));

    const html = renderWithCitations('Mixed sources [2] [4] [5]', [
        null,
        'valid.example/path',
        42,
        { id: 4, title: 123, url: 'https://four.example' },
        { id: 5, title: '', url: '' },
    ]);
    const container = document.createElement('div');
    container.innerHTML = html;

    const citations = Array.from(container.querySelectorAll('.citation-link'));
    assert.equal(citations.length, 3);
    assert.equal(citations[0].textContent.trim(), '2');
    assert.equal(citations[0].getAttribute('href'), 'https://valid.example/path');
    assert.equal(citations[1].textContent.trim(), '4');
    assert.equal(citations[1].getAttribute('href'), 'https://four.example');
    assert.equal(citations[2].textContent.trim(), '5');
    assert.equal(citations[2].getAttribute('href'), '#');
    assert.equal(citations[2].hasAttribute('target'), false);
});
