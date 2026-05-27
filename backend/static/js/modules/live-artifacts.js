import { showToast } from './toast.js';

const ARTIFACT_LANGUAGES = new Set(['html', 'svg']);
const SUPPORTING_LANGUAGES = new Set(['css', 'javascript', 'js']);
const LIVE_ARTIFACT_HTML_LANGUAGE = 'amc-live-artifact-html';
const LIVE_ARTIFACT_INTERACTION_LANGUAGE = 'amc-live-artifact-interaction';
const STREAM_PREVIEW_ROOT = '<div data-amc-stream-preview-root="true"></div>';
const STREAM_RENDER_EVENT = 'stream-render';
const INTERACTION_SOURCE = 'amc-live-artifact-interaction:v1';
const PREVIEW_CONTENT_SECURITY_POLICY = [
    "default-src 'none'",
    "img-src https: data: blob:",
    "style-src 'unsafe-inline' https:",
    "script-src 'unsafe-inline' https: blob:",
    "font-src https: data:",
    "media-src https: data: blob:",
    "connect-src https: data: blob:",
    "worker-src blob:",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
].join('; ');
const PREVIEW_CONTENT_SECURITY_POLICY_META = `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CONTENT_SECURITY_POLICY}">`;
const registry = new Map();

let artifactCounter = 0;
let activeArtifactId = '';
let activeArtifactKey = '';
let activeView = 'preview';
let panelState = null;
let lastDiagnosticToastAt = 0;

export function renderLiveArtifactsForMessage(container, markdownText, options = {}) {
    if (!container) return [];
    ensurePanel();

    const messageId = resolveMessageId(container, options.messageId);
    const interactionSpec = extractLiveArtifactInteraction(markdownText, Boolean(options.isStreaming));
    if (interactionSpec) {
        syncRegistryForMessage(messageId, []);
        clearArtifactControls(container);
        renderLiveArtifactInteraction(container, interactionSpec);
        return [];
    }

    const inlineArtifact = extractInlineLiveArtifact(markdownText, messageId, Boolean(options.isStreaming));
    if (inlineArtifact) {
        syncRegistryForMessage(messageId, [inlineArtifact]);
        clearArtifactControls(container);
        renderInlineArtifactFrame(container, inlineArtifact);
        return [inlineArtifact];
    }

    const artifacts = extractLiveArtifacts(markdownText, messageId);
    syncRegistryForMessage(messageId, artifacts);
    clearArtifactControls(container);

    if (artifacts.length === 0) {
        if (activeArtifactKey.startsWith(`${messageId}:`)) {
            closeLiveArtifactsPanel();
        }
        return [];
    }

    renderArtifactStrip(container, artifacts, Boolean(options.isStreaming));
    const codeBlocks = Array.from(container.querySelectorAll('pre.code-block-wrapper'));
    decorateCodeBlocks(codeBlocks, artifacts);
    hideSupportingCodeBlocks(codeBlocks, artifacts);

    if (activeArtifactKey) {
        const liveArtifact = artifacts.find(artifact => artifact.key === activeArtifactKey);
        if (liveArtifact) {
            registry.set(liveArtifact.id, liveArtifact);
            activeArtifactId = liveArtifact.id;
            renderPanel(liveArtifact);
        }
    }

    return artifacts;
}

export function getInlineLiveArtifact(markdownText, messageId = 'message', isStreaming = false) {
    return extractInlineLiveArtifact(markdownText, messageId, isStreaming);
}

export function getLiveArtifactInteraction(markdownText, isStreaming = false) {
    return extractLiveArtifactInteraction(markdownText, isStreaming);
}

function resolveMessageId(container, requestedId = '') {
    if (requestedId) {
        container.dataset.liveArtifactsMessageId = requestedId;
        return requestedId;
    }
    if (container.dataset.liveArtifactsMessageId) {
        return container.dataset.liveArtifactsMessageId;
    }
    artifactCounter += 1;
    const generated = `message-${artifactCounter}`;
    container.dataset.liveArtifactsMessageId = generated;
    return generated;
}

function extractLiveArtifacts(markdownText, messageId) {
    const blocks = extractCodeBlocks(markdownText);
    const rawHtmlArtifacts = extractRawHtmlArtifacts(markdownText, messageId);
    if (blocks.length === 0) return rawHtmlArtifacts;

    const cssBlocks = blocks.filter(block => block.language === 'css');
    const jsBlocks = blocks.filter(block => block.language === 'javascript' || block.language === 'js');
    const artifacts = [];

    blocks.forEach((block) => {
        const artifact = createArtifactFromBlock(block, {
            messageId,
            cssBlocks,
            jsBlocks,
            ordinal: artifacts.length,
        });
        if (artifact) artifacts.push(artifact);
    });

    return [...artifacts, ...rawHtmlArtifacts];
}

function extractInlineLiveArtifact(markdownText, messageId, isStreaming) {
    const text = String(markdownText || '').trim();
    if (!text) return null;

    const singleFence = extractSingleLiveArtifactFence(text);
    if (singleFence) {
        return createInlineArtifact(singleFence.code, messageId, {
            isStreaming,
            language: singleFence.language === 'svg' ? 'svg' : 'html',
        });
    }

    const unfenced = stripFencedCodeBlocks(text).trim();
    if (!unfenced || unfenced !== text) return null;

    if (isStandaloneHtmlArtifact(unfenced) || (isStreaming && isLikelyStreamingHtmlArtifact(unfenced))) {
        return createInlineArtifact(unfenced, messageId, {
            isStreaming,
            language: /^<svg[\s>]/i.test(unfenced) ? 'svg' : 'html',
        });
    }

    return null;
}

function extractSingleLiveArtifactFence(text) {
    const match = text.match(/^```([^\n`]*)\n([\s\S]*?)\n?```\s*$/);
    if (!match) return null;
    const language = normalizeLanguage(match[1] || '');
    if (language !== LIVE_ARTIFACT_HTML_LANGUAGE && language !== 'html' && language !== 'svg') {
        return null;
    }
    return {
        language,
        code: String(match[2] || '').trim(),
    };
}

function extractLiveArtifactInteraction(markdownText, isStreaming) {
    const text = String(markdownText || '').trim();
    if (!text) return null;

    const fenced = text.match(/^```([^\n`]*)\n([\s\S]*?)\n?```\s*$/);
    const openFence = text.match(/^```([^\n`]*)\n([\s\S]*)$/);
    const language = normalizeLanguage((fenced || openFence)?.[1] || '');
    if (language !== LIVE_ARTIFACT_INTERACTION_LANGUAGE) return null;

    const content = String((fenced || openFence)?.[2] || '').trim();
    if (isStreaming && !fenced) {
        return { pending: true };
    }

    return parseLiveArtifactInteractionSpec(content);
}

