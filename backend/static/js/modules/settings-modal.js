import { authFetch } from './auth.js?v=1';
import { coerceBooleanSetting, setCurrentSessionId, state } from './state.js?v=2';
import { showToast } from './toast.js';
import { elements, showConfirm } from './ui.js?v=15';
import { renderHistory } from './history-view.js?v=22';
import * as API from './api.js?v=3';

const WORKFLOW_STEPS = [
    { id: 'analysis', label: '问题分析' },
    { id: 'relevance', label: '相关性评估' },
    { id: 'interaction', label: '页面交互' },
    { id: 'answer', label: '最终回答' },
];

const PROVIDER_ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;
const SETTINGS_SAVE_STATES = {
    saved: { icon: 'check_circle', text: '已保存' },
    pending: { icon: 'sync', text: '待保存' },
    saving: { icon: 'progress_activity', text: '保存中' },
    invalid: { icon: 'error', text: '需要补全' },
    error: { icon: 'warning', text: '保存失败' },
};

let isApplyingSettingsForm = false;
let requestSettingsAutoSave = () => {};
let flushSettingsAutoSave = () => Promise.resolve(false);

export function setupSettingsModal({ updateModelSelector, historyCallbacks, onSettingsSaved }) {
    const settingsBtn = document.getElementById('settings-btn');
    const closeBtn = elements.settingsModal.querySelector('.close-btn');
    const resetSettingsBtn = document.getElementById('reset-settings-btn');
    const clearHistoryBtn = document.getElementById('clear-history-btn');
    const clearCacheBtn = document.getElementById('clear-cache-btn');
    const exportHistoryBtn = document.getElementById('export-history-btn');
    const importHistoryBtn = document.getElementById('import-history-btn');
    const historyImportInput = document.getElementById('history-import-input');

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
        await API.fetchSettings();
        await populateSettingsForm(rememberCurrentSettingsPayload);
    };

    settingsBtn.addEventListener('click', openSettings);

    const miniSettingsBtn = document.getElementById('mini-settings-btn');
    if (miniSettingsBtn) {
        miniSettingsBtn.addEventListener('click', openSettings);
    }

    const closeSettingsModal = async () => {
        try {
            await flushSettingsAutoSave();
        } finally {
            elements.settingsModal.classList.remove('active');
        }
    };

    closeBtn.addEventListener('click', closeSettingsModal);

    window.addEventListener('click', (event) => {
        if (event.target === elements.settingsModal) {
            closeSettingsModal();
        }
    });

    resetSettingsBtn.addEventListener('click', async () => {
        if (!(await showConfirm('您确定要恢复默认设置吗？', '恢复默认设置'))) return;
        const defaults = await API.restoreDefaultSettingsAPI();
        if (defaults) {
            fillSettingsForm(defaults);
            if (await flushSettingsAutoSave()) {
                showToast('已恢复默认设置', 'success');
            } else {
                showToast('恢复默认设置失败', 'error');
            }
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

    if (exportHistoryBtn) {
        exportHistoryBtn.addEventListener('click', async () => {
            exportHistoryBtn.disabled = true;
            try {
                if (await API.exportHistoryAPI()) {
                    showToast('聊天记录已导出', 'success');
                } else {
                    showToast('导出聊天记录失败', 'error');
                }
            } finally {
                exportHistoryBtn.disabled = false;
            }
        });
    }

    if (importHistoryBtn && historyImportInput) {
        importHistoryBtn.addEventListener('click', () => {
            historyImportInput.click();
        });
        historyImportInput.addEventListener('change', async () => {
            const file = historyImportInput.files?.[0];
            historyImportInput.value = '';
            if (!file) return;
            await importHistoryFile(file, historyCallbacks, importHistoryBtn);
        });
    }

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

    setupEngineCheckControls();
    initProviderListUI();

    let saveTimeout = null;
    let saveInFlight = false;
    let saveAgain = false;
    let lastSavedPayload = '';

    setSettingsSaveStatus('saved');

    function rememberCurrentSettingsPayload() {
        const currentSettings = collectSettingsForm();
        lastSavedPayload = canAutoSaveSettings(currentSettings)
            ? JSON.stringify(currentSettings)
            : '';
        updateProviderValidationUI(currentSettings);
        updateProviderCountLabel(currentSettings.providers.length);
        setSettingsSaveStatus('saved');
    }

    async function persistSettings() {
        if (isApplyingSettingsForm) return false;
        if (saveInFlight) {
            saveAgain = true;
            return false;
        }

        const newSettings = collectSettingsForm();
        const validation = validateSettingsForm(newSettings);
        updateProviderValidationUI(newSettings, validation);
        updateProviderCountLabel(newSettings.providers.length);
        if (!validation.ok) {
            setSettingsSaveStatus('invalid', validation.message);
            return false;
        }

        const payload = JSON.stringify(newSettings);
        if (payload === lastSavedPayload) {
            setSettingsSaveStatus('saved');
            return true;
        }

        saveInFlight = true;
        setSettingsSaveStatus('saving');
        try {
            if (await API.saveSettingsAPI(newSettings)) {
                markSavedProviderIdentities();
                lastSavedPayload = JSON.stringify(collectSettingsForm());
                setSettingsSaveStatus('saved');
                updateModelSelector(state.settings);
                if (typeof onSettingsSaved === 'function') {
                    onSettingsSaved();
                }
                return true;
            }
            setSettingsSaveStatus('error', '自动保存失败，请稍后重试');
            showToast('自动保存设置失败', 'error');
            return false;
        } finally {
            saveInFlight = false;
            if (saveAgain) {
                saveAgain = false;
                requestSettingsAutoSave();
            }
        }
    }

    requestSettingsAutoSave = ({ immediate = false } = {}) => {
        if (isApplyingSettingsForm) return;
        if (saveTimeout) clearTimeout(saveTimeout);
        const settings = collectSettingsForm();
        const validation = validateSettingsForm(settings);
        updateProviderValidationUI(settings, validation);
        updateProviderCountLabel(settings.providers.length);
        setSettingsSaveStatus(validation.ok ? 'pending' : 'invalid', validation.ok ? '' : validation.message);
        saveTimeout = setTimeout(persistSettings, immediate ? 0 : 700);
    };

    flushSettingsAutoSave = async () => {
        if (saveTimeout) {
            clearTimeout(saveTimeout);
            saveTimeout = null;
        }
        return persistSettings();
    };

    const autoSaveInputs = [
        'theme-select',
        'engine-select',
        'max-results-input',
        'max-iterations-input',
        'interactive-search-input',
        'max-concurrent-pages-input'
    ];

    autoSaveInputs.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const eventType = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
            el.addEventListener(eventType, () => requestSettingsAutoSave());
        }
    });
}

