// Initialize markdown-it
const mdInstance = window.markdownit({
    html: false,
    linkify: true,
    typographer: true
});

export const md = {
    render: (text) => {
        const rawHtml = mdInstance.render(text);
        return window.DOMPurify.sanitize(rawHtml, {
            ADD_ATTR: ['target'] // Allow target="_blank" for links
        });
    }
};

export function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
}

/**
 * Strip markdown formatting to get plain text.
 * Handles: headings, bold, italic, links, images, code blocks, lists, blockquotes, horizontal rules.
 */
export function stripMarkdown(mdText) {
    if (!mdText) return '';
    let text = mdText;
    // Remove code blocks (fenced)
    text = text.replace(/```[\s\S]*?```/g, (match) => {
        const lines = match.split('\n');
        // Remove first line (language hint) and last ```
        return lines.slice(1, lines.length - 1).join('\n');
    });
    // Remove inline code
    text = text.replace(/`([^`]+)`/g, '$1');
    // Remove images
    text = text.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
    // Remove links, keep text
    text = text.replace(/\[([^\]]*)\]\([^)]+\)/g, '$1');
    // Remove headings markers
    text = text.replace(/^#{1,6}\s+/gm, '');
    // Remove bold/italic markers
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, '$1');
    text = text.replace(/\*\*(.+?)\*\*/g, '$1');
    text = text.replace(/\*(.+?)\*/g, '$1');
    text = text.replace(/___(.+?)___/g, '$1');
    text = text.replace(/__(.+?)__/g, '$1');
    text = text.replace(/_(.+?)_/g, '$1');
    // Remove strikethrough
    text = text.replace(/~~(.+?)~~/g, '$1');
    // Remove blockquotes
    text = text.replace(/^>\s?/gm, '');
    // Remove horizontal rules
    text = text.replace(/^[-*_]{3,}\s*$/gm, '');
    // Remove list markers
    text = text.replace(/^\s*[-*+]\s+/gm, '');
    text = text.replace(/^\s*\d+\.\s+/gm, '');
    // Clean up excessive newlines
    text = text.replace(/\n{3,}/g, '\n\n');
    return text.trim();
}

export function createCopyButton(contentGetter) {
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.innerHTML = '<span class="material-symbols-rounded">content_copy</span>';
    btn.title = '复制';
    
    btn.onclick = async (e) => {
        e.stopPropagation();
        const raw = typeof contentGetter === 'function' ? contentGetter() : contentGetter;
        if (!raw) return;
        
        // Extract plain text from markdown
        const text = stripMarkdown(raw);
        
        try {
            await navigator.clipboard.writeText(text);
            const icon = btn.querySelector('span');
            icon.textContent = 'check';
            setTimeout(() => {
                icon.textContent = 'content_copy';
            }, 2000);
        } catch (err) {
            console.error('Failed to copy:', err);
        }
    };
    
    return btn;
}