function createInlineArtifact(code, messageId, { isStreaming = false, language = 'html' } = {}) {
    const previewCode = isStreaming ? STREAM_PREVIEW_ROOT : code;
    const title = getArtifactTitle({ info: '', language, code }, language, 0);
    return {
        id: `${messageId}-inline-0`,
        key: `${messageId}:inline-0`,
        index: 0,
        blockIndex: -1,
        messageId,
        title,
        language,
        fileName: getArtifactFileName(title, language),
        code,
        renderable: true,
        supportBlockIndices: [],
        srcdoc: buildSrcdoc(previewCode, language),
        inline: true,
        isStreaming,
        streamHtml: isStreaming ? code : '',
    };
}

function extractCodeBlocks(markdownText) {
    const blocks = [];
    const text = String(markdownText || '');
    const fenceRegex = /(^|\n)(`{3,}|~{3,})([^\n`~]*)\n([\s\S]*?)(?:\n\2(?=\n|$)|$)/g;
    let match;
    let blockIndex = 0;

    while ((match = fenceRegex.exec(text)) !== null) {
        const currentBlockIndex = blockIndex;
        blockIndex += 1;
        const info = String(match[3] || '').trim();
        const rawLanguage = normalizeLanguage(info.split(/\s+/)[0] || '');
        const code = String(match[4] || '').trim();
        if (!code) continue;

        blocks.push({
            blockIndex: currentBlockIndex,
            info,
            language: rawLanguage,
            code,
        });
    }

    return blocks;
}

function extractRawHtmlArtifacts(markdownText, messageId) {
    const text = stripFencedCodeBlocks(String(markdownText || ''));
    const rawHtmlRegex = /(?:<!doctype\s+html[\s\S]*?<\/html>|<html\b[\s\S]*?<\/html>)/gi;
    const artifacts = [];
    let match;

    while ((match = rawHtmlRegex.exec(text)) !== null) {
        const code = String(match[0] || '').trim();
        if (!code) continue;
        const index = artifacts.length;
        const title = getArtifactTitle({ info: '', language: 'html', code }, 'html', index);
        artifacts.push({
            id: `${messageId}-raw-${index}`,
            key: `${messageId}:raw-${index}`,
            index,
            blockIndex: -1,
            messageId,
            title,
            language: 'html',
            fileName: getArtifactFileName(title, 'html'),
            code,
            renderable: true,
            supportBlockIndices: [],
            srcdoc: buildSrcdoc(code, 'html'),
        });
    }

    return artifacts;
}

function stripFencedCodeBlocks(text) {
    return text.replace(/(^|\n)(`{3,}|~{3,})([^\n`~]*)\n[\s\S]*?(?:\n\2(?=\n|$)|$)/g, '\n');
}

function normalizeLanguage(language) {
    const raw = String(language || '')
        .replace(/[{}]/g, '')
        .replace(/^language-/i, '')
        .replace(/;.*$/g, '')
        .toLowerCase();
    const aliases = {
        'application/xhtml+xml': 'html',
        'image/svg+xml': 'svg',
        'text/html': 'html',
        'text/xml': 'html',
        htm: 'html',
        xhtml: 'html',
        xml: 'html',
        js: 'javascript',
        mjs: 'javascript',
        jsx: 'javascript',
        ts: 'javascript',
        tsx: 'javascript',
    };
    return aliases[raw] || raw;
}

function parseLiveArtifactInteractionSpec(content) {
    let parsed;
    try {
        parsed = JSON.parse(content);
    } catch {
        return null;
    }

    if (!isPlainObject(parsed) || !isPlainObject(parsed.schema)) return null;
    const version = parsed.version === undefined ? 1 : parsed.version;
    if (version !== 1) return null;

    const instruction = normalizeInteractionText(parsed.instruction, 2000);
    if (!instruction) return null;

    if (parsed.schema.type !== 'object' || !isPlainObject(parsed.schema.properties)) return null;
    const entries = Object.entries(parsed.schema.properties);
    if (entries.length === 0 || entries.length > 24) return null;

    const properties = {};
    for (const [key, rawProperty] of entries) {
        if (!/^[A-Za-z0-9_.-]{1,80}$/.test(key)) return null;
        const property = normalizeInteractionProperty(rawProperty);
        if (!property) return null;
        properties[key] = property;
    }

    const required = Array.isArray(parsed.schema.required)
        ? parsed.schema.required.filter(key => typeof key === 'string' && key in properties)
        : [];

    return {
        version: 1,
        instruction,
        schema: {
            type: 'object',
            properties,
            ...(required.length > 0 ? { required } : {}),
        },
        ...(normalizeInteractionText(parsed.title, 500) ? { title: normalizeInteractionText(parsed.title, 500) } : {}),
        ...(normalizeInteractionText(parsed.description, 2000) ? { description: normalizeInteractionText(parsed.description, 2000) } : {}),
        ...(normalizeInteractionText(parsed.submitLabel, 120) ? { submitLabel: normalizeInteractionText(parsed.submitLabel, 120) } : {}),
    };
}

function normalizeInteractionProperty(value) {
    if (!isPlainObject(value) || typeof value.type !== 'string') return null;
    const type = value.type.toLowerCase();
    if (!['string', 'number', 'integer', 'boolean'].includes(type)) return null;

    const property = { type };
    const title = normalizeInteractionText(value.title, 500);
    const description = normalizeInteractionText(value.description, 2000);
    const format = normalizeInteractionText(value.format, 80);
    if (title) property.title = title;
    if (description) property.description = description;
    if (format) property.format = format;

    if (value.default !== undefined) {
        if (!isInteractionValueValidForType(value.default, type)) return null;
        property.default = value.default;
    }

    if (value.enum !== undefined) {
        if (!Array.isArray(value.enum) || value.enum.length === 0 || value.enum.length > 50) return null;
        if (!value.enum.every(item => isInteractionValueValidForType(item, type))) return null;
        property.enum = value.enum.slice();
        if (Array.isArray(value.enumNames) && value.enumNames.length === value.enum.length) {
            const names = value.enumNames.map(name => normalizeInteractionText(name, 500));
            if (names.every(Boolean)) property.enumNames = names;
        }
    }

    if (typeof value.minimum === 'number' && Number.isFinite(value.minimum)) property.minimum = value.minimum;
    if (typeof value.maximum === 'number' && Number.isFinite(value.maximum)) property.maximum = value.maximum;
    if (property.minimum !== undefined && property.maximum !== undefined && property.minimum > property.maximum) return null;

    return property;
}

function isPlainObject(value) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function normalizeInteractionText(value, maxLength) {
    if (value === undefined || value === null) return '';
    if (typeof value !== 'string') return '';
    const trimmed = value.trim();
    return trimmed.length <= maxLength ? trimmed : '';
}