async function importHistoryFile(file, historyCallbacks, importHistoryBtn) {
    if (!file.name.toLowerCase().endsWith('.json')) {
        showToast('请选择 JSON 文件', 'warning');
        return;
    }

    importHistoryBtn.disabled = true;
    try {
        const text = await file.text();
        let payload;
        try {
            payload = JSON.parse(text);
        } catch (e) {
            showToast('JSON 文件格式不正确', 'error');
            return;
        }

        const result = await API.importHistoryAPI(payload);
        if (!result || result.status !== 'ok') {
            showToast(result?.detail || '导入聊天记录失败', 'error');
            return;
        }

        const [history, groups] = await Promise.all([
            API.fetchHistory(),
            API.fetchChatGroups(),
        ]);
        renderHistory(history, state.currentSessionId, historyCallbacks, groups);
        showToast(
            `已导入 ${result.imported_sessions || 0} 个对话，跳过 ${result.skipped_sessions || 0} 个重复对话`,
            'success',
        );
    } finally {
        importHistoryBtn.disabled = false;
    }
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

async function populateSettingsForm(onFilled) {
    fillSettingsForm(state.settings);
    if (typeof onFilled === 'function') {
        onFilled();
    }

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
    isApplyingSettingsForm = true;
    try {
        document.getElementById('theme-select').value = settings.theme || 'light';
        document.getElementById('engine-select').value = settings.search_engine || 'searxng';
        document.getElementById('max-results-input').value = normalizeNumberSetting(settings.max_results, 50, 1, 50);
        document.getElementById('max-iterations-input').value = normalizeNumberSetting(settings.max_iterations, 5, 1, 10);
        renderProviderList(settings.providers || [], settings.default_provider_id || '');
        renderWorkflowStepModels(
            settings.workflow_step_models || {},
            settings.providers || [],
            settings.default_provider_id || '',
        );
        document.getElementById('interactive-search-input').checked = coerceBooleanSetting(settings.interactive_search, true);
        document.getElementById('max-concurrent-pages-input').value = normalizeNumberSetting(settings.max_concurrent_pages, 10, 1, 20);
        updateProviderValidationUI();
        updateProviderCountLabel(collectProvidersForm().length);
        setSettingsSaveStatus('saved');
    } finally {
        isApplyingSettingsForm = false;
    }
}

function collectSettingsForm() {
    const providers = collectProvidersForm();
    const defaultProvider = document.querySelector('input[name="default-provider-radio"]:checked');
    return {
        theme: document.getElementById('theme-select').value,
        search_engine: document.getElementById('engine-select').value,
        max_results: normalizeNumberSetting(document.getElementById('max-results-input').value, 50, 1, 50),
        max_iterations: normalizeNumberSetting(document.getElementById('max-iterations-input').value, 5, 1, 10),
        default_provider_id: defaultProvider?.value || providers[0]?.id || '',
        providers,
        workflow_step_models: collectWorkflowStepModels(),
        interactive_search: document.getElementById('interactive-search-input').checked,
        max_concurrent_pages: normalizeNumberSetting(document.getElementById('max-concurrent-pages-input').value, 10, 1, 20),
    };
}

function normalizeNumberSetting(value, fallback, min, max) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function canAutoSaveSettings(settings) {
    return validateSettingsForm(settings).ok;
}

function validateSettingsForm(settings) {
    const providers = Array.isArray(settings?.providers) ? settings.providers : [];
    const errors = [];
    const providerIds = new Map();

    if (providers.length === 0) {
        errors.push({ message: '至少需要一个 Provider' });
    }

    providers.forEach((provider, index) => {
        const id = String(provider.id || '').trim();
        const baseUrl = String(provider.base_url || '').trim();
        const modelId = String(provider.model_id || '').trim();

        if (!id) {
            errors.push({ index, field: 'id', message: 'Provider ID 不能为空' });
        } else if (!PROVIDER_ID_PATTERN.test(id)) {
            errors.push({ index, field: 'id', message: '仅支持字母、数字、下划线和连字符' });
        } else if (providerIds.has(id)) {
            errors.push({ index, field: 'id', message: 'Provider ID 重复' });
            errors.push({ index: providerIds.get(id), field: 'id', message: 'Provider ID 重复' });
        } else {
            providerIds.set(id, index);
        }

        if (!baseUrl) {
            errors.push({ index, field: 'base_url', message: 'API 基础地址不能为空' });
        }

        if (!modelId) {
            errors.push({ index, field: 'model_id', message: '至少添加一个模型' });
        }
    });

    const defaultProviderId = String(settings.default_provider_id || '').trim();
    if (defaultProviderId && !providerIds.has(defaultProviderId)) {
        errors.push({ message: '默认 Provider 不存在' });
    }

    return {
        ok: errors.length === 0,
        errors,
        message: errors[0]?.message || '',
    };
}

function setSettingsSaveStatus(status, message = '') {
    const el = document.getElementById('settings-save-status');
    if (!el) return;

    const nextStatus = SETTINGS_SAVE_STATES[status] ? status : 'saved';
    const stateConfig = SETTINGS_SAVE_STATES[nextStatus];
    el.className = `settings-save-status is-${nextStatus}`;
    const icon = el.querySelector('.material-symbols-rounded');
    const text = el.querySelector('span:last-child');
    if (icon) {
        icon.textContent = stateConfig.icon;
    }
    if (text) {
        text.textContent = message || stateConfig.text;
    }
}

function updateProviderCountLabel(count) {
    const label = document.getElementById('provider-count-label');
    if (!label) return;
    label.textContent = `${count || 0} 个连接`;
}

function updateProviderValidationUI(settings = collectSettingsForm(), validation = validateSettingsForm(settings)) {
    clearProviderValidationUI();
    const cards = Array.from(document.querySelectorAll('.provider-card'));
    validation.errors.forEach((error) => {
        if (typeof error.index !== 'number') return;
        const card = cards[error.index];
        if (!card) return;
        const fieldMap = {
            id: '.provider-id-input',
            base_url: '.provider-base-url-input',
            model_id: '.model-settings-group',
        };
        const target = card.querySelector(fieldMap[error.field]);
        if (target) {
            markProviderFieldError(target, error.message);
        }
    });
}

function clearProviderValidationUI() {
    document.querySelectorAll('.provider-field-error').forEach(el => el.remove());
    document.querySelectorAll('.provider-card .has-error').forEach(el => el.classList.remove('has-error'));
    document.querySelectorAll('.provider-card [aria-invalid="true"]').forEach(el => {
        el.removeAttribute('aria-invalid');
    });
}

function markProviderFieldError(target, message) {
    const group = target.classList.contains('form-group') || target.classList.contains('model-settings-group')
        ? target
        : target.closest('.form-group');
    if (!group) return;
    group.classList.add('has-error');
    if ('setAttribute' in target && target.tagName !== 'DIV') {
        target.setAttribute('aria-invalid', 'true');
    }
    const error = document.createElement('div');
    error.className = 'provider-field-error';
    error.textContent = message;
    group.appendChild(error);
}

function setupEngineCheckControls() {
    const checkEnginesBtn = document.getElementById('check-engines-btn');
    if (checkEnginesBtn) {
        checkEnginesBtn.addEventListener('click', checkSearchEngines);
    }
}

async function checkSearchEngines(e) {
    e.preventDefault();
    const checkEnginesBtn = e.currentTarget;
    const resultsEl = document.getElementById('engine-check-results');
    const checkIcon = checkEnginesBtn.querySelector('.material-symbols-rounded');

    checkEnginesBtn.disabled = true;
    checkEnginesBtn.classList.add('is-checking');
    if (checkIcon) {
        checkIcon.textContent = 'progress_activity';
    }
    if (resultsEl) {
        resultsEl.classList.add('active');
        renderEngineCheckStatus(resultsEl, 'engine-check-pending', 'progress_activity', '正在检测搜索引擎...');
    }

    try {
        const data = await API.checkEnginesAPI();
        if (!data || !Array.isArray(data.results)) {
            showToast('搜索引擎检测失败', 'error');
            renderEngineCheckResults({ results: [] });
            return;
        }

        renderEngineCheckResults(data);
        const availableCount = data.results.filter(item => item.available).length;
        const totalCount = data.results.length;
        const toastType = availableCount === totalCount ? 'success' : 'warning';
        showToast(`搜索引擎检测完成：${availableCount}/${totalCount} 可用`, toastType);
    } catch (err) {
        showToast('搜索引擎检测请求失败', 'error');
        renderEngineCheckResults({ results: [] });
    } finally {
        checkEnginesBtn.disabled = false;
        checkEnginesBtn.classList.remove('is-checking');
        if (checkIcon) {
            checkIcon.textContent = 'network_check';
        }
    }
}

function renderEngineCheckResults(data) {
    const resultsEl = document.getElementById('engine-check-results');
    if (!resultsEl) return;

    const results = Array.isArray(data.results) ? data.results : [];
    resultsEl.classList.add('active');
    resultsEl.replaceChildren();

    if (results.length === 0) {
        renderEngineCheckStatus(resultsEl, 'engine-check-empty', 'error', '暂无检测结果');
        return;
    }

    if (data.query) {
        const queryEl = document.createElement('div');
        queryEl.className = 'engine-check-query';
        queryEl.textContent = `测试词：${data.query}`;
        resultsEl.appendChild(queryEl);
    }

    const list = document.createElement('div');
    list.className = 'engine-check-list';

    results.forEach(result => {
        const available = Boolean(result.available);
        const statusClass = available ? 'available' : 'unavailable';
        const icon = available ? 'check_circle' : 'error';
        const label = getEngineDisplayName(result.engine);
        const resultCount = Number(result.result_count || 0);
        const detail = available
            ? `可用 · ${Number.isFinite(resultCount) ? resultCount : 0} 个结果`
            : `不可用 · ${result.error || '未解析到搜索结果'}`;

        const item = document.createElement('div');
        item.className = `engine-check-result ${statusClass}`;

        const iconEl = document.createElement('span');
        iconEl.className = 'material-symbols-rounded';
        iconEl.textContent = icon;

        const copy = document.createElement('div');
        copy.className = 'engine-check-copy';

        const name = document.createElement('div');
        name.className = 'engine-check-name';
        name.textContent = label;

        const detailEl = document.createElement('div');
        detailEl.className = 'engine-check-detail';
        detailEl.textContent = detail;

        copy.append(name, detailEl);
        item.append(iconEl, copy);
        list.appendChild(item);
    });

    resultsEl.appendChild(list);
}

function renderEngineCheckStatus(resultsEl, className, icon, message) {
    resultsEl.replaceChildren();

    const wrapper = document.createElement('div');
    wrapper.className = className;

    const iconEl = document.createElement('span');
    iconEl.className = 'material-symbols-rounded';
    iconEl.textContent = icon;

    const copy = document.createElement('span');
    copy.textContent = message;

    wrapper.append(iconEl, copy);
    resultsEl.appendChild(wrapper);
}

function getEngineDisplayName(engine) {
    const names = {
        bing: 'Bing',
        sogou: '搜狗（中文备用）',
        searxng: 'SearXNG（默认，自托管）',
        duckduckgo: 'DuckDuckGo（易触发验证）',
        google: 'Google（易触发验证码）',
        brave: 'Brave Search',
    };
    return names[engine] || engine || 'Unknown';
}

async function validateApiKey(e) {
    e.preventDefault();
    const validateBtn = e.currentTarget;
    const providerCard = validateBtn.closest('.provider-card');
    const apiKey = providerCard.querySelector('.provider-api-key-input').value.trim();
    const baseUrl = providerCard.querySelector('.provider-base-url-input').value.trim();
    const modelId = providerCard.querySelector('.provider-model-input').value.trim();
    const providerId = providerCard.querySelector('.provider-id-input').value.trim();
    if (isUnsupportedGemini25Model(modelId)) {
        showToast('Gemini 2.5 系列模型不再支持', 'warning');
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
                provider_id: providerId,
                previous_provider_id: providerCard.dataset.savedProviderId || providerId,
                api_key: apiKey,
                base_url: baseUrl,
                model_id: (() => {
                    let first = modelId.split(',')[0].trim();
                    return splitModelItem(first).modelId;
                })(),
            }),
        });
        const data = await res.json();
        if (data.valid) {
            showToast('API 连接验证通过', 'success');
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
    renderHistory([], state.currentSessionId, historyCallbacks, []);
    elements.chatContainer.innerHTML = '';
    elements.heroSection.style.display = 'block';
    elements.chatContainer.appendChild(elements.heroSection);
}

