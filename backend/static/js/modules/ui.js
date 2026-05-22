import { createCopyButton } from './utils.js';
import { extractSources, renderWithCitations } from './source-renderer.js';
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
            document.removeEventListener('keydown', onKeyDown);
            modal.removeEventListener('click', onBackdropClick);
            resolve(result);
        }

        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }
        function onKeyDown(event) {
            if (event.key === 'Escape') {
                cleanup(false);
            }
        }
        function onBackdropClick(event) {
            if (event.target === modal) {
                cleanup(false);
            }
        }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        closeBtn.addEventListener('click', onCancel);
        document.addEventListener('keydown', onKeyDown);
        modal.addEventListener('click', onBackdropClick);
    });
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