function isInteractionValueValidForType(value, type) {
    if (type === 'boolean') return typeof value === 'boolean';
    if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
    if (type === 'integer') return typeof value === 'number' && Number.isInteger(value);
    return typeof value === 'string';
}

function createArtifactFromBlock(block, { messageId, cssBlocks, jsBlocks, ordinal }) {
    const language = inferRenderableLanguage(block);
    if (!ARTIFACT_LANGUAGES.has(language)) {
        if (!isExplicitArtifact(block)) return null;
    }

    const index = ordinal;
    const key = `${messageId}:${index}`;
    const code = buildArtifactCode(block, language, cssBlocks, jsBlocks);
    const title = getArtifactTitle(block, language, index);
    const renderable = ARTIFACT_LANGUAGES.has(language);
    const shouldMergeSupport = shouldMergeSupportingBlocks(block, language);

    return {
        id: `${messageId}-${index}`,
        key,
        index,
        blockIndex: block.blockIndex,
        messageId,
        title,
        language: language || block.language || 'text',
        fileName: parseInfoFileName(block.info) || getArtifactFileName(title, language || block.language || 'txt'),
        code,
        renderable,
        supportBlockIndices: shouldMergeSupport
            ? [...cssBlocks, ...jsBlocks].map(supportBlock => supportBlock.blockIndex)
            : [],
        srcdoc: renderable ? buildSrcdoc(code, language) : '',
    };
}

function inferRenderableLanguage(block) {
    const infoLanguage = inferLanguageFromInfo(block.info);
    if (ARTIFACT_LANGUAGES.has(infoLanguage)) return infoLanguage;
    if (block.language === 'html' || block.language === 'svg') return block.language;
    if (block.language && SUPPORTING_LANGUAGES.has(block.language)) return '';

    const code = block.code.trim();
    if (/^<svg[\s>]/i.test(code)) return 'svg';
    if (isFullHtmlDocument(code) || looksLikeHtmlFragment(code)) return 'html';
    return '';
}

function isExplicitArtifact(block) {
    return /\b(?:artifact|canvas)\b/i.test(block.info || '');
}

function buildArtifactCode(block, language, cssBlocks, jsBlocks) {
    if (language !== 'html') return block.code;

    const shouldMergeSupport = shouldMergeSupportingBlocks(block, language);
    const css = shouldMergeSupport ? cssBlocks.map(item => item.code).join('\n\n') : '';
    const js = shouldMergeSupport ? jsBlocks.map(item => item.code).join('\n\n') : '';
    let html = block.code;

    if (!isFullHtmlDocument(html)) {
        html = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
${css}
  </style>
</head>
<body>
${html}
  <script>
${js}
  </script>
</body>
</html>`;
        return html;
    }

    if (css && !/<\/head>/i.test(html)) {
        html = html.replace(/<html[^>]*>/i, match => `${match}\n<head><style>\n${css}\n</style></head>`);
    } else if (css) {
        html = html.replace(/<\/head>/i, `<style>\n${css}\n</style>\n</head>`);
    }

    if (js && !/<\/body>/i.test(html)) {
        html += `\n<script>\n${js}\n</script>`;
    } else if (js) {
        html = html.replace(/<\/body>/i, `<script>\n${js}\n</script>\n</body>`);
    }

    return html;
}

function isFullHtmlDocument(code) {
    return /<!doctype html|<html[\s>]/i.test(code);
}

function looksLikeHtmlFragment(code) {
    return /<\/?(?:a|article|aside|body|button|canvas|div|footer|form|h[1-6]|head|header|html|input|li|main|nav|ol|p|script|section|span|style|svg|table|tbody|td|textarea|th|thead|tr|ul)\b/i.test(code);
}

function isStandaloneHtmlArtifact(code) {
    const normalized = String(code || '').trim();
    if (!normalized) return false;
    if (/^<svg\b[\s\S]*<\/svg>$/i.test(normalized)) return true;
    if (/^(?:<!doctype\s+html\b[^>]*>\s*)?<html\b[\s\S]*<\/html>$/i.test(normalized)) return true;
    return isStandaloneHtmlFragment(normalized);
}

function isStandaloneHtmlFragment(code) {
    const normalized = String(code || '').trim();
    if (!normalized || /<(?:script|style|iframe|object|embed)\b/i.test(normalized)) return false;
    const withoutComments = normalized.replace(/<!--[\s\S]*?-->/g, '').trim();
    const fragmentTags = '(?:article|aside|blockquote|button|caption|details|div|figure|figcaption|footer|form|h[1-6]|header|label|li|main|meter|nav|ol|p|progress|section|select|span|summary|table|tbody|td|tfoot|th|thead|tr|ul)';
    const sameRoot = new RegExp(`^<(${fragmentTags})(?:\\s[^>]*)?>[\\s\\S]*<\\/\\1>$`, 'i');
    const container = new RegExp(`^<${fragmentTags}(?:\\s[^>]*)?>[\\s\\S]*<\\/${fragmentTags}>$`, 'i');
    return sameRoot.test(withoutComments) || container.test(withoutComments);
}

function isLikelyStreamingHtmlArtifact(code) {
    const normalized = String(code || '').trim();
    if (!normalized || /<(?:script|style|iframe|object|embed)\b/i.test(normalized)) return false;
    if (/^(?:<!doctype\s+html\b[^>]*>\s*)?(?:<html\b|<head\b|<body\b)/i.test(normalized)) return true;
    return /^(?:<!--[\s\S]*?-->\s*)?<(?:article|aside|blockquote|button|caption|details|div|figure|figcaption|footer|form|h[1-6]|header|label|li|main|meter|nav|ol|p|progress|section|select|span|summary|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>/i.test(normalized);
}

function shouldMergeSupportingBlocks(block, language) {
    return language === 'html' && !isFullHtmlDocument(block.code);
}

function buildSrcdoc(code, language) {
    let srcdoc;
    if (language === 'svg') {
        srcdoc = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body { margin: 0; min-height: 100%; background: #fff; }
    body { display: grid; place-items: center; padding: 24px; box-sizing: border-box; }
    svg { max-width: 100%; max-height: calc(100vh - 48px); }
  </style>
</head>
<body>
${code}
</body>
</html>`;
    } else {
        srcdoc = code;
    }
    return injectPreviewSecurityPolicy(injectPreviewBridge(srcdoc));
}

