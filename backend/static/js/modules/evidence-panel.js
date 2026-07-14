/**
 * Evidence side panel: open when user clicks a citation chip [n].
 * Shows claim → quote mapping with honest match status.
 */

let panelEl = null;
let bodyEl = null;
let titleEl = null;
let closeBtn = null;
let currentContext = { sources: [], citations: [] };

function ensurePanel() {
    if (panelEl) return panelEl;
    if (typeof document === 'undefined') return null;

    panelEl = document.createElement('aside');
    panelEl.id = 'evidence-panel';
    panelEl.className = 'evidence-panel';
    panelEl.setAttribute('aria-hidden', 'true');
    panelEl.innerHTML = `
        <div class="evidence-panel-header">
            <div class="evidence-panel-heading">
                <span class="material-symbols-rounded evidence-panel-icon" aria-hidden="true">menu_book</span>
                <div>
                    <div class="evidence-panel-title" id="evidence-panel-title">证据</div>
                    <div class="evidence-panel-subtitle">引用原文片段</div>
                </div>
            </div>
            <button type="button" class="evidence-panel-close icon-btn" aria-label="关闭证据面板" title="关闭">
                <span class="material-symbols-rounded" aria-hidden="true">close</span>
            </button>
        </div>
        <div class="evidence-panel-body" id="evidence-panel-body"></div>
    `;
    document.body.appendChild(panelEl);

    titleEl = panelEl.querySelector('#evidence-panel-title');
    bodyEl = panelEl.querySelector('#evidence-panel-body');
    closeBtn = panelEl.querySelector('.evidence-panel-close');
    closeBtn?.addEventListener('click', closeEvidencePanel);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && panelEl?.classList.contains('open')) {
            closeEvidencePanel();
        }
    });

    // Live Artifacts iframe citations
    window.addEventListener('message', (event) => {
        const data = event?.data;
        if (!data || data.channel !== 'justsearch-live-artifacts') return;
        if (data.event !== 'citation-click') return;
        const sourceId = data.sourceId;
        if (sourceId === undefined || sourceId === null || sourceId === '') return;
        openEvidencePanel({
            sourceId,
            sources: currentContext.sources,
            citations: currentContext.citations,
        });
    });

    return panelEl;
}

export function setEvidenceContext({ sources = [], citations = [] } = {}) {
    currentContext = {
        sources: Array.isArray(sources) ? sources : [],
        citations: Array.isArray(citations) ? citations : [],
    };
}

function normalizeId(id) {
    return String(id ?? '').trim();
}

function findSource(sources, sourceId) {
    const id = normalizeId(sourceId);
    const list = Array.isArray(sources) ? sources : [];
    return list.find((s) => normalizeId(s?.id) === id) || null;
}

function findCitationsForMarker(citations, sourceId) {
    const id = normalizeId(sourceId);
    return (Array.isArray(citations) ? citations : []).filter(
        (c) => normalizeId(c?.marker) === id || normalizeId(c?.source_id) === id
    );
}

function statusLabel(status) {
    switch (String(status || '').toLowerCase()) {
        case 'matched':
            return { text: '已定位', className: 'matched' };
        case 'weak':
            return { text: '弱匹配', className: 'weak' };
        case 'missing':
            return { text: '未定位', className: 'missing' };
        default:
            return { text: '相关段落', className: 'weak' };
    }
}

function scoreDots(score) {
    const s = Math.max(0, Math.min(1, Number(score) || 0));
    const filled = Math.round(s * 5);
    return '●'.repeat(filled) + '○'.repeat(5 - filled);
}

