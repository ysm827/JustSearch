import { authFetch } from './auth.js';
import { setCurrentSessionId, state } from './state.js';
import { showToast } from './toast.js';
import { elements, showConfirm } from './ui.js';
import { renderHistory } from './history-view.js';
import * as API from './api.js';

export function setupSettingsModal({ updateModelSelector, historyCallbacks, onSettingsSaved }) {
    const settingsBtn = document.getElementById('settings-btn');
    const closeBtn = elements.settingsModal.querySelector('.close-btn');
    const cancelSettingsBtn = document.getElementById('cancel-settings-btn');
    const saveSettingsBtn = document.getElementById('save-settings-btn');
    const resetSettingsBtn = document.getElementById('reset-settings-btn');
    const clearHistoryBtn = document.getElementById('clear-history-btn');
    const clearCacheBtn = document.getElementById('clear-cache-btn');

    // Tab Switching Logic
    const tabs = elements.settingsModal.querySelectorAll('.settings-tab-btn');
    const panels = elements.settingsModal.querySelectorAll('.settings-panel');

    function switchTab(tabId) {
        // Validate tabId exists, otherwise fallback to 'general'
        const hasTab = Array.from(tabs).some(tab => tab.getAttribute('data-tab') === tabId);
        const activeTabId = hasTab ? tabId : 'general';

        tabs.forEach(tab => {
            const isActive = tab.getAttribute('data-tab') === activeTabId;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        panels.forEach(panel => {
            panel.classList.toggle('active', panel.id === `tab-${activeTabId}`);
        });
        localStorage.setItem('justsearch_settings_last_tab', activeTabId);
    }

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const tabId = tab.getAttribute('data-tab');
            switchTab(tabId);
        });
    });

    const openSettings = async () => {
        const sidebar = document.getElementById('sidebar');
        const mobileOverlay = document.getElementById('mobile-overlay');
        if (sidebar) {
            sidebar.classList.remove('mobile-open');
        }
        if (mobileOverlay) {
            mobileOverlay.classList.remove('active');
        }
        const lastTab = localStorage.getItem('justsearch_settings_last_tab') || 'general';
        switchTab(lastTab);
        elements.settingsModal.classList.add('active');
        await updateVersionDisplay();
        await populateSettingsForm();
    };

    settingsBtn.addEventListener('click', openSettings);

    const miniSettingsBtn = document.getElementById('mini-settings-btn');
    if (miniSettingsBtn) {
        miniSettingsBtn.addEventListener('click', openSettings);
    }

    closeBtn.addEventListener('click', () => {
        elements.settingsModal.classList.remove('active');
    });

    if (cancelSettingsBtn) {
        cancelSettingsBtn.addEventListener('click', () => {
            elements.settingsModal.classList.remove('active');
        });
    }

    window.addEventListener('click', (event) => {
        if (event.target === elements.settingsModal) {
            elements.settingsModal.classList.remove('active');
        }
    });

    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', async () => {
            const newSettings = collectSettingsForm();
            if (await API.saveSettingsAPI(newSettings)) {
                updateModelSelector(newSettings.model_id);
                if (typeof onSettingsSaved === 'function') {
                    onSettingsSaved();
                }
                elements.settingsModal.classList.remove('active');
                showToast('设置已保存', 'success');
            } else {
                showToast('保存设置失败', 'error');
            }
        });
    }

    resetSettingsBtn.addEventListener('click', async () => {
        if (!(await showConfirm('您确定要恢复默认设置吗？', '恢复默认设置'))) return;
        const defaults = await API.restoreDefaultSettingsAPI();
        if (defaults) {
            fillSettingsForm(defaults);
            if (typeof onSettingsSaved === 'function') {
                onSettingsSaved();
            }
            showToast('已恢复默认设置', 'success');
        } else {
            showToast('加载默认设置失败', 'error');
        }
    });

    clearHistoryBtn.addEventListener('click', async () => {
        if (!(await showConfirm('确定要清除所有对话历史吗？此操作不可撤销。', '清除历史记录'))) return;
        if (await API.clearHistoryAPI()) {
            resetConversationView(historyCallbacks);
            elements.settingsModal.classList.remove('active');
            showToast('历史记录已清除', 'success');
        } else {
            showToast('清除历史记录失败', 'error');
        }
    });

    if (clearCacheBtn) {
        clearCacheBtn.addEventListener('click', async () => {
            if (!(await showConfirm('此操作将清除所有聊天记录、浏览器缓存（Cookies 等）并重置设置为默认值。确定要继续吗？此操作不可撤销。', '清除全部缓存'))) return;
            if (await API.clearCacheAPI()) {
                resetConversationView(historyCallbacks);
                elements.settingsModal.classList.remove('active');
                showToast('全部缓存已清除，页面即将刷新', 'success');
                setTimeout(() => window.location.reload(), 1500);
            } else {
                showToast('清除缓存失败', 'error');
            }
        });
    }

    setupPasswordControls();
    initModelListUI();

    // Auto-save logic
    let saveTimeout = null;
    function triggerAutoSave() {
        if (saveTimeout) clearTimeout(saveTimeout);
        saveTimeout = setTimeout(async () => {
            const newSettings = collectSettingsForm();
            if (await API.saveSettingsAPI(newSettings)) {
                updateModelSelector(newSettings.model_id);
                if (typeof onSettingsSaved === 'function') {
                    onSettingsSaved();
                }
            } else {
                showToast('保存设置失败', 'error');
            }
        }, 500);
    }

    const autoSaveInputs = [
        'theme-select',
        'engine-select',
        'max-results-input',
        'max-iterations-input',
        'api-key-input',
        'base-url-input',
        'model-input',
        'interactive-search-input',
        'max-concurrent-pages-input',
        'max-context-turns-input'
    ];

    autoSaveInputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const eventType = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
            el.addEventListener(eventType, triggerAutoSave);
        }
    });
}