function injectPreviewBridge(code) {
    const bridge = `<script>
(() => {
  const resize = () => {
    try {
      const body = document.body;
      const root = document.documentElement;
      const height = Math.max(body ? body.scrollHeight : 0, body ? body.offsetHeight : 0, root ? root.scrollHeight : 0, root ? root.offsetHeight : 0);
      parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'resize', height }, '*');
    } catch {}
  };
  const notifyDiagnostic = (payload) => {
    try {
      parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'diagnostic', payload }, '*');
    } catch {}
  };
  const readResourceUrl = (element) => {
    if (!(element instanceof Element)) return undefined;
    return element.getAttribute('src') || element.getAttribute('href') || element.getAttribute('poster') || undefined;
  };
  const reportResourceError = (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return false;
    const tagName = target.tagName.toLowerCase();
    if (!['img', 'script', 'link', 'video', 'audio', 'source'].includes(tagName)) return false;
    notifyDiagnostic({
      type: 'resource-error',
      tagName,
      url: readResourceUrl(target),
    });
    return true;
  };
  window.addEventListener('error', (event) => {
    if (reportResourceError(event)) return;
    notifyDiagnostic({
      type: 'runtime-error',
      message: event.message || 'Unknown Live Artifact runtime error',
      source: event.filename || undefined,
      line: event.lineno || undefined,
      column: event.colno || undefined,
    });
  }, true);
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    notifyDiagnostic({
      type: 'runtime-error',
      message: reason && typeof reason.message === 'string'
        ? reason.message
        : String(reason || 'Unhandled promise rejection'),
    });
  });
  window.addEventListener('securitypolicyviolation', (event) => {
    notifyDiagnostic({
      type: 'csp-violation',
      blockedURI: event.blockedURI,
      violatedDirective: event.violatedDirective,
      effectiveDirective: event.effectiveDirective,
    });
  });
  window.addEventListener('load', resize, { once: true });
  window.addEventListener('resize', resize);
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(resize);
    if (document.documentElement) observer.observe(document.documentElement);
    if (document.body) observer.observe(document.body);
  }
  if ('MutationObserver' in window) {
    const observer = new MutationObserver(resize);
    observer.observe(document.documentElement || document, { childList: true, subtree: true, attributes: true });
  }
  const sanitizeStreamDocument = (parsedDocument) => {
    parsedDocument.querySelectorAll('script, iframe, object, embed').forEach((node) => node.remove());
    parsedDocument.querySelectorAll('*').forEach((node) => {
      Array.from(node.attributes).forEach((attribute) => {
        if (/^on/i.test(attribute.name) || attribute.name === 'srcdoc') node.removeAttribute(attribute.name);
      });
    });
  };
  const renderStreamHtml = (html) => {
    const root = document.querySelector('[data-amc-stream-preview-root]');
    if (!root || typeof html !== 'string') return;
    const parser = new DOMParser();
    const parsedDocument = parser.parseFromString(html, 'text/html');
    sanitizeStreamDocument(parsedDocument);
    const fragment = document.createDocumentFragment();
    parsedDocument.head.querySelectorAll('style, link[rel="stylesheet"]').forEach((node) => {
      fragment.appendChild(document.importNode(node, true));
    });
    Array.from(parsedDocument.body.childNodes).forEach((node) => {
      fragment.appendChild(document.importNode(node, true));
    });
    root.replaceChildren(fragment);
    resize();
  };
  window.addEventListener('message', (event) => {
    if (!event.data || event.data.channel !== 'justsearch-live-artifacts' || event.data.event !== 'stream-render') return;
    renderStreamHtml(event.data.html);
  });
  const parsePayload = (raw) => {
    const value = raw.trim();
    if (!value) return null;
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === 'string') {
        const instruction = parsed.trim();
        return instruction ? { instruction } : null;
      }
      return parsed;
    } catch {
      return /^[{[]/.test(value) ? null : { instruction: value };
    }
  };
  const resolveScope = (trigger) => {
    const selector = trigger.getAttribute('data-amc-followup-scope');
    if (selector && selector.trim()) {
      try {
        return document.querySelector(selector) || trigger.closest(selector) || document;
      } catch {
        return document;
      }
    }
    return trigger.closest('[data-amc-followup-scope]') || document;
  };
  const readStateValue = (element) => {
    if (element instanceof HTMLInputElement) {
      const type = element.type.toLowerCase();
      if (type === 'checkbox') return element.checked;
      if (type === 'radio') return element.checked ? element.value || true : undefined;
      if (type === 'number' || type === 'range') {
        return element.value === '' || Number.isNaN(element.valueAsNumber) ? element.value : element.valueAsNumber;
      }
      return element.value;
    }
    if (element instanceof HTMLSelectElement) {
      return element.multiple ? Array.from(element.selectedOptions).map(option => option.value) : element.value;
    }
    if (element instanceof HTMLTextAreaElement) return element.value;
    const stateValue = element.getAttribute('data-amc-state-value');
    if (stateValue !== null) {
      const toggleLike = element.hasAttribute('aria-pressed') || element.hasAttribute('aria-selected') || element.hasAttribute('aria-checked');
      if (!toggleLike) return stateValue;
      return element.getAttribute('aria-pressed') === 'true' || element.getAttribute('aria-selected') === 'true' || element.getAttribute('aria-checked') === 'true'
        ? stateValue
        : undefined;
    }
    const text = element.textContent ? element.textContent.trim() : '';
    return text || undefined;
  };
  const appendState = (state, key, value) => {
    if (value === undefined) return;
    if (Object.prototype.hasOwnProperty.call(state, key)) {
      state[key] = Array.isArray(state[key]) ? [...state[key], value] : [state[key], value];
      return;
    }
    state[key] = value;
  };
  const collectState = (trigger) => {
    const scope = resolveScope(trigger);
    const state = {};
    const elements = [];
    if (scope instanceof Element && scope.matches('[data-amc-state-key]')) elements.push(scope);
    elements.push(...Array.from(scope.querySelectorAll('[data-amc-state-key]')));
    elements.forEach((element) => {
      const key = element.getAttribute('data-amc-state-key');
      if (!key || element.disabled) return;
      appendState(state, key, readStateValue(element));
    });
    return state;
  };
  const mergeState = (payload, state) => {
    if (!state || Object.keys(state).length === 0) return payload;
    const existing = payload && typeof payload.state === 'object' && !Array.isArray(payload.state)
      ? payload.state
      : payload && payload.state !== undefined
        ? { value: payload.state }
        : {};
    return { ...payload, state: { ...existing, ...state } };
  };
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest?.('[data-amc-followup]');
    if (!trigger) return;
    const payload = parsePayload(trigger.getAttribute('data-amc-followup') || '');
    if (!payload) return;
    event.preventDefault();
    parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'followup', payload: mergeState(payload, collectState(trigger)) }, '*');
  });
  Promise.resolve().then(resize);
})();
</script>`;

    if (/<\/body>/i.test(code)) {
        return code.replace(/<\/body>/i, `${bridge}</body>`);
    }
    if (/<\/html>/i.test(code)) {
        return code.replace(/<\/html>/i, `${bridge}</html>`);
    }
    return `<!doctype html><html><body>${code}${bridge}</body></html>`;
}

