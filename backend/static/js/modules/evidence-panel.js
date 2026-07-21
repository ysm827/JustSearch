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
            occurrenceId: data.occurrenceId,
            occurrenceIndex: data.occurrenceIndex,
            markerOccurrenceIndex: data.markerOccurrenceIndex,
            groupIndex: data.groupIndex,
            markerIndex: data.markerIndex,
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

function findEvidenceForOccurrence(citations, opts) {
    const list = Array.isArray(citations) ? citations : [];
    if (!list.length) return [];
    const occurrenceId = normalizeId(opts.occurrenceId);
    if (occurrenceId) {
        const exact = list.filter((c) => normalizeId(c?.occurrence_id) === occurrenceId);
        if (exact.length) return exact;
    }
    // source + per-marker occurrence
    const sourceId = normalizeId(opts.sourceId);
    const moi = opts.markerOccurrenceIndex;
    if (sourceId && moi !== undefined && moi !== null && moi !== '') {
        const moiNum = Number(moi);
        const byMarkerOcc = list.filter((c) =>
            (normalizeId(c?.marker) === sourceId || normalizeId(c?.source_id) === sourceId)
            && Number(c?.marker_occurrence_index) === moiNum
        );
        if (byMarkerOcc.length) return byMarkerOcc;
    }
    // source + group/marker indices
    const gi = opts.groupIndex;
    const mi = opts.markerIndex;
    if (sourceId && gi !== undefined && gi !== null && gi !== '') {
        const giNum = Number(gi);
        const miNum = Number(mi);
        const byGroup = list.filter((c) =>
            (normalizeId(c?.marker) === sourceId || normalizeId(c?.source_id) === sourceId)
            && Number(c?.group_index) === giNum
            && Number(c?.marker_index) === miNum
        );
        if (byGroup.length) return byGroup;
    }
    // Legacy / marker-only fallback: all records for this source.
    if (sourceId) {
        return list.filter((c) => normalizeId(c?.marker) === sourceId || normalizeId(c?.source_id) === sourceId);
    }
    return [];
}

function findCitationsForMarker(citations, sourceId) {
    const id = normalizeId(sourceId);
    return (Array.isArray(citations) ? citations : []).filter(
        (c) => normalizeId(c?.marker) === id || normalizeId(c?.source_id) === id
    );
}

function statusLabel(status) {
    // Map any status (legacy or new) to the honest 4-level set.
    const s = String(status || '').toLowerCase();
    if (s === 'verified-literal') return { text: '原文直证', className: 'verified' };
    if (s === 'likely') return { text: '高度支持', className: 'likely' };
    if (s === 'related') return { text: '仅相关', className: 'related' };
    if (s === 'missing') return { text: '未找到证据', className: 'missing' };
    // Legacy compatibility.
    if (s === 'matched') return { text: '高度支持', className: 'likely' };
    if (s === 'weak') return { text: '仅相关', className: 'related' };
    return { text: '相关段落', className: 'related' };
}

function verificationLabel(verification) {
    if (!verification || typeof verification !== 'object') return null;
    const verdict = String(verification.verdict || '').toUpperCase();
    if (verdict === 'SUPPORTED') return { text: '语义复核：支持', className: 'verified' };
    if (verdict === 'CONTRADICTED') return { text: '语义复核：矛盾', className: 'missing' };
    if (verdict === 'NOT_ENOUGH_INFO') return { text: '语义复核：信息不足', className: 'related' };
    return null;
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
    occurrenceId,
    occurrenceIndex,
    markerOccurrenceIndex,
    groupIndex,
    markerIndex,
    sources = currentContext.sources,
    citations = currentContext.citations,
    claim = '',
} = {}) {
    const panel = ensurePanel();
    if (!panel || !bodyEl) return;

    const source = findSource(sources, sourceId);
    const occEvidence = findEvidenceForOccurrence(citations, {
        sourceId, occurrenceId, markerOccurrenceIndex, groupIndex, markerIndex,
    });
    // Sort atomic claims by claim_index for stable display.
    const evidenceList = occEvidence
        .slice()
        .sort((a, b) => (Number(a?.claim_index) || 0) - (Number(b?.claim_index) || 0));
    const primary = evidenceList[0] || occEvidence[0] || null;

    const marker = normalizeId(sourceId);
    if (titleEl) titleEl.textContent = `证据 · [${marker}]`;

    const title = primary?.title || source?.title || `来源 ${marker}`;
    const url = primary?.url || source?.url || '';
    const domain = primary?.domain || source?.domain || '';
    const date = primary?.date || source?.date || '';
    const openUrl = buildTextFragmentUrl(url, primary?.quote || source?.excerpt || source?.snippet || '');
    const safeOpen = getSafeUrl(openUrl);

    // Source-level header card.
    const headerStatus = primary ? statusLabel(primary.status) : null;
    let headerHtml = `
        <div class="evidence-source-card">
            <div class="evidence-source-meta">
                ${domain ? `<span class="evidence-domain">${escapeHtml(domain)}</span>` : ''}
                ${date ? `<span class="evidence-date">${escapeHtml(date)}</span>` : ''}
                ${headerStatus ? `<span class="evidence-status evidence-status-${headerStatus.className}">${headerStatus.text}</span>` : ''}
            </div>
            <h3 class="evidence-source-title">${escapeHtml(title)}</h3>
        </div>
    `;

    const claimCards = evidenceList.length
        ? evidenceList.map((ev) => renderClaimCard(ev)).join('')
        : renderClaimCard({
            claim: claim || '',
            quote: source?.excerpt || source?.snippet || '',
            status: 'missing',
            score: 0,
            method: 'no-evidence',
            verification: null,
        });

    bodyEl.innerHTML = `
        ${headerHtml}
        <div class="evidence-claims">${claimCards}</div>
        <div class="evidence-actions">
            ${safeOpen ? `
                <a class="evidence-action-btn primary" href="${escapeHtml(safeOpen)}" target="_blank" rel="noopener noreferrer">
                    <span class="material-symbols-rounded" aria-hidden="true">open_in_new</span>
                    打开原文
                </a>
            ` : ''}
        </div>
    `;

    // Delegated copy handling (one listener on the body, safe across re-renders).
    if (!bodyEl.dataset.copyBound) {
        bodyEl.dataset.copyBound = '1';
        bodyEl.addEventListener('click', handleCopyQuoteClick);
    }

    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('evidence-panel-open');
}