async function updateVersionDisplay() {
    const versionEl = document.getElementById('version-display');
    const aboutVersionEl = document.getElementById('about-version');
    if (!versionEl && !aboutVersionEl) return;

    try {
        const healthRes = await authFetch('/api/health');
        if (healthRes.ok) {
            const health = await healthRes.json();
            const versionText = formatVersionText(health.version);
            if (versionEl) {
                versionEl.textContent = versionText;
            }
            if (aboutVersionEl) {
                aboutVersionEl.textContent = versionText;
            }
            if (versionEl && health.memory_mb) {
                versionEl.title = `Memory: ${health.memory_mb} MB`;
            }
        }
    } catch (e) {
        // Version metadata is non-critical.
    }
}

function formatVersionText(version) {
    const rawVersion = String(version || '?.?.?').trim();
    if (!rawVersion || rawVersion === '?.?.?') return 'v?.?.?';
    return rawVersion.startsWith('v') || /[^\d.]/.test(rawVersion)
        ? rawVersion
        : `v${rawVersion}`;
}

async function populateSettingsForm() {
    fillSettingsForm(state.settings);

    const starsCountElement = document.getElementById('github-stars-count');
    const aboutStarsCountElement = document.getElementById('about-stars-count');
    if (starsCountElement || aboutStarsCountElement) {
        const stats = await API.fetchGitHubStats();
        if (stats && stats.stars !== undefined) {
            if (starsCountElement) {
                starsCountElement.textContent = stats.stars;
            }
            if (aboutStarsCountElement) {
                aboutStarsCountElement.textContent = stats.stars;
            }
        }
    }
}

function fillSettingsForm(settings) {
    document.getElementById('theme-select').value = settings.theme || 'light';
    document.getElementById('engine-select').value = settings.search_engine || 'duckduckgo';
    document.getElementById('max-results-input').value = settings.max_results || 8;
    document.getElementById('max-iterations-input').value = settings.max_iterations || 5;
    document.getElementById('api-key-input').value = settings.api_key || '';
    document.getElementById('api-key-input').placeholder = settings.api_key ? '已配置 (留空保持不变)' : '输入 API Key';
    document.getElementById('base-url-input').value = settings.base_url || '';
    document.getElementById('model-input').value = settings.model_id || '';
    
    const modelInput = document.getElementById('model-input');
    if (modelInput && typeof modelInput.renderModelRows === 'function') {
        modelInput.renderModelRows();
    }

    document.getElementById('interactive-search-input').checked = settings.interactive_search !== undefined ? settings.interactive_search : true;
    document.getElementById('max-concurrent-pages-input').value = settings.max_concurrent_pages || 10;
    document.getElementById('max-context-turns-input').value = settings.max_context_turns || 6;
}

function collectSettingsForm() {
    const apiKeyInput = document.getElementById('api-key-input');
    let apiKeyValue = apiKeyInput.value.trim();

    if (apiKeyValue && apiKeyValue.includes('****')) {
        apiKeyValue = '';
    }

    return {
        theme: document.getElementById('theme-select').value,
        search_engine: document.getElementById('engine-select').value,
        max_results: parseInt(document.getElementById('max-results-input').value) || 8,
        max_iterations: parseInt(document.getElementById('max-iterations-input').value) || 5,
        api_key: apiKeyValue,
        base_url: document.getElementById('base-url-input').value,
        model_id: document.getElementById('model-input').value,
        interactive_search: document.getElementById('interactive-search-input').checked,
        max_concurrent_pages: parseInt(document.getElementById('max-concurrent-pages-input').value) || 10,
        max_context_turns: parseInt(document.getElementById('max-context-turns-input').value) || 6,
    };
}