function initProviderListUI() {
    const addButton = document.getElementById('add-provider-btn');
    if (!addButton) return;

    addButton.addEventListener('click', () => {
        const providers = collectProvidersForm();
        const currentDefaultProviderId = getSelectedDefaultProviderId();
        const newProvider = createEmptyProvider(providers.length + 1);
        providers.push(newProvider);
        renderProviderList(
            providers,
            resolveProviderDefaultId(providers, currentDefaultProviderId),
            { preserveCollapsed: true, expandedProviderId: newProvider.id },
        );
        requestSettingsAutoSave({ immediate: true });
    });
}

function renderProviderList(providers, defaultProviderId, options = {}) {
    const container = document.getElementById('provider-list-container');
    if (!container) return;

    const collapseStates = options.preserveCollapsed ? getProviderCollapseStates() : new Map();
    const expandedProviderId = String(options.expandedProviderId || '').trim();
    const items = Array.isArray(providers) && providers.length > 0
        ? providers
        : [createEmptyProvider(1)];
    const normalizedDefaultProviderId = String(defaultProviderId || '').trim();
    const fallbackDefault = normalizedDefaultProviderId || String(items[0]?.id || '').trim() || '';

    container.innerHTML = '';
    items.forEach((provider, index) => {
        const providerId = String(provider.id || `provider-${index + 1}`).trim();
        const collapsed = expandedProviderId && providerId === expandedProviderId
            ? false
            : collapseStates.has(providerId)
                ? collapseStates.get(providerId)
                : null;
        container.appendChild(createProviderCard(provider, fallbackDefault, index, { collapsed }));
    });
    updateProviderCountLabel(items.length);
    syncDefaultProviderBadges();
    refreshWorkflowStepModelOptions();
}