function injectPreviewSecurityPolicy(srcdoc) {
    if (srcdoc.includes(PREVIEW_CONTENT_SECURITY_POLICY)) {
        return srcdoc;
    }
    if (/<head\b[^>]*>/i.test(srcdoc)) {
        return srcdoc.replace(/<head\b[^>]*>/i, headTag => `${headTag}${PREVIEW_CONTENT_SECURITY_POLICY_META}`);
    }
    if (/<html\b[^>]*>/i.test(srcdoc)) {
        return srcdoc.replace(/<html\b[^>]*>/i, htmlTag => `${htmlTag}<head>${PREVIEW_CONTENT_SECURITY_POLICY_META}</head>`);
    }
    return `<!doctype html><html><head>${PREVIEW_CONTENT_SECURITY_POLICY_META}</head><body>${srcdoc}</body></html>`;
}

function getArtifactTitle(block, language, index) {
    const named = parseInfoName(block.info);
    if (named) return named;

    if (language === 'html') {
        const title = block.code.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]?.trim();
        if (title) return stripTags(title);
        const heading = block.code.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1]?.trim();
        if (heading) return stripTags(heading);
        return `Live Web Artifact ${index + 1}`;
    }

    if (language === 'svg') {
        const svgTitle = block.code.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]?.trim();
        return svgTitle ? stripTags(svgTitle) : `SVG Artifact ${index + 1}`;
    }

    return `Artifact ${index + 1}`;
}

function parseInfoName(info) {
    const attrs = parseInfoAttributes(info);
    const named = attrs.title || attrs.name || '';
    if (named) return named.trim();
    const filename = attrs.filename || attrs.file || '';
    if (filename) return filename.replace(/\.[a-z0-9]+$/i, '').trim();
    return '';
}

function parseInfoFileName(info) {
    const attrs = parseInfoAttributes(info);
    const raw = (attrs.filename || attrs.file || '').trim();
    if (!raw) return '';
    const safeName = raw
        .replace(/[\\/]+/g, '-')
        .replace(/[^\w.\-\u4e00-\u9fa5]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 64);
    return safeName || '';
}

function inferLanguageFromInfo(info) {
    const attrs = parseInfoAttributes(info);
    const values = [
        attrs.type,
        attrs.mime,
        attrs.mimetype,
        attrs.contenttype,
        attrs.content_type,
        attrs.language,
        attrs.lang,
        attrs.format,
        attrs.filename,
        attrs.file,
    ].filter(Boolean);

    for (const value of values) {
        const lowered = String(value).toLowerCase();
        if (lowered.includes('text/html') || lowered.includes('application/xhtml+xml')) return 'html';
        if (lowered.includes('image/svg+xml')) return 'svg';
        const normalized = normalizeLanguage(value);
        if (ARTIFACT_LANGUAGES.has(normalized)) return normalized;
        if (/\.html?$/i.test(value)) return 'html';
        if (/\.svg$/i.test(value)) return 'svg';
    }

    return '';
}

function parseInfoAttributes(info) {
    const attrs = {};
    const raw = String(info || '');
    const attrRegex = /\b([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+))/g;
    let match;

    while ((match = attrRegex.exec(raw)) !== null) {
        const key = match[1].toLowerCase().replace(/-/g, '_');
        attrs[key] = (match[2] ?? match[3] ?? match[4] ?? '').trim();
    }

    return attrs;
}

function stripTags(value) {
    if (typeof document === 'undefined') {
        return String(value || '').replace(/<[^>]*>/g, '').trim();
    }
    const div = document.createElement('div');
    div.innerHTML = value;
    return div.textContent.trim();
}

function getArtifactFileName(title, language) {
    const ext = language === 'svg' ? 'svg' : language === 'html' ? 'html' : 'txt';
    const base = String(title || 'artifact')
        .toLowerCase()
        .replace(/[^a-z0-9\u4e00-\u9fa5]+/gi, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 48) || 'artifact';
    return `${base}.${ext}`;
}

function syncRegistryForMessage(messageId, artifacts) {
    Array.from(registry.values()).forEach((artifact) => {
        if (artifact.messageId === messageId) {
            registry.delete(artifact.id);
        }
    });
    artifacts.forEach(artifact => registry.set(artifact.id, artifact));
}

function clearArtifactControls(container) {
    container.querySelectorAll('.live-artifacts-strip').forEach(el => el.remove());
    container.querySelectorAll('.live-artifact-open-btn').forEach(el => el.remove());
    container.querySelectorAll('.live-artifact-support-block').forEach((el) => {
        el.classList.remove('live-artifact-support-block');
        el.hidden = false;
        el.removeAttribute('aria-hidden');
    });
}

function renderArtifactStrip(container, artifacts, isStreaming) {
    const strip = document.createElement('div');
    strip.className = 'live-artifacts-strip';
    strip.setAttribute('role', 'list');
    strip.setAttribute('aria-label', 'Live Artifacts');

    const header = document.createElement('div');
    header.className = 'live-artifacts-strip-header';

    const title = document.createElement('div');
    title.className = 'live-artifacts-strip-title';
    const icon = document.createElement('span');
    icon.className = 'material-symbols-rounded';
    icon.textContent = 'auto_awesome_motion';
    title.appendChild(icon);
    title.appendChild(document.createTextNode('Live Artifacts'));

    const meta = document.createElement('span');
    meta.className = 'live-artifacts-strip-meta';
    meta.textContent = isStreaming ? '实时更新' : `${artifacts.length} 个`;
    header.appendChild(title);
    header.appendChild(meta);
    strip.appendChild(header);

    const list = document.createElement('div');
    list.className = 'live-artifacts-list';
    artifacts.forEach((artifact) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'live-artifact-card';
        item.dataset.artifactId = artifact.id;
        item.setAttribute('role', 'listitem');
        item.innerHTML = `
            <span class="material-symbols-rounded">preview</span>
            <span class="live-artifact-card-copy">
                <span class="live-artifact-card-title"></span>
                <span class="live-artifact-card-meta"></span>
            </span>
        `;
        item.querySelector('.live-artifact-card-title').textContent = artifact.title;
        item.querySelector('.live-artifact-card-meta').textContent = artifact.language.toUpperCase();
        list.appendChild(item);
    });
    strip.appendChild(list);

    container.prepend(strip);
}

