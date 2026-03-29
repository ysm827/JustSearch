import { md, createCopyButton } from './utils.js';
import { renameChatAPI } from './api.js';
import { showToast } from './toast.js';
import { state } from './state.js';

/**
 * 自定义确认弹窗（替代浏览器原生 confirm）。
 * @param {string} message - 提示信息
 * @param {string} [title='确认'] - 弹窗标题
 * @returns {Promise<boolean>}
 */
export function showConfirm(message, title = '确认') {
    return new Promise((resolve) => {
        const modal = document.getElementById('confirm-modal');
        const titleEl = document.getElementById('confirm-title');
        const messageEl = document.getElementById('confirm-message');
        const okBtn = document.getElementById('confirm-ok-btn');
        const cancelBtn = document.getElementById('confirm-cancel-btn');
        const closeBtn = document.getElementById('confirm-close-btn');

        titleEl.textContent = title;
        messageEl.textContent = message;
        modal.classList.add('active');

        function cleanup(result) {
            modal.classList.remove('active');
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            closeBtn.removeEventListener('click', onCancel);
            resolve(result);
        }

        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        closeBtn.addEventListener('click', onCancel);
    });
}

/**
 * 从 URL 提取 favicon URL（使用 Google favicon 服务）。
 * 使用内存缓存避免重复请求。
 */
const _faviconCache = new Map();

function getFaviconUrl(url) {
    try {
        const u = new URL(url);
        const domain = u.hostname;
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

export const elements = {
    chatContainer: null,
    historyList: null,
    heroSection: null,
    userInput: null,
    sendBtn: null,
    newChatBtn: null,
    settingsModal: null,
    sidebar: null,
    expandSidebarBtn: null,
    collapseSidebarBtn: null,
    closeSidebarBtn: null,
    mobileOverlay: null,
    scrollToBottomBtn: null,
    historySearchInput: null
};

// 缓存完整历史数据，用于搜索过滤
let _fullHistory = [];

/**
 * 获取缓存的历史记录（供搜索过滤使用，避免重复 fetch）
 */
export function getCachedHistory() {
    return _fullHistory;
}

export function initUI() {
    elements.chatContainer = document.getElementById('chat-container');
    elements.historyList = document.getElementById('history-list');
    elements.heroSection = document.getElementById('hero-section');
    elements.userInput = document.getElementById('user-input');
    elements.sendBtn = document.getElementById('send-btn');
    elements.newChatBtn = document.getElementById('new-chat-btn');
    elements.settingsModal = document.getElementById('settings-modal');
    elements.sidebar = document.getElementById('sidebar');
    elements.expandSidebarBtn = document.getElementById('expand-sidebar-btn');
    elements.collapseSidebarBtn = document.getElementById('collapse-sidebar-btn');
    elements.closeSidebarBtn = document.getElementById('close-sidebar-btn');
    elements.mobileOverlay = document.getElementById('mobile-overlay');
    elements.scrollToBottomBtn = document.getElementById('scroll-to-bottom-btn');
    elements.historySearchInput = document.getElementById('history-search-input');

    initScrollBehavior();
}

function initScrollBehavior() {
    const { chatContainer, scrollToBottomBtn } = elements;

    chatContainer.addEventListener('scroll', () => {
        const { scrollTop, scrollHeight, clientHeight } = chatContainer;
        const scrollBottom = scrollHeight - scrollTop - clientHeight;
        
        if (scrollBottom > 100) {
            scrollToBottomBtn.classList.add('visible');
        } else {
            scrollToBottomBtn.classList.remove('visible');
        }
    });

    scrollToBottomBtn.addEventListener('click', () => {
        scrollToBottom();
    });
}

/**
 * 按日期分组对话
 */
function groupChatsByDate(history) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today.getTime() - 86400000);
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - ((today.getDay() + 6) % 7));

    const groups = {
        '今天': [],
        '昨天': [],
        '本周': [],
        '更早': []
    };

    history.forEach(chat => {
        const ts = chat.timestamp ? new Date(chat.timestamp) : null;
        if (!ts) {
            groups['更早'].push(chat);
            return;
        }
        const chatDate = new Date(ts.getFullYear(), ts.getMonth(), ts.getDate());
        if (chatDate.getTime() >= today.getTime()) {
            groups['今天'].push(chat);
        } else if (chatDate.getTime() >= yesterday.getTime()) {
            groups['昨天'].push(chat);
        } else if (chatDate.getTime() >= weekStart.getTime()) {
            groups['本周'].push(chat);
        } else {
            groups['更早'].push(chat);
        }
    });

    return groups;
}