function getProviderCollapseStates() {
    const states = new Map();
    document.querySelectorAll('.provider-card').forEach((card) => {
        const providerId = card.querySelector('.provider-id-input')?.value.trim() || '';
        if (providerId) {
            states.set(providerId, card.classList.contains('collapsed'));
        }
    });
    return states;
}

function renderWorkflowStepModels(stepModels, providers, defaultProviderId) {
    const container = document.getElementById('workflow-step-models-container');
    if (!container) return;

    const options = getConfiguredModelOptions(providers);
    container.innerHTML = '';

    WORKFLOW_STEPS.forEach((step) => {
        const row = document.createElement('div');
        row.className = 'workflow-step-model-row';
        const selectId = `workflow-step-model-${step.id}`;
        const selected = stepModels?.[step.id] || {};
        const selectedValue = selected.provider_id && selected.model_id
            ? encodeStepModelValue(selected.provider_id, selected.model_id)
            : '';

        const optionHtml = [
            `<option value="">跟随聊天栏默认模型</option>`,
            ...getGroupedWorkflowModelOptions(options, selectedValue),
        ].join('');

        row.innerHTML = `
            <label for="${selectId}">${escapeHtml(step.label)}</label>
            <select id="${selectId}" class="workflow-step-model-select" data-step-id="${escapeHtml(step.id)}">
                ${optionHtml}
            </select>
        `;

        container.appendChild(row);
        row.querySelector('select').addEventListener('change', () => {
            requestSettingsAutoSave({ immediate: true });
        });
    });

    container.classList.toggle('is-empty', options.length === 0);
}

