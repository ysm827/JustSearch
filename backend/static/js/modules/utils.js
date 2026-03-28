// 初始化 markdown-it
const mdInstance = window.markdownit({
    html: false,
    linkify: true,
    typographer: true
});

export const md = {
    render: (text) => {
        const rawHtml = mdInstance.render(text);
        const sanitized = window.DOMPurify.sanitize(rawHtml, {
            ADD_ATTR: ['target']
        });
        // 为代码块添加包装器（不含 onclick，用事件委托处理复制）
        return sanitized.replace(/<pre><code([^>]*)>/g, (match, attrs) => {
            return `<pre class="code-block-wrapper"><div class="code-block-header"><span class="code-block-lang">${extractLangFromAttrs(attrs)}</span><button class="code-copy-btn" data-action="copy-code" title="复制代码"><span class="material-symbols-rounded">content_copy</span><span>复制</span></button></div><code${attrs}>`;
        });
    }
};

function extractLangFromAttrs(attrs) {
    const m = attrs.match(/class="language-(\w+)"/);
    return m ? m[1] : 'TEXT';
}

// 全局事件委托：处理代码块复制按钮
document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action="copy-code"]');
    if (!btn) return;
    const pre = btn.closest('pre');
    const code = pre ? pre.querySelector('code') : null;
    if (!code) return;
    try {
        await navigator.clipboard.writeText(code.textContent);
        const icon = btn.querySelector('.material-symbols-rounded');
        const textSpan = btn.querySelector('span:not(.material-symbols-rounded)');
        icon.textContent = 'check';
        if(textSpan) textSpan.textContent = '已复制';
        btn.style.color = 'var(--success)';
        
        setTimeout(() => { 
            icon.textContent = 'content_copy'; 
            if(textSpan) textSpan.textContent = '复制';
            btn.style.color = '';
        }, 2000);
    } catch (err) { console.error('Copy failed:', err); }
});

export function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
}

/**
 * Strip markdown formatting to get plain text.
 */
export function stripMarkdown(mdText) {
    if (!mdText) return '';
    let text = mdText;
    text = text.replace(/```[\s\S]*?```/g, (match) => {
        const lines = match.split('\n');
        return lines.slice(1, lines.length - 1).join('\n');
    });
    text = text.replace(/`([^`]+)`/g, '$1');
    text = text.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');
    text = text.replace(/\[([^\]]*)\]\([^)]+\)/g, '$1');
    text = text.replace(/^#{1,6}\s+/gm, '');
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, '$1');
    text = text.replace(/\*\*(.+?)\*\*/g, '$1');
    text = text.replace(/\*(.+?)\*/g, '$1');
    text = text.replace(/___(.+?)___/g, '$1');
    text = text.replace(/__(.+?)__/g, '$1');
    text = text.replace(/_(.+?)_/g, '$1');
    text = text.replace(/~~(.+?)~~/g, '$1');
    text = text.replace(/^>\s?/gm, '');
    text = text.replace(/^[-*_]{3,}\s*$/gm, '');
    text = text.replace(/^\s*[-*+]\s+/gm, '');
    text = text.replace(/^\s*\d+\.\s+/gm, '');
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
