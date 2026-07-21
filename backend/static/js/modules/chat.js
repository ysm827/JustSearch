import {
    abortActiveStream,
    bumpChatEpoch,
    clearEditingMessage,
    coerceBooleanSetting,
    isChatEpochCurrent,
    isEditingMessage,
    setEditingMessage,
    setLastUserMessageIndex,
    setSessionMessageCount,
    state,
    setAbortController,
    setCurrentSessionId,
    setIsProcessing,
    setLiveArtifactsMode,
} from './state.js?v=5';
import { createCopyButton, createMessageActionRail, createRegenerateButton } from './utils.js?v=6';
import { updateActiveHistoryItem } from './history-view.js?v=23';
import { createDynamicLogContainer, createLogEntry, scrollToBottom, appendMessage, renderMessages, showConfirm, createMessageShell } from './ui.js?v=31';
import { extractSources, hasCitationSources, linkCitationsInElement, renderWithCitations } from './source-renderer.js?v=10';
import { getInlineLiveArtifact, renderLiveArtifactsForMessage } from './live-artifacts.js?v=27';
import { bindCitationEvidenceClicks, setEvidenceContext } from './evidence-panel.js?v=2';
import {
    applyIntensityPresetToSettings,
    getIntensityPreset,
    updateIntensityUI,
} from './search-intensity.js?v=1';
import { showToast } from './toast.js';
import * as API from './api.js?v=11';
import { ensureBridgeConnected, warnIfBridgeDisconnected } from './bridge.js?v=7';

function chatRoute(sessionId) {
    return `/c/${encodeURIComponent(String(sessionId ?? ''))}`;
}

/**
 * Leave the current chat view cleanly: abort any stream so its late SSE
 * events cannot re-bind state.currentSessionId, bump epoch so stale
 * callbacks become no-ops, and restore the send button.
 */
