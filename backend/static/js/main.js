import { state, setCurrentSessionId, setIsProcessing, setAbortController } from './modules/state.js';
import { createCopyButton } from './modules/utils.js';
import { initUI, elements, renderHistory, renderMessages, appendMessage, scrollToBottom, updateActiveHistoryItem, showConfirm, getCachedHistory } from './modules/ui.js';
import { showToast } from './modules/toast.js';
import { setupChatHandler } from './modules/chat.js';
import * as API from './modules/api.js';

document.addEventListener('DOMContentLoaded', async () => {
    initUI();

    // --- Initialization ---
    const settings = await API.fetchSettings();
    updateModelSelector(settings.model_id);
    const history = await API.fetchHistory();

    const { loadChat, deleteChat } = setupChatHandler(elements, renderHistory);

    renderHistory(history, state.currentSessionId, {
        onSelect: loadChat,
        onDelete: deleteChat
    });

    // --- URL 路由：从地址栏恢复对话 ---
    const pathMatch = window.location.pathname.match(/^\/c\/([a-zA-Z0-9_-]+)/);
    if (pathMatch) {
        const urlSessionId = pathMatch[1];
        // 确保该会话存在于历史中
        const exists = history.some(h => h.id === urlSessionId);
        if (exists) {
            loadChat(urlSessionId);
        } else {
            // 会话不存在，回到首页
            history.replaceState(null, '', '/');
        }
    }

    // 浏览器前进/后退时恢复对应对话
    window.addEventListener('popstate', (e) => {
        if (e.state && e.state.sessionId) {
            loadChat(e.state.sessionId);
        } else {
            // 回到首页状态
            setCurrentSessionId(null);
            elements.chatContainer.innerHTML = '';
            elements.heroSection.style.display = 'block';
            elements.chatContainer.appendChild(elements.heroSection);
            updateActiveHistoryItem(null);
        }
    });

    function updateModelSelector(modelString) {
        const select = document.getElementById('model-select');
        if (!select) return;

        const currentVal = select.value;
        select.innerHTML = '';

        if (!modelString) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'Default';
            select.appendChild(option);
            return;
        }

        const models = modelString.split(',').map(s => s.trim()).filter(s => s);

        if (models.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'Default';
            select.appendChild(option);
            return;
        }

        models.forEach(model => {
            const option = document.createElement('option');
            option.value = model;
            // 友好短名：取斜杠后最后一段
            const shortName = model.includes('/') ? model.split('/').pop() : model;
            option.textContent = shortName;
            option.title = model; // 完整名称在 tooltip
            select.appendChild(option);
        });

        // Restore selection if possible, otherwise first
        if (models.includes(currentVal)) {
            select.value = currentVal;
        } else {
            select.value = models[0];
        }
    }

    // Initialize Sidebar State
    const isMobile = window.innerWidth <= 768;
    if (!isMobile) {
        const sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
        if (sidebarCollapsed) {
            elements.sidebar.classList.add('collapsed');
        }
    }

    // --- Event Listeners ---
    setupEventListeners();

    // --- Functions ---
    function setupEventListeners() {
        // Sidebar
        const toggleSidebar = () => {
            if (window.innerWidth <= 768) {
                elements.sidebar.classList.add('mobile-open');
                elements.mobileOverlay.classList.add('active');
            } else {
                elements.sidebar.classList.toggle('collapsed');
                localStorage.setItem('sidebarCollapsed', elements.sidebar.classList.contains('collapsed'));
            }
        };

        if (elements.expandSidebarBtn) {
            elements.expandSidebarBtn.addEventListener('click', toggleSidebar);
        }

        if (elements.collapseSidebarBtn) {
            elements.collapseSidebarBtn.addEventListener('click', toggleSidebar);
        }

        elements.closeSidebarBtn.addEventListener('click', () => {
            elements.sidebar.classList.remove('mobile-open');
            elements.mobileOverlay.classList.remove('active');
        });

        elements.mobileOverlay.addEventListener('click', () => {
            elements.sidebar.classList.remove('mobile-open');
            elements.mobileOverlay.classList.remove('active');
        });

        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) {
                elements.sidebar.classList.remove('mobile-open');
                elements.mobileOverlay.classList.remove('active');
            }
        });

        // Export all chats button
        const exportAllBtn = document.getElementById('export-all-btn');
        if (exportAllBtn) {
            exportAllBtn.addEventListener('click', async () => {
                const format = await showConfirm('选择导出格式？', '导出全部对话');
                // Default to markdown
                window.open('/api/history/export/all?format=markdown', '_blank');
            });
        }

        elements.newChatBtn.addEventListener('click', () => {
            setCurrentSessionId(null);
            elements.chatContainer.innerHTML = '';
            elements.heroSection.style.display = 'block';
            elements.chatContainer.appendChild(elements.heroSection);
            updateActiveHistoryItem(null);
            elements.userInput.value = '';
            elements.userInput.style.height = '40px';
            elements.userInput.style.overflowY = 'hidden';
            elements.userInput.focus();
            // 回到首页 URL
            if (window.location.pathname !== '/') {
                history.pushState(null, '', '/');
            }
        });

        setupSettingsModal();
        setupPasswordToggle();
        setupBrowserModal();
        setupHistorySearch();
    }

    function setupHistorySearch() {
        const searchInput = document.getElementById('history-search-input');
        if (!searchInput) return;

        let searchTimeout;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                // Use cached history instead of re-fetching from server
                renderHistory(getCachedHistory(), state.currentSessionId, {
                    onSelect: loadChat,
                    onDelete: deleteChat
                });
            }, 200);
        });
    }

    function setupPasswordToggle() {
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
    }

    function setupSettingsModal() {
        const settingsBtn = document.getElementById('settings-btn');
        const closeBtn = document.querySelector('.close-btn');
        const saveSettingsBtn = document.getElementById('save-settings-btn');
        const resetSettingsBtn = document.getElementById('reset-settings-btn');
        const clearHistoryBtn = document.getElementById('clear-history-btn');

        settingsBtn.addEventListener('click', async () => {
            elements.settingsModal.style.display = 'block';
            
            // Load and display version + memory info
            const versionEl = document.getElementById('version-display');
            if (versionEl) {
                try {
                    const healthRes = await fetch('/api/health');
                    if (healthRes.ok) {
                        const health = await healthRes.json();
                        versionEl.textContent = `v${health.version || '?.?.?'}`;
                        // Show memory usage if available
                        if (health.memory_mb) {
                            versionEl.title = `Memory: ${health.memory_mb} MB`;
                        }
                    }
                } catch (e) { /* ignore */ }
            }
            
            document.getElementById('theme-select').value = state.settings.theme || 'light';
            document.getElementById('engine-select').value = state.settings.search_engine || 'duckduckgo';
            document.getElementById('max-results-input').value = state.settings.max_results || 8;
            document.getElementById('max-iterations-input').value = state.settings.max_iterations || 5;
            document.getElementById('api-key-input').value = state.settings.api_key || '';
            document.getElementById('api-key-input').placeholder = state.settings.api_key ? '已配置 (留空保持不变)' : '输入 API Key';
            document.getElementById('base-url-input').value = state.settings.base_url || '';
            document.getElementById('model-input').value = state.settings.model_id || '';
            document.getElementById('interactive-search-input').checked = state.settings.interactive_search !== undefined ? state.settings.interactive_search : true;
            document.getElementById('max-concurrent-pages-input').value = state.settings.max_concurrent_pages || 10;
            document.getElementById('max-context-turns-input').value = state.settings.max_context_turns || 6;

            const starsCountElement = document.getElementById('github-stars-count');
            if (starsCountElement) {
                const stats = await API.fetchGitHubStats();
                if (stats && stats.stars !== undefined) {
                    starsCountElement.textContent = stats.stars;
                }
            }
        });

        closeBtn.addEventListener('click', () => {
            elements.settingsModal.style.display = 'none';
        });

        window.onclick = (event) => {
            if (event.target === elements.settingsModal) {
                elements.settingsModal.style.display = 'none';
            }
        };

        saveSettingsBtn.addEventListener('click', async () => {
             const apiKeyInput = document.getElementById('api-key-input');
             let apiKeyValue = apiKeyInput.value.trim();

             if (apiKeyValue && apiKeyValue.includes('****')) {
                 apiKeyValue = '';
             }

             const newSettings = {
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

            if (await API.saveSettingsAPI(newSettings)) {
                updateModelSelector(newSettings.model_id);
                elements.settingsModal.style.display = 'none';
                showToast('设置已保存', 'success');
            } else {
                showToast('保存设置失败', 'error');
            }
        });

        resetSettingsBtn.addEventListener('click', async () => {
            if (!(await showConfirm('您确定要恢复默认设置吗？', '恢复默认设置'))) return;
            const defaults = await API.restoreDefaultSettingsAPI();
            if (defaults) {
                document.getElementById('theme-select').value = defaults.theme || 'light';
                document.getElementById('engine-select').value = defaults.search_engine || 'duckduckgo';
                document.getElementById('max-results-input').value = defaults.max_results || 8;
                document.getElementById('max-iterations-input').value = defaults.max_iterations || 5;
                document.getElementById('api-key-input').value = defaults.api_key || '';
                document.getElementById('base-url-input').value = defaults.base_url || '';
                document.getElementById('model-input').value = defaults.model_id || '';
                document.getElementById('interactive-search-input').checked = defaults.interactive_search !== undefined ? defaults.interactive_search : true;
                document.getElementById('max-concurrent-pages-input').value = defaults.max_concurrent_pages || 10;
                document.getElementById('max-context-turns-input').value = defaults.max_context_turns || 6;
                showToast('已恢复默认设置', 'success');
            } else {
                showToast('加载默认设置失败', 'error');
            }
        });

        clearHistoryBtn.addEventListener('click', async () => {
            if (!(await showConfirm('确定要清除所有对话历史吗？此操作不可撤销。', '清除历史记录'))) return;
            if (await API.clearHistoryAPI()) {
                 setCurrentSessionId(null);
                 elements.historyList.innerHTML = '';
                 elements.chatContainer.innerHTML = '';
                 elements.heroSection.style.display = 'block';
                 elements.chatContainer.appendChild(elements.heroSection);
                 updateActiveHistoryItem(null);
                 elements.settingsModal.style.display = 'none';
                 showToast('历史记录已清除', 'success');
            } else {
                showToast('清除历史记录失败', 'error');
            }
        });

        const clearCacheBtn = document.getElementById('clear-cache-btn');
        if (clearCacheBtn) {
            clearCacheBtn.addEventListener('click', async () => {
                if (!(await showConfirm('此操作将清除所有聊天记录、浏览器缓存（Cookies 等）并重置设置为默认值。确定要继续吗？此操作不可撤销。', '清除全部缓存'))) return;
                if (await API.clearCacheAPI()) {
                    setCurrentSessionId(null);
                    elements.historyList.innerHTML = '';
                    elements.chatContainer.innerHTML = '';
                    elements.heroSection.style.display = 'block';
                    elements.chatContainer.appendChild(elements.heroSection);
                    updateActiveHistoryItem(null);
                    elements.settingsModal.style.display = 'none';
                    showToast('全部缓存已清除，页面即将刷新', 'success');
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    showToast('清除缓存失败', 'error');
                }
            });
        }
    }

    function setupBrowserModal() {
        const modal = document.getElementById('browser-modal');
        const closeBtn = document.getElementById('browser-close-btn');
        const completeBtn = document.getElementById('browser-complete-btn');
        const img = document.getElementById('browser-viewport');
        const status = document.querySelector('.browser-status-overlay');

        if (!modal) return;

        let ws = null;

        closeBtn.addEventListener('click', () => {
            modal.style.display = 'none';
            if (ws) {
                ws.close();
                ws = null;
            }
        });

        completeBtn.addEventListener('click', () => {
            if (ws) {
                ws.send(JSON.stringify({ action: 'complete' }));
                completeBtn.disabled = true;
                completeBtn.textContent = '正在提交...';
            }
        });

        img.addEventListener('mousedown', (e) => {
            if (!ws || img.style.display === 'none') return;
            const rect = img.getBoundingClientRect();
            const x = (e.clientX - rect.left) * (img.naturalWidth / rect.width);
            const y = (e.clientY - rect.top) * (img.naturalHeight / rect.height);
            ws.send(JSON.stringify({ action: 'click', x, y }));
        });

        img.addEventListener('wheel', (e) => {
            if (!ws || img.style.display === 'none') return;
            e.preventDefault();
            ws.send(JSON.stringify({ action: 'scroll', dy: e.deltaY }));
        }, { passive: false });

        img.addEventListener('keydown', (e) => {
            if (!ws || img.style.display === 'none') return;
            ws.send(JSON.stringify({ action: 'key', key: e.key }));
        });
        img.tabIndex = 0;

        state.openBrowserModal = (sessionId) => {
            modal.style.display = 'block';
            status.style.display = 'block';
            status.textContent = '正在连接浏览器...';
            img.style.display = 'none';
            completeBtn.disabled = false;
            completeBtn.textContent = '完成验证，继续执行';

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/browser/${sessionId}`;

            if (ws) ws.close();
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                status.textContent = '已连接。等待画面...';
                img.focus();
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'frame') {
                    status.style.display = 'none';
                    img.style.display = 'block';
                    img.src = `data:image/jpeg;base64,${data.image}`;
                } else if (data.type === 'status') {
                     if (data.msg === 'Completed') {
                         modal.style.display = 'none';
                         if (ws) {
                             ws.close();
                             ws = null;
                         }
                     }
                }
            };

            ws.onclose = () => {
                if (modal.style.display !== 'none') {
                    if (completeBtn.textContent !== '正在提交...') {
                        status.style.display = 'block';
                        status.textContent = '连接已断开 (会话可能已结束)';
                    }
                }
            };

            ws.onerror = (e) => {
                console.error("WS Error", e);
                status.textContent = '连接错误';
            };
        };
    }
    // --- Listen for system theme changes (when set to 'auto') ---
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if ((state.settings.theme || 'light') === 'auto') {
            import('./modules/utils.js').then(m => m.applyTheme('auto'));
        }
    });

    // --- Suggestion Chips ---
    document.querySelectorAll('.suggestion-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const query = chip.dataset.query;
            if (query) {
                elements.userInput.value = query;
                elements.userInput.dispatchEvent(new Event('input'));
                // Auto-send
                const sendBtn = document.getElementById('send-btn');
                if (sendBtn) sendBtn.click();
            }
        });
    });

    // --- Keyboard Shortcuts ---
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const activeModal = document.querySelector('.modal[style*="flex"]');
            if (activeModal) {
                activeModal.style.display = 'none';
            }
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
            e.preventDefault();
            elements.newChatBtn.click();
        }
        // Ctrl/Cmd+K to focus search input
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            const searchInput = document.getElementById('history-search-input');
            if (searchInput) {
                searchInput.focus();
            }
        }
        // Ctrl/Cmd+/ to toggle sidebar
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            const sidebar = document.getElementById('sidebar');
            if (sidebar) {
                if (window.innerWidth <= 768) {
                    sidebar.classList.toggle('mobile-open');
                    document.getElementById('mobile-overlay').classList.toggle('active');
                } else {
                    sidebar.classList.toggle('collapsed');
                    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
                }
            }
        }
    });
});