function refreshWorkflowStepModelOptions({ providerIdMap = null } = {}) {
    const container = document.getElementById('workflow-step-models-container');
    if (!container) return;
    const current = collectWorkflowStepModels();
    if (providerIdMap) {
        Object.values(current).forEach((stepModel) => {
            if (providerIdMap.has(stepModel.provider_id)) {
                stepModel.provider_id = providerIdMap.get(stepModel.provider_id);
            }
        });
    }
    const providers = collectProvidersForm();
    renderWorkflowStepModels(current, providers, getSelectedDefaultProviderId() || providers[0]?.id || '');
}

function getSelectedDefaultProviderId() {
    return document.querySelector('input[name="default-provider-radio"]:checked')?.value || '';
}

function resolveProviderDefaultId(providers, preferredProviderId = '') {
    const providerIds = (Array.isArray(providers) ? providers : [])
        .map(provider => String(provider.id || '').trim())
        .filter(Boolean);
    const preferred = String(preferredProviderId || '').trim();
    return providerIds.includes(preferred) ? preferred : (providerIds[0] || '');
}

function collectWorkflowStepModels() {
    const result = {};
    WORKFLOW_STEPS.forEach((step) => {
        result[step.id] = { provider_id: '', model_id: '' };
    });

    document.querySelectorAll('.workflow-step-model-select').forEach((select) => {
        const stepId = select.dataset.stepId;
        if (!stepId || !result[stepId]) return;
        const parsed = decodeStepModelValue(select.value);
        result[stepId] = parsed || { provider_id: '', model_id: '' };
    });

    return result;
}

function getConfiguredModelOptions(providers) {
    const options = [];
    (Array.isArray(providers) ? providers : []).forEach((provider) => {
        const providerId = String(provider.id || '').trim();
        if (!providerId) return;

        getModelItems(provider.model_id).forEach((modelValue) => {
            const { modelId, displayName } = splitModelItem(modelValue);
            if (!modelId) return;
            const providerName = String(provider.name || providerId).trim() || providerId;
            options.push({
                value: encodeStepModelValue(providerId, modelId),
                providerId,
                modelId,
                modelLabel: displayName,
                providerLabel: providerName,
                label: displayName,
                title: `${providerId} / ${modelId}`,
            });
        });
    });
    return options;
}

function getGroupedWorkflowModelOptions(options, selectedValue) {
    const groups = new Map();
    options.forEach((option) => {
        const key = option.providerId || '';
        if (!groups.has(key)) {
            groups.set(key, {
                label: option.providerLabel || option.providerId || 'Provider',
                options: [],
            });
        }
        groups.get(key).options.push(option);
    });

    return Array.from(groups.values()).map((group) => {
        const items = group.options.map((option) => {
            const isSelected = option.value === selectedValue ? 'selected' : '';
            return `<option value="${escapeHtml(option.value)}" title="${escapeHtml(option.title)}" ${isSelected}>${escapeHtml(option.modelLabel || option.label)}</option>`;
        }).join('');
        return `<optgroup label="${escapeHtml(group.label)}">${items}</optgroup>`;
    });
}

function encodeStepModelValue(providerId, modelId) {
    return `${encodeURIComponent(providerId)}|||${encodeURIComponent(modelId)}`;
}

function decodeStepModelValue(value) {
    if (!value) return null;
    const parts = String(value).split('|||');
    if (parts.length !== 2) return null;
    try {
        return {
            provider_id: decodeURIComponent(parts[0]),
            model_id: decodeURIComponent(parts[1]),
        };
    } catch (e) {
        return null;
    }
}