function renderInlineArtifactFrame(container, artifact) {
    let frameShell = container.querySelector(':scope > .live-artifact-inline-frame');
    let viewport = frameShell?.querySelector('.live-artifact-inline-viewport');
    let frame = frameShell?.querySelector('.live-artifact-inline-iframe');

    if (!frameShell || !viewport || !frame) {
        container.innerHTML = '';
        frameShell = document.createElement('div');
        frameShell.className = 'live-artifact-inline-frame';
        frameShell.dataset.liveArtifactFrame = 'true';

        viewport = document.createElement('div');
        viewport.className = 'live-artifact-inline-viewport';
        viewport.dataset.liveArtifactViewport = 'true';

        frame = document.createElement('iframe');
        frame.className = 'live-artifact-inline-iframe';
        frame.title = 'HTML Preview';
        frame.sandbox = 'allow-scripts allow-forms';
        frame.setAttribute('scrolling', 'no');
        frame.addEventListener('load', () => resizeInlineArtifactFrame(frame, viewport));

        viewport.appendChild(frame);
        frameShell.appendChild(viewport);
        container.appendChild(frameShell);
    }

    if (frame.srcdoc !== artifact.srcdoc) {
        frame.srcdoc = artifact.srcdoc;
    }
    if (artifact.isStreaming && artifact.streamHtml) {
        postInlineArtifactStream(frame, artifact.streamHtml);
    }
}

function postInlineArtifactStream(frame, html) {
    const send = () => {
        try {
            frame.contentWindow?.postMessage({
                channel: 'justsearch-live-artifacts',
                event: STREAM_RENDER_EVENT,
                html,
            }, '*');
        } catch {
            // Ignore frame messaging failures while the iframe is mounting.
        }
    };
    send();
    setTimeout(send, 0);
}

function renderLiveArtifactInteraction(container, spec) {
    container.innerHTML = '';

    if (spec.pending) {
        const pending = document.createElement('div');
        pending.className = 'live-artifact-interaction pending';
        pending.dataset.liveArtifactInteractionPending = 'true';
        pending.textContent = 'Live Artifact 正在准备交互表单...';
        container.appendChild(pending);
        return;
    }

    const form = document.createElement('form');
    form.className = 'live-artifact-interaction';
    form.dataset.liveArtifactInteraction = 'true';

    const header = document.createElement('div');
    header.className = 'live-artifact-interaction-header';
    if (spec.title) {
        const title = document.createElement('h2');
        title.textContent = spec.title;
        header.appendChild(title);
    }
    if (spec.description) {
        const description = document.createElement('p');
        description.textContent = spec.description;
        header.appendChild(description);
    }
    form.appendChild(header);

    const fields = document.createElement('div');
    fields.className = 'live-artifact-interaction-fields';
    const required = new Set(spec.schema.required || []);
    Object.entries(spec.schema.properties).forEach(([key, property]) => {
        fields.appendChild(createInteractionField(key, property, required.has(key)));
    });
    form.appendChild(fields);

    const error = document.createElement('p');
    error.className = 'live-artifact-interaction-error';
    error.hidden = true;
    form.appendChild(error);

    const actions = document.createElement('div');
    actions.className = 'live-artifact-interaction-actions';
    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.className = 'live-artifact-interaction-submit';
    submit.innerHTML = '<span class="material-symbols-rounded">send</span><span></span>';
    submit.querySelector('span:last-child').textContent = spec.submitLabel || '继续';
    actions.appendChild(submit);
    form.appendChild(actions);

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const result = readInteractionFormState(form, spec);
        if (result.error) {
            error.textContent = result.error;
            error.hidden = false;
            return;
        }
        error.hidden = true;
        const prompt = formatInteractionFollowupPrompt({
            instruction: spec.instruction,
            ...(spec.title ? { title: spec.title } : {}),
            source: INTERACTION_SOURCE,
            state: result.state,
        });
        const input = document.getElementById('user-input');
        if (input) {
            input.value = prompt;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
        }
        showToast('Live Artifact 已填入下一步请求', 'success');
    });

    container.appendChild(form);
}

function createInteractionField(key, property, required) {
    const wrapper = document.createElement('label');
    wrapper.className = property.type === 'boolean'
        ? 'live-artifact-interaction-field boolean'
        : 'live-artifact-interaction-field';

    const label = document.createElement('span');
    label.className = 'live-artifact-interaction-label';
    label.textContent = property.title || key;
    if (required) {
        const requiredMark = document.createElement('span');
        requiredMark.className = 'live-artifact-interaction-required';
        requiredMark.textContent = '*';
        label.appendChild(requiredMark);
    }

    const description = property.description ? document.createElement('span') : null;
    if (description) {
        description.className = 'live-artifact-interaction-description';
        description.textContent = property.description;
    }

    const control = createInteractionControl(key, property, required);
    if (property.type === 'boolean') {
        wrapper.appendChild(control);
        const copy = document.createElement('span');
        copy.className = 'live-artifact-interaction-copy';
        copy.appendChild(label);
        if (description) copy.appendChild(description);
        wrapper.appendChild(copy);
    } else {
        wrapper.appendChild(label);
        if (description) wrapper.appendChild(description);
        wrapper.appendChild(control);
    }
    return wrapper;
}

function createInteractionControl(key, property, required) {
    const defaultValue = property.default ?? property.enum?.[0] ?? (property.type === 'boolean' ? false : '');

    if (property.type === 'boolean') {
        const input = document.createElement('input');
        input.name = key;
        input.type = 'checkbox';
        input.checked = Boolean(defaultValue);
        return input;
    }

    if (property.enum) {
        const select = document.createElement('select');
        select.name = key;
        select.required = required;
        property.enum.forEach((option, index) => {
            const item = document.createElement('option');
            item.value = String(option);
            item.textContent = property.enumNames?.[index] || String(option);
            select.appendChild(item);
        });
        select.value = String(defaultValue);
        return select;
    }

    if (property.format === 'textarea') {
        const textarea = document.createElement('textarea');
        textarea.name = key;
        textarea.required = required;
        textarea.rows = 4;
        textarea.value = String(defaultValue);
        return textarea;
    }

    const input = document.createElement('input');
    input.name = key;
    input.type = property.type === 'string' ? 'text' : 'number';
    input.required = required;
    if (property.type === 'integer') input.step = '1';
    if (property.minimum !== undefined) input.min = String(property.minimum);
    if (property.maximum !== undefined) input.max = String(property.maximum);
    input.value = String(defaultValue);
    return input;
}

function readInteractionFormState(form, spec) {
    const state = {};
    for (const [key, property] of Object.entries(spec.schema.properties)) {
        const required = (spec.schema.required || []).includes(key);
        const control = form.elements[key];
        const value = readInteractionControlValue(control, property);
        if (required && (value === '' || value === undefined)) {
            return { error: '请填写所有必填字段。' };
        }
        if ((property.type === 'number' || property.type === 'integer') && value !== '') {
            if (typeof value !== 'number' || !Number.isFinite(value)) return { error: '请输入有效数字。' };
            if (property.type === 'integer' && !Number.isInteger(value)) return { error: '请输入整数。' };
            if (property.minimum !== undefined && value < property.minimum) return { error: '数值超出允许范围。' };
            if (property.maximum !== undefined && value > property.maximum) return { error: '数值超出允许范围。' };
        }
        if (property.enum && !property.enum.some(option => String(option) === String(value))) {
            return { error: '请选择允许的选项。' };
        }
        state[key] = value;
    }
    return { state };
}

