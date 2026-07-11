import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

// Provide localStorage before importing the module under test.
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'http://localhost/',
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.HTMLOptionElement = dom.window.HTMLOptionElement;
globalThis.HTMLSelectElement = dom.window.HTMLSelectElement;

const {
    SELECTED_MODEL_STORAGE_KEY,
    findOptionForModelPreference,
    loadSelectedModelPreference,
    saveSelectedModelPreference,
    persistSelectedModelFromSelect,
} = await import('../../backend/static/js/modules/model-selector.js');

test('save/load selected model preference round-trips via localStorage', () => {
    localStorage.clear();
    saveSelectedModelPreference('nim', 'nvidia/nemotron-3-ultra-550b-a55b');
    const loaded = loadSelectedModelPreference();
    assert.deepEqual(loaded, {
        providerId: 'nim',
        modelId: 'nvidia/nemotron-3-ultra-550b-a55b',
    });
    assert.ok(localStorage.getItem(SELECTED_MODEL_STORAGE_KEY));
});

test('findOptionForModelPreference prefers provider+model exact match', () => {
    const select = document.createElement('select');
    const a = document.createElement('option');
    a.value = 'model-a';
    a.dataset.providerId = 'p1';
    const b = document.createElement('option');
    b.value = 'model-a';
    b.dataset.providerId = 'p2';
    const c = document.createElement('option');
    c.value = 'model-b';
    c.dataset.providerId = 'p2';
    select.append(a, b, c);

    const match = findOptionForModelPreference(select.options, {
        providerId: 'p2',
        modelId: 'model-a',
    });
    assert.equal(match, b);
});

test('findOptionForModelPreference falls back to model id only', () => {
    const select = document.createElement('select');
    const a = document.createElement('option');
    a.value = 'glm-5.2';
    a.dataset.providerId = 'glm';
    select.append(a);

    const match = findOptionForModelPreference(select.options, {
        providerId: 'old-provider',
        modelId: 'glm-5.2',
    });
    assert.equal(match, a);
});

test('persistSelectedModelFromSelect writes current option', () => {
    localStorage.clear();
    const select = document.createElement('select');
    const opt = document.createElement('option');
    opt.value = 'step-3.7-flash';
    opt.dataset.providerId = 'nim';
    opt.selected = true;
    select.append(opt);
    persistSelectedModelFromSelect(select);
    assert.deepEqual(loadSelectedModelPreference(), {
        providerId: 'nim',
        modelId: 'step-3.7-flash',
    });
});
