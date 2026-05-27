import {
    createCopyButton,
    createDeleteMessageButton,
    createEditMessageButton,
    createMessageActionRail,
    createRegenerateButton
} from './utils.js?v=3';
import { extractSources, renderWithCitations } from './source-renderer.js?v=6';
import { getInlineLiveArtifact, renderLiveArtifactsForMessage } from './live-artifacts.js?v=8';
import { state } from './state.js?v=2';

const USER_MESSAGE_COLLAPSE_CHARACTER_THRESHOLD = 600;
const USER_MESSAGE_COLLAPSE_LINE_THRESHOLD = 8;
const MESSAGE_GROUP_WINDOW_MS = 5 * 60 * 1000;

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

function findPreviousUserContent(messages, fromIndex) {
    for (let i = fromIndex - 1; i >= 0; i -= 1) {
        if (messages[i]?.role === 'user' && messages[i]?.content) {
            return messages[i].content;
        }
    }
    return '';
}

function parseMessageTimestamp(value) {
    if (!value) return null;
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : null;
}

function isGroupedWithPrevious(message, previousMessage) {
    if (!message || !previousMessage) return false;
    if (normalizeMessageRole(message.role) !== normalizeMessageRole(previousMessage.role)) return false;
    const currentTime = parseMessageTimestamp(message.timestamp);
    const previousTime = parseMessageTimestamp(previousMessage.timestamp);
    if (currentTime === null || previousTime === null) return false;
    return currentTime - previousTime >= 0 && currentTime - previousTime < MESSAGE_GROUP_WINDOW_MS;
}

function shouldCollapseUserMessageContent(content) {
    const text = String(content || '');
    if (text.length > USER_MESSAGE_COLLAPSE_CHARACTER_THRESHOLD) return true;
    return (text.match(/\n/g)?.length || 0) + 1 > USER_MESSAGE_COLLAPSE_LINE_THRESHOLD;
}

function normalizeMessageRole(role) {
    return role === 'model' ? 'assistant' : role;
}

function createMessageAvatar(role) {
    const avatar = document.createElement('div');
    const avatarRoleClass = role === 'assistant'
        ? 'assistant-avatar'
        : role === 'error'
            ? 'error-avatar'
            : 'user-avatar';
    avatar.className = `message-avatar ${avatarRoleClass}`;
    avatar.setAttribute('aria-hidden', 'true');

    if (role === 'assistant') {
        const img = document.createElement('img');
        img.src = '/static/assets/justsearch-favicon.png';
        img.alt = '';
        avatar.appendChild(img);
    } else if (role === 'error') {
        const icon = document.createElement('span');
        icon.className = 'material-symbols-rounded';
        icon.textContent = 'warning';
        avatar.appendChild(icon);
    } else {
        const icon = document.createElement('span');
        icon.className = 'material-symbols-rounded';
        icon.textContent = 'person';
        avatar.appendChild(icon);
    }

    return avatar;
}

