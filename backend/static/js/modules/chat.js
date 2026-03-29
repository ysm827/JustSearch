import { state, setCurrentSessionId, setIsProcessing, setAbortController } from './state.js';
import { createCopyButton } from './utils.js';
import { createDynamicLogContainer, renderWithCitations, scrollToBottom, appendMessage, updateActiveHistoryItem, renderMessages, showConfirm } from './ui.js';
import { showToast } from './toast.js';
import * as API from './api.js';

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

    async function loadChat(sessionId) {
        setCurrentSessionId(sessionId);
        updateActiveHistoryItem(sessionId);
        // 更新浏览器地址栏
        if (window.location.pathname !== `/c/${sessionId}`) {
            history.pushState({ sessionId }, '', `/c/${sessionId}`);
        }
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

        const selectedModel = document.getElementById('model-select').value;

        elements.userInput.value = '';
        resetInputHeight();
        setIsProcessing(true);
        updateSendButtonState();
        elements.heroSection.style.display = 'none';

        const sendBtnIcon = elements.sendBtn.querySelector('.material-symbols-rounded');
        sendBtnIcon.textContent = 'stop_circle';
        elements.sendBtn.classList.remove('inactive', 'active');
        elements.sendBtn.classList.add('processing');

        appendMessage('user', text);
        scrollToBottom();

        // Assistant Message Placeholder
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message assistant';

        // Progress bar
        const progressBar = document.createElement('div');
        progressBar.className = 'search-progress-bar';
        const progressFill = document.createElement('div');
        progressFill.className = 'progress-fill';
        progressBar.appendChild(progressFill);
        msgDiv.appendChild(progressBar);

        const { logContainer, logDetails, spinner, statusText, expandIcon } = createDynamicLogContainer();
        const seenLogs = new Set(); // 去重
        msgDiv.appendChild(logContainer);

        const answerDiv = document.createElement('div');
        answerDiv.className = 'message-content markdown-body';
        const contentWrapper = document.createElement('div');
        contentWrapper.innerHTML = '<span class="blinking-cursor"></span>';
        answerDiv.appendChild(contentWrapper);
        msgDiv.appendChild(answerDiv);
        elements.chatContainer.appendChild(msgDiv);
        scrollToBottom();

        const controller = new AbortController();
        setAbortController(controller);

        let currentAnswerBuffer = '';
        const copyBtn = createCopyButton(() => currentAnswerBuffer);
        answerDiv.appendChild(copyBtn);

        // 重新生成按钮
        const regenBtn = document.createElement('button');
        regenBtn.className = 'regenerate-btn';
        regenBtn.title = '重新生成';
        regenBtn.innerHTML = '<span class="material-symbols-rounded">refresh</span>';
        regenBtn.onclick = async (e) => {
            e.stopPropagation();
            // 移除当前助手消息
            msgDiv.remove();
            // 重新发送
            await handleSendMessage(lastUserMessage);
        };
        answerDiv.appendChild(regenBtn);

        let currentSources = [];
        let hasReceivedChunk = false;
        let logCount = 0;
        let searchStats = null;
        let searchStartTime = Date.now();

        // 重置滚动跟踪（新一轮对话）
        userScrolled = false;

        // 实时耗时更新器
        const elapsedTimer = setInterval(() => {
            const elapsed = ((Date.now() - searchStartTime) / 1000).toFixed(1);
            if (statusText.textContent.includes('正在')) {
                statusText.textContent = statusText.textContent.replace(/ \([\d.]+s\)$/, '') + ` (${elapsed}s)`;
            }
        }, 500);

        // Progress tracker
        function updateProgress() {
            logCount++;
            const pct = Math.min(90, 10 + logCount * 6);
            progressFill.style.width = pct + '%';
        }

        try {
            await API.streamChat(text, {
                model: selectedModel,
                signal: controller.signal,
                onMeta: (meta) => {
                    const sessionId = typeof meta === 'string' ? meta : meta.session_id;
                    if (sessionId) {
                        setCurrentSessionId(sessionId);
                        if (window.location.pathname !== `/c/${sessionId}`) {
                            history.replaceState({ sessionId }, '', `/c/${sessionId}`);
                        }
                    }
                },
                onLog: (msg) => {
                    if (msg.includes('ACTION_REQUIRED: CAPTCHA_DETECTED')) {
                        if (state.openBrowserModal) {
                            state.openBrowserModal(state.currentSessionId);
                        }
                        msg = "需要人工验证。请在弹出的窗口中解决验证码。";
                    }
                    // Detect engine fallback notification
                    if (msg.includes('自动切换到')) {
                        const match = msg.match(/切换到\s*(\S+)/);
                        if (match) showToast(`搜索引擎已切换到 ${match[1]}`, 'warning');
                    }
                    statusText.textContent = msg;
                    updateProgress();

                    const entry = document.createElement('div');
                    if (/search|搜索|query/i.test(msg)) {
                        entry.className = 'log-entry log-search';
                    } else if (/crawl|爬取|fetch|reading/i.test(msg)) {
                        entry.className = 'log-entry log-crawl';
                    } else if (/analyz|分析|assess|评估/i.test(msg)) {
                        entry.className = 'log-entry log-analysis';
                    } else if (/error|失败|fail/i.test(msg)) {
                        entry.className = 'log-entry log-error';
                    } else {
                        entry.className = 'log-entry';
                    }
                    const tsSpan = document.createElement('span');
                    tsSpan.className = 'log-timestamp';
                    tsSpan.textContent = new Date().toLocaleTimeString();
                    const msgSpan = document.createElement('span');
                    msgSpan.textContent = msg;

                    // 去重检查
                    const logKey = msg.trim().substring(0, 80);
                    if (seenLogs.has(logKey)) return;
                    seenLogs.add(logKey);

                    entry.appendChild(tsSpan);
                    entry.appendChild(msgSpan);
                    logDetails.appendChild(entry);
                    logDetails.scrollTop = logDetails.scrollHeight;
                },
                onSources: (sources) => {
                    currentSources = sources;
                },
                onStats: (stats) => {
                    searchStats = stats;
                },
                onAnswerChunk: (chunk) => {
                    if (!hasReceivedChunk) {
                        hasReceivedChunk = true;
                        contentWrapper.innerHTML = '';
                        progressFill.style.width = '95%';
                    }
                    currentAnswerBuffer += chunk;
                    contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, currentSources);
                    if (!userScrolled) scrollToBottom();
                },
                onAnswer: (finalAnswer, sessionId) => {
                    currentAnswerBuffer = finalAnswer;
                    contentWrapper.innerHTML = renderWithCitations(finalAnswer, currentSources);
                    setCurrentSessionId(sessionId);
                    API.fetchHistory().then(h => renderHistory(h, state.currentSessionId, { onSelect: loadChat, onDelete: deleteChat }));
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
                            errMsg = '请先在设置中配置 API 密钥。点击左上角 ⚙️ 设置按钮，填入 API Key 后保存。';
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
            sendBtnIcon.textContent = 'send';
            elements.sendBtn.classList.remove('processing');
            updateSendButtonState();
            spinner.classList.remove('rotating');
            spinner.textContent = 'check_circle';
            spinner.classList.add('completed');
            if (searchStats && searchStats.sites_searched > 0) {
                let statsText = `已完成 · 搜索 ${searchStats.sites_searched} 个结果`;
                if (searchStats.sites_crawled > 0) {
                    statsText += ` · 深度阅读 ${searchStats.sites_crawled} 个页面`;
                }
                statsText += ` · ${totalElapsed}s`;
                if (searchStats.prompt_tokens || searchStats.completion_tokens) {
                    const totalTokens = (searchStats.prompt_tokens || 0) + (searchStats.completion_tokens || 0);
                    if (totalTokens > 0) {
                        statsText += ` · ${totalTokens.toLocaleString()} tokens`;
                    }
                }
                statusText.textContent = statsText;
            } else {
                statusText.textContent = `已完成 · ${totalElapsed}s`;
            }
            // 搜索完成，自动折叠过程日志
            logDetails.classList.remove('open');
            if (expandIcon) expandIcon.classList.remove('expanded');
            // 完成进度条
            progressBar.classList.add('done');
            setTimeout(() => { progressBar.style.display = 'none'; }, 1500);
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

        // Update character count
        const charCount = document.getElementById('char-count');
        if (charCount) {
            const len = elements.userInput.value.length;
            if (len > 0) {
                charCount.style.display = 'block';
                charCount.textContent = `${len}`;
                charCount.style.color = len > 2000 ? 'var(--error)' : 'var(--text-muted)';
            } else {
                charCount.style.display = 'none';
            }
        }
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
            const shortName = modelSelect.value.includes('/') ? modelSelect.value.split('/').pop() : modelSelect.value;
            showToast(`已切换至 ${shortName}`, 'info');
        });
    }

    return { loadChat, deleteChat };
}