export function abandonActiveChatWork(uiElements = elements) {
    abortActiveStream();
    bumpChatEpoch();
    clearEditingMessage();
    setLastUserMessageIndex(null);
    setSessionMessageCount(0);
    const banner = uiElements?.editMessageBanner || document.getElementById('edit-message-banner');
    const inputArea = uiElements?.inputArea || document.getElementById('input-area');
    if (banner) banner.hidden = true;
    if (inputArea) inputArea.classList.remove('is-editing-message');
    if (uiElements?.userInput) {
        uiElements.userInput.placeholder = '提出问题...';
    }
    if (!uiElements?.sendBtn) return;
    const sendBtnIcon = uiElements.sendBtn.querySelector('.material-symbols-rounded');
    if (sendBtnIcon) sendBtnIcon.textContent = 'send';
    uiElements.sendBtn.classList.remove('processing');
    uiElements.sendBtn.setAttribute('aria-label', '发送消息');
    uiElements.sendBtn.title = '发送';
    const hasText = Boolean(uiElements.userInput?.value?.trim());
    uiElements.sendBtn.disabled = !hasText;
    uiElements.sendBtn.setAttribute('aria-disabled', hasText ? 'false' : 'true');
    try {
        syncQuickSettingsFromState();
    } catch {
        // settings UI may not be ready during early init
    }
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

    function syncEditChrome() {
        const banner = elements.editMessageBanner || document.getElementById('edit-message-banner');
        const inputArea = elements.inputArea || document.getElementById('input-area');
        const bannerText = elements.editMessageBannerText || document.getElementById('edit-message-banner-text');
        const editing = isEditingMessage();
        if (banner) banner.hidden = !editing;
        if (inputArea) inputArea.classList.toggle('is-editing-message', editing);
        if (bannerText && editing) {
            bannerText.textContent = state.editMode === 'update'
                ? '正在修改消息内容 · 发送后仅更新该条'
                : '正在编辑消息 · 发送后将从此处截断并重新生成';
        }
        if (elements.sendBtn && !state.isProcessing) {
            elements.sendBtn.setAttribute(
                'aria-label',
                editing ? (state.editMode === 'update' ? '更新消息' : '更新并重新发送') : '发送消息',
            );
            elements.sendBtn.title = editing
                ? (state.editMode === 'update' ? '更新消息' : '更新并重新发送')
                : '发送';
        }
    }

    function cancelEdit() {
        clearEditingMessage();
        syncEditChrome();
        if (elements.userInput) {
            elements.userInput.placeholder = '提出问题...';
        }
    }

    /**
     * AMC edit: fill composer + set editingMessageIndex (resend by default).
     * @param {{ content: string, messageIndex?: number|null, mode?: 'resend'|'update' }|string} payload
     */
    function beginEditMessage(payload) {
        const content = typeof payload === 'string' ? payload : String(payload?.content || '');
        const messageIndex = typeof payload === 'object' && payload
            ? payload.messageIndex
            : null;
        const mode = typeof payload === 'object' && payload?.mode === 'update' ? 'update' : 'resend';

        if (!content) return;
        if (state.isProcessing) {
            // AMC stops generation when starting an edit.
            if (state.abortController) {
                state.abortController.abort();
                setAbortController(null);
            }
            setIsProcessing(false);
            syncQuickSettingsFromState();
        }

        if (messageIndex !== null && messageIndex !== undefined && Number.isFinite(Number(messageIndex))) {
            setEditingMessage(Number(messageIndex), mode);
        } else {
            // No stable index (e.g. live bubble) — still prefill for convenience.
            clearEditingMessage();
        }

        elements.userInput.value = content;
        elements.userInput.placeholder = mode === 'update' ? '修改消息内容...' : '编辑后发送将重新生成...';
        elements.userInput.dispatchEvent(new Event('input', { bubbles: true }));
        resetInputHeight();
        elements.userInput.focus({ preventScroll: true });
        scrollToBottom();
        syncEditChrome();
        showToast(
            mode === 'update' ? '已进入修改模式，发送后仅更新该条' : '已进入编辑模式，发送后将从此处重新生成',
            'info',
        );
    }

    /**
     * Remove DOM message bubbles from `fromIndex` onward (optimistic AMC truncate).
     */
    function removeMessagesFromDom(fromIndex) {
        if (!Number.isFinite(Number(fromIndex))) return;
        const cutoff = Math.floor(Number(fromIndex));
        const nodes = Array.from(elements.chatContainer.querySelectorAll('.message'));
        const lastKept = nodes
            .filter((node) => {
                const idx = Number(node.dataset.messageIndex);
                return Number.isFinite(idx) && idx < cutoff;
            })
            .pop();

        nodes.forEach((node) => {
            const idx = Number(node.dataset.messageIndex);
            if (Number.isFinite(idx)) {
                if (idx >= cutoff) node.remove();
                return;
            }
            // Unindexed stream bubbles after the last kept message are abandoned tails.
            if (
                node.classList.contains('user')
                || node.classList.contains('assistant')
                || node.classList.contains('error')
            ) {
                if (!lastKept || (lastKept.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) {
                    node.remove();
                }
            }
        });
    }

    /**
     * AMC Retry: truncate at the previous user message and re-send it.
     * If a stream is active, stop it first (retry-and-stop).
     */
    async function regenerateFromPrompt(prompt, meta = {}) {
        if (!prompt) return;
        if (state.isProcessing) {
            if (state.abortController) {
                try { state.abortController.abort(); } catch { /* ignore */ }
                setAbortController(null);
            }
            setIsProcessing(false);
            // Let the aborted stream's finally settle before starting a new turn.
            await new Promise((resolve) => setTimeout(resolve, 0));
        }
        let truncateIndex = meta.previousUserIndex;
        if (truncateIndex === null || truncateIndex === undefined) {
            truncateIndex = state.lastUserMessageIndex;
        }
        if (truncateIndex === null || truncateIndex === undefined) {
            // Fallback: resend without truncate (legacy live bubble).
            await handleSendMessage(prompt, { skipAppendUser: false });
            return;
        }
        clearEditingMessage();
        syncEditChrome();
        await handleSendMessage(prompt, {
            truncateFromIndex: Number(truncateIndex),
            skipAppendUser: false,
        });
    }

    async function refreshAfterMessageDeleted() {
        cancelEdit();
        if (state.currentSessionId) {
            await loadChat(state.currentSessionId);
        }
        await refreshHistory();
        showToast('消息已删除', 'success');
    }

    async function loadChat(sessionId) {
        // Drop any in-flight stream so its answer cannot land on another chat.
        abandonActiveChatWork(elements);
        cancelEdit();
        const loadEpoch = state.chatEpoch;
        setCurrentSessionId(sessionId);
        updateActiveHistoryItem(sessionId);
        // 更新浏览器地址栏
        const route = chatRoute(sessionId);
        if (window.location.pathname !== route) {
            history.pushState({ sessionId }, '', route);
        }
        const data = await API.fetchChat(sessionId);
        // Stale response: user already switched away (new chat / other history).
        if (!isChatEpochCurrent(loadEpoch) || state.currentSessionId !== sessionId) {
            return;
        }
        if (data) {
            const messages = Array.isArray(data.messages) ? data.messages : [];
            setSessionMessageCount(messages.length);
            let lastUserIdx = null;
            let lastUserContent = '';
            messages.forEach((msg, idx) => {
                if (msg?.role === 'user' && msg?.content) {
                    lastUserIdx = idx;
                    lastUserContent = msg.content;
                }
            });
            setLastUserMessageIndex(lastUserIdx);
            lastUserMessage = lastUserContent || '';
            renderMessages(messages, {
                onEdit: beginEditMessage,
                onRegenerate: regenerateFromPrompt,
                onMessageDeleted: refreshAfterMessageDeleted,
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

    /**
     * @param {string} [overrideText]
     * @param {{ truncateFromIndex?: number|null, skipAppendUser?: boolean }} [options]
     */
    async function handleSendMessage(overrideText, options = {}) {
        if (state.isProcessing) {
            if (state.abortController) {
                state.abortController.abort();
                setAbortController(null);
            }
            return;
        }

        const text = (overrideText !== undefined ? overrideText : elements.userInput.value).trim();
        if (!text) return;

        // Capture session + view epoch BEFORE any await. New-chat / history
        // switch during bridge check must not let us attach to another session,
        // and late SSE from a previous stream must not reclaim session_id.
        const requestSessionId = state.currentSessionId;
        const streamEpoch = state.chatEpoch;
        const isStreamActive = () => isChatEpochCurrent(streamEpoch);

        // AMC resend: prefer explicit truncate option, else active edit state.
        let truncateFromIndex = options.truncateFromIndex;
        if (
            (truncateFromIndex === null || truncateFromIndex === undefined)
            && isEditingMessage()
            && state.editMode === 'resend'
        ) {
            truncateFromIndex = state.editingMessageIndex;
        }
        if (truncateFromIndex !== null && truncateFromIndex !== undefined) {
            truncateFromIndex = Number(truncateFromIndex);
            if (!Number.isFinite(truncateFromIndex) || truncateFromIndex < 0) {
                truncateFromIndex = null;
            } else {
                truncateFromIndex = Math.floor(truncateFromIndex);
            }
        } else {
            truncateFromIndex = null;
        }

        // Fail fast before clearing the input: all engines need the Chrome bridge.
        const bridgeReady = await ensureBridgeConnected({ forceRefresh: true });
        if (!bridgeReady) {
            showToast('请先连接 JustSearch Bridge 扩展后再搜索', 'warning', 4000);
            return;
        }
        if (!isStreamActive()) {
            // User already left this view (new chat / switched history) while
            // we were waiting on the bridge. Do not start a stray request.
            return;
        }

        lastUserMessage = text;

        // Optimistic DOM truncate (AMC slice) before appending the new turn.
        if (truncateFromIndex !== null) {
            removeMessagesFromDom(truncateFromIndex);
            setSessionMessageCount(truncateFromIndex);
        }

        const userMessageIndex = truncateFromIndex !== null
            ? truncateFromIndex
            : state.sessionMessageCount;
        const assistantMessageIndex = userMessageIndex + 1;
        setLastUserMessageIndex(userMessageIndex);

        // Clear edit chrome once send starts.
        clearEditingMessage();
        syncEditChrome();
        if (elements.userInput) {
            elements.userInput.placeholder = '提出问题...';
        }

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

        if (!options.skipAppendUser) {
            appendMessage('user', text, null, null, null, userMessageIndex, null, {
                onEdit: beginEditMessage,
            });
        }
        scrollToBottom();

        // Assistant Message Placeholder
        const { msgDiv, contentDiv: answerDiv, sideColumn } = createMessageShell('assistant');
        msgDiv.dataset.messageIndex = String(assistantMessageIndex);

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
            // AMC Retry: truncate at this turn's user message and re-send.
            await regenerateFromPrompt(lastUserMessage, {
                previousUserIndex: userMessageIndex,
            });
        });
        sideColumn.appendChild(createMessageActionRail([copyBtn, regenBtn], '助手消息操作'));

        let currentSources = [];
        let hasReceivedChunk = false;
        let searchStats = null;
        let currentCitations = [];
        let searchStartTime = Date.now();
        let streamOutcome = 'completed';
        // 流式渲染节流：避免每个 chunk 都全量 md.render+DOMPurify（O(n²)）。
        // 用 rAF 合并到下一帧；完成时强制立即渲染保证最终态正确。
        let pendingRender = false;
        let pendingRenderIsStreaming = false;
        let reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function scheduleStreamRender(isStreaming) {
            if (!isStreamActive()) return;
            pendingRenderIsStreaming = isStreaming;
            if (pendingRender) return;
            pendingRender = true;
            // rAF 批处理；reduced-motion 下仍需渲染但不做 smooth 滚动
            requestAnimationFrame(() => {
                pendingRender = false;
                if (!isStreamActive()) return;
                renderCurrentAssistantAnswer(pendingRenderIsStreaming);
                if (!userScrolled) scrollToBottom();
            });
        }

        function renderCurrentAssistantAnswer(isStreaming) {
            const resolvedSources = hasCitationSources(currentSources)
                ? currentSources
                : extractSources(currentAnswerBuffer);
            // AMC-aligned Live Artifacts path: native HTML → iframe; when the mode
            // is on, Markdown / mixed answers are coerced into one themed iframe so
            // clipped parent-page HTML (thin gray bars) cannot appear.
            const suppressUnfencedInlineArtifact = !state.liveArtifactsMode && hasCitationSources(resolvedSources);
            const liveArtifactOptions = {
                suppressUnfencedInlineArtifact,
                liveArtifactsMode: Boolean(state.liveArtifactsMode),
            };
            if (!getInlineLiveArtifact(currentAnswerBuffer, liveArtifactMessageId, isStreaming, liveArtifactOptions)) {
                contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, resolvedSources);
            }
            renderLiveArtifactsForMessage(contentWrapper, currentAnswerBuffer, {
                messageId: liveArtifactMessageId,
                isStreaming,
                sources: resolvedSources,
                ...liveArtifactOptions,
            });
            linkCitationsInElement(contentWrapper, resolvedSources);
            setEvidenceContext({ sources: resolvedSources, citations: currentCitations });
            bindCitationEvidenceClicks(contentWrapper, {
                sources: resolvedSources,
                citations: currentCitations,
            });
        }

        // 重置滚动跟踪（新一轮对话）
        userScrolled = false;

        // 实时耗时更新器
        const elapsedTimer = setInterval(() => {
            if (!isStreamActive()) return;
            const elapsed = ((Date.now() - searchStartTime) / 1000).toFixed(1);
            if (statusText.textContent.includes('正在')) {
                statusText.textContent = statusText.textContent.replace(/ \([\d.]+s\)$/, '') + ` (${elapsed}s)`;
            }
        }, 500);

        try {
            await API.streamChat(text, {
                model: selectedModel,
                providerId: selectedProviderId,
                // Freeze session id at send time so a concurrent view switch
                // cannot redirect this request into another conversation.
                sessionId: requestSessionId,
                truncateFromIndex,
                liveArtifactsMode: state.liveArtifactsMode,
                signal: controller.signal,
                onMeta: (meta) => {
                    if (!isStreamActive()) return;
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
                    if (!isStreamActive()) return;
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
                    if (!isStreamActive()) return;
                    currentSources = sources;
                    setEvidenceContext({ sources: currentSources, citations: currentCitations });
                    if (currentAnswerBuffer) {
                        renderCurrentAssistantAnswer(true);
                        if (!userScrolled) scrollToBottom();
                    }
                },
                onStats: (stats) => {
                    if (!isStreamActive()) return;
                    searchStats = stats;
                },
                onAnswerChunk: (chunk) => {
                    if (!isStreamActive()) return;
                    if (!hasReceivedChunk) {
                        hasReceivedChunk = true;
                        contentWrapper.innerHTML = '';
                    }
                    currentAnswerBuffer += chunk;
                    // 节流到下一帧，避免逐 token 全量重渲染
                    scheduleStreamRender(true);
                },
                onAnswer: (finalAnswer, sessionId, finalSources, finalCitations) => {
                    if (!isStreamActive()) return;
                    if (hasCitationSources(finalSources)) {
                        currentSources = finalSources;
                    }
                    if (Array.isArray(finalCitations)) {
                        currentCitations = finalCitations;
                    }
                    currentAnswerBuffer = finalAnswer;
                    // 取消任何挂起的节流渲染，立即用最终态渲染一次
                    pendingRender = false;
                    renderCurrentAssistantAnswer(false);
                    setCurrentSessionId(sessionId);
                    // user + assistant persisted → count is assistantIndex + 1
                    setSessionMessageCount(assistantMessageIndex + 1);
                    setLastUserMessageIndex(userMessageIndex);
                    refreshHistory();
                },
                onError: (err) => {
                    if (!isStreamActive()) return;
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
            if (e.name === 'AbortError') {
                streamOutcome = 'cancelled';
                // Only annotate the bubble if this stream still owns the view.
                if (isStreamActive()) {
                    if (!hasReceivedChunk) {
                        contentWrapper.innerHTML = '';
                    }
                    const warnDiv = document.createElement('div');
                    warnDiv.className = 'warning-box';
                    warnDiv.textContent = '[已由用户停止]';
                    contentWrapper.appendChild(warnDiv);
                }
            } else if (isStreamActive()) {
                streamOutcome = 'failed';
                console.error(e);
                if (!hasReceivedChunk) {
                    contentWrapper.innerHTML = '';
                }
                const errDiv = document.createElement('div');
                errDiv.className = 'error-box';
                errDiv.textContent = `网络错误: ${e.message}`;
                contentWrapper.appendChild(errDiv);
            } else {
                streamOutcome = 'cancelled';
            }
        } finally {
            clearInterval(elapsedTimer);
            // Always clear the controller if we still own it — even after view switch.
            if (state.abortController === controller) {
                setAbortController(null);
            }
            // Only mutate shared UI / processing flags when this stream still owns the view.
            // Otherwise a newer send (or new-chat abandon) already took over.
            if (!isStreamActive()) {
                return;
            }
            const totalElapsed = ((Date.now() - searchStartTime) / 1000).toFixed(1);
            setIsProcessing(false);
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
    // ArrowUp on empty input → AMC edit last user message (resend mode)
    // Escape while editing → cancel
    elements.userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            handleSendMessage();
            return;
        }
        if (e.key === 'Escape' && isEditingMessage()) {
            e.preventDefault();
            cancelEdit();
            elements.userInput.value = '';
            resetInputHeight();
            updateSendButtonState();
            return;
        }
        if (
            e.key === 'ArrowUp'
            && !e.shiftKey
            && !e.altKey
            && !e.ctrlKey
            && !e.metaKey
            && !state.isProcessing
            && !elements.userInput.value.trim()
            && lastUserMessage
        ) {
            e.preventDefault();
            beginEditMessage({
                content: lastUserMessage,
                messageIndex: state.lastUserMessageIndex,
                mode: 'resend',
            });
        }
    });

    elements.userInput.addEventListener('input', autoResizeInput);

    // 粘贴大段文本时自动展开
    elements.userInput.addEventListener('paste', () => {
        setTimeout(autoResizeInput, 0);
    });

    // AMC cancel-edit control on the composer banner
    const cancelEditBtn = elements.cancelEditBtn || document.getElementById('cancel-edit-btn');
    if (cancelEditBtn) {
        cancelEditBtn.addEventListener('click', (e) => {
            e.preventDefault();
            cancelEdit();
            // Keep draft text so user can still send as a new message if desired;
            // clear only the edit anchor (matches AMC cancel clearing draft optionally).
            // AMC clears input on cancel — mirror that.
            elements.userInput.value = '';
            resetInputHeight();
            updateSendButtonState();
            elements.userInput.focus({ preventScroll: true });
            showToast('已取消编辑', 'info');
        });
    }
    syncEditChrome();

    // 初始化按钮状态
    updateSendButtonState();

    // Ctrl+Shift+R: regenerate last answer (AMC retry last turn)
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'R') {
            e.preventDefault();
            if (lastUserMessage && !state.isProcessing) {
                regenerateFromPrompt(lastUserMessage, {
                    previousUserIndex: state.lastUserMessageIndex,
                });
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