function setupPasswordControls() {
    const toggleBtn = document.getElementById('toggle-api-key-btn');
    const apiKeyInput = document.getElementById('api-key-input');

    if (toggleBtn && apiKeyInput) {
        toggleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const type = apiKeyInput.getAttribute('type') === 'password' ? 'text' : 'password';
            apiKeyInput.setAttribute('type', type);

            const icon = toggleBtn.querySelector('.material-symbols-rounded');
            if (icon) {
                icon.textContent = type === 'password' ? 'visibility' : 'visibility_off';
            }
        });
    }

    const validateBtn = document.getElementById('validate-key-btn');
    if (validateBtn) {
        validateBtn.addEventListener('click', validateApiKey);
    }
}

async function validateApiKey(e) {
    e.preventDefault();
    const validateBtn = e.currentTarget;
    const apiKey = document.getElementById('api-key-input').value.trim();
    const baseUrl = document.getElementById('base-url-input').value.trim();
    const modelId = document.getElementById('model-input').value.trim();
    if (!apiKey) {
        showToast('请先输入 API 密钥', 'warning');
        return;
    }

    validateBtn.disabled = true;
    validateBtn.classList.add('is-validating');
    const validateIcon = validateBtn.querySelector('.material-symbols-rounded');
    if (validateIcon) {
        validateIcon.textContent = 'progress_activity';
    }
    try {
        const res = await authFetch('/api/settings/validate-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: apiKey,
                base_url: baseUrl,
                model_id: (() => {
                    let first = modelId.split(',')[0].trim();
                    return first.includes(':') ? first.split(':')[0].trim() : first;
                })(),
            }),
        });
        const data = await res.json();
        if (data.valid) {
            showToast('API 密钥验证通过', 'success');
        } else {
            showToast(data.error || '验证失败', 'error');
        }
    } catch (err) {
        showToast('验证请求失败', 'error');
    } finally {
        validateBtn.disabled = false;
        validateBtn.classList.remove('is-validating');
        if (validateIcon) {
            validateIcon.textContent = 'verified';
        }
    }
}

function resetConversationView(historyCallbacks) {
    setCurrentSessionId(null);
    if (elements.historySearchInput) {
        elements.historySearchInput.value = '';
    }
    renderHistory([], state.currentSessionId, historyCallbacks);
    elements.chatContainer.innerHTML = '';
    elements.heroSection.style.display = 'block';
    elements.chatContainer.appendChild(elements.heroSection);
}

function initModelListUI() {
    const container = document.getElementById('model-list-container');
    const addButton = document.getElementById('add-model-btn');
    const hiddenInput = document.getElementById('model-input');
    if (!container || !addButton || !hiddenInput) return;

    function render() {
        container.innerHTML = '';
        const value = hiddenInput.value || '';
        const items = value.split(',').map(s => s.trim()).filter(Boolean);
        
        if (items.length === 0) {
            addModelRow('', '');
            return;
        }

        items.forEach(item => {
            let id = item;
            let name = '';
            const colonIdx = item.indexOf(':');
            if (colonIdx !== -1) {
                id = item.substring(0, colonIdx).trim();
                name = item.substring(colonIdx + 1).trim();
            }
            addModelRow(id, name);
        });
    }

    function serialize() {
        const rows = container.querySelectorAll('.model-row');
        const serialized = Array.from(rows)
            .map(row => {
                const id = row.querySelector('.model-id-input').value.trim();
                const name = row.querySelector('.model-name-input').value.trim();
                if (!id) return '';
                return name ? `${id}:${name}` : id;
            })
            .filter(Boolean)
            .join(', ');
        hiddenInput.value = serialized;
        hiddenInput.dispatchEvent(new Event('input'));
    }

    function addModelRow(id = '', name = '') {
        const row = document.createElement('div');
        row.className = 'model-row';
        row.innerHTML = `
            <input type="text" class="model-id-input" placeholder="模型 ID" value="${escapeHtml(id)}">
            <input type="text" class="model-name-input" placeholder="显示名称" value="${escapeHtml(name)}">
            <button type="button" class="remove-model-btn" title="删除模型">
                <span class="material-symbols-rounded">delete</span>
            </button>
        `;

        row.querySelector('.model-id-input').addEventListener('input', serialize);
        row.querySelector('.model-name-input').addEventListener('input', serialize);

        row.querySelector('.remove-model-btn').addEventListener('click', () => {
            row.remove();
            serialize();
            if (container.querySelectorAll('.model-row').length === 0) {
                addModelRow('', '');
            }
        });

        container.appendChild(row);
    }

    addButton.addEventListener('click', () => {
        addModelRow('', '');
        serialize();
    });

    hiddenInput.addEventListener('change', render);
    hiddenInput.renderModelRows = render;
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
