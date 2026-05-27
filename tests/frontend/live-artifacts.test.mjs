import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import { getInlineLiveArtifact, __liveArtifactsTestHooks } from '../../backend/static/js/modules/live-artifacts.js';

const {
    buildSrcdoc,
    extractInlineLiveArtifact,
    extractLiveArtifactInteraction,
    extractLiveArtifacts,
    injectPreviewSecurityPolicy,
    normalizePreviewDiagnostic,
    parseLiveArtifactInteractionSpec,
} = __liveArtifactsTestHooks;

const require = createRequire(import.meta.url);

function installBrowserGlobals(html = '<!doctype html><body></body>') {
    const { JSDOM } = require('/Users/jones/Documents/Code/AMC-WebUI/node_modules/jsdom/lib/api.js');
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

test('AMC-style raw inline HTML fragments render as inline Live Artifact frames', () => {
    const html = '<section style="display:grid"><strong>Inline Artifact</strong></section>';
    const artifact = extractInlineLiveArtifact(html, 'message-inline', false);

    assert.ok(artifact);
    assert.equal(artifact.inline, true);
    assert.equal(artifact.language, 'html');
    assert.match(artifact.srcdoc, /Inline Artifact/);
    assert.match(artifact.srcdoc, /justsearch-live-artifacts/);
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
        const { state, setLiveArtifactsMode } = await import('../../backend/static/js/modules/state.js');
        const { setupChatHandler } = await import('../../backend/static/js/modules/chat.js?v=12');
        const button = document.getElementById('quick-live-artifacts-btn');

        state.settings = { search_engine: 'searxng', interactive_search: true };
        setLiveArtifactsMode(false);
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
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
});

test('streamChat sends live_artifacts_mode without the old Canvas request field', async () => {
    installBrowserGlobals();
    const { state } = await import('../../backend/static/js/modules/state.js');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=1');
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
    const { state } = await import('../../backend/static/js/modules/state.js');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=1');
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
                    'data: {"type":"answer","content":"final answer","session_id":"session-trailing-sse"}',
                ));
                controller.close();
            },
        }), { status: 200 });
    };

    await streamChat('hello', {
        model: 'model-a',
        providerId: 'provider-a',
        onAnswer: (content, sessionId) => { answer = { content, sessionId }; },
        onDone: () => { doneCalled = true; },
    });

    assert.deepEqual(answer, {
        content: 'final answer',
        sessionId: 'session-trailing-sse',
    });
    assert.equal(doneCalled, true);
});

test('streamChat does not retry a non-idempotent chat request after response starts', async () => {
    installBrowserGlobals();
    const { state } = await import('../../backend/static/js/modules/state.js');
    const { streamChat } = await import('../../backend/static/js/modules/api.js?v=1');
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

test('citation rendering resolves sparse source ids instead of array positions', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=3');

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

test('citation rendering neutralizes non-http source urls', async () => {
    installBrowserGlobals();
    const { renderWithCitations } = await import('../../backend/static/js/modules/source-renderer.js?v=3');

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