export function renderHistory(history, currentSessionId, callbacks) {
    // 缓存完整历史
    _fullHistory = history || [];
    
    const { onSelect, onDelete } = callbacks;
    const searchTerm = elements.historySearchInput ? elements.historySearchInput.value.trim().toLowerCase() : '';
    
    elements.historyList.innerHTML = '';
    
    if (!history || history.length === 0) {
        if (searchTerm) {
            elements.historyList.innerHTML = '<div class="history-no-results"><span class="material-symbols-rounded" style="font-size:32px;color:var(--text-muted);margin-bottom:8px;">search_off</span><br>未找到匹配的对话</div>';
        } else {
            elements.historyList.innerHTML = '<div class="history-no-results"><span class="material-symbols-rounded" style="font-size:32px;color:var(--text-muted);margin-bottom:8px;">chat_bubble_outline</span><br>暂无对话记录</div>';
        }
        return;
    }

    // 搜索过滤 — search titles and keep fuzzy matches
    let filtered = history;
    if (searchTerm) {
        // Split search into terms for partial matching
        const terms = searchTerm.split(/\s+/).filter(t => t);
        filtered = history.filter(chat => {
            const title = (chat.title || '新对话').toLowerCase();
            return terms.every(term => title.includes(term));
        });
        if (filtered.length === 0) {
            elements.historyList.innerHTML = '<div class="history-no-results"><span class="material-symbols-rounded" style="font-size:32px;color:var(--text-muted);margin-bottom:8px;">search_off</span><br>未找到匹配的对话</div>';
            return;
        }
    }

    const groups = groupChatsByDate(filtered);
    const groupOrder = ['今天', '昨天', '本周', '更早'];

    groupOrder.forEach(groupName => {
        const items = groups[groupName];
        if (items.length === 0) return;

        const group = document.createElement('div');
        group.className = 'history-group';

        const header = document.createElement('div');
        header.className = 'history-group-header';
        header.textContent = groupName;
        group.appendChild(header);

        items.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'history-item';
            if (chat.id === currentSessionId) item.classList.add('active');
            
            const titleSpan = document.createElement('span');
            titleSpan.className = 'history-title';
            titleSpan.textContent = chat.title || '新对话';
            item.appendChild(titleSpan);

            // 双击重命名
            titleSpan.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                startRename(titleSpan, chat.id, callbacks);
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'delete-history-btn';
            deleteBtn.title = '删除对话';
            deleteBtn.setAttribute('aria-label', '删除对话');
            deleteBtn.innerHTML = '<span class="material-symbols-rounded">delete</span>';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                onDelete(chat.id);
            };
            item.appendChild(deleteBtn);

            const exportBtn = document.createElement('button');
            exportBtn.className = 'history-action-btn history-export-btn';
            exportBtn.title = '导出对话';
            exportBtn.setAttribute('aria-label', '导出对话');
            exportBtn.innerHTML = '<span class="material-symbols-rounded">download</span>';
            exportBtn.onclick = (e) => {
                e.stopPropagation();
                window.open(`/api/history/${chat.id}/export`, '_blank');
            };
            item.appendChild(exportBtn);

            const shareBtn = document.createElement('button');
            shareBtn.className = 'history-action-btn history-share-btn';
            shareBtn.title = '复制对话链接';
            shareBtn.setAttribute('aria-label', '复制对话链接');
            shareBtn.innerHTML = '<span class="material-symbols-rounded">link</span>';
            shareBtn.onclick = (e) => {
                e.stopPropagation();
                const url = `${window.location.origin}/c/${chat.id}`;
                navigator.clipboard.writeText(url).then(() => {
                    showToast('对话链接已复制', 'success');
                }).catch(() => {
                    showToast('复制失败', 'error');
                });
            };
            item.appendChild(shareBtn);

            item.dataset.id = chat.id;
            item.onclick = () => onSelect(chat.id);
            group.appendChild(item);
        });

        elements.historyList.appendChild(group);
    });
}

/**
 * 启动历史记录重命名
 */
function startRename(titleSpan, chatId, callbacks) {
    const currentTitle = titleSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'history-title-edit';
    input.value = currentTitle;
    
    titleSpan.replaceWith(input);
    input.focus();
    input.select();

    async function save() {
        const newTitle = input.value.trim() || currentTitle;
        // 恢复 span
        const newSpan = document.createElement('span');
        newSpan.className = 'history-title';
        newSpan.textContent = newTitle;
        newSpan.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            startRename(newSpan, chatId, callbacks);
        });
        input.replaceWith(newSpan);

        if (newTitle !== currentTitle) {
            const ok = await renameChatAPI(chatId, newTitle);
            if (ok) {
                showToast('已重命名', 'success');
            } else {
                // 优雅降级：恢复原标题
                newSpan.textContent = currentTitle;
                showToast('重命名失败，服务端可能不支持', 'warning');
            }
        }
    }

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
        } else if (e.key === 'Escape') {
            input.value = currentTitle;
            input.blur();
        }
    });
    input.addEventListener('blur', save);
}

