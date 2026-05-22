import { buildBrowserWebSocketUrl } from './auth.js';
import { state } from './state.js';

export function setupBrowserModal() {
    const modal = document.getElementById('browser-modal');
    const closeBtn = document.getElementById('browser-close-btn');
    const completeBtn = document.getElementById('browser-complete-btn');
    const img = document.getElementById('browser-viewport');

    if (!modal) return;
    const status = modal.querySelector('.browser-status-overlay');

    let ws = null;

    closeBtn.addEventListener('click', () => {
        modal.classList.remove('active');
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
        modal.classList.add('active');
        status.style.display = 'block';
        status.textContent = '正在连接浏览器...';
        img.style.display = 'none';
        completeBtn.disabled = false;
        completeBtn.textContent = '完成验证，继续执行';

        const wsUrl = buildBrowserWebSocketUrl(window.location, sessionId);

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
            } else if (data.type === 'status' && data.msg === 'Completed') {
                modal.classList.remove('active');
                if (ws) {
                    ws.close();
                    ws = null;
                }
            }
        };

        ws.onclose = () => {
            if (modal.classList.contains('active') && completeBtn.textContent !== '正在提交...') {
                status.style.display = 'block';
                status.textContent = '连接已断开 (会话可能已结束)';
            }
        };

        ws.onerror = (e) => {
            console.error("WS Error", e);
            status.textContent = '连接错误';
        };
    };
}
