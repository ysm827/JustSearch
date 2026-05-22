import { buildAuthenticatedUrl } from './auth.js';
import { renameChatAPI } from './api.js';
import { state } from './state.js';
import { showToast } from './toast.js';
import { elements } from './ui.js';

let _fullHistory = [];

export function getCachedHistory() {
    return _fullHistory;
}

function updateCachedHistoryTitle(chatId, title) {
    _fullHistory = _fullHistory.map(chat => (
        chat.id === chatId ? { ...chat, title } : chat
    ));
}

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

function renderEmptyHistory(iconName, message) {
    const emptyState = document.createElement('div');
    emptyState.className = 'history-no-results';

    let svgHtml = '';
    if (iconName === 'search_off') {
        svgHtml = `<svg class="history-no-results-icon" xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line><line x1="8" y1="11" x2="14" y2="11"></line></svg>`;
    } else {
        svgHtml = `<svg class="history-no-results-icon" xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
    }

    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = svgHtml.trim();
    const icon = tempDiv.firstChild;
    icon.setAttribute('aria-hidden', 'true');
    emptyState.appendChild(icon);

    const label = document.createElement('span');
    label.className = 'history-no-results-label';
    label.textContent = message;
    emptyState.appendChild(label);

    elements.historyList.appendChild(emptyState);
}

export function renderHistory(history, currentSessionId, callbacks) {
    _fullHistory = history || [];

    const { onSelect, onDelete } = callbacks;
    const searchTerm = elements.historySearchInput ? elements.historySearchInput.value.trim().toLowerCase() : '';

    elements.historyList.innerHTML = '';

    if (!history || history.length === 0) {
        if (searchTerm) {
            renderEmptyHistory('search_off', '未找到匹配的对话');
        } else {
            renderEmptyHistory('chat_bubble_outline', '暂无对话记录');
        }
        return;
    }

    let filtered = history;
    if (searchTerm) {
        const terms = searchTerm.split(/\s+/).filter(t => t);
        filtered = history.filter(chat => {
            const title = (chat.title || '新对话').toLowerCase();
            return terms.every(term => title.includes(term));
        });
        if (filtered.length === 0) {
            renderEmptyHistory('search_off', '未找到匹配的对话');
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

            titleSpan.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                startRename(titleSpan, chat.id, callbacks);
            });

            // Create actions container
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'history-item-actions';

            // 1. Rename button
            const renameBtn = document.createElement('button');
            renameBtn.className = 'history-action-btn history-rename-btn';
            renameBtn.title = '重命名';
            renameBtn.setAttribute('aria-label', '重命名对话');
            renameBtn.innerHTML = '<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>';
            renameBtn.onclick = (e) => {
                e.stopPropagation();
                startRename(titleSpan, chat.id, callbacks);
            };
            actionsContainer.appendChild(renameBtn);

            // 2. Share button
            const shareBtn = document.createElement('button');
            shareBtn.className = 'history-action-btn history-share-btn';
            shareBtn.title = '复制链接';
            shareBtn.setAttribute('aria-label', '复制对话链接');
            shareBtn.innerHTML = '<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>';
            shareBtn.onclick = (e) => {
                e.stopPropagation();
                const url = `${window.location.origin}/c/${chat.id}`;
                navigator.clipboard.writeText(url).then(() => {
                    showToast('对话链接已复制', 'success');
                }).catch(() => {
                    showToast('复制失败', 'error');
                });
            };
            actionsContainer.appendChild(shareBtn);

            // 3. Export button
            const exportBtn = document.createElement('button');
            exportBtn.className = 'history-action-btn history-export-btn';
            exportBtn.title = '导出';
            exportBtn.setAttribute('aria-label', '导出对话');
            exportBtn.innerHTML = '<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" x2="12" y1="15" y2="3"></line></svg>';
            exportBtn.onclick = (e) => {
                e.stopPropagation();
                window.open(buildAuthenticatedUrl(`/api/history/${chat.id}/export`), '_blank');
            };
            actionsContainer.appendChild(exportBtn);

            // 4. Delete button
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'history-action-btn history-delete-btn';
            deleteBtn.title = '删除';
            deleteBtn.setAttribute('aria-label', '删除对话');
            deleteBtn.innerHTML = '<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" x2="10" y1="11" y2="17"></line><line x1="14" x2="14" y1="11" y2="17"></line></svg>';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                onDelete(chat.id);
            };
            actionsContainer.appendChild(deleteBtn);

            item.appendChild(actionsContainer);

            item.dataset.id = chat.id;
            item.onclick = () => onSelect(chat.id);
            group.appendChild(item);
        });

        elements.historyList.appendChild(group);
    });
}

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
                updateCachedHistoryTitle(chatId, newTitle);
                showToast('已重命名', 'success');
            } else {
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

export function setupHistorySearch(callbacks) {
    const searchInput = document.getElementById('history-search-input');
    if (!searchInput) return;

    let searchTimeout;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            renderHistory(getCachedHistory(), state.currentSessionId, callbacks);
        }, 200);
    });
}
