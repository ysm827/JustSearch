import { showToast } from './toast.js';
import { state } from './state.js?v=5';

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
// Mirror AMC-WebUI preview base styles: transparent body, natural height (no 100vh lock).
// Color comes from injectPreviewTheme tokens so dark mode stays readable.
const PREVIEW_BASE_STYLES = `<style data-amc-preview-base="true">
html, body {
  margin: 0;
  padding: 0;
  background: transparent !important;
  color: var(--amc-live-artifact-text, inherit);
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  overflow-x: auto;
  overflow-y: visible !important;
}
body > section, body > main, body > article, body > div,
body > [data-amc-stream-preview-root] {
  height: auto !important;
  max-height: none !important;
  min-height: 0 !important;
  overflow: visible !important;
}
</style>`;
const PREVIEW_BASE_FONT_SIZE_ATTRIBUTE = 'data-amc-live-artifact-base-font-size';
const PREVIEW_THEME_ATTRIBUTE = 'data-amc-live-artifact-theme';
// Theme tokens aligned with JustSearch :root / [data-theme="dark"] and AMC onyx/pearl.
const LIVE_ARTIFACT_THEME_PALETTES = {
    light: {
        colorScheme: 'light',
        text: '#111827',
        muted: '#6b7280',
        subtle: '#9ca3af',
        surface: '#f3f4f6',
        surfaceMuted: '#ffffff',
        border: '#e5e7eb',
        accent: '#2563eb',
        accentSurface: 'rgba(37, 99, 235, 0.12)',
        success: '#10b981',
        danger: '#ef4444',
        warning: '#f59e0b',
    },
    dark: {
        colorScheme: 'dark',
        text: '#f4f4f5',
        muted: '#a1a1aa',
        subtle: '#71717a',
        surface: '#18181b',
        surfaceMuted: '#1c1c1f',
        border: '#27272a',
        accent: '#38bdf8',
        accentSurface: 'rgba(56, 189, 248, 0.14)',
        success: '#34d399',
        danger: '#f87171',
        warning: '#fbbf24',
    },
};
const DEFAULT_LIVE_ARTIFACT_FONT_SIZE = 16;
const LIVE_ARTIFACT_FONT_SIZE_MIN = 10;
const LIVE_ARTIFACT_FONT_SIZE_MAX = 32;
// Align with AMC-WebUI ArtifactFrame height constants.
const INLINE_ARTIFACT_MIN_HEIGHT = 120;
const INLINE_ARTIFACT_DEFAULT_HEIGHT = 320;
const INLINE_ARTIFACT_MAX_HEIGHT = 50000;
const FRAME_HEIGHT_CACHE_MAX = 200;
const frameHeightCache = new Map();
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
    const artifactSources = normalizeArtifactSources(options.sources);
    const interactionSpec = extractLiveArtifactInteraction(markdownText, Boolean(options.isStreaming));
    if (interactionSpec) {
        syncRegistryForMessage(messageId, []);
        clearArtifactControls(container);
        renderLiveArtifactInteraction(container, interactionSpec);
        return [];
    }

    const inlineArtifact = extractInlineLiveArtifact(markdownText, messageId, Boolean(options.isStreaming), {
        suppressUnfencedInlineArtifact: Boolean(options.suppressUnfencedInlineArtifact),
    });
    if (inlineArtifact) {
        hydrateArtifactCitations(inlineArtifact, artifactSources);
        syncRegistryForMessage(messageId, [inlineArtifact]);
        clearArtifactControls(container);
        renderInlineArtifactFrame(container, inlineArtifact);
        renderLiveArtifactSources(container, inlineArtifact, artifactSources);
        return [inlineArtifact];
    }

    const artifacts = extractLiveArtifacts(markdownText, messageId);
    artifacts.forEach(artifact => hydrateArtifactCitations(artifact, artifactSources));
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

export function getInlineLiveArtifact(markdownText, messageId = 'message', isStreaming = false, options = {}) {
    return extractInlineLiveArtifact(markdownText, messageId, isStreaming, options);
}

export function getLiveArtifactInteraction(markdownText, isStreaming = false) {
    return extractLiveArtifactInteraction(markdownText, isStreaming);
}

/**
 * Rebuild open Live Artifact iframe srcdocs when base font size or app theme changes.
 * Mirrors AMC injecting --amc-live-artifact-font-size and transparent theme tokens.
 */
export function refreshLiveArtifactFontSizes(settings) {
    refreshLiveArtifactPreviews(settings);
}

/**
 * Rebuild all open Live Artifact previews with current font size + theme tokens.
 * Call after theme switch so dark mode text/surface tokens stay readable.
 */
export function refreshLiveArtifactPreviews(settings) {
    const fontSize = resolveLiveArtifactFontSizePx(settings);
    const themeId = resolveLiveArtifactThemeId(settings);
    registry.forEach((artifact) => {
        if (!artifact?.renderable) return;
        const sources = Array.isArray(artifact.sources) ? artifact.sources : [];
        const previewCode = resolveArtifactPreviewCode(artifact);
        artifact.srcdoc = buildSrcdoc(previewCode, artifact.language, sources, {
            frameId: artifact.id,
            baseFontSize: fontSize,
            themeId,
        });
    });

    document.querySelectorAll('.live-artifact-inline-iframe').forEach((frame) => {
        const frameId = frame.dataset.liveArtifactFrameId || '';
        const artifact = frameId ? registry.get(frameId) : null;
        if (artifact?.srcdoc && frame.srcdoc !== artifact.srcdoc) {
            frame.srcdoc = artifact.srcdoc;
            // After srcdoc reload the bridge re-listens; re-push any pending stream HTML.
            syncPendingStreamToFrame(frame, artifact);
        }
    });

    if (panelState?.frame && activeArtifactId) {
        const active = registry.get(activeArtifactId);
        if (active?.renderable && active.srcdoc) {
            panelState.frame.srcdoc = active.srcdoc;
            syncPendingStreamToFrame(panelState.frame, active);
        }
    }
}

/**
 * Prefer the real HTML/SVG payload for preview srcdoc.
 * Empty shell is only used while streaming before any markup arrives.
 * (Previously streaming always used an empty root + postMessage, which often
 * painted a blank iframe when the message raced the sandbox bridge.)
 */
function resolveArtifactPreviewCode(artifact) {
    const code = String(artifact?.code || '').trim();
    if (code) return artifact.code;
    if (artifact?.isStreaming) {
        const stream = String(artifact.streamHtml || '').trim();
        if (stream) return artifact.streamHtml;
        return STREAM_PREVIEW_ROOT;
    }
    return artifact?.code || '';
}

function resolveLiveArtifactFontSizePx(settings) {
    const candidate = settings?.live_artifacts_font_size
        ?? state?.settings?.live_artifacts_font_size;
    if (candidate !== undefined && candidate !== null && candidate !== '') {
        return clampLiveArtifactFontSize(candidate);
    }
    if (typeof document !== 'undefined' && document.documentElement) {
        try {
            const cssValue = getComputedStyle(document.documentElement)
                .getPropertyValue('--js-live-artifacts-font-size')
                .trim();
            const cssMatch = cssValue.match(/^(\d+(?:\.\d+)?)px$/i);
            if (cssMatch) {
                return clampLiveArtifactFontSize(cssMatch[1]);
            }
        } catch {
            // getComputedStyle can fail outside a browser document.
        }
    }
    return DEFAULT_LIVE_ARTIFACT_FONT_SIZE;
}

