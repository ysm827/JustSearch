import { state, setCurrentSessionId, setIsProcessing, setAbortController } from './modules/state.js';
import { createCopyButton } from './modules/utils.js';
import { initUI, elements, renderHistory, renderMessages, appendMessage, scrollToBottom, createDynamicLogContainer, renderWithCitations, updateActiveHistoryItem } from './modules/ui.js';
import { showToast } from './modules/toast.js';
import * as API from './modules/api.js';

document.addEventListener('DOMContentLoaded', async () => {
    initUI();
    
    // --- Initialization ---
    const settings = await API.fetchSettings(); // This also applies theme
    updateModelSelector(settings.model_id);
    const history = await API.fetchHistory();
    renderHistory(history, state.currentSessionId, {
        onSelect: loadChat,
        onDelete: deleteChat
    });

    function updateModelSelector(modelString) {
        const select = document.getElementById('model-select');
        if (!select) return;

        const currentVal = select.value;
        select.innerHTML = '';
        
        if (!modelString) {
             // Fallback
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
            option.textContent = model;
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

        // Window Resize Handling for Stability
        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) {
                elements.sidebar.classList.remove('mobile-open');
                elements.mobileOverlay.classList.remove('active');
            }
        });

        // Chat
        elements.sendBtn.addEventListener('click', handleSendMessage);
        elements.userInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSendMessage();
            }
        });

        // Auto-resize textarea
        elements.userInput.addEventListener('input', () => {
            elements.userInput.style.height = '40px'; // Reset to base height to calculate scrollHeight
            const scrollHeight = elements.userInput.scrollHeight;
            const maxHeight = 200; // Matches CSS max-height
            
            if (scrollHeight > maxHeight) {
                elements.userInput.style.height = maxHeight + 'px';
                elements.userInput.style.overflowY = 'auto';
            } else {
                elements.userInput.style.height = scrollHeight + 'px';
                elements.userInput.style.overflowY = 'hidden';
            }
        });

        elements.newChatBtn.addEventListener('click', () => {
            setCurrentSessionId(null);
            elements.chatContainer.innerHTML = '';
            elements.chatContainer.appendChild(elements.heroSection);
            elements.heroSection.style.display = 'block';
            updateActiveHistoryItem(null);
            elements.userInput.value = '';
            elements.userInput.style.height = '40px';
            elements.userInput.style.overflowY = 'hidden';
            elements.userInput.focus();
        });
        
        // Settings Modal
        setupSettingsModal();
        setupPasswordToggle();
        setupBrowserModal();
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

        // Click handling on image
        img.addEventListener('mousedown', (e) => {
            if (!ws || img.style.display === 'none') return;
            
            const rect = img.getBoundingClientRect();
            
            // Map click on <img> to click on original viewport
            // img.naturalWidth is the actual width of the screenshot (viewport width)
            // rect.width is the displayed width
            
            const x = (e.clientX - rect.left) * (img.naturalWidth / rect.width);
            const y = (e.clientY - rect.top) * (img.naturalHeight / rect.height);
            
            ws.send(JSON.stringify({
                action: 'click',
                x: x,
                y: y
            }));
        });
        
        // Expose open function to state or window
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
                    // Only show disconnected if we didn't close it intentionally
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
            document.getElementById('theme-select').value = state.settings.theme || 'light';
            document.getElementById('engine-select').value = state.settings.search_engine || 'duckduckgo';
            document.getElementById('max-results-input').value = state.settings.max_results || 8;
            document.getElementById('max-iterations-input').value = state.settings.max_iterations || 5;
            document.getElementById('api-key-input').value = state.settings.api_key || '';
            document.getElementById('api-key-input').placeholder = state.settings.api_key ? '已配置 (留空保持不变)' : '输入 API Key';
            document.getElementById('base-url-input').value = state.settings.base_url || '';
            document.getElementById('model-input').value = state.settings.model_id || '';
            document.getElementById('interactive-search-input').checked = state.settings.interactive_search !== undefined ? state.settings.interactive_search : true;

            // Fetch GitHub Stars
            const starsCountElement = document.getElementById('github-stars-count');
            if (starsCountElement) {
                starsCountElement.textContent = '...';
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

             // If the value looks masked (unchanged), don't send it
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
                interactive_search: document.getElementById('interactive-search-input').checked
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
            if (!confirm('您确定要恢复默认设置吗？')) return;
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
                showToast('已恢复默认设置', 'success');
            } else {
                showToast('加载默认设置失败', 'error');
            }
        });

        clearHistoryBtn.addEventListener('click', async () => {
            if (await API.clearHistoryAPI()) {
                 setCurrentSessionId(null);
                 elements.historyList.innerHTML = '';
                 elements.chatContainer.innerHTML = '';
                 elements.chatContainer.appendChild(elements.heroSection);
                 elements.heroSection.style.display = 'block';
                 updateActiveHistoryItem(null);
                 elements.settingsModal.style.display = 'none';
                 showToast('历史记录已清除', 'success');
            } else {
                showToast('清除历史记录失败', 'error');
            }
        });
    }

    async function loadChat(sessionId) {
        setCurrentSessionId(sessionId);
        updateActiveHistoryItem(sessionId);
        const data = await API.fetchChat(sessionId);
        if (data) {
            renderMessages(data.messages);
        }
    }

    async function deleteChat(sessionId) {
        if (await API.deleteChatAPI(sessionId)) {
            if (state.currentSessionId === sessionId) {
                elements.newChatBtn.click();
            }
            const history = await API.fetchHistory();
            renderHistory(history, state.currentSessionId, { onSelect: loadChat, onDelete: deleteChat });
            showToast('对话已删除', 'success');
        } else {
            showToast('删除对话失败', 'error');
        }
    }

    async function handleSendMessage() {
        if (state.isProcessing) {
            if (state.abortController) {
                state.abortController.abort();
                setAbortController(null);
            }
            return;
        }

        const text = elements.userInput.value.trim();
        if (!text) return;

        const selectedModel = document.getElementById('model-select').value;

        elements.userInput.value = '';
        elements.userInput.style.height = '40px';
        elements.userInput.style.overflowY = 'hidden';
        setIsProcessing(true);
        elements.heroSection.style.display = 'none';

        const sendBtnIcon = elements.sendBtn.querySelector('.material-symbols-rounded');
        sendBtnIcon.textContent = 'stop_circle';

        appendMessage('user', text);
        scrollToBottom();

        // Assistant Message Placeholder
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message assistant';
        
        // Log Container
        const { logContainer, logDetails, spinner, statusText } = createDynamicLogContainer();
        msgDiv.appendChild(logContainer);

        // Content
        const answerDiv = document.createElement('div');
        answerDiv.className = 'message-content markdown-body';
        const contentWrapper = document.createElement('div');
        contentWrapper.innerHTML = '<span class="blinking-cursor">...</span>';
        answerDiv.appendChild(contentWrapper);
        msgDiv.appendChild(answerDiv);
        elements.chatContainer.appendChild(msgDiv);
        scrollToBottom();

        const controller = new AbortController();
        setAbortController(controller);
        
        let currentAnswerBuffer = '';
        const copyBtn = createCopyButton(() => currentAnswerBuffer);
        answerDiv.appendChild(copyBtn);

        let currentSources = [];

        try {
            await API.streamChat(text, {
                model: selectedModel,
                signal: controller.signal,
                onMeta: (sessionId) => {
                    setCurrentSessionId(sessionId);
                },
                onLog: (msg) => {
                    if (msg.includes('ACTION_REQUIRED: CAPTCHA_DETECTED')) {
                        if (state.openBrowserModal) {
                            state.openBrowserModal(state.currentSessionId);
                        }
                        msg = "需要人工验证。请在弹出的窗口中解决验证码。";
                    }
                    logContainer.style.display = 'block';
                    statusText.textContent = msg;
                    const entry = document.createElement('div');
                    entry.className = 'log-entry';
                    entry.innerHTML = `<span class="log-timestamp">${new Date().toLocaleTimeString()}</span> <span>${msg}</span>`;
                    logDetails.appendChild(entry);
                    logDetails.scrollTop = logDetails.scrollHeight;
                },
                onSources: (sources) => {
                    currentSources = sources;
                },
                onAnswerChunk: (chunk) => {
                    currentAnswerBuffer += chunk;
                    contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, currentSources);
                    scrollToBottom();
                },
                onAnswer: (finalAnswer, sessionId) => {
                    currentAnswerBuffer = finalAnswer;
                    contentWrapper.innerHTML = renderWithCitations(finalAnswer, currentSources);
                    setCurrentSessionId(sessionId);
                    
                    // Refresh history list to show new chat title
                    API.fetchHistory().then(h => renderHistory(h, state.currentSessionId, { onSelect: loadChat, onDelete: deleteChat }));
                },
                onError: (err) => {
                    contentWrapper.innerHTML += `<div style="color:red">Error: ${err}</div>`;
                },
                onDone: () => {
                     // Done
                }
            });
        } catch (e) {
             if (e.name === 'AbortError') {
                contentWrapper.innerHTML += `<div style="color:orange; margin-top: 10px;">[已由用户停止]</div>`;
            } else {
                console.error(e);
                contentWrapper.innerHTML = `<div style="color:red">网络错误: ${e.message}</div>`;
            }
        } finally {
            setIsProcessing(false);
            setAbortController(null);
            sendBtnIcon.textContent = 'send';
            
            spinner.classList.remove('rotating');
            spinner.textContent = 'check_circle';
            statusText.textContent = '已完成';
        }
    }
});