function createProviderCard(provider, defaultProviderId, index, options = {}) {
    const card = document.createElement('div');
    card.className = 'provider-card collapsed';
    const providerId = String(provider.id || `provider-${index + 1}`).trim();
    const isDefaultProvider = providerId === String(defaultProviderId || '').trim();
    card.dataset.savedProviderId = provider.previous_id || providerId;
    card.dataset.liveProviderId = providerId;
    const radioId = `default-provider-${index}`;
    const providerSummary = formatProviderSummary(provider);
    card.classList.toggle('is-default', isDefaultProvider);
    card.innerHTML = `
        <div class="provider-card-header">
            <button type="button" class="provider-collapse-btn" aria-expanded="false">
                <span class="provider-collapse-copy">
                    <span class="provider-title-row">
                        <span class="provider-card-name">${escapeHtml(provider.name || providerId)}</span>
                        <span class="provider-default-badge">默认</span>
                    </span>
                    <span class="provider-card-subtitle">${escapeHtml(providerId)}</span>
                    <span class="provider-summary-row" aria-hidden="true">
                        <span class="provider-summary-pill provider-summary-base-url">${escapeHtml(providerSummary.baseUrl)}</span>
                        <span class="provider-summary-pill provider-summary-model-count">${escapeHtml(providerSummary.modelCount)}</span>
                        <span class="provider-summary-pill provider-summary-key-status">${escapeHtml(providerSummary.keyStatus)}</span>
                    </span>
                </span>
                <span class="material-symbols-rounded provider-collapse-icon">expand_more</span>
            </button>
            <div class="provider-card-actions">
                <label class="provider-default-label" for="${radioId}" title="设为默认 Provider">
                    <input type="radio" id="${radioId}" name="default-provider-radio" value="${escapeHtml(providerId)}" ${isDefaultProvider ? 'checked' : ''}>
                    <span>默认</span>
                </label>
                <button type="button" class="remove-provider-btn" title="删除 Provider" aria-label="删除 Provider">
                    <span class="material-symbols-rounded">delete</span>
                </button>
            </div>
        </div>
        <div class="provider-card-body">
            <div class="provider-grid">
                <div class="form-group">
                    <label>Provider ID</label>
                    <input type="text" class="provider-id-input" value="${escapeHtml(providerId)}" placeholder="openai">
                </div>
                <div class="form-group">
                    <label>显示名称</label>
                    <input type="text" class="provider-name-input" value="${escapeHtml(provider.name || providerId)}" placeholder="OpenAI">
                </div>
            </div>
            <div class="form-group">
                <label>API 密钥</label>
                <div class="password-input-wrapper">
                    <input type="password" class="provider-api-key-input" autocomplete="new-password" value="${escapeHtml(provider.api_key || '')}" placeholder="sk-..., sk-...">
                    <div class="password-actions-wrapper">
                        <button type="button" class="password-toggle-btn provider-toggle-key-btn" title="显示/隐藏密钥">
                            <span class="material-symbols-rounded">visibility</span>
                        </button>
                        <button type="button" class="password-toggle-btn provider-validate-key-btn" title="验证 API 密钥">
                            <span class="material-symbols-rounded">verified</span>
                        </button>
                    </div>
                </div>
            </div>
            <div class="form-group">
                <label>API 基础地址</label>
                <input type="text" class="provider-base-url-input" value="${escapeHtml(provider.base_url || '')}" placeholder="https://api.openai.com/v1">
            </div>
            <div class="form-group model-settings-group">
                <div class="model-panel-header">
                    <button type="button" class="model-panel-toggle" aria-expanded="false">
                        <span class="model-panel-title">模型列表</span>
                        <span class="model-panel-summary">${escapeHtml(providerSummary.modelCount)}</span>
                        <span class="material-symbols-rounded model-panel-icon">expand_more</span>
                    </button>
                </div>
                <div class="model-list-container"></div>
                <button type="button" class="add-model-btn provider-add-model-btn">
                    <span class="material-symbols-rounded">add</span>
                    <span>添加模型</span>
                </button>
                <input type="hidden" class="provider-model-input" value="${escapeHtml(provider.model_id || '')}">
            </div>
        </div>
    `;

    const idInput = card.querySelector('.provider-id-input');
    const radio = card.querySelector('input[name="default-provider-radio"]');
    idInput.addEventListener('input', () => {
        const previousProviderId = card.dataset.liveProviderId || card.dataset.savedProviderId || providerId;
        const nextProviderId = idInput.value.trim();
        if (nextProviderId) {
            card.dataset.liveProviderId = nextProviderId;
        }
        radio.value = nextProviderId;
        const displayName = card.querySelector('.provider-name-input').value.trim() || nextProviderId;
        card.querySelector('.provider-card-name').textContent = displayName;
        card.querySelector('.provider-card-subtitle').textContent = nextProviderId;
        updateProviderSummary(card);
        if (nextProviderId) {
            const providerIdMap = previousProviderId
                ? new Map([[previousProviderId, nextProviderId]])
                : null;
            refreshWorkflowStepModelOptions({ providerIdMap });
        }
        requestSettingsAutoSave();
    });
    card.querySelector('.provider-name-input').addEventListener('input', () => {
        const displayName = card.querySelector('.provider-name-input').value.trim() || idInput.value.trim();
        card.querySelector('.provider-card-name').textContent = displayName;
        updateProviderSummary(card);
        refreshWorkflowStepModelOptions();
        requestSettingsAutoSave();
    });

    const collapseBtn = card.querySelector('.provider-collapse-btn');
    const setCollapsed = (collapsed) => {
        card.classList.toggle('collapsed', collapsed);
        collapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const icon = collapseBtn.querySelector('.provider-collapse-icon');
        if (icon) {
            icon.textContent = collapsed ? 'expand_more' : 'expand_less';
        }
    };
    collapseBtn.addEventListener('click', (event) => {
        setCollapsed(!card.classList.contains('collapsed'));
    });
    if (typeof options.collapsed === 'boolean') {
        setCollapsed(options.collapsed);
    } else if (providerId === defaultProviderId) {
        setCollapsed(false);
    }

    card.querySelector('.provider-toggle-key-btn').addEventListener('click', (event) => {
        event.preventDefault();
        const input = card.querySelector('.provider-api-key-input');
        const type = input.getAttribute('type') === 'password' ? 'text' : 'password';
        input.setAttribute('type', type);
        const icon = event.currentTarget.querySelector('.material-symbols-rounded');
        if (icon) {
            icon.textContent = type === 'password' ? 'visibility' : 'visibility_off';
        }
    });

    card.querySelector('.provider-validate-key-btn').addEventListener('click', validateApiKey);
    radio.addEventListener('change', () => {
        syncDefaultProviderBadges();
        requestSettingsAutoSave({ immediate: true });
    });
    card.querySelector('.provider-api-key-input').addEventListener('input', () => {
        updateProviderSummary(card);
        requestSettingsAutoSave();
    });
    card.querySelector('.provider-base-url-input').addEventListener('input', () => {
        updateProviderSummary(card);
        requestSettingsAutoSave();
    });
    card.querySelector('.remove-provider-btn').addEventListener('click', async () => {
        const providerName = card.querySelector('.provider-card-name')?.textContent?.trim() || providerId;
        if (!(await showConfirm(`确定删除 ${providerName} 吗？`, '删除 Provider'))) return;
        const currentDefaultProviderId = getSelectedDefaultProviderId();
        const providers = Array.from(document.querySelectorAll('.provider-card'))
            .filter(providerCard => providerCard !== card)
            .map(collectProviderCardForm)
            .filter(provider => provider.id || provider.base_url || provider.model_id || provider.api_key);
        const nextProviders = providers.length > 0 ? providers : [createEmptyProvider(1)];
        const preferredDefaultId = radio.checked ? '' : currentDefaultProviderId;
        renderProviderList(
            nextProviders,
            resolveProviderDefaultId(nextProviders, preferredDefaultId),
            { preserveCollapsed: true },
        );
        requestSettingsAutoSave({ immediate: true });
    });
    setupProviderModelList(card);
    return card;
}

