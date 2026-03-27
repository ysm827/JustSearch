import { state, setCurrentSessionId, setIsProcessing, setAbortController } from './state.js';
import { createCopyButton } from './utils.js';
import { createDynamicLogContainer, renderWithCitations, scrollToBottom, appendMessage, updateActiveHistoryItem, renderMessages } from './ui.js';
import { showToast } from './toast.js';
import * as API from './api.js';

/**
 * 设置聊天处理器：发送消息、加载/删除对话、输入框自动调整等。
 * @param {Object} elements - UI 元素引用
 * @param {Function} renderHistory - 刷新侧边栏历史的函数
 * @returns {Object} 暴露 loadChat / deleteChat 供外部调用
 */
export function setupChatHandler(elements, renderHistory) {
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
        updateSendButtonState();
        elements.heroSection.style.display = 'none';

        const sendBtnIcon = elements.sendBtn.querySelector('.material-symbols-rounded');
        sendBtnIcon.textContent = 'stop_circle';

        appendMessage('user', text);
        scrollToBottom();

        // Assistant Message Placeholder
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message assistant';

        const { logContainer, logDetails, spinner, statusText } = createDynamicLogContainer();
        msgDiv.appendChild(logContainer);

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
        let hasReceivedChunk = false;

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
                    const tsSpan = document.createElement('span');
                    tsSpan.className = 'log-timestamp';
                    tsSpan.textContent = new Date().toLocaleTimeString();
                    const msgSpan = document.createElement('span');
                    msgSpan.textContent = msg;
                    entry.appendChild(tsSpan);
                    entry.appendChild(msgSpan);
                    logDetails.appendChild(entry);
                    logDetails.scrollTop = logDetails.scrollHeight;
                },
                onSources: (sources) => {
                    currentSources = sources;
                },
                onAnswerChunk: (chunk) => {
                    if (!hasReceivedChunk) {
                        hasReceivedChunk = true;
                        contentWrapper.innerHTML = '';
                    }
                    currentAnswerBuffer += chunk;
                    contentWrapper.innerHTML = renderWithCitations(currentAnswerBuffer, currentSources);
                    scrollToBottom();
                },
                onAnswer: (finalAnswer, sessionId) => {
                    currentAnswerBuffer = finalAnswer;
                    contentWrapper.innerHTML = renderWithCitations(finalAnswer, currentSources);
                    setCurrentSessionId(sessionId);
                    API.fetchHistory().then(h => renderHistory(h, state.currentSessionId, { onSelect: loadChat, onDelete: deleteChat }));
                },
                onError: (err) => {
                    contentWrapper.innerHTML = `<div style="color:red">Error: ${err}</div>`;
                },
                onDone: () => {}
            });
        } catch (e) {
            if (!hasReceivedChunk) {
                contentWrapper.innerHTML = '';
            }
            if (e.name === 'AbortError') {
                contentWrapper.innerHTML += `<div style="color:orange; margin-top: 10px;">[已由用户停止]</div>`;
            } else {
                console.error(e);
                contentWrapper.innerHTML += `<div style="color:red">网络错误: ${e.message}</div>`;
            }
        } finally {
            setIsProcessing(false);
            setAbortController(null);
            sendBtnIcon.textContent = 'send';
            updateSendButtonState();
            spinner.classList.remove('rotating');
            spinner.textContent = 'check_circle';
            statusText.textContent = '已完成';
        }
    }

    function updateSendButtonState() {
        const hasText = elements.userInput.value.trim().length > 0;
        elements.sendBtn.disabled = !hasText && !state.isProcessing;
        elements.sendBtn.style.opacity = (hasText || state.isProcessing) ? '1' : '0.4';
        elements.sendBtn.style.cursor = (hasText || state.isProcessing) ? 'pointer' : 'default';
    }

    // 绑定事件
    elements.sendBtn.addEventListener('click', handleSendMessage);
    elements.userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    });

    elements.userInput.addEventListener('input', () => {
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
    });

    // 初始化按钮状态
    updateSendButtonState();

    return { loadChat, deleteChat };
}
