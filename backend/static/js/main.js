import { initializeAuth, normalizeSettings } from './modules/auth.js';
import { state, setCurrentSessionId } from './modules/state.js';
import { initUI, elements } from './modules/ui.js';
import { setupChatHandler, syncQuickSettingsFromState } from './modules/chat.js';
import { setupBrowserModal } from './modules/browser-modal.js';
import { renderHistory, setupHistorySearch, updateActiveHistoryItem } from './modules/history-view.js';
import { setupSettingsModal } from './modules/settings-modal.js';
import { setupSidebar, toggleSidebarFromShortcut } from './modules/sidebar.js';
import { initCustomModelSelect, syncCustomModelSelect } from './modules/model-selector.js';
import * as API from './modules/api.js';

document.addEventListener('DOMContentLoaded', async () => {
    initUI();
    initializeAuth();
    initCustomModelSelect();

    const settings = normalizeSettings(await API.fetchSettings());
    updateModelSelector(settings.model_id || '');

    const chatHistory = await API.fetchHistory();
    const { loadChat, deleteChat } = setupChatHandler(elements, renderHistory);
    const historyCallbacks = { onSelect: loadChat, onDelete: deleteChat };

    renderHistory(chatHistory, state.currentSessionId, historyCallbacks);
    restoreSessionFromUrl(chatHistory, loadChat);

    window.addEventListener('popstate', (event) => {
        if (event.state && event.state.sessionId) {
            loadChat(event.state.sessionId);
        } else {
            showHomeState();
        }
    });

    setupSidebar(loadChat);
    setupSettingsModal({ 
        updateModelSelector, 
        historyCallbacks, 
        onSettingsSaved: syncQuickSettingsFromState 
    });
    setupBrowserModal();
    setupHistorySearch(historyCallbacks);
    setupSystemThemeListener();
    setupPwaInstallPrompt();
    setupSuggestionChips();
    setupKeyboardShortcuts();
    setupContextMenuSuppression();
});

function updateModelSelector(modelString) {
    const select = document.getElementById('model-select');
    if (!select) return;

    const currentVal = select.value;
    select.innerHTML = '';

    const models = (modelString || '').split(',').map(s => s.trim()).filter(Boolean);
    if (models.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'Default';
        select.appendChild(option);
        syncCustomModelSelect();
        return;
    }

    const cleanModelIds = [];

    models.forEach(model => {
        const option = document.createElement('option');
        let val = model;
        let displayName = model;
        const colonIdx = model.indexOf(':');
        if (colonIdx !== -1) {
            val = model.substring(0, colonIdx).trim();
            displayName = model.substring(colonIdx + 1).trim();
        } else {
            displayName = val.includes('/') ? val.split('/').pop() : val;
        }
        option.value = val;
        option.textContent = displayName;
        option.title = val;
        select.appendChild(option);
        cleanModelIds.push(val);
    });

    select.value = cleanModelIds.includes(currentVal) ? currentVal : cleanModelIds[0];
    syncCustomModelSelect();
}

function restoreSessionFromUrl(chatHistory, loadChat) {
    const pathMatch = window.location.pathname.match(/^\/c\/([a-zA-Z0-9_-]+)/);
    if (!pathMatch) return;

    const urlSessionId = pathMatch[1];
    const exists = chatHistory.some(h => h.id === urlSessionId);
    if (exists) {
        loadChat(urlSessionId);
    } else {
        window.history.replaceState(null, '', '/');
    }
}

function showHomeState() {
    setCurrentSessionId(null);
    elements.chatContainer.innerHTML = '';
    elements.heroSection.style.display = 'block';
    elements.chatContainer.appendChild(elements.heroSection);
    updateActiveHistoryItem(null);
}

function setupSystemThemeListener() {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if ((state.settings.theme || 'light') === 'auto') {
            import('./modules/utils.js').then(m => m.applyTheme('auto'));
        }
    });
}

function setupPwaInstallPrompt() {
    window.addEventListener('beforeinstallprompt', (event) => {
        event.preventDefault();
    });
}

function setupSuggestionChips() {
    document.querySelectorAll('.suggestion-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const query = chip.dataset.query;
            if (!query) return;

            elements.userInput.value = query;
            elements.userInput.dispatchEvent(new Event('input'));
            elements.sendBtn.click();
        });
    });
}

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            const activeModals = document.querySelectorAll('.modal.active');
            const activeModal = activeModals[activeModals.length - 1];
            if (activeModal) {
                activeModal.classList.remove('active');
            }
        }

        if ((event.ctrlKey || event.metaKey) && event.key === 'n') {
            event.preventDefault();
            elements.newChatBtn.click();
        }

        if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
            event.preventDefault();
            elements.historySearchInput?.focus();
        }

        if ((event.ctrlKey || event.metaKey) && event.key === '/') {
            event.preventDefault();
            toggleSidebarFromShortcut();
        }
    });
}

function setupContextMenuSuppression() {
    document.addEventListener('contextmenu', (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;

        if (target.closest('input, textarea, select, [contenteditable="true"]')) {
            return;
        }

        if (target.closest('.hero-header, .hero-brand-logo, .hero-container, #main, #sidebar, #mobile-overlay, .modal')) {
            event.preventDefault();
        }
    }, { capture: true });
}