function clampLiveArtifactFontSize(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_LIVE_ARTIFACT_FONT_SIZE;
    return Math.min(
        LIVE_ARTIFACT_FONT_SIZE_MAX,
        Math.max(LIVE_ARTIFACT_FONT_SIZE_MIN, Math.round(parsed)),
    );
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

function extractInlineLiveArtifact(markdownText, messageId, isStreaming, options = {}) {
    const text = String(markdownText || '').trim();
    if (!text) return null;

    const singleFence = extractSingleLiveArtifactFence(text);
    if (singleFence) {
        return createInlineArtifact(singleFence.code, messageId, {
            isStreaming,
            language: singleFence.language === 'svg' ? 'svg' : 'html',
        });
    }

    const streamingFence = isStreaming ? extractStreamingLiveArtifactFence(text) : null;
    if (streamingFence) {
        return createInlineArtifact(streamingFence.code, messageId, {
            isStreaming,
            language: streamingFence.language === 'svg' ? 'svg' : 'html',
        });
    }

    const unfenced = stripFencedCodeBlocks(text).trim();
    if (!unfenced || unfenced !== text) return null;

    if (
        !options.suppressUnfencedInlineArtifact
        && (isStandaloneHtmlArtifact(unfenced) || (isStreaming && isLikelyStreamingHtmlArtifact(unfenced)))
    ) {
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

function extractStreamingLiveArtifactFence(text) {
    const match = text.match(/^```([^\n`]*)\n([\s\S]*)$/);
    if (!match) return null;
    const language = normalizeLanguage(match[1] || '');
    if (language !== LIVE_ARTIFACT_HTML_LANGUAGE && language !== 'html' && language !== 'svg') {
        return null;
    }
    return {
        language,
        code: String(match[2] || '').trimStart(),
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
    const rawCode = String(code || '');
    // Always bake live markup into srcdoc so the iframe is never an empty shell
    // waiting on a racy postMessage. STREAM_PREVIEW_ROOT is only a last-resort
    // placeholder when the stream has started but no markup has arrived yet.
    const previewCode = rawCode.trim()
        ? rawCode
        : (isStreaming ? STREAM_PREVIEW_ROOT : '');
    const title = getArtifactTitle({ info: '', language, code: rawCode }, language, 0);
    const id = `${messageId}-inline-0`;
    return {
        id,
        key: `${messageId}:inline-0`,
        index: 0,
        blockIndex: -1,
        messageId,
        title,
        language,
        fileName: getArtifactFileName(title, language),
        code: rawCode,
        renderable: true,
        supportBlockIndices: [],
        srcdoc: buildSrcdoc(previewCode, language, [], { frameId: id }),
        inline: true,
        isStreaming,
        streamHtml: isStreaming ? rawCode : '',
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
        const id = `${messageId}-raw-${index}`;
        const title = getArtifactTitle({ info: '', language: 'html', code }, 'html', index);
        artifacts.push({
            id,
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
            srcdoc: buildSrcdoc(code, 'html', [], { frameId: id }),
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
    const id = `${messageId}-${index}`;
    const key = `${messageId}:${index}`;
    const code = buildArtifactCode(block, language, cssBlocks, jsBlocks);
    const title = getArtifactTitle(block, language, index);
    const renderable = ARTIFACT_LANGUAGES.has(language);
    const shouldMergeSupport = shouldMergeSupportingBlocks(block, language);

    return {
        id,
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
        srcdoc: renderable ? buildSrcdoc(code, language, [], { frameId: id }) : '',
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
    if (!normalized || /<(?:script|iframe|object|embed)\b/i.test(normalized)) return false;
    const withoutComments = normalized.replace(/<!--[\s\S]*?-->/g, '').trim();
    const withoutTopLevelStyles = stripTopLevelStyleBlocks(withoutComments);
    const fragmentTags = '(?:article|aside|blockquote|button|caption|details|div|figure|figcaption|footer|form|h[1-6]|header|label|li|main|meter|nav|ol|p|progress|section|select|span|summary|table|tbody|td|tfoot|th|thead|tr|ul)';
    const sameRoot = new RegExp(`^<(${fragmentTags})(?:\\s[^>]*)?>[\\s\\S]*<\\/\\1>$`, 'i');
    const container = new RegExp(`^<${fragmentTags}(?:\\s[^>]*)?>[\\s\\S]*<\\/${fragmentTags}>$`, 'i');
    return sameRoot.test(withoutTopLevelStyles) || container.test(withoutTopLevelStyles);
}

function isLikelyStreamingHtmlArtifact(code) {
    const normalized = String(code || '').trim();
    if (!normalized || /<(?:script|iframe|object|embed)\b/i.test(normalized)) return false;
    if (/^(?:<!doctype\s+html\b[^>]*>\s*)?(?:<html\b|<head\b|<body\b)/i.test(normalized)) return true;
    return /^(?:<!--[\s\S]*?-->\s*)?<(?:style|article|aside|blockquote|button|caption|details|div|figure|figcaption|footer|form|h[1-6]|header|label|li|main|meter|nav|ol|p|progress|section|select|span|summary|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>/i.test(normalized);
}

function stripTopLevelStyleBlocks(code) {
    let text = String(code || '').trim();
    const styleBlock = /<style\b[^>]*>[\s\S]*?<\/style>/i;
    while (styleBlock.test(text)) {
        const next = text
            .replace(/^\s*<style\b[^>]*>[\s\S]*?<\/style>\s*/i, '')
            .replace(/\s*<style\b[^>]*>[\s\S]*?<\/style>\s*$/i, '')
            .trim();
        if (next === text) break;
        text = next;
    }
    return text;
}

function shouldMergeSupportingBlocks(block, language) {
    return language === 'html' && !isFullHtmlDocument(block.code);
}

function buildSrcdoc(code, language, sources = [], options = {}) {
    const frameId = String(options.frameId || '');
    const baseFontSize = options.baseFontSize !== undefined
        ? clampLiveArtifactFontSize(options.baseFontSize)
        : resolveLiveArtifactFontSizePx();
    const themeId = options.themeId !== undefined
        ? options.themeId
        : resolveLiveArtifactThemeId();
    let srcdoc;
    if (language === 'svg') {
        srcdoc = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body { margin: 0; background: transparent; color: var(--amc-live-artifact-text, inherit); }
    body { display: grid; place-items: center; padding: 24px; box-sizing: border-box; }
    svg { max-width: 100%; height: auto; }
  </style>
</head>
<body>
${code}
</body>
</html>`;
    } else {
        // 1) rewrite light-only hardcodes  2) materialize theme tokens to concrete colors
        // (AMC injects the same tokens; baking values makes dark mode reliable even if :root is missed)
        srcdoc = materializeLiveArtifactThemeVars(
            adaptArtifactHtmlForTheme(linkArtifactCitationsInHtml(code, sources), themeId),
            themeId,
        );
    }
    // Order mirrors AMC prepareHtmlPreviewSrcDoc: security → theme → font → bridge.
    return injectPreviewSecurityPolicy(
        injectPreviewBridge(
            injectPreviewBaseFontSize(
                injectPreviewTheme(injectPreviewBaseStyles(srcdoc), themeId),
                baseFontSize,
            ),
            frameId,
        ),
    );
}

/**
 * Rebuild artifact.srcdoc from source code with the live app theme.
 * Call at iframe mount so history reloads always match current data-theme.
 */
function ensureArtifactSrcdocTheme(artifact, sources = null) {
    if (!artifact?.renderable) return artifact;
    const themeId = resolveLiveArtifactThemeId();
    const fontSize = resolveLiveArtifactFontSizePx();
    const normalizedSources = sources !== null && sources !== undefined
        ? normalizeArtifactSources(sources)
        : (Array.isArray(artifact.sources) ? artifact.sources : []);
    // Keep streamHtml themed for any secondary postMessage path, but always bake
    // the real payload into srcdoc (never an empty stream shell when markup exists).
    if (artifact.isStreaming && artifact.streamHtml) {
        artifact.streamHtml = materializeLiveArtifactThemeVars(
            adaptArtifactHtmlForTheme(artifact.streamHtml, themeId),
            themeId,
        );
        if (!String(artifact.code || '').trim() && artifact.streamHtml) {
            artifact.code = artifact.streamHtml;
        }
    }
    const previewCode = resolveArtifactPreviewCode(artifact);
    artifact.srcdoc = buildSrcdoc(previewCode, artifact.language, normalizedSources, {
        frameId: artifact.id,
        baseFontSize: fontSize,
        themeId,
    });
    return artifact;
}

function hydrateArtifactCitations(artifact, sources) {
    const normalizedSources = normalizeArtifactSources(sources);
    if (artifact) {
        artifact.sources = normalizedSources;
    }
    if (!artifact?.renderable || artifact.language !== 'html' || normalizedSources.length === 0) {
        return artifact;
    }
    // Keep original artifact.code intact for copy/download; buildSrcdoc links citations.
    if (artifact.isStreaming && artifact.streamHtml) {
        artifact.streamHtml = linkArtifactCitationsInHtml(artifact.streamHtml, normalizedSources);
    }
    const previewCode = resolveArtifactPreviewCode(artifact);
    artifact.srcdoc = buildSrcdoc(previewCode, artifact.language, normalizedSources, { frameId: artifact.id });
    return artifact;
}

function linkArtifactCitationsInHtml(html, sources = []) {
    const raw = String(html || '');
    if (!/\[\d+(?:\s*,\s*\d+)*\]/.test(raw)) return raw;
    const sourceById = buildSourceMap(sources);
    if (sourceById.size === 0 || typeof document === 'undefined') return raw;

    const fullDocument = /(?:<!doctype\s+html\b|<html\b|<head\b|<body\b)/i.test(raw);
    const Parser = (typeof window !== 'undefined' && window.DOMParser) || globalThis.DOMParser;
    if (fullDocument && Parser) {
        const parsed = new Parser().parseFromString(raw, 'text/html');
        linkCitationTextNodes(parsed.body, sourceById);
        const doctype = /^\s*<!doctype\s+html\b/i.test(raw) ? '<!doctype html>\n' : '';
        return `${doctype}${parsed.documentElement.outerHTML}`;
    }

    const template = document.createElement('template');
    template.innerHTML = raw;
    linkCitationTextNodes(template.content, sourceById);
    return template.innerHTML;
}

function buildSourceMap(sources) {
    return new Map(
        normalizeArtifactSources(sources)
            .map(source => [String(source.id), source])
    );
}

function linkCitationTextNodes(root, sourceById) {
    const filter = (typeof NodeFilter !== 'undefined' && NodeFilter)
        || (typeof window !== 'undefined' && window.NodeFilter);
    if (!root || !filter) return;

    const walker = document.createTreeWalker(root, filter.SHOW_TEXT, {
        acceptNode(node) {
            if (!/\[\d+(?:\s*,\s*\d+)*\]/.test(node.textContent || '')) {
                return filter.FILTER_REJECT;
            }
            let parent = node.parentElement;
            while (parent) {
                if (['A', 'CODE', 'PRE', 'SCRIPT', 'STYLE', 'TEXTAREA', 'TITLE', 'NOSCRIPT'].includes(parent.tagName)) {
                    return filter.FILTER_REJECT;
                }
                parent = parent.parentElement;
            }
            return filter.FILTER_ACCEPT;
        }
    });

    const nodes = [];
    while (walker.nextNode()) {
        nodes.push(walker.currentNode);
    }
    nodes.forEach(node => replaceCitationTextNode(node, sourceById));
}

function replaceCitationTextNode(node, sourceById) {
    const text = node.textContent || '';
    const regex = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
    const fragment = document.createDocumentFragment();
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(text)) !== null) {
        if (match.index > lastIndex) {
            fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        }

        const group = document.createElement('span');
        group.className = 'citation-group live-artifact-citation-group';
        const ids = match[1].split(',').map(id => id.trim()).filter(Boolean);
        let linkedCount = 0;

        ids.forEach((id, index) => {
            const source = sourceById.get(id);
            const safeUrl = source ? getSafeSourceUrl(source.url) : '';
            if (source && safeUrl) {
                group.appendChild(createArtifactCitationLink(id, source, safeUrl));
                linkedCount += 1;
            } else {
                group.appendChild(document.createTextNode(`[${id}]`));
            }
            if (index < ids.length - 1) {
                const comma = document.createElement('span');
                comma.textContent = ',';
                comma.setAttribute('aria-hidden', 'true');
                group.appendChild(comma);
            }
        });

        fragment.appendChild(linkedCount > 0 ? group : document.createTextNode(match[0]));
        lastIndex = regex.lastIndex;
    }

    if (lastIndex < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    node.parentNode?.replaceChild(fragment, node);
}

function createArtifactCitationLink(id, source, safeUrl) {
    const anchor = document.createElement('a');
    anchor.href = safeUrl || '#';
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.className = 'citation-link live-artifact-citation-link';
    anchor.dataset.liveArtifactSourceUrl = safeUrl || '';
    anchor.dataset.liveArtifactSourceId = id;
    anchor.dataset.evidenceSourceId = id;
    anchor.title = source.title || source.url || `Source ${id}`;
    anchor.setAttribute('aria-label', `查看来源 ${id} 的原文证据`);
    // Use injected theme tokens so citation chips stay readable in dark mode.
    anchor.setAttribute('style', 'color:var(--amc-live-artifact-accent,#2563eb);text-decoration:none;cursor:pointer;margin:0 1px;font-weight:700;font-size:11px;padding:0 4px;border-radius:6px;background:var(--amc-live-artifact-accent-surface,rgba(37,99,235,.12));display:inline-flex;align-items:center;justify-content:center;vertical-align:super;line-height:16px;min-height:16px;white-space:nowrap;');
    anchor.textContent = id;
    return anchor;
}

function injectPreviewHeadStyle(srcdoc, style) {
    const code = String(srcdoc || '');
    if (!style) return code;
    if (code.includes(PREVIEW_CONTENT_SECURITY_POLICY_META)) {
        return code.replace(PREVIEW_CONTENT_SECURITY_POLICY_META, `${PREVIEW_CONTENT_SECURITY_POLICY_META}${style}`);
    }
    if (/<head\b[^>]*>/i.test(code)) {
        return code.replace(/<head\b[^>]*>/i, headTag => `${headTag}${style}`);
    }
    if (/<html\b[^>]*>/i.test(code)) {
        return code.replace(/<html\b[^>]*>/i, htmlTag => `${htmlTag}<head>${style}</head>`);
    }
    return `<!doctype html><html><head>${style}</head><body>${code}</body></html>`;
}

function injectPreviewBaseStyles(srcdoc) {
    const code = String(srcdoc || '');
    if (code.includes('data-amc-preview-base')) {
        return code;
    }
    return injectPreviewHeadStyle(code, PREVIEW_BASE_STYLES);
}

function resolveLiveArtifactThemeId(settings) {
    // Prefer the live DOM theme — matches what the user actually sees after quick toggle.
    if (typeof document !== 'undefined' && document.documentElement) {
        const attr = document.documentElement.getAttribute('data-theme');
        if (attr === 'dark' || attr === 'light') {
            return attr;
        }
    }
    const explicit = settings?.theme;
    if (explicit === 'dark' || explicit === 'light') {
        return explicit;
    }
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
        try {
            return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        } catch {
            // ignore
        }
    }
    return 'light';
}

function resolveLiveArtifactThemePalette(themeId) {
    const id = themeId === 'dark' ? 'dark' : 'light';
    return LIVE_ARTIFACT_THEME_PALETTES[id] || LIVE_ARTIFACT_THEME_PALETTES.light;
}

/**
 * Rewrite model-hardcoded light-theme colors so dark mode stays readable.
 * Models often emit color:#111 / background:#f5f5f5 while the root stays transparent;
 * theme CSS variables alone cannot override inline style attributes.
 */
function adaptArtifactHtmlForTheme(html, themeId) {
    const raw = String(html || '');
    if (!raw || themeId !== 'dark') return raw;
    if (typeof DOMParser === 'undefined') {
        return adaptArtifactStyleStringForDark(raw);
    }

    try {
        const fullDocument = /(?:<!doctype\s+html\b|<html\b|<head\b|<body\b)/i.test(raw);
        const parsed = new DOMParser().parseFromString(
            fullDocument ? raw : `<div data-amc-theme-adapt-root="true">${raw}</div>`,
            'text/html',
        );
        const scope = fullDocument
            ? parsed.documentElement
            : parsed.body.querySelector('[data-amc-theme-adapt-root="true"]') || parsed.body;

        scope.querySelectorAll('[style]').forEach((node) => {
            const next = adaptArtifactStyleStringForDark(node.getAttribute('style') || '');
            if (next) node.setAttribute('style', next);
            else node.removeAttribute('style');
        });
        scope.querySelectorAll('style').forEach((node) => {
            node.textContent = adaptArtifactStyleStringForDark(node.textContent || '');
        });

        if (fullDocument) {
            const doctype = /^\s*<!doctype\s+html\b/i.test(raw) ? '<!doctype html>\n' : '';
            return `${doctype}${parsed.documentElement.outerHTML}`;
        }
        const root = parsed.body.querySelector('[data-amc-theme-adapt-root="true"]');
        return root ? root.innerHTML : parsed.body.innerHTML;
    } catch {
        return adaptArtifactStyleStringForDark(raw);
    }
}

/**
 * Expand AMC theme tokens in artifact HTML to concrete colors for the active theme.
 * Guarantees dark mode surfaces are zinc-900 (#18181b), never unresolved light fallbacks.
 */
function materializeLiveArtifactThemeVars(html, themeId) {
    const raw = String(html || '');
    if (!raw || !raw.includes('--amc-live-artifact-')) return raw;
    const colors = resolveLiveArtifactThemePalette(themeId);
    const tokenMap = {
        '--amc-live-artifact-text': colors.text,
        '--amc-live-artifact-muted': colors.muted,
        '--amc-live-artifact-subtle': colors.subtle,
        '--amc-live-artifact-surface': colors.surface,
        '--amc-live-artifact-surface-muted': colors.surfaceMuted,
        '--amc-live-artifact-border': colors.border,
        '--amc-live-artifact-accent': colors.accent,
        '--amc-live-artifact-accent-surface': colors.accentSurface,
        '--amc-live-artifact-success': colors.success,
        '--amc-live-artifact-danger': colors.danger,
        '--amc-live-artifact-warning': colors.warning,
    };
    return raw.replace(
        /var\(\s*(--amc-live-artifact-[\w-]+)\s*(?:,[^)]+)?\)/gi,
        (match, tokenName) => {
            const key = String(tokenName || '').toLowerCase();
            return tokenMap[key] || match;
        },
    );
}

function adaptArtifactStyleStringForDark(styleText) {
    const input = String(styleText || '');
    if (!input || !/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|\b(white|black|gray|grey)\b/i.test(input)) {
        return input;
    }

    // Match property:value in bare style attrs and full HTML/CSS snippets.
    // Includes border shorthands like "border-bottom:1px solid #ddd".
    return input.replace(
        /(^|;\s*|[\s{"'])((?:background-color|background|border-color|border-top-color|border-right-color|border-bottom-color|border-left-color|border-top|border-right|border-bottom|border-left|border|outline-color|outline|color|fill|stroke))\s*:\s*([^;{}"']+)/gi,
        (match, prefix, prop, value) => {
            const trimmed = value.trim();
            const propName = prop.trim().toLowerCase();
            const colorToken = trimmed.match(/(#[0-9a-f]{3,8}|rgba?\([^)]+\)|hsla?\([^)]+\)|\b(?:white|black|gray|grey)\b)/i);

            // border / border-left / outline shorthands: rewrite only the light gray color token.
            if (/^border(?:-top|-right|-bottom|-left)?$/.test(propName) || propName === 'outline') {
                if (!colorToken) return match;
                const mappedBorder = mapHardcodedColorForDarkTheme('border-color', colorToken[1]);
                if (!mappedBorder) return match;
                return `${prefix}${prop}: ${trimmed.replace(colorToken[1], mappedBorder)}`;
            }

            // Skip multi-value backgrounds like "url(...) #fff".
            if (/\burl\s*\(/i.test(trimmed) || (/\s/.test(trimmed) && !/^(rgba?|hsla?)\(/i.test(trimmed))) {
                if (!colorToken || propName !== 'background') return match;
                const mappedBg = mapHardcodedColorForDarkTheme('background-color', colorToken[1]);
                if (!mappedBg) return match;
                return `${prefix}${prop}: ${mappedBg}`;
            }

            const mapped = mapHardcodedColorForDarkTheme(propName, trimmed);
            if (!mapped) return match;
            return `${prefix}${prop}: ${mapped}`;
        },
    );
}

function mapHardcodedColorForDarkTheme(property, value) {
    const parsed = parseCssColorValue(value);
    if (!parsed) return null;

    const { r, g, b, a } = parsed;
    if (a < 0.08) return null;

    const lum = relativeLuminance(r, g, b);
    const isBg = property === 'background' || property === 'background-color';
    const isBorder = property.startsWith('border') || property === 'outline-color';
    const isText = property === 'color' || property === 'fill' || property === 'stroke';
    const chroma = Math.max(r, g, b) - Math.min(r, g, b);

    if (isBg) {
        // Near-white / pale tinted surfaces → dark surfaces (keep a hint of hue when chromatic).
        if (lum >= 0.72) {
            if (chroma < 25) return 'var(--amc-live-artifact-surface)';
            // Pale blue/amber callouts → accent-tinted surface.
            return 'var(--amc-live-artifact-accent-surface)';
        }
        if (lum >= 0.55 && chroma < 20) return 'var(--amc-live-artifact-surface-muted)';
        return null;
    }

    if (isBorder) {
        if (lum >= 0.55) return 'var(--amc-live-artifact-border)';
        return null;
    }

    if (isText) {
        // Dark saturated brand blues/greens used as emphasis on light cards.
        if (chroma >= 40 && lum <= 0.55) return 'var(--amc-live-artifact-accent)';
        // Near-black body text (#000/#111/#333) → primary text token.
        if (lum <= 0.12 && chroma < 40) return 'var(--amc-live-artifact-text)';
        // Mid gray muted labels (#666/#888/#999).
        if (lum > 0.12 && lum < 0.65 && chroma < 30) return 'var(--amc-live-artifact-muted)';
        return null;
    }

    return null;
}

function parseCssColorValue(value) {
    const raw = String(value || '').trim().toLowerCase();
    if (!raw || raw.startsWith('var(') || raw === 'transparent' || raw === 'inherit' || raw === 'currentcolor') {
        return null;
    }
    if (raw === 'white') return { r: 255, g: 255, b: 255, a: 1 };
    if (raw === 'black') return { r: 0, g: 0, b: 0, a: 1 };
    if (raw === 'gray' || raw === 'grey') return { r: 128, g: 128, b: 128, a: 1 };

    const hex = raw.match(/^#([0-9a-f]{3,8})$/i);
    if (hex) {
        let h = hex[1];
        if (h.length === 3 || h.length === 4) {
            h = h.split('').map((ch) => ch + ch).join('');
        }
        if (h.length === 6 || h.length === 8) {
            const r = parseInt(h.slice(0, 2), 16);
            const g = parseInt(h.slice(2, 4), 16);
            const b = parseInt(h.slice(4, 6), 16);
            const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
            if ([r, g, b].every(Number.isFinite)) return { r, g, b, a: Number.isFinite(a) ? a : 1 };
        }
        return null;
    }

    const rgb = raw.match(/^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*([0-9.]+))?\s*\)$/);
    if (rgb) {
        return {
            r: Math.min(255, Math.max(0, Number(rgb[1]))),
            g: Math.min(255, Math.max(0, Number(rgb[2]))),
            b: Math.min(255, Math.max(0, Number(rgb[3]))),
            a: rgb[4] === undefined ? 1 : Math.min(1, Math.max(0, Number(rgb[4]))),
        };
    }
    return null;
}

function relativeLuminance(r, g, b) {
    const toLinear = (c) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

/**
 * Build injected theme CSS for Live Artifact iframes (AMC-compatible token names).
 * Transparent root + themed text, matching AMC previewDocument.buildPreviewThemeStyle.
 */
function buildPreviewThemeStyle(themeId) {
    const colors = resolveLiveArtifactThemePalette(themeId);
    // Mirror AMC: transparent html/body, tokenized text. Concrete hex also set so
    // unresolved var() never falls back to the browser's default white canvas look.
    return `<style ${PREVIEW_THEME_ATTRIBUTE}="true">:root,html{color-scheme:${colors.colorScheme};--amc-live-artifact-text:${colors.text};--amc-live-artifact-muted:${colors.muted};--amc-live-artifact-subtle:${colors.subtle};--amc-live-artifact-surface:${colors.surface};--amc-live-artifact-surface-muted:${colors.surfaceMuted};--amc-live-artifact-border:${colors.border};--amc-live-artifact-accent:${colors.accent};--amc-live-artifact-accent-surface:${colors.accentSurface};--amc-live-artifact-success:${colors.success};--amc-live-artifact-danger:${colors.danger};--amc-live-artifact-warning:${colors.warning};}html,body{margin:0;padding:0;background:transparent!important;color:${colors.text}!important;}body{overflow-x:auto;color:${colors.text};}h1,h2,h3,h4,h5,h6,p,li,td,th,summary,label,span,a,strong,em,small,div,section,article,aside,header,footer,main,ul,ol,table{color:inherit;}</style>`;
}

function injectPreviewTheme(srcdoc, themeId) {
    const code = String(srcdoc || '');
    if (code.includes(PREVIEW_THEME_ATTRIBUTE)) {
        return code;
    }
    return injectPreviewHeadStyle(code, buildPreviewThemeStyle(themeId));
}

function buildPreviewBaseFontSizeStyle(baseFontSize) {
    const fontSize = clampLiveArtifactFontSize(baseFontSize);
    return `<style ${PREVIEW_BASE_FONT_SIZE_ATTRIBUTE}="true">:root{--amc-live-artifact-font-size:${fontSize}px;font-size:var(--amc-live-artifact-font-size);}body{font-size:var(--amc-live-artifact-font-size);}</style>`;
}

function injectPreviewBaseFontSize(srcdoc, baseFontSize) {
    const code = String(srcdoc || '');
    if (code.includes(PREVIEW_BASE_FONT_SIZE_ATTRIBUTE)) {
        return code;
    }
    return injectPreviewHeadStyle(code, buildPreviewBaseFontSizeStyle(baseFontSize));
}

function injectPreviewBridge(code, frameId = '') {
    const safeFrameId = JSON.stringify(String(frameId || ''));
    // Bridge resize logic aligned with AMC-WebUI previewBridgeScript.ts:
    // simple scrollHeight measurement + rAF-debounced scheduleResize.
    const bridge = `<script>
(() => {
  const MIN_HEIGHT = ${INLINE_ARTIFACT_MIN_HEIGHT};
  const FRAME_ID = ${safeFrameId};
  const notifyResize = () => {
    try {
      const body = document.body;
      const root = document.documentElement;
      // Neutralize common full-viewport locks before measuring (AMC-compatible + 100vh fix).
      if (root) {
        root.style.setProperty('height', 'auto', 'important');
        root.style.setProperty('max-height', 'none', 'important');
      }
      if (body) {
        body.style.setProperty('height', 'auto', 'important');
        body.style.setProperty('max-height', 'none', 'important');
        body.style.setProperty('overflow-y', 'visible', 'important');
        Array.from(body.children).forEach((el) => {
          if (!(el instanceof Element)) return;
          if (['SCRIPT','STYLE','LINK','META','NOSCRIPT','TEMPLATE'].includes(el.tagName)) return;
          el.style.setProperty('height', 'auto', 'important');
          el.style.setProperty('max-height', 'none', 'important');
          el.style.setProperty('overflow', 'visible', 'important');
        });
      }
      const height = Math.max(
        MIN_HEIGHT,
        body ? body.scrollHeight : 0,
        body ? body.offsetHeight : 0,
        root ? root.scrollHeight : 0,
        root ? root.offsetHeight : 0
      );
      parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'resize', height, frameId: FRAME_ID }, '*');
    } catch {}
  };
  let resizeFrame = 0;
  const scheduleResize = () => {
    if (resizeFrame) return;
    if (typeof requestAnimationFrame !== 'function') {
      notifyResize();
      return;
    }
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      notifyResize();
    });
  };
  const notifyReady = () => {
    try {
      parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'ready', frameId: FRAME_ID }, '*');
    } catch {}
    scheduleResize();
    setTimeout(notifyResize, 50);
    setTimeout(notifyResize, 250);
  };
  // Citation chips open the parent evidence panel instead of navigating away.
  document.addEventListener('click', (event) => {
    try {
      const anchor = event.target && event.target.closest
        ? event.target.closest('a.live-artifact-citation-link, a.citation-link')
        : null;
      if (!anchor) return;
      const sourceId = anchor.getAttribute('data-live-artifact-source-id')
        || anchor.getAttribute('data-evidence-source-id')
        || (anchor.textContent || '').trim();
      if (!sourceId) return;
      event.preventDefault();
      event.stopPropagation();
      parent.postMessage({
        channel: 'justsearch-live-artifacts',
        event: 'citation-click',
        sourceId: sourceId,
        url: anchor.getAttribute('data-live-artifact-source-url') || anchor.href || '',
        title: anchor.getAttribute('title') || '',
        frameId: FRAME_ID,
      }, '*');
    } catch {}
  }, true);
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
  if (document.readyState === 'complete') {
    Promise.resolve().then(notifyReady);
  } else {
    window.addEventListener('load', notifyReady, { once: true });
  }
  window.addEventListener('resize', scheduleResize);
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(scheduleResize);
    if (document.documentElement) observer.observe(document.documentElement);
    if (document.body) observer.observe(document.body);
  }
  if ('MutationObserver' in window) {
    const observer = new MutationObserver(scheduleResize);
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
    scheduleResize();
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
  const openSourceUrl = (url) => {
    if (!/^https?:\\/\\//i.test(url)) return false;
    try {
      const opened = window.open(url, '_blank');
      if (opened) {
        try { opened.opener = null; } catch {}
        return true;
      }
    } catch {}
    try {
      parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'open-source', url }, '*');
    } catch {}
    return true;
  };
  document.addEventListener('click', (event) => {
    const sourceLink = event.target.closest?.('[data-live-artifact-source-url]');
    if (sourceLink) {
      const url = sourceLink.getAttribute('data-live-artifact-source-url') || '';
      const href = sourceLink.getAttribute('href') || '';
      if (sourceLink.tagName === 'A' && /^https?:\/\//i.test(href)) {
        event.preventDefault();
        openSourceUrl(href);
        return;
      }
      if (url) {
        event.preventDefault();
        openSourceUrl(url);
      }
      return;
    }
    const trigger = event.target.closest?.('[data-amc-followup]');
    if (!trigger) return;
    const payload = parsePayload(trigger.getAttribute('data-amc-followup') || '');
    if (!payload) return;
    event.preventDefault();
    parent.postMessage({ channel: 'justsearch-live-artifacts', event: 'followup', payload: mergeState(payload, collectState(trigger)) }, '*');
  });
  Promise.resolve().then(scheduleResize);
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
    const text = String(value || '')
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/<[^>]*>/g, '');
    return decodeHtmlEntities(text).replace(/\s+/g, ' ').trim();
}

function decodeHtmlEntities(value) {
    return String(value || '').replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (match, entity) => {
        const normalized = entity.toLowerCase();
        if (normalized.startsWith('#x')) {
            const codePoint = Number.parseInt(normalized.slice(2), 16);
            return Number.isFinite(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
                ? String.fromCodePoint(codePoint)
                : match;
        }
        if (normalized.startsWith('#')) {
            const codePoint = Number.parseInt(normalized.slice(1), 10);
            return Number.isFinite(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
                ? String.fromCodePoint(codePoint)
                : match;
        }
        return {
            amp: '&',
            lt: '<',
            gt: '>',
            quot: '"',
            apos: "'",
            nbsp: ' ',
        }[normalized] ?? match;
    });
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
    container.querySelectorAll('.live-artifact-source-strip').forEach(el => el.remove());
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

function hashArtifactContent(value) {
    const text = String(value || '');
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) {
        hash = (hash * 31 + text.charCodeAt(i)) | 0;
    }
    return `${text.length}:${(hash >>> 0).toString(36)}`;
}

function getArtifactHeightCacheKey(artifact) {
    const html = artifact?.isStreaming ? (artifact.streamHtml || artifact.code || '') : (artifact?.code || '');
    const contentHash = hashArtifactContent(html);
    if (artifact?.isStreaming) {
        return `stream:${artifact.id || 'inline'}`;
    }
    return artifact?.id ? `${artifact.id}:${contentHash}` : `html:${contentHash}`;
}

function readCachedFrameHeight(cacheKey, fallbackKey = '') {
    return (
        frameHeightCache.get(cacheKey)
        ?? (fallbackKey ? frameHeightCache.get(fallbackKey) : undefined)
        ?? INLINE_ARTIFACT_DEFAULT_HEIGHT
    );
}

function cacheFrameHeight(cacheKey, height) {
    if (!cacheKey) return;
    if (frameHeightCache.has(cacheKey)) {
        frameHeightCache.delete(cacheKey);
    }
    frameHeightCache.set(cacheKey, height);
    if (frameHeightCache.size > FRAME_HEIGHT_CACHE_MAX) {
        const oldestKey = frameHeightCache.keys().next().value;
        if (oldestKey) frameHeightCache.delete(oldestKey);
    }
}

/**
 * Parent-side height probe (AMC-WebUI createStaticPreviewSnapshotContainer pattern).
 * Does not depend on sandboxed iframe postMessage, so short-box failures recover reliably.
 */
function measureArtifactContentHeight(html, widthPx) {
    if (typeof document === 'undefined') return INLINE_ARTIFACT_DEFAULT_HEIGHT;
    const width = Math.max(280, Math.floor(Number(widthPx) || 680));
    const probe = document.createElement('div');
    probe.setAttribute('data-amc-height-probe', 'true');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.cssText = [
        'position:absolute',
        'left:-100000px',
        'top:0',
        `width:${width}px`,
        'visibility:hidden',
        'pointer-events:none',
        'box-sizing:border-box',
        'overflow:visible',
        'background:transparent',
    ].join(';');

    try {
        const raw = String(html || '').trim();
        if (!raw) return INLINE_ARTIFACT_DEFAULT_HEIGHT;

        if (typeof DOMParser !== 'undefined') {
            const parsed = new DOMParser().parseFromString(raw, 'text/html');
            parsed.querySelectorAll('script, iframe, object, embed').forEach(node => node.remove());
            parsed.querySelectorAll('*').forEach((node) => {
                Array.from(node.attributes).forEach((attribute) => {
                    if (/^on/i.test(attribute.name) || attribute.name === 'srcdoc') {
                        node.removeAttribute(attribute.name);
                    }
                });
                // Unclip model-generated full-viewport shells for accurate measurement.
                const style = node.getAttribute('style') || '';
                if (/max-height|height\s*:\s*\d+vh|height\s*:\s*100%|overflow\s*:\s*(auto|scroll|hidden)/i.test(style)) {
                    node.style.setProperty('max-height', 'none', 'important');
                    node.style.setProperty('height', 'auto', 'important');
                    node.style.setProperty('overflow', 'visible', 'important');
                }
            });
            parsed.head.querySelectorAll('style, link[rel="stylesheet"]').forEach((node) => {
                probe.appendChild(document.importNode(node, true));
            });
            Array.from(parsed.body.childNodes).forEach((node) => {
                probe.appendChild(document.importNode(node, true));
            });
        } else {
            probe.innerHTML = raw;
        }

        document.body.appendChild(probe);
        let height = Math.ceil(Math.max(probe.scrollHeight || 0, probe.offsetHeight || 0));
        // jsdom / pre-layout environments often report 0; estimate from structure as a floor.
        if (height <= INLINE_ARTIFACT_MIN_HEIGHT) {
            const textLength = (probe.textContent || '').replace(/\s+/g, ' ').trim().length;
            const blockCount = probe.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,tr,pre,blockquote,section,article,div').length;
            const estimated = Math.ceil(textLength / 42) * 22 + blockCount * 28 + 64;
            height = Math.max(height, estimated);
        }
        probe.remove();
        return Math.min(
            INLINE_ARTIFACT_MAX_HEIGHT,
            Math.max(INLINE_ARTIFACT_MIN_HEIGHT, height || INLINE_ARTIFACT_DEFAULT_HEIGHT),
        );
    } catch {
        try { probe.remove(); } catch { /* ignore */ }
        return INLINE_ARTIFACT_DEFAULT_HEIGHT;
    }
}

function resolveInlineFrameWidth(viewport, container) {
    const width = viewport?.clientWidth
        || viewport?.getBoundingClientRect?.().width
        || container?.clientWidth
        || container?.getBoundingClientRect?.().width
        || 680;
    return Math.max(280, Math.floor(width));
}

function syncInlineArtifactFrameHeight(viewport, frame, artifact, container) {
    const cacheKey = getArtifactHeightCacheKey(artifact);
    const contentHtml = artifact.isStreaming ? (artifact.streamHtml || artifact.code || '') : (artifact.code || '');
    const width = resolveInlineFrameWidth(viewport, container);
    const probed = measureArtifactContentHeight(contentHtml, width);
    const cached = readCachedFrameHeight(cacheKey);
    // Prefer the larger of probe vs cache so streaming growth and remounts stay tall.
    const nextHeight = Math.max(probed, cached, INLINE_ARTIFACT_MIN_HEIGHT);
    cacheFrameHeight(cacheKey, nextHeight);
    if (artifact.id) {
        cacheFrameHeight(`stream:${artifact.id}`, nextHeight);
    }
    applyInlineArtifactFrameHeight(viewport, frame, nextHeight, {
        allowShrink: !artifact.isStreaming,
    });
    return nextHeight;
}

function renderInlineArtifactFrame(container, artifact) {
    // Always re-bake srcdoc against the live data-theme (AMC rebuilds when themeId changes).
    ensureArtifactSrcdocTheme(artifact);

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
        // Match AMC-WebUI ArtifactFrame sandbox (no allow-same-origin).
        frame.setAttribute('sandbox', 'allow-scripts allow-forms');
        frame.setAttribute('scrolling', 'no');
        // Hint the browser's built-in form/scroll styling for the active scheme.
        frame.style.colorScheme = resolveLiveArtifactThemeId();
        frame.addEventListener('load', () => {
            // After every srcdoc navigation, re-push pending stream HTML and remeasure.
            // Sandboxed frames often drop postMessages sent before the bridge listens.
            const frameId = frame.dataset.liveArtifactFrameId || '';
            const liveArtifact = frameId ? registry.get(frameId) : null;
            if (liveArtifact) {
                syncPendingStreamToFrame(frame, liveArtifact);
            } else if (frame.dataset.liveArtifactStreaming === 'true' && frame.dataset.liveArtifactProbeHtml) {
                postInlineArtifactStream(frame, frame.dataset.liveArtifactProbeHtml);
            }
            scheduleInlineArtifactFrameResize(frame, viewport);
            const html = frame.dataset.liveArtifactProbeHtml || '';
            if (html) {
                const height = measureArtifactContentHeight(html, resolveInlineFrameWidth(viewport, container));
                applyInlineArtifactFrameHeight(viewport, frame, height, {
                    allowShrink: frame.dataset.liveArtifactStreaming !== 'true',
                });
                cacheFrameHeight(frame.dataset.liveArtifactHeightKey || '', height);
            }
        });

        viewport.appendChild(frame);
        frameShell.appendChild(viewport);
        container.appendChild(frameShell);
    }

    const heightKey = getArtifactHeightCacheKey(artifact);
    const contentHtml = artifact.isStreaming ? (artifact.streamHtml || artifact.code || '') : (artifact.code || '');
    frame.dataset.liveArtifactFrameId = artifact.id || '';
    frame.dataset.liveArtifactStreaming = artifact.isStreaming ? 'true' : 'false';
    frame.dataset.liveArtifactHeightKey = heightKey;
    frame.dataset.liveArtifactProbeHtml = contentHtml;

    // Parent-side height first (reliable), then iframe postMessage can only grow further.
    syncInlineArtifactFrameHeight(viewport, frame, artifact, container);

    if (frame.srcdoc !== artifact.srcdoc) {
        frame.srcdoc = artifact.srcdoc;
    }
    // Secondary path: if the baked srcdoc still uses the empty stream shell, push HTML
    // via postMessage (with retries). Primary path already embeds markup in srcdoc.
    syncPendingStreamToFrame(frame, artifact);
    scheduleInlineArtifactFrameResize(frame, viewport);
}

function syncPendingStreamToFrame(frame, artifact) {
    if (!frame || !artifact?.isStreaming) return;
    const html = artifact.streamHtml || artifact.code || '';
    if (!html || !String(html).trim()) return;
    // Only postMessage when srcdoc is still the empty stream shell; otherwise content
    // is already baked in and a reload will show it without messaging.
    const srcdoc = frame.srcdoc || artifact.srcdoc || '';
    if (srcdoc.includes('data-amc-stream-preview-root="true"') && !String(artifact.code || '').trim()) {
        postInlineArtifactStream(frame, html);
        return;
    }
    // Also re-push when the shell is present as a wrapper (legacy/partial docs).
    if (srcdoc.includes('data-amc-stream-preview-root="true"') && String(html).trim()) {
        postInlineArtifactStream(frame, html);
    }
}

function postInlineArtifactStream(frame, html) {
    if (!frame || typeof html !== 'string' || !html.trim()) return;
    const themeId = resolveLiveArtifactThemeId();
    const adaptedHtml = materializeLiveArtifactThemeVars(
        adaptArtifactHtmlForTheme(html, themeId),
        themeId,
    );
    frame.dataset.liveArtifactPendingStreamHtml = adaptedHtml;
    const send = () => {
        try {
            frame.contentWindow?.postMessage({
                channel: 'justsearch-live-artifacts',
                event: STREAM_RENDER_EVENT,
                html: adaptedHtml,
            }, '*');
        } catch {
            // Ignore frame messaging failures while the iframe is mounting.
        }
    };
    // Retries cover the common race where srcdoc navigation has not installed the
    // bridge listener yet (setTimeout(0) alone is often too early).
    send();
    setTimeout(send, 0);
    setTimeout(send, 50);
    setTimeout(send, 150);
    setTimeout(send, 400);
}

function normalizeArtifactSources(sources) {
    const sourceList = Array.isArray(sources) ? sources : (() => {
        if (typeof sources !== 'string') return [];
        try {
            const parsed = JSON.parse(sources);
            return Array.isArray(parsed) ? parsed : [];
        } catch {
            return [];
        }
    })();

    return sourceList
        .map((source, index) => {
            if (typeof source === 'string') {
                const url = source.trim();
                return {
                    id: String(index + 1),
                    title: url || `Source ${index + 1}`,
                    url,
                };
            }
            return {
                id: String(source?.id ?? index + 1).trim() || String(index + 1),
                title: String(source?.title || source?.url || `Source ${index + 1}`).replace(/\s+/g, ' ').trim(),
                url: String(source?.url || '').trim(),
            };
        })
        .filter(source => source.title || source.url);
}

function getSafeSourceUrl(url) {
    try {
        const raw = String(url || '').trim();
        if (!raw) return '';

        let candidate = raw;
        if (raw.startsWith('//')) {
            candidate = `https:${raw}`;
        } else if (!/^[a-z][a-z0-9+.-]*:/i.test(raw) && /^[^\s/?#]+\.[^\s]+/.test(raw)) {
            candidate = `https://${raw}`;
        }

        const parsed = new URL(candidate);
        return ['http:', 'https:'].includes(parsed.protocol) ? candidate : '';
    } catch {
        return '';
    }
}

function getSourceHost(url) {
    try {
        return new URL(url).hostname.replace(/^www\./, '');
    } catch {
        return '';
    }
}

function getCitedSourceIds(code) {
    const ids = new Set();
    const regex = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
    let match;
    while ((match = regex.exec(String(code || ''))) !== null) {
        match[1].split(',').forEach(id => {
            const trimmed = id.trim();
            if (trimmed) ids.add(trimmed);
        });
    }
    return ids;
}

function selectArtifactSources(artifact, sources) {
    if (!artifact || sources.length === 0) return [];
    const citedIds = getCitedSourceIds(artifact.code);
    const selected = citedIds.size > 0
        ? sources.filter(source => citedIds.has(String(source.id)))
        : sources;
    return selected.slice(0, 8);
}

function renderLiveArtifactSources(container, artifact, sources) {
    container.querySelectorAll('.live-artifact-source-strip').forEach(el => el.remove());
    const selected = selectArtifactSources(artifact, sources);
    if (selected.length === 0) return;

    const strip = document.createElement('div');
    strip.className = 'live-artifact-source-strip';
    strip.setAttribute('aria-label', '搜索来源');

    const header = document.createElement('div');
    header.className = 'live-artifact-source-header';
    const icon = document.createElement('span');
    icon.className = 'material-symbols-rounded';
    icon.textContent = 'travel_explore';
    const label = document.createElement('span');
    label.textContent = '搜索来源';
    const count = document.createElement('span');
    count.className = 'live-artifact-source-count';
    count.textContent = `${selected.length} 个`;
    header.append(icon, label, count);
    strip.appendChild(header);

    const list = document.createElement('div');
    list.className = 'live-artifact-source-list';
    selected.forEach((source) => {
        const safeUrl = getSafeSourceUrl(source.url);
        const item = safeUrl ? document.createElement('a') : document.createElement('span');
        item.className = safeUrl ? 'live-artifact-source-chip' : 'live-artifact-source-chip is-disabled';
        if (safeUrl) {
            item.href = safeUrl;
            item.target = '_blank';
            item.rel = 'noopener noreferrer';
        }

        const id = document.createElement('span');
        id.className = 'live-artifact-source-id';
        id.textContent = `[${source.id}]`;
        const title = document.createElement('span');
        title.className = 'live-artifact-source-title';
        title.textContent = source.title || source.url || `Source ${source.id}`;
        item.title = title.textContent;

        item.append(id, title);
        const host = safeUrl ? getSourceHost(safeUrl) : '';
        if (host) {
            const hostEl = document.createElement('span');
            hostEl.className = 'live-artifact-source-host';
            hostEl.textContent = host;
            item.appendChild(hostEl);
        }
        list.appendChild(item);
    });
    strip.appendChild(list);
    container.appendChild(strip);
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

function applyInlineArtifactFrameHeight(viewport, frame, height, { allowShrink = true } = {}) {
    const requested = Math.max(INLINE_ARTIFACT_MIN_HEIGHT, Math.ceil(Number(height) || 0));
    const capped = Math.min(INLINE_ARTIFACT_MAX_HEIGHT, requested);
    const current = Math.max(
        parseInt(frame?.style?.height, 10) || 0,
        parseInt(viewport?.style?.height, 10) || 0,
        0,
    );
    // Match AMC-WebUI: while streaming / recovering, never collapse below current height.
    const nextHeight = allowShrink ? capped : Math.max(current || INLINE_ARTIFACT_DEFAULT_HEIGHT, capped);
    const next = `${nextHeight}px`;
    if (viewport) {
        viewport.style.height = next;
        viewport.style.minHeight = `${INLINE_ARTIFACT_MIN_HEIGHT}px`;
        viewport.style.overflow = 'hidden';
    }
    if (frame) {
        // AMC uses h-full of the viewport; set explicit px so sandbox iframe always fills.
        frame.style.height = next;
        frame.style.minHeight = `${INLINE_ARTIFACT_MIN_HEIGHT}px`;
    }
    if (frame?.dataset?.liveArtifactHeightKey) {
        cacheFrameHeight(frame.dataset.liveArtifactHeightKey, nextHeight);
    }
    return nextHeight;
}

function measureInlineArtifactDocumentHeight(doc) {
    if (!doc) return INLINE_ARTIFACT_MIN_HEIGHT;
    const body = doc.body;
    const root = doc.documentElement;
    if (!body || !root) return INLINE_ARTIFACT_MIN_HEIGHT;

    try {
        [root, body].forEach((el) => {
            el.style.setProperty('height', 'auto', 'important');
            el.style.setProperty('min-height', '0', 'important');
            el.style.setProperty('max-height', 'none', 'important');
            el.style.setProperty('overflow-y', 'visible', 'important');
        });
    } catch {
        // Ignore style writes if the frame document is unavailable mid-navigation.
    }

    let contentBottom = 0;
    const skip = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE']);
    const visit = (el) => {
        if (!(el instanceof Element) || skip.has(el.tagName)) return;
        const rect = el.getBoundingClientRect();
        if (!rect || (rect.width === 0 && rect.height === 0 && el.childElementCount === 0)) return;
        let marginBottom = 0;
        try {
            marginBottom = parseFloat(doc.defaultView?.getComputedStyle(el)?.marginBottom) || 0;
        } catch {
            marginBottom = 0;
        }
        contentBottom = Math.max(contentBottom, rect.bottom + marginBottom);
    };
    Array.from(body.children).forEach(visit);

    const scrollY = doc.defaultView?.pageYOffset || root.scrollTop || body.scrollTop || 0;
    return Math.max(
        Math.ceil(contentBottom + scrollY),
        body.scrollHeight || 0,
        body.offsetHeight || 0,
        root.scrollHeight || 0,
        root.offsetHeight || 0,
        INLINE_ARTIFACT_MIN_HEIGHT,
    );
}

function resizeInlineArtifactFrame(frame, viewport) {
    if (!frame || !viewport) return;
    try {
        const doc = frame.contentDocument;
        // Sandboxed frames without allow-same-origin cannot be read; bridge postMessage handles those.
        if (!doc) return;
        applyInlineArtifactFrameHeight(viewport, frame, measureInlineArtifactDocumentHeight(doc));
    } catch {
        // Keep last good height; bridge resize events remain the primary path.
    }
}

function scheduleInlineArtifactFrameResize(frame, viewport) {
    if (!frame || !viewport) return;
    const run = () => resizeInlineArtifactFrame(frame, viewport);
    run();
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => {
            run();
            requestAnimationFrame(run);
        });
    }
    setTimeout(run, 50);
    setTimeout(run, 250);
    setTimeout(run, 1000);
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
    const sourceFrame = findArtifactFrameByMessage(event);
    if (!sourceFrame) return;

    if (data.event === 'ready') {
        // Bridge is listening — re-deliver any pending stream HTML that raced load.
        const frame = sourceFrame.frame;
        const frameId = frame.dataset.liveArtifactFrameId || '';
        const artifact = frameId ? registry.get(frameId) : null;
        if (artifact) {
            syncPendingStreamToFrame(frame, artifact);
        } else if (frame.dataset.liveArtifactPendingStreamHtml) {
            postInlineArtifactStream(frame, frame.dataset.liveArtifactPendingStreamHtml);
        } else if (frame.dataset.liveArtifactStreaming === 'true' && frame.dataset.liveArtifactProbeHtml) {
            postInlineArtifactStream(frame, frame.dataset.liveArtifactProbeHtml);
        }
        if (sourceFrame.kind === 'inline') {
            const viewport = frame.closest('.live-artifact-inline-viewport');
            scheduleInlineArtifactFrameResize(frame, viewport);
        }
        return;
    }

    if (data.event === 'resize' && typeof data.height === 'number' && Number.isFinite(data.height)) {
        if (sourceFrame.kind === 'inline') {
            const viewport = sourceFrame.frame.closest('.live-artifact-inline-viewport');
            const streaming = sourceFrame.frame.dataset.liveArtifactStreaming === 'true';
            // AMC-WebUI only grows from bridge measurements when streaming; final answers may shrink.
            applyInlineArtifactFrameHeight(viewport, sourceFrame.frame, data.height, {
                allowShrink: !streaming,
            });
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
        return;
    }

    if (data.event === 'open-source') {
        openArtifactSourceUrl(data.url);
        return;
    }

    if (data.event === 'diagnostic') {
        handlePreviewDiagnostic(data.payload);
    }
}

function findArtifactFrameByMessage(event) {
    const data = event?.data || {};
    const frameId = typeof data.frameId === 'string' ? data.frameId.trim() : '';
    if (frameId) {
        const byId = Array.from(document.querySelectorAll('.live-artifact-inline-iframe'))
            .find(frame => frame.dataset.liveArtifactFrameId === frameId);
        if (byId) {
            return { frame: byId, kind: 'inline' };
        }
    }
    return findArtifactFrameByMessageSource(event?.source);
}

function findArtifactFrameByMessageSource(source) {
    if (!source) return null;

    const inlineFrame = Array.from(document.querySelectorAll('.live-artifact-inline-iframe'))
        .find(frame => frame.contentWindow === source);
    if (inlineFrame) {
        return { frame: inlineFrame, kind: 'inline' };
    }

    if (panelState?.frame?.contentWindow === source) {
        return { frame: panelState.frame, kind: 'panel' };
    }

    return null;
}

function openArtifactSourceUrl(url) {
    const safeUrl = getSafeSourceUrl(url);
    if (!safeUrl) {
        showToast('来源链接无效，已阻止打开', 'warning', 4000);
        return;
    }
    window.open(safeUrl, '_blank', 'noopener,noreferrer');
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

    ensureArtifactSrcdocTheme(artifact);
    panelState.title.textContent = artifact.title;
    panelState.meta.textContent = `${artifact.language.toUpperCase()} · ${artifact.fileName}`;
    panelState.code.textContent = artifact.code;

    const canPreview = Boolean(artifact.renderable && artifact.srcdoc);
    panelState.frame.hidden = !canPreview;
    panelState.empty.hidden = canPreview;
    if (canPreview) {
        panelState.frame.style.colorScheme = resolveLiveArtifactThemeId();
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
    adaptArtifactHtmlForTheme,
    applyInlineArtifactFrameHeight,
    buildArtifactCode,
    buildPreviewBaseFontSizeStyle,
    buildPreviewThemeStyle,
    buildSrcdoc,
    clampLiveArtifactFontSize,
    ensureArtifactSrcdocTheme,
    extractCodeBlocks,
    extractInlineLiveArtifact,
    extractLiveArtifactInteraction,
    extractLiveArtifacts,
    findArtifactFrameByMessage,
    findArtifactFrameByMessageSource,
    handleArtifactFrameMessage,
    injectPreviewBaseFontSize,
    injectPreviewBaseStyles,
    injectPreviewTheme,
    injectPreviewSecurityPolicy,
    inferRenderableLanguage,
    linkArtifactCitationsInHtml,
    mapHardcodedColorForDarkTheme,
    materializeLiveArtifactThemeVars,
    measureArtifactContentHeight,
    measureInlineArtifactDocumentHeight,
    normalizePreviewDiagnostic,
    parseLiveArtifactInteractionSpec,
    parseInfoAttributes,
    resolveLiveArtifactFontSizePx,
    resolveLiveArtifactThemeId,
    shouldMergeSupportingBlocks,
};
