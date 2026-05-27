import { md } from './utils.js?v=3';

const _faviconCache = new Map();

function getSafeExternalUrl(url) {
    try {
        const rawUrl = String(url || '').trim();
        if (!rawUrl) return '';

        let candidate = rawUrl;
        if (rawUrl.startsWith('//')) {
            candidate = `https:${rawUrl}`;
        } else if (!/^[a-z][a-z0-9+.-]*:/i.test(rawUrl) && /^[^\s/?#]+\.[^\s]+/.test(rawUrl)) {
            candidate = `https://${rawUrl}`;
        }

        const parsedUrl = new URL(candidate);
        if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
            return '';
        }
        return candidate;
    } catch {
        return '';
    }
}

function getFaviconUrl(url) {
    try {
        const parsedUrl = new URL(url);
        if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
            return null;
        }
        const domain = parsedUrl.hostname;
        if (_faviconCache.has(domain)) {
            return _faviconCache.get(domain);
        }
        const faviconUrl = `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
        _faviconCache.set(domain, faviconUrl);
        return faviconUrl;
    } catch {
        return null;
    }
}

export function extractSources(text) {
    const sources = [];
    const regex = /\[(\d+)\] \[([^\]]*)\]\(([^)]+)\)/g;
    let match;
    while ((match = regex.exec(text)) !== null) {
        sources.push({ id: match[1], title: match[2], url: match[3] });
    }
    return sources;
}

export function renderWithCitations(text, sources) {
    const html = md.render(text);
    if (!sources || sources.length === 0) return html;
    const sourceById = new Map(
        sources
            .map((source, index) => [String(source.id ?? index + 1).trim(), source])
    );

    const div = document.createElement('div');
    div.innerHTML = html;

    const walker = document.createTreeWalker(div, NodeFilter.SHOW_TEXT, {
        acceptNode: function(node) {
            let parent = node.parentNode;
            while (parent && parent !== div) {
                if (parent.tagName === 'CODE' || parent.tagName === 'PRE' || parent.tagName === 'A') {
                    return NodeFilter.FILTER_REJECT;
                }
                parent = parent.parentNode;
            }
            return NodeFilter.FILTER_ACCEPT;
        }
    });

    const nodesToReplace = [];
    while (walker.nextNode()) {
        const node = walker.currentNode;
        if (/\[\d+(?:,\s*\d+)*\]/.test(node.textContent)) {
            nodesToReplace.push(node);
        }
    }

    nodesToReplace.forEach(node => {
        const content = node.textContent;
        const fragment = document.createDocumentFragment();

        const regex = /\[(\d+(?:,\s*\d+)*)\]/g;
        let lastIndex = 0;
        let match;

        while ((match = regex.exec(content)) !== null) {
            if (match.index > lastIndex) {
                fragment.appendChild(document.createTextNode(content.substring(lastIndex, match.index)));
            }

            const ids = match[1].split(',').map(id => id.trim());
            const linkSpan = document.createElement('span');
            linkSpan.className = 'citation-group';

            ids.forEach((id, idx) => {
                const source = sourceById.get(id);
                if (source) {
                    const safeUrl = getSafeExternalUrl(source.url);
                    const anchor = document.createElement('a');
                    anchor.href = safeUrl || '#';
                    anchor.className = 'citation-link';
                    if (safeUrl) {
                        anchor.target = '_blank';
                        anchor.rel = 'noopener noreferrer';
                    }
                    anchor.title = source.title || source.url;

                    const faviconUrl = getFaviconUrl(safeUrl);
                    if (faviconUrl) {
                        const img = document.createElement('img');
                        img.src = faviconUrl;
                        img.className = 'citation-favicon';
                        img.alt = '';
                        img.loading = 'lazy';
                        img.onerror = () => img.remove();
                        anchor.appendChild(img);
                    }

                    anchor.appendChild(document.createTextNode(id));
                    linkSpan.appendChild(anchor);

                    if (idx < ids.length - 1) {
                        const comma = document.createElement('span');
                        comma.textContent = ',';
                        comma.style.color = 'var(--text-muted)';
                        comma.style.marginRight = '2px';
                        linkSpan.appendChild(comma);
                    }
                } else {
                    linkSpan.appendChild(document.createTextNode(`[${id}]`));
                }
            });

            fragment.appendChild(linkSpan);
            lastIndex = regex.lastIndex;
        }

        if (lastIndex < content.length) {
            fragment.appendChild(document.createTextNode(content.substring(lastIndex)));
        }

        if (fragment.childNodes.length > 0) {
            node.parentNode.replaceChild(fragment, node);
        }
    });

    const hasCitations = html.match(/\[\d+(?:,\s*\d+)*\]/);
    if (hasCitations && sources.length > 0) {
        const refsBlock = document.createElement('div');
        refsBlock.className = 'references-block';

        const ol = document.createElement('ol');
        sources.forEach((source, idx) => {
            const li = document.createElement('li');
            li.id = `ref-${source.id ?? idx + 1}`;
            li.value = Number(source.id) || idx + 1;

            const safeUrl = getSafeExternalUrl(source.url);
            const faviconUrl = getFaviconUrl(safeUrl);
            if (faviconUrl) {
                const img = document.createElement('img');
                img.src = faviconUrl;
                img.className = 'ref-favicon';
                img.alt = '';
                img.loading = 'lazy';
                img.onerror = () => img.remove();
                li.appendChild(img);
            }

            const anchor = document.createElement('a');
            anchor.href = safeUrl || '#';
            anchor.textContent = source.title || source.url;
            if (safeUrl) {
                anchor.target = '_blank';
                anchor.rel = 'noopener noreferrer';
            }

            li.appendChild(anchor);
            ol.appendChild(li);
        });

        refsBlock.appendChild(ol);
        div.appendChild(refsBlock);
    }

    return div.innerHTML;
}
