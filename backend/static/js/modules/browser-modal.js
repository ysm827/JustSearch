import { buildBrowserWebSocketUrl } from './auth.js?v=1';
import { state } from './state.js';

export function setupBrowserModal() {
    const modal = document.getElementById('browser-modal');
    const closeBtn = document.getElementById('browser-close-btn');
    const completeBtn = document.getElementById('browser-complete-btn');
    const typeInput = document.getElementById('browser-type-input');
    const typeSendBtn = document.getElementById('browser-type-send-btn');
    const img = document.getElementById('browser-viewport');

    if (!modal) return;
    const status = modal.querySelector('.browser-status-overlay');

    let ws = null;

    function updateTypeSendState() {
        if (!typeSendBtn || !typeInput) return;
        typeSendBtn.disabled = !ws || ws.readyState !== WebSocket.OPEN || typeInput.value.length === 0;
    }

    function sendTypedText() {
        if (!ws || ws.readyState !== WebSocket.OPEN || !typeInput) return;
        const text = typeInput.value;
        if (!text) return;

        ws.send(JSON.stringify({ action: 'type', text }));
        typeInput.value = '';
        updateTypeSendState();
        typeInput.focus();
    }

    closeBtn.addEventListener('click', () => {
        modal.classList.remove('active');
        if (ws) {
            ws.close();
            ws = null;
        }
        updateTypeSendState();
    });

    completeBtn.addEventListener('click', () => {
        if (ws) {
            ws.send(JSON.stringify({ action: 'complete' }));
            completeBtn.disabled = true;
            completeBtn.textContent = '正在提交...';
        }
    });

    if (typeSendBtn) {
        typeSendBtn.addEventListener('click', sendTypedText);
    }

    if (typeInput) {
        typeInput.addEventListener('input', updateTypeSendState);
        typeInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendTypedText();
            }
        });
    }

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
        modal.classList.add('active');
        status.style.display = 'block';
        status.textContent = '正在连接浏览器...';
        img.style.display = 'none';
        completeBtn.disabled = false;
        completeBtn.textContent = '完成验证，继续执行';
        if (typeInput) {
            typeInput.value = '';
        }
        updateTypeSendState();

        const wsUrl = buildBrowserWebSocketUrl(window.location, sessionId);

        if (ws) ws.close();
        const activeWs = new WebSocket(wsUrl);
        ws = activeWs;

        activeWs.onopen = () => {
            if (ws !== activeWs) return;
            status.textContent = '已连接。等待画面...';
            updateTypeSendState();
            img.focus();
        };

        activeWs.onmessage = (event) => {
            if (ws !== activeWs) return;
            const data = JSON.parse(event.data);
            if (data.type === 'frame') {
                status.style.display = 'none';
                img.style.display = 'block';
                img.src = `data:image/jpeg;base64,${data.image}`;
            } else if (data.type === 'status' && data.msg === 'Completed') {
                modal.classList.remove('active');
                if (ws === activeWs) {
                    activeWs.close();
                    ws = null;
                }
                updateTypeSendState();
            }
        };

        activeWs.onclose = () => {
            if (ws !== activeWs) return;
            ws = null;
            updateTypeSendState();
            if (modal.classList.contains('active') && completeBtn.textContent !== '正在提交...') {
                status.style.display = 'block';
                status.textContent = '连接已断开 (会话可能已结束)';
            }
        };

        activeWs.onerror = (e) => {
            if (ws !== activeWs) return;
            console.error("WS Error", e);
            status.textContent = '连接错误';
            updateTypeSendState();
        };
    };
}
