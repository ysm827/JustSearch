import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildAuthenticatedUrl,
    buildAuthHeaders,
    buildBrowserWebSocketUrl,
    normalizeSettings,
    resolveClientAuth,
} from '../../backend/static/js/modules/auth.js';

test('resolveClientAuth prefers token from query string and strips it from url', () => {
    const result = resolveClientAuth({
        bootstrapToken: '',
        storedToken: 'stored-token',
        url: 'http://example.com/c/demo?token=query-token&foo=bar#hash',
    });

    assert.equal(result.token, 'query-token');
    assert.equal(result.shouldPersist, true);
    assert.equal(result.cleanedPath, '/c/demo?foo=bar#hash');
});

test('buildAuthHeaders adds bearer token when available', () => {
    assert.deepEqual(buildAuthHeaders('secret-token'), {
        Authorization: 'Bearer secret-token',
    });
    assert.deepEqual(buildAuthHeaders(''), {});
});

test('buildBrowserWebSocketUrl appends token and protocol correctly', () => {
    const result = buildBrowserWebSocketUrl(
        { protocol: 'https:', host: 'example.com' },
        'session-1',
        'secret-token',
    );

    assert.equal(
        result,
        'wss://example.com/ws/browser/session-1?token=secret-token',
    );
});

test('buildAuthenticatedUrl appends token when available', () => {
    assert.equal(
        buildAuthenticatedUrl('/api/history/export/all?format=markdown', 'secret-token'),
        '/api/history/export/all?format=markdown&token=secret-token',
    );
    assert.equal(buildAuthenticatedUrl('/api/history/export/all', ''), '/api/history/export/all');
});

test('normalizeSettings turns nullish values into an empty object', () => {
    assert.deepEqual(normalizeSettings(null), {});
    assert.deepEqual(normalizeSettings(undefined), {});
    assert.deepEqual(normalizeSettings({ model_id: 'demo-model' }), {
        model_id: 'demo-model',
    });
});
