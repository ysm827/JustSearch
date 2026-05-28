import { initializeAuth, normalizeSettings } from './modules/auth.js?v=1';
import { state, setCurrentSessionId, setLiveArtifactsMode } from './modules/state.js?v=2';
import { initUI, elements } from './modules/ui.js?v=20';
import { setupChatHandler, syncQuickSettingsFromState } from './modules/chat.js?v=26';
import { setupBrowserModal } from './modules/browser-modal.js?v=4';
import { openHistorySearch, renderHistory, setupHistoryGroups, setupHistorySearch, updateActiveHistoryItem } from './modules/history-view.js?v=22';
import { setupSettingsModal } from './modules/settings-modal.js?v=43';
import { setupSidebar, toggleSidebarFromShortcut } from './modules/sidebar.js?v=16';
import { initCustomModelSelect, syncCustomModelSelect } from './modules/model-selector.js?v=14';
import { getSupportedModelItems, splitModelItem } from './modules/provider-models.js?v=1';
import * as API from './modules/api.js?v=4';

document.addEventListener('DOMContentLoaded', async () => {
    initUI();
    initializeAuth();
    initCustomModelSelect();

    const settings = normalizeSettings(await API.fetchSettings());
    setLiveArtifactsMode(settings.live_artifacts_mode);
    updateModelSelector(settings);

    const [chatHistory, chatGroups] = await Promise.all([
        API.fetchHistory(),
        API.fetchChatGroups()
    ]);
    const { loadChat, deleteChat } = setupChatHandler(elements, renderHistory);
    const historyCallbacks = { onSelect: loadChat, onDelete: deleteChat };

    renderHistory(chatHistory, state.currentSessionId, historyCallbacks, chatGroups);
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
    setupHistoryGroups(historyCallbacks);
    setupHistorySearch(historyCallbacks);
    setupSystemThemeListener();
    setupPwaInstallPrompt();
    setupSuggestionChips();
    setupKeyboardShortcuts();
    setupContextMenuSuppression();
});

function updateModelSelector(settings) {
    const select = document.getElementById('model-select');
    if (!select) return;

    const selectedOption = select.options[select.selectedIndex];
    const currentKey = selectedOption
        ? `${selectedOption.dataset.providerId || ''}:${selectedOption.value}`
        : '';
    select.innerHTML = '';

    const providers = Array.isArray(settings?.providers) ? settings.providers : [];
    if (providers.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'Default';
        option.dataset.providerId = '';
        select.appendChild(option);
        syncCustomModelSelect();
        return;
    }

    providers.forEach(provider => {
        const providerId = String(provider.id || '').trim();
        if (!providerId) return;

        const models = getSupportedModelItems(provider.model_id);
        models.forEach(model => {
            const option = document.createElement('option');
            const { modelId: val, displayName } = splitModelItem(model);
            if (!val) return;
            option.value = val;
            option.textContent = `${displayName} · ${provider.name || providerId}`;
            option.title = `${providerId} / ${val}`;
            option.dataset.providerId = providerId;
            option.dataset.providerName = provider.name || providerId;
            option.dataset.modelDisplayName = displayName;
            select.appendChild(option);
        });
    });

    const preferredProviderId = settings?.default_provider_id || providers[0]?.id || '';
    let selected = Array.from(select.options).find(
        option => `${option.dataset.providerId || ''}:${option.value}` === currentKey
    );
    if (!selected) {
        selected = Array.from(select.options).find(
            option => option.dataset.providerId === preferredProviderId
        );
    }
    if (selected) {
        selected.selected = true;
    }
    syncCustomModelSelect();
}


function restoreSessionFromUrl(chatHistory, loadChat) {
    const pathMatch = window.location.pathname.match(/^\/c\/([^/?#]+)\/?$/);
    if (!pathMatch) return;

    let urlSessionId = '';
    try {
        urlSessionId = decodeURIComponent(pathMatch[1]);
    } catch (e) {
        window.history.replaceState(null, '', '/');
        return;
    }
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
            import('./modules/utils.js?v=3').then(m => m.applyTheme('auto'));
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
            openHistorySearch();
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
