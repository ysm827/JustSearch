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

export function createCopyButton(contentGetter) {
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.innerHTML = '<span class="material-symbols-rounded">content_copy</span>';
    btn.title = '复制';
    
    btn.onclick = async (e) => {
        e.stopPropagation();
        const text = typeof contentGetter === 'function' ? contentGetter() : contentGetter;
        if (!text) return;
        
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