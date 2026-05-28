import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

function installBrowserGlobals() {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM(`
        <!doctype html>
        <body>
            <select id="theme-select"><option value="light">Light</option></select>
            <select id="engine-select"><option value="searxng">SearXNG</option></select>
            <input id="max-results-input" type="number">
            <input id="max-iterations-input" type="number">
            <input id="interactive-search-input" type="checkbox" checked>
            <input id="max-concurrent-pages-input" type="number">
            <div id="provider-list-container"></div>
            <div id="workflow-step-models-container"></div>
            <div id="engine-check-results"></div>
            <span id="provider-count-label"></span>
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
    window.markdownit = () => ({
        render: value => String(value || ''),
        utils: { escapeHtml: value => String(value || '') },
    });
    window.DOMPurify = { sanitize: value => String(value || '') };
    window.hljs = { getLanguage: () => false };
    window.matchMedia = () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} });
}

test('provider id rename preserves workflow step model after transient empty input', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=provider-rename');
    const providers = [
        {
            id: 'openai',
            name: 'OpenAI',
            api_key: 'ope****1234',
            base_url: 'https://api.openai.com/v1',
            model_id: 'gpt-4.1:GPT 4.1, gpt-4.1-mini',
        },
    ];

    __settingsModalTestHooks.renderProviderList(providers, 'openai');
    __settingsModalTestHooks.renderWorkflowStepModels(
        {
            analysis: { provider_id: '', model_id: '' },
            relevance: { provider_id: '', model_id: '' },
            interaction: { provider_id: '', model_id: '' },
            answer: { provider_id: 'openai', model_id: 'gpt-4.1' },
        },
        providers,
        'openai',
    );

    const idInput = document.querySelector('.provider-id-input');
    idInput.value = '';
    idInput.dispatchEvent(new Event('input', { bubbles: true }));

    assert.deepEqual(
        __settingsModalTestHooks.collectWorkflowStepModels().answer,
        { provider_id: 'openai', model_id: 'gpt-4.1' },
    );

    idInput.value = 'openai-renamed';
    idInput.dispatchEvent(new Event('input', { bubbles: true }));

    assert.deepEqual(
        __settingsModalTestHooks.collectWorkflowStepModels().answer,
        { provider_id: 'openai-renamed', model_id: 'gpt-4.1' },
    );
});

test('compact model display names do not become API model ids', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=model-alias');
    const providers = [
        {
            id: 'gateway',
            name: 'Gateway',
            api_key: 'sk-****24a8',
            base_url: 'https://gw2.oops.asia/v1',
            model_id: 'gpt-5.5',
        },
    ];

    __settingsModalTestHooks.renderProviderList(providers, 'gateway');

    const nameInput = document.querySelector('.model-name-input');
    nameInput.value = '5.5';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));

    assert.equal(
        __settingsModalTestHooks.collectProvidersForm()[0].model_id,
        'gpt-5.5::5.5',
    );

    __settingsModalTestHooks.renderProviderList(
        [
            {
                ...providers[0],
                model_id: 'gpt-5.5:5.5, qwen2.5:7b::Qwen 7B',
            },
        ],
        'gateway',
    );

    const rows = Array.from(document.querySelectorAll('.model-row'));
    assert.equal(rows[0].querySelector('.model-id-input').value, 'gpt-5.5');
    assert.equal(rows[0].querySelector('.model-name-input').value, '5.5');
    assert.equal(rows[1].querySelector('.model-id-input').value, 'qwen2.5:7b');
    assert.equal(rows[1].querySelector('.model-name-input').value, 'Qwen 7B');

    __settingsModalTestHooks.renderProviderList(
        [
            {
                ...providers[0],
                model_id: 'foo::, org/foo::',
            },
        ],
        'gateway',
    );

    const fallbackRows = Array.from(document.querySelectorAll('.model-row'));
    assert.equal(fallbackRows[0].querySelector('.model-id-input').value, 'foo::');
    assert.equal(fallbackRows[0].querySelector('.model-name-input').value, '');
    assert.equal(fallbackRows[1].querySelector('.model-id-input').value, 'org/foo::');
    assert.equal(fallbackRows[1].querySelector('.model-name-input').value, '');
});

test('shared provider model parser preserves compact ids and display aliases', async () => {
    const {
        getModelDisplayName,
        getSupportedModelItems,
        isUnsupportedGemini25Model,
        splitModelItem,
    } = await import('../../backend/static/js/modules/provider-models.js?test=shared-parser');

    assert.deepEqual(splitModelItem('gpt-5.5::5.5'), {
        modelId: 'gpt-5.5',
        displayName: '5.5',
    });
    assert.deepEqual(splitModelItem('qwen2.5:7b::Qwen 7B'), {
        modelId: 'qwen2.5:7b',
        displayName: 'Qwen 7B',
    });
    assert.deepEqual(splitModelItem('qwen2.5:7b'), {
        modelId: 'qwen2.5:7b',
        displayName: 'qwen2.5:7b',
    });
    assert.equal(getModelDisplayName('org/model::Friendly'), 'Friendly');
    assert.equal(isUnsupportedGemini25Model('Gemini 2.5 Flash Lite'), true);
    assert.deepEqual(
        getSupportedModelItems('gemini-2.5-pro, gpt-4.1::GPT 4.1, qwen2.5:7b'),
        ['gpt-4.1::GPT 4.1', 'qwen2.5:7b'],
    );
});

test('provider rendering tolerates non-string settings values and escapes markup', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=provider-normalize');

    assert.doesNotThrow(() => {
        __settingsModalTestHooks.renderProviderList(
            [
                {
                    id: 7,
                    name: '<img src=x onerror=alert(1)>Gateway',
                    api_key: 12345,
                    base_url: '<script>alert(1)</script>',
                    model_id: 'gpt-5.5::<b>Alias</b>',
                },
            ],
            7,
        );
    });

    const card = document.querySelector('.provider-card');
    assert.ok(card);
    assert.equal(card.querySelector('.provider-id-input').value, '7');
    assert.equal(card.querySelector('.provider-card-name').textContent, '<img src=x onerror=alert(1)>Gateway');
    assert.equal(card.querySelector('.provider-base-url-input').value, '<script>alert(1)</script>');
    assert.equal(card.querySelector('.model-id-input').value, 'gpt-5.5');
    assert.equal(card.querySelector('.model-name-input').value, '<b>Alias</b>');
    assert.equal(card.querySelector('img'), null);
    assert.equal(card.querySelector('script'), null);
    assert.equal(card.querySelector('b'), null);
});

test('engine check results render untrusted response fields as text', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=engine-results');

    __settingsModalTestHooks.renderEngineCheckResults({
        query: '<img src=x onerror=alert(1)>',
        results: [
            {
                engine: '<svg onload=alert(1)>',
                available: false,
                error: '<script>alert(1)</script>',
            },
            {
                engine: 'searxng',
                available: true,
                result_count: 'not-a-number',
            },
        ],
    });

    const resultsEl = document.getElementById('engine-check-results');

    assert.equal(resultsEl.querySelector('.engine-check-query').textContent, '测试词：<img src=x onerror=alert(1)>');
    assert.equal(resultsEl.querySelector('.engine-check-name').textContent, '<svg onload=alert(1)>');
    assert.equal(resultsEl.querySelector('.engine-check-detail').textContent, '不可用 · <script>alert(1)</script>');
    assert.equal(resultsEl.querySelectorAll('script, img, svg').length, 0);
    assert.equal(
        Array.from(resultsEl.querySelectorAll('.engine-check-detail'))[1].textContent,
        '可用 · 0 个结果',
    );
});

test('settings form clamps numeric fields before saving', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=numeric-clamp');

    __settingsModalTestHooks.renderProviderList([
        {
            id: 'deepseek',
            name: 'DeepSeek',
            api_key: 'secret',
            base_url: 'https://api.deepseek.com/v1',
            model_id: 'deepseek-chat',
        },
    ], 'deepseek');

    document.getElementById('max-results-input').value = '500';
    document.getElementById('max-iterations-input').value = '-2';
    document.getElementById('max-concurrent-pages-input').value = '20.8';

    const settings = __settingsModalTestHooks.collectSettingsForm();

    assert.equal(settings.max_results, 50);
    assert.equal(settings.max_iterations, 1);
    assert.equal(settings.max_concurrent_pages, 20);
    assert.equal(__settingsModalTestHooks.normalizeNumberSetting('not-a-number', 5, 1, 10), 5);
});

test('settings form coerces string boolean toggles when filling form', async () => {
    installBrowserGlobals();
    const { __settingsModalTestHooks } = await import('../../backend/static/js/modules/settings-modal.js?test=boolean-coerce');
    const checkbox = document.getElementById('interactive-search-input');

    __settingsModalTestHooks.fillSettingsForm({
        theme: 'light',
        search_engine: 'searxng',
        interactive_search: 'false',
        providers: [],
        workflow_step_models: {},
    });
    assert.equal(checkbox.checked, false);

    __settingsModalTestHooks.fillSettingsForm({
        theme: 'light',
        search_engine: 'searxng',
        interactive_search: 'true',
        providers: [],
        workflow_step_models: {},
    });
    assert.equal(checkbox.checked, true);
});