function syncDefaultProviderBadges() {
    document.querySelectorAll('.provider-card').forEach((card) => {
        const radio = card.querySelector('input[name="default-provider-radio"]');
        card.classList.toggle('is-default', Boolean(radio?.checked));
    });
}

function setupProviderModelList(providerCard) {
    const container = providerCard.querySelector('.model-list-container');
    const addButton = providerCard.querySelector('.provider-add-model-btn');
    const hiddenInput = providerCard.querySelector('.provider-model-input');
    const modelGroup = providerCard.querySelector('.model-settings-group');
    const toggleButton = providerCard.querySelector('.model-panel-toggle');

    function render() {
        container.innerHTML = '';
        const items = getModelItems(hiddenInput.value);
        hiddenInput.value = items.join(', ');
        if (items.length === 0) {
            addModelRow('', '');
        } else {
            items.forEach(item => {
                const { modelId, displayName } = splitModelItem(item);
                addModelRow(
                    modelId,
                    displayName === modelId || displayName === (modelId.includes('/') ? modelId.split('/').pop() : modelId)
                        ? ''
                        : displayName,
                );
            });
        }
        updateModelPanelSummary(providerCard);
        updateProviderSummary(providerCard);
    }

    function serialize({ save = true } = {}) {
        hiddenInput.value = Array.from(container.querySelectorAll('.model-row'))
            .map(row => {
                const id = row.querySelector('.model-id-input').value.trim();
                const name = row.querySelector('.model-name-input').value.trim();
                if (!id) return '';
                return name ? `${id}::${name}` : id;
            })
            .filter(model => model && !isUnsupportedGemini25Model(model))
            .join(', ');
        updateModelPanelSummary(providerCard);
        updateProviderSummary(providerCard);
        refreshWorkflowStepModelOptions();
        if (save) {
            requestSettingsAutoSave();
        }
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

    function setModelPanelCollapsed(collapsed) {
        modelGroup.classList.toggle('collapsed', collapsed);
        toggleButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        const icon = toggleButton.querySelector('.model-panel-icon');
        if (icon) {
            icon.textContent = collapsed ? 'expand_more' : 'expand_less';
        }
    }

    toggleButton.addEventListener('click', () => {
        setModelPanelCollapsed(!modelGroup.classList.contains('collapsed'));
    });

    addButton.addEventListener('click', () => {
        setModelPanelCollapsed(false);
        addModelRow('', '');
        serialize({ save: false });
    });
    render();
    setModelPanelCollapsed(true);
}

function formatProviderSummary(provider) {
    const modelCount = getModelItems(provider.model_id).length;
    const baseUrl = String(provider.base_url || '').trim() || '未设置 API 地址';
    const apiKey = String(provider.api_key || '').trim();
    return {
        baseUrl,
        modelCount: `${modelCount || 0} 个模型`,
        keyStatus: apiKey ? '已配置密钥' : '未配置密钥',
    };
}

function updateProviderSummary(providerCard) {
    const summary = formatProviderSummary({
        api_key: providerCard.querySelector('.provider-api-key-input')?.value || '',
        base_url: providerCard.querySelector('.provider-base-url-input')?.value || '',
        model_id: providerCard.querySelector('.provider-model-input')?.value || '',
    });
    const baseUrlEl = providerCard.querySelector('.provider-summary-base-url');
    const modelCountEl = providerCard.querySelector('.provider-summary-model-count');
    const keyStatusEl = providerCard.querySelector('.provider-summary-key-status');
    if (baseUrlEl) baseUrlEl.textContent = summary.baseUrl;
    if (modelCountEl) modelCountEl.textContent = summary.modelCount;
    if (keyStatusEl) {
        keyStatusEl.textContent = summary.keyStatus;
        keyStatusEl.classList.toggle('is-empty', summary.keyStatus === '未配置密钥');
    }
}

function updateModelPanelSummary(providerCard) {
    const summaryEl = providerCard.querySelector('.model-panel-summary');
    if (!summaryEl) return;
    const hiddenInput = providerCard.querySelector('.provider-model-input');
    const items = getModelItems(hiddenInput?.value || '');
    if (items.length === 0) {
        summaryEl.textContent = '0 个模型';
        return;
    }
    const first = getModelDisplayName(items[0]);
    summaryEl.textContent = `${first} · ${items.length} 个模型`;
}

function getModelItems(modelId) {
    return String(modelId || '')
        .split(',')
        .map(s => s.trim())
        .filter(model => model && !isUnsupportedGemini25Model(model));
}

function isUnsupportedGemini25Model(model) {
    return /(^|[^a-z0-9])gemini[\s._-]*2[\s._-]*5($|[^a-z0-9])/i.test(String(model || ''));
}

function getModelDisplayName(modelValue) {
    return splitModelItem(modelValue).displayName;
}

function splitModelItem(modelValue) {
    const raw = String(modelValue || '').trim();
    if (!raw) {
        return { modelId: '', displayName: '' };
    }
    const aliasIdx = raw.indexOf('::');
    if (aliasIdx !== -1) {
        const modelId = raw.substring(0, aliasIdx).trim();
        const displayName = raw.substring(aliasIdx + 2).trim();
        if (modelId && displayName) {
            return { modelId, displayName };
        }
        return {
            modelId: raw,
            displayName: raw.includes('/') ? raw.split('/').pop() : raw,
        };
    }
    const colonIdx = raw.indexOf(':');
    if (colonIdx !== -1) {
        const modelId = raw.substring(0, colonIdx).trim();
        const displayName = raw.substring(colonIdx + 1).trim();
        const compactTag = /^[A-Za-z0-9._-]+$/.test(displayName);
        const repeatedCompactName = compactTag
            && modelId
            && displayName
            && modelId.toLowerCase() === displayName.toLowerCase();
        const suffixCompactName = compactTag
            && modelId
            && displayName
            && modelId.toLowerCase().endsWith(displayName.toLowerCase())
            && /[-_.]/.test(modelId);
        if (modelId && displayName && (
            /\s/.test(displayName)
            || !compactTag
            || repeatedCompactName
            || suffixCompactName
        )) {
            return { modelId, displayName };
        }
    }
    return {
        modelId: raw,
        displayName: raw.includes('/') ? raw.split('/').pop() : raw,
    };
}

function collectProvidersForm() {
    return Array.from(document.querySelectorAll('.provider-card'))
        .map(collectProviderCardForm)
        .filter(provider => provider.id || provider.base_url || provider.model_id || provider.api_key);
}

function collectProviderCardForm(card) {
    const providerId = card.querySelector('.provider-id-input').value.trim();
    return {
        id: providerId,
        previous_id: card.dataset.savedProviderId || providerId,
        name: card.querySelector('.provider-name-input').value.trim(),
        api_key: card.querySelector('.provider-api-key-input').value.trim(),
        base_url: card.querySelector('.provider-base-url-input').value.trim(),
        model_id: card.querySelector('.provider-model-input').value.trim(),
    };
}

function markSavedProviderIdentities() {
    document.querySelectorAll('.provider-card').forEach((card) => {
        const providerId = card.querySelector('.provider-id-input')?.value.trim() || '';
        if (providerId) {
            card.dataset.savedProviderId = providerId;
            card.dataset.liveProviderId = providerId;
        }
    });
}

function createEmptyProvider(index) {
    return {
        id: index === 1 ? 'deepseek' : `provider-${index}`,
        name: index === 1 ? 'DeepSeek' : `Provider ${index}`,
        api_key: '',
        base_url: index === 1 ? 'https://api.deepseek.com/v1' : 'https://api.openai.com/v1',
        model_id: index === 1 ? 'deepseek-v4-pro' : '',
    };
}

function escapeHtml(str) {
    const value = String(str ?? '');
    if (!value) return '';
    return value
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export const __settingsModalTestHooks = {
    collectSettingsForm,
    collectProvidersForm,
    collectWorkflowStepModels,
    fillSettingsForm,
    normalizeNumberSetting,
    renderEngineCheckResults,
    renderProviderList,
    renderWorkflowStepModels,
};
