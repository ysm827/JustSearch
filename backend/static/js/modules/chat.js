import { coerceBooleanSetting, state, setAbortController, setCurrentSessionId, setIsProcessing, setLiveArtifactsMode } from './state.js?v=2';
import { createCopyButton, createMessageActionRail, createRegenerateButton } from './utils.js?v=3';
import { updateActiveHistoryItem } from './history-view.js?v=22';
import { createDynamicLogContainer, createLogEntry, scrollToBottom, appendMessage, renderMessages, showConfirm, createMessageShell } from './ui.js?v=20';
import { renderWithCitations } from './source-renderer.js?v=7';
import { getInlineLiveArtifact, renderLiveArtifactsForMessage } from './live-artifacts.js?v=11';
import { showToast } from './toast.js';
import * as API from './api.js?v=4';

function chatRoute(sessionId) {
    return `/c/${encodeURIComponent(String(sessionId ?? ''))}`;
}

function hasCitationSources(sources) {
    return Array.isArray(sources) && sources.length > 0;
}

/**
 * 设置聊天处理器：发送消息、加载/删除对话、输入框自动调整等。
 */
export function setupChatHandler(elements, renderHistory) {
    // 记录最后一条用户消息，用于重新生成
    let lastUserMessage = '';

    // 全局滚动状态跟踪（只注册一次，避免内存泄漏）
    let userScrolled = false;
    elements.chatContainer.addEventListener('scroll', () => {
        const { scrollTop, scrollHeight, clientHeight } = elements.chatContainer;
        userScrolled = (scrollHeight - scrollTop - clientHeight) > 100;
    });

    async function refreshHistory() {
        const [history, groups] = await Promise.all([
            API.fetchHistory(),
            API.fetchChatGroups()
        ]);
        renderHistory(history, state.currentSessionId, { onSelect: loadChat, onDelete: deleteChat }, groups);
    }

    function stageMessageForInput(text) {
        elements.userInput.value = text;
        elements.userInput.dispatchEvent(new Event('input', { bubbles: true }));
        resetInputHeight();
        elements.userInput.focus({ preventScroll: true });
        scrollToBottom();
        showToast('已填入输入框，可修改后发送', 'info');
    }

    async function regenerateFromPrompt(prompt) {
        if (!prompt || state.isProcessing) return;
        await handleSendMessage(prompt);
    }

    async function refreshAfterMessageDeleted() {
        if (state.currentSessionId) {
            await loadChat(state.currentSessionId);
        }
        await refreshHistory();
        showToast('消息已删除', 'success');
    }

    async function loadChat(sessionId) {
        setCurrentSessionId(sessionId);
        updateActiveHistoryItem(sessionId);
        // 更新浏览器地址栏
        const route = chatRoute(sessionId);
        if (window.location.pathname !== route) {
            history.pushState({ sessionId }, '', route);
        }
        const data = await API.fetchChat(sessionId);
        if (data) {
            renderMessages(data.messages, {
                onEdit: stageMessageForInput,
                onRegenerate: regenerateFromPrompt,
                onMessageDeleted: refreshAfterMessageDeleted
            });
        }
    }

    async function deleteChat(sessionId) {
        if (await API.deleteChatAPI(sessionId)) {
            if (state.currentSessionId === sessionId) {
                elements.newChatBtn.click();
            }
            await refreshHistory();
            showToast('对话已删除', 'success');
        } else {
            showToast('删除对话失败', 'error');
        }
    }

    async function handleSendMessage(overrideText) {
        if (state.isProcessing) {
            if (state.abortController) {
                state.abortController.abort();
                setAbortController(null);
            }
            return;
        }

        const text = overrideText || elements.userInput.value.trim();
        if (!text) return;

        lastUserMessage = text;

        const modelSelect = document.getElementById('model-select');
        const selectedModelOption = modelSelect?.options[modelSelect.selectedIndex] || null;
        const selectedModel = selectedModelOption ? selectedModelOption.value : '';
        const selectedProviderId = selectedModelOption?.dataset.providerId || state.settings.default_provider_id || '';

        elements.userInput.value = '';
        resetInputHeight();
        setIsProcessing(true);
        updateSendButtonState();
        elements.heroSection.style.display = 'none';

        const sendBtnIcon = elements.sendBtn.querySelector('.material-symbols-rounded');
        if (sendBtnIcon) {
            sendBtnIcon.textContent = 'stop_circle';
        }
        elements.sendBtn.classList.remove('inactive', 'active');
        elements.sendBtn.classList.add('processing');

        appendMessage('user', text, null, null, null, null, null, {
            onEdit: stageMessageForInput
        });
        scrollToBottom();

        // Assistant Message Placeholder
        const { msgDiv, contentDiv: answerDiv, sideColumn } = createMessageShell('assistant');

        const { logContainer, logSummary, logDetails, spinner, statusText, expandIcon } = createDynamicLogContainer();
        const seenLogs = new Set(); // 去重
        answerDiv.classList.add('markdown-body');
        answerDiv.appendChild(logContainer);

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-answer-body';
        const liveArtifactMessageId = `stream-${Date.now().toString(36)}`;
        contentWrapper.dataset.liveArtifactsMessageId = liveArtifactMessageId;
        contentWrapper.innerHTML = '<span class="blinking-cursor"></span>';
        answerDiv.appendChild(contentWrapper);
        elements.chatContainer.appendChild(msgDiv);
        scrollToBottom();

        const controller = new AbortController();
        setAbortController(controller);

        let currentAnswerBuffer = '';
        const copyBtn = createCopyButton(() => currentAnswerBuffer);
        const regenBtn = createRegenerateButton(async () => {
            // 移除当前助手消息
            msgDiv.remove();
            // 重新发送
            await handleSendMessage(lastUserMessage);
        });
        sideColumn.appendChild(createMessageActionRail([copyBtn, regenBtn], '助手消息操作'));

        let currentSources = [];
        let hasReceivedChunk = false;
        let searchStats = null;
        let searchStartTime = Date.now();

        function renderCurrentAssistantAnswer(isStreaming) {
            const suppressUnfencedInlineArtifact = hasCitationSources(currentSources);
            if (!getInlineLiveArtifact(currentAnswerBuffer, liveArtifactMessageId, isStreaming, { suppressUnfencedInlineArtifact })) {
                contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, currentSources);
            }
            renderLiveArtifactsForMessage(contentWrapper, currentAnswerBuffer, {
                messageId: liveArtifactMessageId,
                isStreaming,
                sources: currentSources,
                suppressUnfencedInlineArtifact,
            });
        }

        // 重置滚动跟踪（新一轮对话）
        userScrolled = false;

        // 实时耗时更新器
        const elapsedTimer = setInterval(() => {
            const elapsed = ((Date.now() - searchStartTime) / 1000).toFixed(1);
            if (statusText.textContent.includes('正在')) {
                statusText.textContent = statusText.textContent.replace(/ \([\d.]+s\)$/, '') + ` (${elapsed}s)`;
            }
        }, 500);

        try {
            await API.streamChat(text, {
                model: selectedModel,
                providerId: selectedProviderId,
                liveArtifactsMode: state.liveArtifactsMode,
                signal: controller.signal,
                onMeta: (meta) => {
                    const sessionId = typeof meta === 'string' ? meta : meta.session_id;
                    if (sessionId) {
                        setCurrentSessionId(sessionId);
                        const route = chatRoute(sessionId);
                        if (window.location.pathname !== route) {
                            history.replaceState({ sessionId }, '', route);
                        }
                    }
                },
                onLog: (msg) => {
                    if (msg.includes('ACTION_REQUIRED: CAPTCHA_DETECTED') ||
                        msg.includes('ACTION_REQUIRED: SEARCH_VERIFICATION_REQUIRED')) {
                        if (state.openBrowserModal) {
                            state.openBrowserModal(state.currentSessionId);
                        }
                        msg = "需要人工验证。请在弹出的窗口中通过搜索引擎验证。";
                    }
                    // Detect engine fallback notification
                    if (msg.includes('自动切换到')) {
                        const match = msg.match(/切换到\s*(\S+)/);
                        if (match) showToast(`搜索引擎已切换到 ${match[1]}`, 'warning');
                    }
                    statusText.textContent = msg;

                    // 去重检查
                    const logKey = msg.trim().substring(0, 80);
                    if (seenLogs.has(logKey)) return;
                    seenLogs.add(logKey);

                    const entry = createLogEntry(msg, new Date().toLocaleTimeString());
                    logDetails.appendChild(entry);
                    logDetails.scrollTop = logDetails.scrollHeight;
                },
                onSources: (sources) => {
                    currentSources = sources;
                    if (currentAnswerBuffer) {
                        renderCurrentAssistantAnswer(true);
                        if (!userScrolled) scrollToBottom();
                    }
                },
                onStats: (stats) => {
                    searchStats = stats;
                },
                onAnswerChunk: (chunk) => {
                    if (!hasReceivedChunk) {
                        hasReceivedChunk = true;
                        contentWrapper.innerHTML = '';
                    }
                    currentAnswerBuffer += chunk;
                    renderCurrentAssistantAnswer(true);
                    if (!userScrolled) scrollToBottom();
                },
                onAnswer: (finalAnswer, sessionId, finalSources) => {
                    if (Array.isArray(finalSources) && finalSources.length > 0) {
                        currentSources = finalSources;
                    }
                    currentAnswerBuffer = finalAnswer;
                    renderCurrentAssistantAnswer(false);
                    setCurrentSessionId(sessionId);
                    refreshHistory();
                },
                onError: (err) => {
                    if (!hasReceivedChunk) {
                        contentWrapper.innerHTML = '';
                    }
                    const errDiv = document.createElement('div');
                    errDiv.className = 'error-box';
                    // 友好的错误消息映射
                    let errMsg = err;
                    if (typeof err === 'string') {
                        if (err.includes('请先在设置中配置 API 密钥')) {
                            errMsg = '请先在设置中配置 API 密钥。点击左上角设置按钮，填入 API Key 后会自动保存。';
                        } else if (err.includes('请求失败 (429)') || err.includes('rate limit')) {
                            errMsg = 'API 请求过于频繁，请稍后重试。';
                        } else if (err.includes('请求失败 (401)') || err.includes('Unauthorized')) {
                            errMsg = 'API 密钥无效或已过期，请检查设置中的 API Key。';
                        } else if (err.includes('请求失败 (402)')) {
                            errMsg = 'API 额度已用完，请检查账户余额。';
                        } else if (err.includes('请求失败 (500)') || err.includes('502') || err.includes('503')) {
                            errMsg = 'API 服务暂时不可用，请稍后重试。';
                        }
                    }
                    errDiv.textContent = `错误: ${errMsg}`;
                    contentWrapper.appendChild(errDiv);
                },
                onDone: () => {}
            });
        } catch (e) {
            if (!hasReceivedChunk) {
                contentWrapper.innerHTML = '';
            }
            if (e.name === 'AbortError') {
                const warnDiv = document.createElement('div');
                warnDiv.className = 'warning-box';
                warnDiv.textContent = '[已由用户停止]';
                contentWrapper.appendChild(warnDiv);
            } else {
                console.error(e);
                const errDiv = document.createElement('div');
                errDiv.className = 'error-box';
                errDiv.textContent = `网络错误: ${e.message}`;
                contentWrapper.appendChild(errDiv);
            }
        } finally {
            clearInterval(elapsedTimer);
            const totalElapsed = ((Date.now() - searchStartTime) / 1000).toFixed(1);
            setIsProcessing(false);
            setAbortController(null);
            if (sendBtnIcon) {
                sendBtnIcon.textContent = 'send';
            }
            elements.sendBtn.classList.remove('processing');
            updateSendButtonState();
            spinner.classList.remove('rotating');
            spinner.textContent = 'check_circle';
            spinner.classList.add('completed');
            logContainer.classList.add('completed');
            if (searchStats && searchStats.sites_searched > 0) {
                let statsText = `已完成 · 搜索 ${searchStats.sites_searched} 个结果`;
                if (searchStats.sites_crawled > 0) {
                    statsText += ` · 深度阅读 ${searchStats.sites_crawled} 个页面`;
                }
                statsText += ` · ${totalElapsed}s`;
                statusText.textContent = statsText;
            } else {
                statusText.textContent = `已完成 · ${totalElapsed}s`;
            }
            // 搜索完成，自动折叠过程日志
            logDetails.classList.remove('open');
            if (expandIcon) expandIcon.classList.remove('expanded');
            if (logSummary) logSummary.setAttribute('aria-expanded', 'false');
        }
    }

    function updateSendButtonState() {
        const hasText = elements.userInput.value.trim().length > 0;
        const isActive = hasText || state.isProcessing;
        elements.sendBtn.disabled = !isActive;
        elements.sendBtn.classList.remove('inactive', 'active', 'processing');
        if (state.isProcessing) {
            elements.sendBtn.classList.add('processing');
        } else if (hasText) {
            elements.sendBtn.classList.add('active');
        } else {
            elements.sendBtn.classList.add('inactive');
        }
    }

    function resetInputHeight() {
        elements.userInput.style.height = '40px';
        elements.userInput.style.overflowY = 'hidden';
    }

    function autoResizeInput() {
        const scrollHeight = elements.userInput.scrollHeight;
        const maxHeight = 200;
        elements.userInput.style.height = '40px';
        if (scrollHeight > maxHeight) {
            elements.userInput.style.height = maxHeight + 'px';
            elements.userInput.style.overflowY = 'auto';
        } else {
            elements.userInput.style.height = scrollHeight + 'px';
            elements.userInput.style.overflowY = 'hidden';
        }
        updateSendButtonState();

    }

    // 绑定事件
    elements.sendBtn.addEventListener('click', () => handleSendMessage());
    elements.userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    });
    // Ctrl+Enter also sends (alternative shortcut)
    elements.userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            handleSendMessage();
        }
    });

    elements.userInput.addEventListener('input', autoResizeInput);

    // 粘贴大段文本时自动展开
    elements.userInput.addEventListener('paste', () => {
        setTimeout(autoResizeInput, 0);
    });

    // 初始化按钮状态
    updateSendButtonState();

    // Ctrl+Shift+R: regenerate last answer
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'R') {
            e.preventDefault();
            if (lastUserMessage && !state.isProcessing) {
                handleSendMessage(lastUserMessage);
            }
        }
    });

    // 模型切换提示
    const modelSelect = document.getElementById('model-select');
    if (modelSelect) {
        modelSelect.addEventListener('change', () => {
            const selectedOption = modelSelect.options[modelSelect.selectedIndex];
            const shortName = selectedOption ? selectedOption.textContent : modelSelect.value;
            showToast(`已切换至 ${shortName}`, 'info');
        });
    }

    // Quick settings toolbar interaction
    const quickEngineBtn = document.getElementById('quick-engine-btn');
    const quickEngineDropdown = document.getElementById('quick-engine-dropdown');
    
    if (quickEngineBtn && quickEngineDropdown) {
        quickEngineBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            quickEngineDropdown.classList.toggle('active');
        });
        
        document.addEventListener('click', (e) => {
            if (!quickEngineBtn.contains(e.target) && !quickEngineDropdown.contains(e.target)) {
                quickEngineDropdown.classList.remove('active');
            }
        });
        
        const dropdownItems = quickEngineDropdown.querySelectorAll('.quick-dropdown-item');
        dropdownItems.forEach(item => {
            item.addEventListener('click', async () => {
                const newEngine = item.getAttribute('data-value');
                quickEngineDropdown.classList.remove('active');
                
                if (state.settings) {
                    state.settings.search_engine = newEngine;
                    
                    const modalSelect = document.getElementById('engine-select');
                    if (modalSelect) {
                        modalSelect.value = newEngine;
                    }
                    
                    await API.saveSettingsAPI(state.settings);
                    syncQuickSettingsFromState();
                    showToast(`搜索引擎已切换为 ${item.textContent}`, 'success');
                }
            });
        });
    }

    const quickInteractiveBtn = document.getElementById('quick-interactive-btn');
    if (quickInteractiveBtn) {
        quickInteractiveBtn.addEventListener('click', async () => {
            if (state.settings) {
                const currentVal = coerceBooleanSetting(state.settings.interactive_search, true);
                const newVal = !currentVal;
                state.settings.interactive_search = newVal;
                
                const modalCheckbox = document.getElementById('interactive-search-input');
                if (modalCheckbox) {
                    modalCheckbox.checked = newVal;
                }

                syncQuickSettingsFromState();

                const saved = await API.saveSettingsAPI(state.settings);
                if (!saved) {
                    state.settings.interactive_search = currentVal;
                    if (modalCheckbox) {
                        modalCheckbox.checked = currentVal;
                    }
                    syncQuickSettingsFromState();
                    showToast('深度搜索设置保存失败，已恢复原状态', 'warning');
                    return;
                }
                syncQuickSettingsFromState();
                
                const status = newVal ? '已开启' : '已关闭';
                showToast(`深度搜索${status}`, 'info');
            }
        });
    }

    const quickLiveArtifactsBtn = document.getElementById('quick-live-artifacts-btn');
    if (quickLiveArtifactsBtn) {
        quickLiveArtifactsBtn.addEventListener('click', async () => {
            const nextValue = !state.liveArtifactsMode;
            setLiveArtifactsMode(nextValue);
            if (state.settings) {
                state.settings.live_artifacts_mode = nextValue;
            }
            syncQuickSettingsFromState();
            showToast(`Live Artifacts ${nextValue ? '已开启' : '已关闭'}`, 'info');

            if (state.settings) {
                const saved = await API.saveSettingsAPI(state.settings);
                if (!saved) {
                    setLiveArtifactsMode(!nextValue);
                    state.settings.live_artifacts_mode = !nextValue;
                    syncQuickSettingsFromState();
                    showToast('Live Artifacts 设置保存失败，已恢复原状态', 'warning');
                }
            }
        });
    }

    syncQuickSettingsFromState();

    return { loadChat, deleteChat };
}

