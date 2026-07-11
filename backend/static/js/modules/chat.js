import { coerceBooleanSetting, state, setAbortController, setCurrentSessionId, setIsProcessing, setLiveArtifactsMode } from './state.js?v=3';
import { createCopyButton, createMessageActionRail, createRegenerateButton } from './utils.js?v=6';
import { updateActiveHistoryItem } from './history-view.js?v=23';
import { createDynamicLogContainer, createLogEntry, scrollToBottom, appendMessage, renderMessages, showConfirm, createMessageShell } from './ui.js?v=25';
import { extractSources, hasCitationSources, linkCitationsInElement, renderWithCitations } from './source-renderer.js?v=8';
import { getInlineLiveArtifact, renderLiveArtifactsForMessage } from './live-artifacts.js?v=18';
import {
    applyIntensityPresetToSettings,
    getIntensityPreset,
    updateIntensityUI,
} from './search-intensity.js?v=1';
import { showToast } from './toast.js';
import * as API from './api.js?v=8';
import { ensureBridgeConnected, warnIfBridgeDisconnected } from './bridge.js?v=1';

function chatRoute(sessionId) {
    return `/c/${encodeURIComponent(String(sessionId ?? ''))}`;
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

        // Fail fast before clearing the input: all engines need the Chrome bridge.
        const bridgeReady = await ensureBridgeConnected({ forceRefresh: true });
        if (!bridgeReady) {
            showToast('请先连接 JustSearch Bridge 扩展后再搜索', 'warning', 4000);
            return;
        }

        lastUserMessage = text;

        const modelSelect = document.getElementById('model-select');
        const selectedModelOption = modelSelect?.options[modelSelect.selectedIndex] || null;
        const selectedModel = selectedModelOption ? selectedModelOption.value : '';
        const selectedProviderId = selectedModelOption?.dataset.providerId || state.settings.default_provider_id || '';

        elements.userInput.value = '';
        resetInputHeight();
        setIsProcessing(true);
        syncQuickSettingsFromState();
        updateSendButtonState();
        elements.heroSection.style.display = 'none';

        const sendBtnIcon = elements.sendBtn.querySelector('.material-symbols-rounded');
        if (sendBtnIcon) {
            sendBtnIcon.textContent = 'stop_circle';
        }
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
        let streamOutcome = 'completed';
        // 流式渲染节流：避免每个 chunk 都全量 md.render+DOMPurify（O(n²)）。
        // 用 rAF 合并到下一帧；完成时强制立即渲染保证最终态正确。
        let pendingRender = false;
        let pendingRenderIsStreaming = false;
        let reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function scheduleStreamRender(isStreaming) {
            pendingRenderIsStreaming = isStreaming;
            if (pendingRender) return;
            pendingRender = true;
            // rAF 批处理；reduced-motion 下仍需渲染但不做 smooth 滚动
            requestAnimationFrame(() => {
                pendingRender = false;
                renderCurrentAssistantAnswer(pendingRenderIsStreaming);
                if (!userScrolled) scrollToBottom();
            });
        }

        function renderCurrentAssistantAnswer(isStreaming) {
            const resolvedSources = hasCitationSources(currentSources)
                ? currentSources
                : extractSources(currentAnswerBuffer);
            // Live Artifacts 模式下保留内联 HTML 走 iframe 预览（样式完整、引用照常链接）；
            // 仅在该模式关闭时，遇到意外 HTML 才退回带引用的 Markdown 渲染。
            const suppressUnfencedInlineArtifact = !state.liveArtifactsMode && hasCitationSources(resolvedSources);
            if (!getInlineLiveArtifact(currentAnswerBuffer, liveArtifactMessageId, isStreaming, { suppressUnfencedInlineArtifact })) {
                contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, resolvedSources);
            }
            renderLiveArtifactsForMessage(contentWrapper, currentAnswerBuffer, {
                messageId: liveArtifactMessageId,
                isStreaming,
                sources: resolvedSources,
                suppressUnfencedInlineArtifact,
            });
            linkCitationsInElement(contentWrapper, resolvedSources);
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
                    // 节流到下一帧，避免逐 token 全量重渲染
                    scheduleStreamRender(true);
                },
                onAnswer: (finalAnswer, sessionId, finalSources) => {
                    if (hasCitationSources(finalSources)) {
                        currentSources = finalSources;
                    }
                    currentAnswerBuffer = finalAnswer;
                    // 取消任何挂起的节流渲染，立即用最终态渲染一次
                    pendingRender = false;
                    renderCurrentAssistantAnswer(false);
                    setCurrentSessionId(sessionId);
                    refreshHistory();
                },
                onError: (err) => {
                    streamOutcome = 'failed';
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
                streamOutcome = 'cancelled';
                const warnDiv = document.createElement('div');
                warnDiv.className = 'warning-box';
                warnDiv.textContent = '[已由用户停止]';
                contentWrapper.appendChild(warnDiv);
            } else {
                streamOutcome = 'failed';
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
            syncQuickSettingsFromState();
            if (sendBtnIcon) {
                sendBtnIcon.textContent = 'send';
            }
            elements.sendBtn.classList.remove('processing');
            updateSendButtonState();
            spinner.classList.remove('rotating');
            spinner.classList.remove('completed', 'failed', 'cancelled');
            logContainer.classList.remove('completed', 'failed', 'cancelled');
            if (streamOutcome === 'failed') {
                spinner.textContent = 'error';
                spinner.classList.add('failed');
                logContainer.classList.add('failed');
                statusText.textContent = `失败 · ${totalElapsed}s`;
            } else if (streamOutcome === 'cancelled') {
                spinner.textContent = 'stop_circle';
                spinner.classList.add('cancelled');
                logContainer.classList.add('cancelled');
                statusText.textContent = `已停止 · ${totalElapsed}s`;
            } else if (searchStats && searchStats.sites_searched > 0) {
                spinner.textContent = 'check_circle';
                spinner.classList.add('completed');
                logContainer.classList.add('completed');
                let statsText = `已完成 · 搜索 ${searchStats.sites_searched} 个结果`;
                if (searchStats.sites_crawled > 0) {
                    statsText += ` · 深度阅读 ${searchStats.sites_crawled} 个页面`;
                }
                statsText += ` · ${totalElapsed}s`;
                statusText.textContent = statsText;
            } else {
                spinner.textContent = 'check_circle';
                spinner.classList.add('completed');
                logContainer.classList.add('completed');
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
        // 统一用 disabled 属性表达「不可用」，class 仅承载视觉态(processing)。
        elements.sendBtn.disabled = !isActive;
        elements.sendBtn.classList.toggle('processing', state.isProcessing);
        elements.sendBtn.setAttribute('aria-disabled', state.isProcessing ? 'false' : (!hasText ? 'true' : 'false'));
    }

    function resetInputHeight() {
        elements.userInput.style.height = '38px';
        elements.userInput.style.overflowY = 'hidden';
    }

    function autoResizeInput() {
        const maxHeight = 200;
        // 先重置再读取 scrollHeight：否则空内容时 scrollHeight 会塌缩为当前
        // clientHeight（上一次撑开的高度），导致清空后高度卡在旧值无法恢复。
        elements.userInput.style.height = '38px';
        const scrollHeight = elements.userInput.scrollHeight;
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
        // 让下拉项可被键盘聚焦与选择（保留 <div> 结构以匹配现有测试）
        const dropdownItems = Array.from(quickEngineDropdown.querySelectorAll('.quick-dropdown-item'));
        dropdownItems.forEach((item, idx) => {
            item.setAttribute('role', 'option');
            item.setAttribute('tabindex', '-1');
            if (!item.id) item.id = `quick-engine-opt-${idx}`;
        });
        quickEngineDropdown.setAttribute('role', 'listbox');
        quickEngineDropdown.setAttribute('aria-label', '搜索引擎列表');

        quickEngineBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = !quickEngineDropdown.classList.contains('active');
            quickEngineDropdown.classList.toggle('active');
            quickEngineBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            if (willOpen) {
                // 打开后将焦点移到当前选中项
                const active = quickEngineDropdown.querySelector('.quick-dropdown-item.active') || dropdownItems[0];
                if (active) active.focus();
            }
        });
        quickEngineBtn.setAttribute('aria-haspopup', 'listbox');
        quickEngineBtn.setAttribute('aria-expanded', 'false');

        document.addEventListener('click', (e) => {
            if (!quickEngineBtn.contains(e.target) && !quickEngineDropdown.contains(e.target)) {
                quickEngineDropdown.classList.remove('active');
                quickEngineBtn.setAttribute('aria-expanded', 'false');
            }
        });

        // 键盘导航：在按钮与选项间用方向键移动焦点
        function moveHighlight(current, dir) {
            const idx = dropdownItems.indexOf(current);
            let next = idx;
            if (dir === 'down') next = (idx + 1) % dropdownItems.length;
            else if (dir === 'up') next = (idx - 1 + dropdownItems.length) % dropdownItems.length;
            else if (dir === 'home') next = 0;
            else if (dir === 'end') next = dropdownItems.length - 1;
            dropdownItems[next]?.focus();
        }

        quickEngineBtn.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                quickEngineDropdown.classList.add('active');
                quickEngineBtn.setAttribute('aria-expanded', 'true');
                const active = quickEngineDropdown.querySelector('.quick-dropdown-item.active') || dropdownItems[0];
                if (active) active.focus();
            }
        });

        dropdownItems.forEach(item => {
            item.addEventListener('keydown', (e) => {
                if (e.key === 'ArrowDown') { e.preventDefault(); moveHighlight(item, 'down'); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); moveHighlight(item, 'up'); }
                else if (e.key === 'Home') { e.preventDefault(); moveHighlight(item, 'home'); }
                else if (e.key === 'End') { e.preventDefault(); moveHighlight(item, 'end'); }
                else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); item.click(); }
                else if (e.key === 'Escape') {
                    quickEngineDropdown.classList.remove('active');
                    quickEngineBtn.setAttribute('aria-expanded', 'false');
                    quickEngineBtn.focus();
                }
            });
        });

        dropdownItems.forEach(item => {
            item.addEventListener('click', async () => {
                const newEngine = item.getAttribute('data-value');
                quickEngineDropdown.classList.remove('active');
                quickEngineBtn.setAttribute('aria-expanded', 'false');
                quickEngineBtn.focus();
                
                if (state.settings) {
                    state.settings.search_engine = newEngine;
                    
                    const modalSelect = document.getElementById('engine-select');
                    if (modalSelect) {
                        modalSelect.value = newEngine;
                    }
                    
                    await API.saveSettingsAPI(state.settings);
                    syncQuickSettingsFromState();
                    showToast(`搜索引擎已切换为 ${item.textContent}`, 'success');
                    warnIfBridgeDisconnected(item.textContent?.trim() || newEngine);
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

    setupSearchIntensityControls();

    syncQuickSettingsFromState();

    return { loadChat, deleteChat };
}

function syncSettingsFormSearchLimits(maxResults, maxIterations) {
    const maxResultsInput = document.getElementById('max-results-input');
    const maxIterationsInput = document.getElementById('max-iterations-input');
    if (maxResultsInput) maxResultsInput.value = String(maxResults);
    if (maxIterationsInput) maxIterationsInput.value = String(maxIterations);
}

function setupSearchIntensityControls() {
    const bar = document.getElementById('search-intensity-bar');
    if (!bar) return;

    const chips = Array.from(bar.querySelectorAll('.intensity-chip[data-intensity]'));
    chips.forEach((chip) => {
        chip.addEventListener('click', async () => {
            if (state.isProcessing) return;
            const presetId = chip.getAttribute('data-intensity');
            if (!presetId || presetId === 'custom') return;
            const preset = getIntensityPreset(presetId);
            if (!preset || !state.settings) return;

            const previousResults = state.settings.max_results;
            const previousIterations = state.settings.max_iterations;
            state.settings = applyIntensityPresetToSettings(state.settings, presetId);
            syncSettingsFormSearchLimits(preset.max_results, preset.max_iterations);
            syncQuickSettingsFromState();

            const saved = await API.saveSettingsAPI(state.settings);
            if (!saved) {
                state.settings.max_results = previousResults;
                state.settings.max_iterations = previousIterations;
                syncSettingsFormSearchLimits(previousResults, previousIterations);
                syncQuickSettingsFromState();
                showToast('搜索强度保存失败，已恢复原状态', 'warning');
                return;
            }
            showToast(`搜索强度：${preset.label}（${preset.hint}）`, 'success');
        });

        chip.addEventListener('keydown', (e) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'Home' && e.key !== 'End') {
                return;
            }
            const visible = chips.filter((c) => !c.hidden && c.getAttribute('data-intensity') !== 'custom');
            const currentIndex = visible.indexOf(chip);
            if (currentIndex < 0) return;
            e.preventDefault();
            let nextIndex = currentIndex;
            if (e.key === 'ArrowRight') nextIndex = (currentIndex + 1) % visible.length;
            else if (e.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + visible.length) % visible.length;
            else if (e.key === 'Home') nextIndex = 0;
            else if (e.key === 'End') nextIndex = visible.length - 1;
            visible[nextIndex]?.focus();
        });
    });
}

export function syncQuickSettingsFromState() {
    const quickEngineName = document.getElementById('quick-engine-name');
    const quickEngineDropdown = document.getElementById('quick-engine-dropdown');
    const quickInteractiveBtn = document.getElementById('quick-interactive-btn');
    const quickLiveArtifactsBtn = document.getElementById('quick-live-artifacts-btn');
    
    if (!state.settings) return;
    
    const engine = state.settings.search_engine || 'google';
    const engineNames = {
        'duckduckgo': 'DuckDuckGo',
        'google': 'Google',
        'bing': 'Bing',
        'sogou': '搜狗 (Sogou)',
        'brave': 'Brave Search',
        'searxng': 'SearXNG',
        'baidu': '百度 (Baidu)',
        'yandex': 'Yandex',
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

    updateIntensityUI({
        maxResults: state.settings.max_results,
        maxIterations: state.settings.max_iterations,
        disabled: Boolean(state.isProcessing),
    });
}
