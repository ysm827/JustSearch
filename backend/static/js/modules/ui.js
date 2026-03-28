import { md, createCopyButton } from './utils.js';
import { renameChatAPI } from './api.js';
import { showToast } from './toast.js';

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
            elements.historyList.innerHTML = '<div class="history-no-results">未找到匹配的对话</div>';
        }
        return;
    }

    // 搜索过滤
    let filtered = history;
    if (searchTerm) {
        filtered = history.filter(chat => 
            (chat.title || '新对话').toLowerCase().includes(searchTerm)
        );
        if (filtered.length === 0) {
            elements.historyList.innerHTML = '<div class="history-no-results">未找到匹配的对话</div>';
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
    
    messages.forEach(msg => {
        appendMessage(msg.role, msg.content, msg.logs, msg.sources);
    });
    
    scrollToBottom();
}

export function appendMessage(role, content, logs = null, sources = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    
    if (role === 'assistant' && logs && logs.length > 0) {
         msgDiv.appendChild(createLogContainer(logs));
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
    
    msgDiv.appendChild(contentDiv);
    elements.chatContainer.appendChild(msgDiv);
    return { msgDiv, contentDiv };
}

export function scrollToBottom() {
    elements.chatContainer.scrollTo({
        top: elements.chatContainer.scrollHeight,
        behavior: 'smooth'
    });
}

export function createLogContainer(logs) {
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
    statusText.textContent = '思考过程';
    
    statusLeft.appendChild(spinner);
    statusLeft.appendChild(statusText);
    
    const expandIcon = document.createElement('span');
    expandIcon.className = 'material-symbols-rounded expand-icon';
    expandIcon.textContent = 'expand_more';
    
    logSummary.appendChild(statusLeft);
    logSummary.appendChild(expandIcon);
    
    const logDetails = document.createElement('div');
    logDetails.className = 'log-details';
    
    if (logs && Array.isArray(logs) && logs.length > 0) {
        logDetails.classList.add('open');
        expandIcon.classList.add('expanded');
    }
    
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
    logContainer.style.display = 'none';
    
    const logSummary = document.createElement('div');
    logSummary.className = 'log-summary';
    
    const statusLeft = document.createElement('div');
    statusLeft.className = 'log-status-left';
    
    const spinner = document.createElement('span');
    spinner.className = 'material-symbols-rounded log-spinner rotating';
    spinner.textContent = 'progress_activity';
    
    const statusText = document.createElement('span');
    statusText.className = 'log-status-text';
    statusText.textContent = '正在思考...';
    
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
    
    return { logContainer, logDetails, spinner, statusText };
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
        if (/\[\d+\]/.test(node.textContent)) {
            nodesToReplace.push(node);
        }
    }
    
    nodesToReplace.forEach(node => {
        const content = node.textContent;
        const fragment = document.createElement('span');
        
        const parts = content.split(/(\[\d+\])/);
        parts.forEach(part => {
            const match = /^\[(\d+)\]$/.exec(part);
            if (match) {
                const id = match[1];
                const source = sources.find(s => s.id == id);
                if (source) {
                    const a = document.createElement('a');
                    a.href = source.url;
                    a.target = '_blank';
                    a.className = 'citation-link';
                    a.textContent = `[${id}]`;
                    // 确保 title 包含 URL 以便 hover 时显示
                    a.title = source.url;
                    fragment.appendChild(a);
                } else {
                    fragment.appendChild(document.createTextNode(part));
                }
            } else {
                fragment.appendChild(document.createTextNode(part));
            }
        });
        
        node.parentNode.replaceChild(fragment, node);
    });
    
    return div.innerHTML;
}