export function createMessageShell(role, options = {}) {
    const normalizedRole = normalizeMessageRole(role);
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${normalizedRole}`;
    msgDiv.dataset.messageRole = normalizedRole;
    if (options.grouped) {
        msgDiv.classList.add('grouped');
    }
    if (options.timestamp) {
        msgDiv.dataset.messageTimestamp = options.timestamp;
    }

    const rowDiv = document.createElement('div');
    rowDiv.className = 'message-row';

    const sideColumn = document.createElement('div');
    sideColumn.className = 'message-side';
    sideColumn.appendChild(createMessageAvatar(normalizedRole));

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content message-content-container';

    if (normalizedRole === 'user') {
        rowDiv.appendChild(contentDiv);
        rowDiv.appendChild(sideColumn);
    } else {
        rowDiv.appendChild(sideColumn);
        rowDiv.appendChild(contentDiv);
    }

    msgDiv.appendChild(rowDiv);
    return { msgDiv, rowDiv, contentDiv, sideColumn };
}

function renderUserMessageContent(content, contentDiv) {
    const textDiv = document.createElement('div');
    textDiv.className = 'message-user-text';
    textDiv.textContent = content || '';
    contentDiv.appendChild(textDiv);

    if (!shouldCollapseUserMessageContent(content)) return;

    contentDiv.classList.add('is-collapsible', 'is-collapsed');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'message-collapse-toggle';
    toggle.setAttribute('aria-expanded', 'false');

    const label = document.createElement('span');
    label.textContent = '展开';
    const icon = document.createElement('span');
    icon.className = 'material-symbols-rounded';
    icon.textContent = 'expand_more';
    toggle.appendChild(label);
    toggle.appendChild(icon);

    toggle.addEventListener('click', () => {
        const expanded = contentDiv.classList.toggle('is-expanded');
        contentDiv.classList.toggle('is-collapsed', !expanded);
        toggle.setAttribute('aria-expanded', String(expanded));
        label.textContent = expanded ? '折叠' : '展开';
        icon.textContent = expanded ? 'expand_less' : 'expand_more';
    });

    contentDiv.appendChild(toggle);
}

export function renderMessages(messages, actionCallbacks = {}) {
    elements.chatContainer.innerHTML = '';
    
    if (!messages || messages.length === 0) {
        elements.heroSection.style.display = 'block';
        elements.chatContainer.appendChild(elements.heroSection);
        return;
    }
    
    elements.heroSection.style.display = 'none';
    
    messages.forEach((msg, idx) => {
        const previousMessage = idx > 0 ? messages[idx - 1] : null;
        appendMessage(msg.role, msg.content, msg.logs, msg.sources, msg.stats, idx, msg.timestamp, {
            ...actionCallbacks,
            isGrouped: isGroupedWithPrevious(msg, previousMessage),
            previousUserContent: normalizeMessageRole(msg.role) === 'assistant' ? findPreviousUserContent(messages, idx) : ''
        });
    });
    
    scrollToBottom();
}

function stageMessageForEdit(content, actionCallbacks = {}) {
    if (typeof actionCallbacks.onEdit === 'function') {
        actionCallbacks.onEdit(content);
        return;
    }

    if (!elements.userInput) return;
    elements.userInput.value = content;
    elements.userInput.dispatchEvent(new Event('input', { bubbles: true }));
    elements.userInput.focus({ preventScroll: true });
    scrollToBottom();
}

function createMessageActions({ role, content, msgDiv, messageIndex, actionCallbacks }) {
    const normalizedRole = normalizeMessageRole(role);
    const buttons = [createCopyButton(content)];

    if (normalizedRole === 'user') {
        buttons.push(createEditMessageButton(content, (value) => {
            stageMessageForEdit(value, actionCallbacks);
        }));
    }

    if (normalizedRole === 'assistant' && actionCallbacks.previousUserContent && typeof actionCallbacks.onRegenerate === 'function') {
        buttons.push(createRegenerateButton(() => actionCallbacks.onRegenerate(actionCallbacks.previousUserContent, { messageIndex })));
    }

    if (messageIndex !== null) {
        buttons.push(createDeleteMessageButton(async () => {
            if (!await showConfirm('确定要删除这条消息吗？', '删除消息')) return;
            const { deleteMessageAPI } = await import('./api.js?v=3');
            const ok = await deleteMessageAPI(state.currentSessionId, messageIndex);
            if (ok) {
                msgDiv.remove();
                if (typeof actionCallbacks.onMessageDeleted === 'function') {
                    await actionCallbacks.onMessageDeleted(messageIndex);
                }
            } else {
                const { showToast } = await import('./toast.js');
                showToast('删除失败', 'error');
            }
        }));
    }

    return createMessageActionRail(buttons, normalizedRole === 'assistant' ? '助手消息操作' : '用户消息操作');
}

export function appendMessage(role, content, logs = null, sources = null, stats = null, messageIndex = null, timestamp = null, actionCallbacks = {}) {
    const normalizedRole = normalizeMessageRole(role);
    const { msgDiv, contentDiv, sideColumn } = createMessageShell(role, {
        grouped: Boolean(actionCallbacks.isGrouped),
        timestamp
    });

    if (normalizedRole === 'assistant' && logs && logs.length > 0) {
         const siteCount = (stats && stats.sites_searched) ? stats.sites_searched : ((sources && sources.length) ? sources.length : 0);
         contentDiv.appendChild(createLogContainer(logs, siteCount));
    }

    if (normalizedRole === 'assistant') {
        contentDiv.classList.add('markdown-body');
        const resolvedSources = (sources && sources.length > 0) ? sources : extractSources(content);
        const answerBody = document.createElement('div');
        answerBody.className = 'message-answer-body';
        answerBody.dataset.liveArtifactsMessageId = `history-${messageIndex ?? Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        if (!getInlineLiveArtifact(content, answerBody.dataset.liveArtifactsMessageId, false)) {
            answerBody.innerHTML = renderWithCitations(content, resolvedSources);
        }
        renderLiveArtifactsForMessage(answerBody, content, {
            messageId: answerBody.dataset.liveArtifactsMessageId,
            isStreaming: false,
            sources: resolvedSources,
        });
        contentDiv.appendChild(answerBody);
    } else {
        renderUserMessageContent(content, contentDiv);
    }

    sideColumn.appendChild(createMessageActions({ role: normalizedRole, content, msgDiv, messageIndex, actionCallbacks }));

    elements.chatContainer.appendChild(msgDiv);
    return { msgDiv, contentDiv };
}