function getSafeUrl(url) {
    try {
        const raw = String(url || '').trim();
        if (!raw) return '';
        let candidate = raw;
        if (raw.startsWith('//')) candidate = `https:${raw}`;
        else if (!/^[a-z][a-z0-9+.-]*:/i.test(raw) && /^[^\s/?#]+\.[^\s]+/.test(raw)) {
            candidate = `https://${raw}`;
        }
        const parsed = new URL(candidate);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
        return candidate;
    } catch {
        return '';
    }
}

/**
 * Build Chrome Text Fragment URL when quote is short enough.
 * Best-effort; browsers may ignore if text not found.
 */
function buildTextFragmentUrl(url, quote) {
    const safe = getSafeUrl(url);
    if (!safe || !quote) return safe;
    // Strip ellipsis and collapse whitespace for fragment
    let frag = String(quote).replace(/^[….\s]+|[….\s]+$/g, '').replace(/\s+/g, ' ').trim();
    if (frag.length < 8 || frag.length > 120) return safe;
    // Avoid characters that break fragments badly
    if (/[&#]/.test(frag)) return safe;
    try {
        const encoded = encodeURIComponent(frag).replace(/-/g, '%2D');
        const base = safe.split('#')[0];
        return `${base}#:~:text=${encoded}`;
    } catch {
        return safe;
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

export function openEvidencePanel({
    sourceId,
    sources = currentContext.sources,
    citations = currentContext.citations,
    claim = '',
} = {}) {
    const panel = ensurePanel();
    if (!panel || !bodyEl) return;

    const source = findSource(sources, sourceId);
    const related = findCitationsForMarker(citations, sourceId);
    // Prefer citation that matches provided claim, else first
    let evidence = related[0] || null;
    if (claim && related.length > 1) {
        const hit = related.find((c) => String(c.claim || '').includes(claim.slice(0, 24)));
        if (hit) evidence = hit;
    }

    const marker = normalizeId(sourceId);
    if (titleEl) titleEl.textContent = `证据 · [${marker}]`;

    const title = evidence?.title || source?.title || `来源 ${marker}`;
    const url = evidence?.url || source?.url || '';
    const domain = evidence?.domain || source?.domain || '';
    const date = evidence?.date || source?.date || '';
    const status = statusLabel(evidence?.status);
    const quote = evidence?.quote || source?.excerpt || source?.snippet || '';
    const claimText = evidence?.claim || claim || '';
    const score = evidence?.score;
    const openUrl = buildTextFragmentUrl(url, quote);
    const safeOpen = getSafeUrl(openUrl);

    let statusHint = '';
    if (status.className === 'missing') {
        statusHint = '未能在原文中定位到精确句子，请打开原文核对。';
    } else if (status.className === 'weak') {
        statusHint = '仅找到相关段落，匹配度较低，请谨慎采信。';
    }

    bodyEl.innerHTML = `
        <div class="evidence-source-card">
            <div class="evidence-source-meta">
                ${domain ? `<span class="evidence-domain">${escapeHtml(domain)}</span>` : ''}
                ${date ? `<span class="evidence-date">${escapeHtml(date)}</span>` : ''}
                <span class="evidence-status evidence-status-${status.className}">${status.text}</span>
            </div>
            <h3 class="evidence-source-title">${escapeHtml(title)}</h3>
            ${typeof score === 'number' ? `
                <div class="evidence-score" title="匹配度 ${score}">
                    <span class="evidence-score-dots" aria-hidden="true">${scoreDots(score)}</span>
                    <span class="evidence-score-value">${Number(score).toFixed(2)}</span>
                </div>
            ` : ''}
        </div>

        ${claimText ? `
            <section class="evidence-section">
                <div class="evidence-section-label">答案中的论断</div>
                <p class="evidence-claim">${escapeHtml(claimText)}</p>
            </section>
        ` : ''}

        <section class="evidence-section">
            <div class="evidence-section-label">原文片段</div>
            ${quote
                ? `<blockquote class="evidence-quote">${escapeHtml(quote)}</blockquote>`
                : `<p class="evidence-empty">暂无摘录。可打开原文查看。</p>`
            }
            ${statusHint ? `<p class="evidence-hint">${escapeHtml(statusHint)}</p>` : ''}
        </section>

        <div class="evidence-actions">
            ${safeOpen ? `
                <a class="evidence-action-btn primary" href="${escapeHtml(safeOpen)}" target="_blank" rel="noopener noreferrer">
                    <span class="material-symbols-rounded" aria-hidden="true">open_in_new</span>
                    打开原文
                </a>
            ` : ''}
            ${quote ? `
                <button type="button" class="evidence-action-btn secondary" data-action="copy-quote">
                    <span class="material-symbols-rounded" aria-hidden="true">content_copy</span>
                    复制摘录
                </button>
            ` : ''}
        </div>
    `;

    const copyBtn = bodyEl.querySelector('[data-action="copy-quote"]');
    copyBtn?.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(quote);
            copyBtn.classList.add('copied');
            const label = copyBtn.childNodes[copyBtn.childNodes.length - 1];
            const prev = label?.textContent;
            if (label) label.textContent = '已复制';
            setTimeout(() => {
                copyBtn.classList.remove('copied');
                if (label && prev) label.textContent = prev;
            }, 1500);
        } catch {
            /* ignore */
        }
    });

    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('evidence-panel-open');
}

export function closeEvidencePanel() {
    if (!panelEl) return;
    panelEl.classList.remove('open');
    panelEl.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('evidence-panel-open');
}

/**
 * Bind click on .citation-link within root to open evidence panel.
 * Uses event delegation so streaming re-renders keep working.
 */
export function bindCitationEvidenceClicks(root, { sources = [], citations = [] } = {}) {
    if (!root || root.dataset.evidenceBound === '1') return;
    root.dataset.evidenceBound = '1';
    root.addEventListener('click', (event) => {
        const anchor = event.target?.closest?.('a.citation-link, a.live-artifact-citation-link');
        if (!anchor || !root.contains(anchor)) return;

        // Prefer data attributes; fall back to link text as id
        const sourceId = anchor.dataset.evidenceSourceId
            || anchor.dataset.liveArtifactSourceId
            || (anchor.textContent || '').trim();
        if (!sourceId) return;

        event.preventDefault();
        event.stopPropagation();

        // Merge context: prefer explicit args, else currentContext
        const ctxSources = (Array.isArray(sources) && sources.length) ? sources : currentContext.sources;
        const ctxCitations = (Array.isArray(citations) && citations.length) ? citations : currentContext.citations;
        setEvidenceContext({ sources: ctxSources, citations: ctxCitations });

        openEvidencePanel({
            sourceId,
            sources: ctxSources,
            citations: ctxCitations,
        });
    });
}

export function initEvidencePanel() {
    ensurePanel();
}