function readInteractionControlValue(control, property) {
    if (!control) return '';
    if (property.type === 'boolean') return Boolean(control.checked);
    if (property.type === 'number' || property.type === 'integer') {
        if (control.value === '') return '';
        const value = Number(control.value);
        return Number.isFinite(value) ? value : Number.NaN;
    }
    return control.value || '';
}

function formatInteractionFollowupPrompt(payload) {
    const lines = ['请根据 Live Artifact 中的交互选择继续处理。', '', `指令：${payload.instruction}`];
    if (payload.title) lines.push(`标题：${payload.title}`);
    lines.push('', '选择状态：', JSON.stringify(payload.state || {}, null, 2), '', `source: ${payload.source}`);
    return lines.join('\n');
}

function resizeInlineArtifactFrame(frame, viewport) {
    try {
        const doc = frame.contentDocument;
        const body = doc?.body;
        const root = doc?.documentElement;
        const height = Math.max(
            body?.scrollHeight || 0,
            body?.offsetHeight || 0,
            root?.scrollHeight || 0,
            root?.offsetHeight || 0,
            120,
        );
        viewport.style.height = `${Math.ceil(height)}px`;
    } catch {
        viewport.style.height = viewport.style.height || '320px';
    }
}

function decorateCodeBlocks(codeBlocks, artifacts) {
    artifacts.forEach((artifact) => {
        const codeBlock = codeBlocks[artifact.blockIndex];
        const header = codeBlock?.querySelector('.code-block-header');
        if (!header) return;

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'code-copy-btn live-artifact-open-btn';
        btn.dataset.artifactId = artifact.id;
        btn.title = '打开 Artifact';
        btn.innerHTML = '<span class="material-symbols-rounded">preview</span><span>预览</span>';
        header.appendChild(btn);
    });
}

function hideSupportingCodeBlocks(codeBlocks, artifacts) {
    const supportBlockIndices = new Set();
    artifacts.forEach((artifact) => {
        if (!artifact.renderable) return;
        (artifact.supportBlockIndices || []).forEach((blockIndex) => {
            if (blockIndex !== artifact.blockIndex) supportBlockIndices.add(blockIndex);
        });
    });

    supportBlockIndices.forEach((blockIndex) => {
        const codeBlock = codeBlocks[blockIndex];
        if (!codeBlock) return;
        codeBlock.classList.add('live-artifact-support-block');
        codeBlock.hidden = true;
        codeBlock.setAttribute('aria-hidden', 'true');
    });
}