export function scrollToBottom() {
    elements.chatContainer.scrollTo({
        top: elements.chatContainer.scrollHeight,
        behavior: 'smooth'
    });
}

function classifyLogMessage(message) {
    if (/search|搜索|query/i.test(message)) return 'log-search';
    if (/crawl|爬取|fetch|reading|读取|阅读/i.test(message)) return 'log-crawl';
    if (/analyz|分析|assess|评估|总结|生成/i.test(message)) return 'log-analysis';
    if (/error|失败|fail|错误/i.test(message)) return 'log-error';
    return '';
}

export function createLogEntry(message, timestamp = '') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${classifyLogMessage(message)}`.trim();

    const dot = document.createElement('span');
    dot.className = 'log-entry-dot';
    dot.setAttribute('aria-hidden', 'true');
    entry.appendChild(dot);

    if (timestamp) {
        const tsSpan = document.createElement('span');
        tsSpan.className = 'log-timestamp';
        tsSpan.textContent = timestamp;
        entry.appendChild(tsSpan);
    }

    const msgSpan = document.createElement('span');
    msgSpan.className = 'log-entry-message';
    msgSpan.textContent = message;
    entry.appendChild(msgSpan);

    return entry;
}

function wireLogToggle(logSummary, logDetails, expandIcon) {
    const toggle = () => {
        const isOpen = logDetails.classList.toggle('open');
        expandIcon.classList.toggle('expanded', isOpen);
        logSummary.setAttribute('aria-expanded', String(isOpen));
    };

    logSummary.onclick = toggle;
    logSummary.onkeydown = (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggle();
    };
}

export function createLogContainer(logs, sourceCount = 0) {
    const logContainer = document.createElement('div');
    logContainer.className = 'log-container message-thoughts-block completed';
    
    const logSummary = document.createElement('div');
    logSummary.className = 'log-summary';
    logSummary.setAttribute('role', 'button');
    logSummary.setAttribute('tabindex', '0');
    logSummary.setAttribute('aria-expanded', 'false');
    
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
            logDetails.appendChild(createLogEntry(log));
        });
    }
    
    wireLogToggle(logSummary, logDetails, expandIcon);
    
    logContainer.appendChild(logSummary);
    logContainer.appendChild(logDetails);
    
    return logContainer;
}

export function createDynamicLogContainer() {
    const logContainer = document.createElement('div');
    logContainer.className = 'log-container message-thoughts-block';
    
    const logSummary = document.createElement('div');
    logSummary.className = 'log-summary';
    logSummary.setAttribute('role', 'button');
    logSummary.setAttribute('tabindex', '0');
    logSummary.setAttribute('aria-expanded', 'true');
    
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
    
    wireLogToggle(logSummary, logDetails, expandIcon);
    
    logContainer.appendChild(logSummary);
    logContainer.appendChild(logDetails);
    
    return { logContainer, logSummary, logDetails, spinner, statusText, expandIcon };
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
