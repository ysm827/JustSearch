import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');

test('browser modal module was removed after Chrome bridge redesign', () => {
    const modalPath = join(root, 'backend/static/js/modules/browser-modal.js');
    assert.equal(existsSync(modalPath), false);
});

test('chat module does not open a remote browser modal', async () => {
    const { readFileSync } = await import('node:fs');
    const chatSource = readFileSync(
        join(root, 'backend/static/js/modules/chat.js'),
        'utf8',
    );
    assert.equal(chatSource.includes('openBrowserModal'), false);
    assert.match(chatSource, /ensureBridgeConnected/);
});
