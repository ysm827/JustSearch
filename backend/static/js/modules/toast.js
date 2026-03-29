// Toast 防重复缓存
const _activeToasts = new Map();

export function showToast(message, type = 'info', duration = 3000) {
    // 防止短时间内重复显示相同的 toast
    const dedupeKey = `${type}:${message}`;
    if (_activeToasts.has(dedupeKey)) {
        return;
    }

    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    // 根据 type 选图标
    let icon = 'info';
    if (type === 'success') icon = 'check_circle';
    if (type === 'error') icon = 'error';
    if (type === 'warning') icon = 'warning';

    toast.innerHTML = `
        <span class="material-symbols-rounded">${icon}</span>
        <span class="toast-message"></span>
    `;
    toast.querySelector('.toast-message').textContent = message;

    _activeToasts.set(dedupeKey, true);

    container.appendChild(toast);

    // 触发回流以启动动画
    toast.offsetHeight;
    toast.classList.add('show');

    setTimeout(() => {
        toast.classList.remove('show');
        _activeToasts.delete(dedupeKey);
        setTimeout(() => {
            if (toast.parentNode) {
                container.removeChild(toast);
            }
        }, 300);
    }, duration);
}