function renderClaimCard(ev) {
    const status = statusLabel(ev?.status);
    const quote = ev?.quote || '';
    const claimText = ev?.claim || '';
    const method = ev?.method || '';
    const vLabel = verificationLabel(ev?.verification);
    let statusHint = '';
    if (status.className === 'missing') {
        statusHint = '未能在原文中定位到精确句子，请打开原文核对。';
    } else if (status.className === 'related') {
        statusHint = '仅找到相关段落或存在冲突，请谨慎采信。';
    }
    return `
        <section class="evidence-section evidence-claim-card">
            <div class="evidence-claim-head">
                <span class="evidence-status evidence-status-${status.className}">${status.text}</span>
                ${vLabel ? `<span class="evidence-verification evidence-verification-${vLabel.className}">${vLabel.text}</span>` : ''}
            </div>
            ${claimText ? `
                <div class="evidence-section-label">答案中的论断</div>
                <p class="evidence-claim">${escapeHtml(claimText)}</p>
            ` : ''}
            <div class="evidence-section-label">原文片段</div>
            ${quote
                ? `<blockquote class="evidence-quote">${escapeHtml(quote)}</blockquote>`
                : `<p class="evidence-empty">暂无摘录。可打开原文查看。</p>`
            }
            ${statusHint ? `<p class="evidence-hint">${escapeHtml(statusHint)}</p>` : ''}
            ${quote ? `
                <button type="button" class="evidence-action-btn secondary evidence-copy-btn" data-quote="${escapeHtml(quote)}">
                    <span class="material-symbols-rounded" aria-hidden="true">content_copy</span>
                    复制摘录
                </button>
            ` : ''}
        </section>
    `;
}

async function handleCopyQuoteClick(event) {
    const copyBtn = event.target?.closest?.('.evidence-copy-btn');
    if (!copyBtn || !bodyEl?.contains(copyBtn)) return;
    const quote = copyBtn.dataset.quote || '';
    if (!quote) return;
    try {
        await navigator.clipboard.writeText(quote);
        copyBtn.classList.add('copied');
        const previous = copyBtn.innerHTML;
        copyBtn.innerHTML = '<span class="material-symbols-rounded" aria-hidden="true">check</span> 已复制';
        setTimeout(() => {
            copyBtn.classList.remove('copied');
            copyBtn.innerHTML = previous;
        }, 1500);
    } catch {
        /* clipboard may be unavailable in sandboxed/test environments */
    }
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
            occurrenceId: anchor.dataset.evidenceOccurrenceId,
            occurrenceIndex: anchor.dataset.evidenceOccurrenceIndex,
            markerOccurrenceIndex: anchor.dataset.evidenceMarkerOccurrenceIndex,
            groupIndex: anchor.dataset.evidenceGroupIndex,
            markerIndex: anchor.dataset.evidenceMarkerIndex,
            sources: ctxSources,
            citations: ctxCitations,
        });
    });
}

export function initEvidencePanel() {
    ensurePanel();
}
