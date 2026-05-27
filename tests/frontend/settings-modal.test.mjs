import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

function installBrowserGlobals() {
    const { JSDOM } = require('jsdom');
    const dom = new JSDOM(`
        <!doctype html>
        <body>
            <div id="provider-list-container"></div>
            <div id="workflow-step-models-container"></div>
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
});