export function updateActiveHistoryItem(sessionId) {
    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.toggle('active', item.dataset.id === sessionId);
    });
}

export function renderMessages(messages) {
    elements.chatContainer.innerHTML = '';
    
    if (!messages || messages.length === 0) {
        elements.heroSection.style.display = 'block';
        elements.chatContainer.appendChild(elements.heroSection);
        return;
    }
    
    elements.heroSection.style.display = 'none';
    
    messages.forEach((msg, idx) => {
        appendMessage(msg.role, msg.content, msg.logs, msg.sources, msg.stats, idx, msg.timestamp);
    });
    
    scrollToBottom();
}

export function appendMessage(role, content, logs = null, sources = null, stats = null, messageIndex = null, timestamp = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    
    if (role === 'assistant' && logs && logs.length > 0) {
         const siteCount = (stats && stats.sites_searched) ? stats.sites_searched : ((sources && sources.length) ? sources.length : 0);
         msgDiv.appendChild(createLogContainer(logs, siteCount));
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    if (role === 'assistant') {
        contentDiv.classList.add('markdown-body');
        const resolvedSources = (sources && sources.length > 0) ? sources : extractSources(content);
        contentDiv.innerHTML = renderWithCitations(content, resolvedSources);
    } else {
        contentDiv.textContent = content;
    }
    
    const copyBtn = createCopyButton(content);
    contentDiv.appendChild(copyBtn);

    // Delete button (only for loaded history messages)
    if (messageIndex !== null) {
        const deleteMsgBtn = document.createElement('button');
        deleteMsgBtn.className = 'msg-delete-btn';
        deleteMsgBtn.title = '删除此条消息';
        deleteMsgBtn.innerHTML = '<span class="material-symbols-rounded">delete</span>';
        deleteMsgBtn.onclick = async (e) => {
            e.stopPropagation();
            if (!await showConfirm('确定要删除这条消息吗？', '删除消息')) return;
            const { deleteMessageAPI } = await import('./api.js');
            const ok = await deleteMessageAPI(state.currentSessionId, messageIndex);
            if (ok) {
                msgDiv.remove();
            } else {
                const { showToast } = await import('./toast.js');
                showToast('删除失败', 'error');
            }
        };
        contentDiv.appendChild(deleteMsgBtn);
    }
    
    msgDiv.appendChild(contentDiv);

    // Add timestamp — use provided timestamp or current time
    const timeDiv = document.createElement('div');
    timeDiv.className = 'message-time';
    if (timestamp) {
        // Try to parse ISO timestamp or date string
        try {
            const d = new Date(timestamp);
            const now = new Date();
            const isToday = d.toDateString() === now.toDateString();
            const hours = d.getHours().toString().padStart(2, '0');
            const minutes = d.getMinutes().toString().padStart(2, '0');
            if (isToday) {
                timeDiv.textContent = `${hours}:${minutes}`;
            } else {
                // Show date for older messages
                const month = (d.getMonth() + 1).toString().padStart(2, '0');
                const day = d.getDate().toString().padStart(2, '0');
                timeDiv.textContent = `${month}-${day} ${hours}:${minutes}`;
            }
        } catch {
            timeDiv.textContent = timestamp;
        }
    } else {
        const now = new Date();
        const hours = now.getHours().toString().padStart(2, '0');
        const minutes = now.getMinutes().toString().padStart(2, '0');
        timeDiv.textContent = `${hours}:${minutes}`;
    }
    msgDiv.appendChild(timeDiv);

    elements.chatContainer.appendChild(msgDiv);
    return { msgDiv, contentDiv };
}

export function scrollToBottom() {
    elements.chatContainer.scrollTo({
        top: elements.chatContainer.scrollHeight,
        behavior: 'smooth'
    });
}

export function createLogContainer(logs, sourceCount = 0) {
    const logContainer = document.createElement('div');
    logContainer.className = 'log-container';
    
    const logSummary = document.createElement('div');
    logSummary.className = 'log-summary';
    
    const statusLeft = document.createElement('div');
    statusLeft.className = 'log-status-left';
    
    const spinner = document.createElement('span');
    spinner.className = 'material-symbols-rounded log-spinner completed';
    spinner.textContent = 'check_circle';
    
    const statusText = document.createElement('span');
    statusText.className = 'log-status-text';
    statusText.textContent = sourceCount > 0 ? `已完成 · 搜索过 ${sourceCount} 个网页` : '思考过程';
    
    statusLeft.appendChild(spinner);
    statusLeft.appendChild(statusText);
    
    const expandIcon = document.createElement('span');
    expandIcon.className = 'material-symbols-rounded expand-icon';
    expandIcon.textContent = 'expand_more';
    
    logSummary.appendChild(statusLeft);
    logSummary.appendChild(expandIcon);
    
    const logDetails = document.createElement('div');
    logDetails.className = 'log-details';
    
    if (logs && Array.isArray(logs)) {
        logs.forEach(log => {
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            const span = document.createElement('span');
            span.textContent = log;
            entry.appendChild(span);
            logDetails.appendChild(entry);
        });
    }
    
    logSummary.onclick = () => {
        logDetails.classList.toggle('open');
        expandIcon.classList.toggle('expanded');
    };
    
    logContainer.appendChild(logSummary);
    logContainer.appendChild(logDetails);
    
    return logContainer;
}

export function createDynamicLogContainer() {
    const logContainer = document.createElement('div');
    logContainer.className = 'log-container';
    
    const logSummary = document.createElement('div');
    logSummary.className = 'log-summary';
    
    const statusLeft = document.createElement('div');
    statusLeft.className = 'log-status-left';
    
    const spinner = document.createElement('span');
    spinner.className = 'material-symbols-rounded log-spinner rotating';
    spinner.textContent = 'progress_activity';
    
    const statusText = document.createElement('span');
    statusText.className = 'log-status-text';
    statusText.textContent = '正在搜索...';
    
    statusLeft.appendChild(spinner);
    statusLeft.appendChild(statusText);
    
    const expandIcon = document.createElement('span');
    expandIcon.className = 'material-symbols-rounded expand-icon expanded';
    expandIcon.textContent = 'expand_more';
    
    logSummary.appendChild(statusLeft);
    logSummary.appendChild(expandIcon);
    
    const logDetails = document.createElement('div');
    logDetails.className = 'log-details open';
    
    logSummary.onclick = () => {
        logDetails.classList.toggle('open');
        expandIcon.classList.toggle('expanded');
    };
    
    logContainer.appendChild(logSummary);
    logContainer.appendChild(logDetails);
    
    return { logContainer, logDetails, spinner, statusText, expandIcon };
}

/**
 * Copy entire conversation to clipboard as plain text
 */
export function copyConversation(messages) {
    if (!messages || messages.length === 0) return;
    const text = messages.map(msg => {
        const role = msg.role === 'user' ? '👤 You' : '🤖 JustSearch';
        const content = msg.content || '';
        return `${role}:
${content}`;
    }).join(`

---

`);
    navigator.clipboard.writeText(text).catch(err => console.error('Copy failed:', err));
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
    
    const div = document.createElement('div');
    div.innerHTML = html;
    
    // Process text nodes to inject citations, avoiding code/links
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
    while(walker.nextNode()) {
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
                const sourceIndex = parseInt(id) - 1;
                if (sourceIndex >= 0 && sourceIndex < sources.length) {
                    const a = document.createElement('a');
                    a.href = sources[sourceIndex].url;
                    a.className = 'citation-link';
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    a.title = sources[sourceIndex].title || sources[sourceIndex].url;
                    
                    const faviconUrl = getFaviconUrl(sources[sourceIndex].url);
                    if (faviconUrl) {
                        const img = document.createElement('img');
                        img.src = faviconUrl;
                        img.className = 'citation-favicon';
                        img.alt = '';
                        img.loading = 'lazy';
                        img.onerror = () => img.remove();
                        a.appendChild(img);
                    }
                    
                    const textNode = document.createTextNode(id);
                    a.appendChild(textNode);
                    linkSpan.appendChild(a);
                    
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
    
    // Add references list block at bottom if citations were found
    const hasCitations = html.match(/\[\d+(?:,\s*\d+)*\]/);
    if (hasCitations && sources.length > 0) {
        const refsBlock = document.createElement('div');
        refsBlock.className = 'references-block';
        
        const ol = document.createElement('ol');
        sources.forEach((source, idx) => {
            const li = document.createElement('li');
            li.id = `ref-${idx + 1}`;
            
            const faviconUrl = getFaviconUrl(source.url);
            if (faviconUrl) {
                const img = document.createElement('img');
                img.src = faviconUrl;
                img.className = 'ref-favicon';
                img.alt = '';
                img.loading = 'lazy';
                img.onerror = () => img.remove();
                li.appendChild(img);
            }
            
            const a = document.createElement('a');
            a.href = source.url;
            a.textContent = source.title || source.url;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            
            li.appendChild(a);
            ol.appendChild(li);
        });
        
        refsBlock.appendChild(ol);
        div.appendChild(refsBlock);
    }
    
    return div.innerHTML;
}