export function syncQuickSettingsFromState() {
    const quickEngineName = document.getElementById('quick-engine-name');
    const quickEngineDropdown = document.getElementById('quick-engine-dropdown');
    const quickInteractiveBtn = document.getElementById('quick-interactive-btn');
    const quickLiveArtifactsBtn = document.getElementById('quick-live-artifacts-btn');
    
    if (!state.settings) return;
    
    const engine = state.settings.search_engine || 'searxng';
    const engineNames = {
        'duckduckgo': 'DuckDuckGo',
        'google': 'Google',
        'bing': 'Bing',
        'sogou': '搜狗 (Sogou)',
        'brave': 'Brave Search',
        'searxng': 'SearXNG'
    };
    if (quickEngineName) {
        quickEngineName.textContent = engineNames[engine] || engine;
    }
    
    if (quickEngineDropdown) {
        const dropdownItems = quickEngineDropdown.querySelectorAll('.quick-dropdown-item');
        let activeSvg = null;
        dropdownItems.forEach(item => {
            const itemVal = item.getAttribute('data-value');
            const isActive = itemVal === engine;
            item.classList.toggle('active', isActive);
            if (isActive) {
                activeSvg = item.querySelector('svg');
            }
        });
        
        const iconContainer = document.getElementById('quick-engine-icon-container');
        if (iconContainer && activeSvg) {
            iconContainer.innerHTML = '';
            iconContainer.appendChild(activeSvg.cloneNode(true));
        }
    }
    
    const interactive = coerceBooleanSetting(state.settings.interactive_search, true);
    if (quickInteractiveBtn) {
        quickInteractiveBtn.classList.toggle('active', interactive);
    }

    if (quickLiveArtifactsBtn) {
        const active = Boolean(state.liveArtifactsMode);
        quickLiveArtifactsBtn.classList.toggle('active', active);
        quickLiveArtifactsBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
        quickLiveArtifactsBtn.setAttribute(
            'aria-label',
            active
                ? 'Live Artifacts 提示已激活。点击移除。'
                : '加载 Live Artifacts 提示并保存设置'
        );
        quickLiveArtifactsBtn.title = active
            ? 'Live Artifacts 提示已激活。点击移除。'
            : '加载 Live Artifacts 提示并保存';
    }
}