function ensurePanel() {
    if (panelState) return panelState;

    const backdrop = document.createElement('div');
    backdrop.className = 'live-artifacts-backdrop';
    backdrop.hidden = true;

    const panel = document.createElement('aside');
    panel.className = 'live-artifacts-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', 'Live Artifact');
    panel.setAttribute('aria-hidden', 'true');
    panel.innerHTML = `
        <div class="live-artifacts-panel-header">
            <div class="live-artifacts-title-block">
                <div class="live-artifacts-kicker">Live Artifact</div>
                <div class="live-artifacts-title">Artifact</div>
                <div class="live-artifacts-meta"></div>
            </div>
            <button type="button" class="live-artifacts-icon-btn live-artifacts-close-btn" aria-label="关闭 Artifact">
                <span class="material-symbols-rounded">close</span>
            </button>
        </div>
        <div class="live-artifacts-toolbar">
            <div class="live-artifacts-tabs" role="tablist" aria-label="Artifact 视图">
                <button type="button" id="live-artifacts-tab-preview" class="live-artifacts-tab is-active" data-artifact-view="preview" role="tab" aria-selected="true" aria-controls="live-artifacts-preview-panel">预览</button>
                <button type="button" id="live-artifacts-tab-code" class="live-artifacts-tab" data-artifact-view="code" role="tab" aria-selected="false" aria-controls="live-artifacts-code-panel">源码</button>
            </div>
            <div class="live-artifacts-actions">
                <button type="button" class="live-artifacts-icon-btn" data-artifact-action="copy" aria-label="复制源码" title="复制源码">
                    <span class="material-symbols-rounded">content_copy</span>
                </button>
                <button type="button" class="live-artifacts-icon-btn" data-artifact-action="download" aria-label="下载 Artifact" title="下载 Artifact">
                    <span class="material-symbols-rounded">download</span>
                </button>
                <button type="button" class="live-artifacts-icon-btn" data-artifact-action="open" aria-label="新窗口打开" title="新窗口打开">
                    <span class="material-symbols-rounded">open_in_new</span>
                </button>
            </div>
        </div>
        <div class="live-artifacts-panel-body">
            <div id="live-artifacts-preview-panel" class="live-artifacts-preview-view is-active" role="tabpanel" aria-labelledby="live-artifacts-tab-preview">
                <iframe class="live-artifacts-frame" title="Live Artifact Preview" sandbox="allow-scripts allow-forms allow-modals allow-popups"></iframe>
                <div class="live-artifacts-empty" hidden>该 Artifact 暂无可运行预览</div>
            </div>
            <pre id="live-artifacts-code-panel" class="live-artifacts-code-view" role="tabpanel" aria-labelledby="live-artifacts-tab-code"><code></code></pre>
        </div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(panel);

    panelState = {
        backdrop,
        panel,
        title: panel.querySelector('.live-artifacts-title'),
        meta: panel.querySelector('.live-artifacts-meta'),
        frame: panel.querySelector('.live-artifacts-frame'),
        empty: panel.querySelector('.live-artifacts-empty'),
        code: panel.querySelector('.live-artifacts-code-view code'),
        previewView: panel.querySelector('.live-artifacts-preview-view'),
        codeView: panel.querySelector('.live-artifacts-code-view'),
        tabs: Array.from(panel.querySelectorAll('.live-artifacts-tab')),
    };

    wirePanelEvents();
    return panelState;
}

function wirePanelEvents() {
    document.addEventListener('click', handleArtifactDocumentClick);
    window.addEventListener('message', handleArtifactFrameMessage);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && document.body.classList.contains('live-artifacts-open')) {
            closeLiveArtifactsPanel();
        }
    });
    panelState.backdrop.addEventListener('click', closeLiveArtifactsPanel);
    panelState.panel.querySelector('.live-artifacts-close-btn').addEventListener('click', closeLiveArtifactsPanel);
}

function handleArtifactFrameMessage(event) {
    const data = event.data || {};
    if (data.channel !== 'justsearch-live-artifacts') return;

    if (data.event === 'resize' && typeof data.height === 'number') {
        const frame = Array.from(document.querySelectorAll('.live-artifact-inline-iframe'))
            .find(item => item.contentWindow === event.source);
        const viewport = frame?.closest('.live-artifact-inline-viewport');
        if (viewport) {
            viewport.style.height = `${Math.max(120, Math.ceil(data.height))}px`;
        }
        return;
    }

    if (data.event === 'followup') {
        const payload = normalizeFollowupPayload(data.payload);
        if (payload) {
            const input = document.getElementById('user-input');
            if (input) {
                input.value = formatInteractionFollowupPrompt(payload);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.focus();
            }
        }
    }

    if (data.event === 'diagnostic') {
        handlePreviewDiagnostic(data.payload);
    }
}

function handlePreviewDiagnostic(payload) {
    const diagnostic = normalizePreviewDiagnostic(payload);
    if (!diagnostic) return;

    if (diagnostic.type === 'resource-error') {
        console.warn('[Live Artifacts] Preview resource failed to load.', diagnostic);
    } else if (diagnostic.type === 'csp-violation') {
        console.warn('[Live Artifacts] Preview content was blocked by CSP.', diagnostic);
    } else {
        console.warn('[Live Artifacts] Preview runtime error.', diagnostic);
    }

    const now = Date.now();
    if (now - lastDiagnosticToastAt > 5000) {
        lastDiagnosticToastAt = now;
        showToast('Live Artifact 预览遇到问题，详情见控制台', 'warning', 5000);
    }
}

function normalizePreviewDiagnostic(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        return null;
    }
    const type = typeof payload.type === 'string' ? payload.type : '';
    if (!['resource-error', 'runtime-error', 'csp-violation'].includes(type)) {
        return null;
    }
    const diagnostic = { type };
    ['tagName', 'url', 'message', 'source', 'blockedURI', 'violatedDirective', 'effectiveDirective'].forEach((key) => {
        if (typeof payload[key] === 'string' && payload[key].trim()) {
            diagnostic[key] = payload[key].trim();
        }
    });
    ['line', 'column'].forEach((key) => {
        if (typeof payload[key] === 'number' && Number.isFinite(payload[key])) {
            diagnostic[key] = payload[key];
        }
    });
    return diagnostic;
}

function normalizeFollowupPayload(payload) {
    if (typeof payload === 'string') {
        const instruction = payload.trim();
        return instruction ? { instruction } : null;
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        return null;
    }
    const instruction = typeof payload.instruction === 'string' ? payload.instruction.trim() : '';
    if (!instruction) return null;
    return {
        instruction,
        ...(typeof payload.title === 'string' && payload.title.trim() ? { title: payload.title.trim() } : {}),
        source: typeof payload.source === 'string' && payload.source.trim() ? payload.source.trim() : 'data-amc-followup',
        ...(payload.state && typeof payload.state === 'object' && !Array.isArray(payload.state) ? { state: payload.state } : {}),
    };
}

function handleArtifactDocumentClick(event) {
    const openTarget = event.target.closest('[data-artifact-id]');
    if (openTarget) {
        const artifact = registry.get(openTarget.dataset.artifactId);
        if (artifact) {
            event.preventDefault();
            openLiveArtifactsPanel(artifact.id);
        }
        return;
    }

    const viewButton = event.target.closest('[data-artifact-view]');
    if (viewButton && panelState?.panel.contains(viewButton)) {
        setArtifactView(viewButton.dataset.artifactView);
        return;
    }

    const actionButton = event.target.closest('[data-artifact-action]');
    if (actionButton && panelState?.panel.contains(actionButton)) {
        handleArtifactAction(actionButton.dataset.artifactAction);
    }
}

function openLiveArtifactsPanel(artifactId) {
    const artifact = registry.get(artifactId);
    if (!artifact) return;

    activeArtifactId = artifact.id;
    activeArtifactKey = artifact.key;
    ensurePanel();
    document.body.classList.add('live-artifacts-open');
    panelState.backdrop.hidden = false;
    panelState.panel.setAttribute('aria-hidden', 'false');
    renderPanel(artifact);
}

function closeLiveArtifactsPanel() {
    if (!panelState) return;
    document.body.classList.remove('live-artifacts-open');
    panelState.backdrop.hidden = true;
    panelState.panel.setAttribute('aria-hidden', 'true');
    activeArtifactId = '';
    activeArtifactKey = '';
}

function renderPanel(artifact) {
    if (!panelState) return;

    panelState.title.textContent = artifact.title;
    panelState.meta.textContent = `${artifact.language.toUpperCase()} · ${artifact.fileName}`;
    panelState.code.textContent = artifact.code;

    const canPreview = Boolean(artifact.renderable && artifact.srcdoc);
    panelState.frame.hidden = !canPreview;
    panelState.empty.hidden = canPreview;
    if (canPreview) {
        panelState.frame.srcdoc = artifact.srcdoc;
    } else {
        panelState.frame.removeAttribute('srcdoc');
    }

    setArtifactView(canPreview ? activeView : 'code', { preservePreference: canPreview });
}

function setArtifactView(view, { preservePreference = true } = {}) {
    const nextView = view === 'code' ? 'code' : 'preview';
    if (preservePreference) activeView = nextView;

    panelState.tabs.forEach((tab) => {
        const isActive = tab.dataset.artifactView === nextView;
        tab.classList.toggle('is-active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    panelState.previewView.classList.toggle('is-active', nextView === 'preview');
    panelState.codeView.classList.toggle('is-active', nextView === 'code');
}

async function handleArtifactAction(action) {
    const artifact = registry.get(activeArtifactId);
    if (!artifact) return;

    if (action === 'copy') {
        try {
            await navigator.clipboard.writeText(artifact.code);
            showToast('Artifact 源码已复制', 'success');
        } catch {
            showToast('复制失败', 'error');
        }
        return;
    }

    if (action === 'download') {
        downloadArtifact(artifact);
        return;
    }

    if (action === 'open') {
        openArtifactInNewWindow(artifact);
    }
}

function downloadArtifact(artifact) {
    const blob = new Blob([artifact.code], { type: artifact.language === 'svg' ? 'image/svg+xml' : 'text/html' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = artifact.fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function openArtifactInNewWindow(artifact) {
    const content = artifact.srcdoc || artifact.code;
    const blob = new Blob([content], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener,noreferrer');
    setTimeout(() => URL.revokeObjectURL(url), 30000);
}

export const __liveArtifactsTestHooks = {
    buildArtifactCode,
    buildSrcdoc,
    extractCodeBlocks,
    extractInlineLiveArtifact,
    extractLiveArtifactInteraction,
    extractLiveArtifacts,
    injectPreviewSecurityPolicy,
    inferRenderableLanguage,
    normalizePreviewDiagnostic,
    parseLiveArtifactInteractionSpec,
    parseInfoAttributes,
    shouldMergeSupportingBlocks,
};
