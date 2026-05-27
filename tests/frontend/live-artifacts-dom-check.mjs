import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { renderLiveArtifactsForMessage } from '../../backend/static/js/modules/live-artifacts.js';

const require = createRequire(import.meta.url);
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><body><div id="message"></div><textarea id="user-input"></textarea></body>', {
  url: 'http://localhost/',
});

globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;

const container = document.getElementById('message');
const html = '<section style="display:block;width:100%;box-sizing:border-box"><h2>Inline Artifact</h2><p>Rendered inside iframe</p></section>';
const artifacts = renderLiveArtifactsForMessage(container, html, {
  messageId: 'dom-check-message',
  isStreaming: false,
});

assert.equal(artifacts.length, 1);
assert.equal(container.querySelectorAll('[data-live-artifact-frame="true"]').length, 1);
assert.equal(container.querySelectorAll('iframe[title="HTML Preview"]').length, 1);
assert.equal(container.querySelectorAll('section').length, 0);
assert.match(container.querySelector('iframe').getAttribute('srcdoc'), /Inline Artifact/);

const interaction = {
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
      outline: { type: 'boolean', title: '先生成大纲', default: true },
      words: { type: 'integer', title: '目标字数', default: 2000 },
    },
  },
};

renderLiveArtifactsForMessage(container, `\`\`\`amc-live-artifact-interaction\n${JSON.stringify(interaction)}\n\`\`\``, {
  messageId: 'interaction-message',
  isStreaming: false,
});

const form = container.querySelector('[data-live-artifact-interaction="true"]');
assert.ok(form);
assert.equal(container.querySelector('input[name="topic"]').value, '');
assert.equal(container.querySelector('select[name="style"]').value, 'APA');
assert.equal(container.querySelector('input[name="outline"]').checked, true);
assert.equal(container.querySelector('input[name="words"]').value, '2000');
container.querySelector('input[name="topic"]').value = '人工智能辅助学术写作';
form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
assert.match(document.getElementById('user-input').value, /请根据 Live Artifact 中的交互选择继续处理/);
assert.match(document.getElementById('user-input').value, /人工智能辅助学术写作/);
assert.match(document.getElementById('user-input').value, /amc-live-artifact-interaction:v1/);

document.getElementById('user-input').value = '';
dom.window.dispatchEvent(new dom.window.MessageEvent('message', {
  data: {
    channel: 'justsearch-live-artifacts',
    event: 'followup',
    payload: {
      instruction: '继续优化',
      state: {
        selected: 'B',
        amount: 3,
      },
    },
  },
}));
assert.match(document.getElementById('user-input').value, /继续优化/);
assert.match(document.getElementById('user-input').value, /"selected": "B"/);
assert.match(document.getElementById('user-input').value, /"amount": 3/);

console.log('inline and interaction live artifact DOM checks passed');
