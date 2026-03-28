import { state, setSettings } from './state.js';
import { applyTheme } from './utils.js';

function getAuthHeaders() {
    const headers = {};
    if (state.authToken) {
        headers['Authorization'] = `Bearer ${state.authToken}`;
    }
    return headers;
}

function showAuthPrompt() {
    // 如果已经存在认证弹窗则不重复创建
    if (document.querySelector('.auth-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.className = 'auth-modal-overlay';
    overlay.innerHTML = `
        <div class="auth-modal">
            <h3>🔑 身份验证</h3>
            <p>请输入访问令牌。令牌在服务端启动时打印到控制台。</p>
            <input type="text" id="auth-token-input" placeholder="输入令牌..." autofocus>
            <button class="auth-modal-submit" id="auth-submit-btn">确认</button>
        </div>
    `;
    document.body.appendChild(overlay);

    const input = document.getElementById('auth-token-input');
    const submitBtn = document.getElementById('auth-submit-btn');

    function submit() {
        const token = input.value.trim();
        if (token) {
            state.authToken = token;
            localStorage.setItem('auth_token', token);
            overlay.remove();
            window.location.reload();
        }
    }

    submitBtn.addEventListener('click', submit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submit();
    });
    input.focus();
}

// Restore token from localStorage, or read from meta tag injected by server
const savedToken = localStorage.getItem('auth_token');
const metaToken = document.querySelector('meta[name="auth-token"]');
if (savedToken) {
    state.authToken = savedToken;
} else if (metaToken) {
    state.authToken = metaToken.getAttribute('content');
    localStorage.setItem('auth_token', state.authToken);
}

/**
 * Wrapper for fetch that handles 401 responses globally.
 * On first 401, prompts for new token and reloads.
 */
async function authedFetch(url, options = {}) {
    const headers = getAuthHeaders();
    if (options.headers) {
        Object.assign(headers, options.headers);
    }
    options.headers = headers;

    const res = await fetch(url, options);
    if (res.status === 401) {
        // Token may have changed (e.g. server restart)
        showAuthPrompt();
        throw new Error('Unauthorized');
    }
    return res;
}

export async function fetchSettings() {
    try {
        const res = await authedFetch('/api/settings');
        if (res.ok) {
            const settings = await res.json();
            setSettings(settings);
            applyTheme(settings.theme);
            return settings;
        }
    } catch (e) {
        console.error("Failed to load settings", e);
    }
    return null;
}

export async function saveSettingsAPI(newSettings) {
    try {
        const res = await authedFetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newSettings)
        });
        if (res.ok) {
            const data = await res.json();
            // Update state with server response (contains masked api_key)
            if (data.settings) {
                setSettings(data.settings);
                applyTheme(data.settings.theme);
            } else {
                setSettings(newSettings);
                applyTheme(newSettings.theme);
            }
            return true;
        }
    } catch (e) {
        console.error(e);
    }
    return false;
}

export async function restoreDefaultSettingsAPI() {
    try {
        const res = await authedFetch('/api/settings/default');
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load default settings", e);
    }
    return null;
}

export async function fetchHistory() {
    try {
        const res = await authedFetch('/api/history');
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load history", e);
    }
    return [];
}

export async function deleteChatAPI(sessionId) {
    try {
        const res = await authedFetch(`/api/history/${sessionId}`, {
            method: 'DELETE'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to delete chat", e);
        return false;
    }
}

export async function renameChatAPI(sessionId, newTitle) {
    try {
        const res = await authedFetch(`/api/history/${sessionId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle })
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to rename chat", e);
        return false;
    }
}

export async function clearHistoryAPI() {
    try {
        const res = await authedFetch('/api/history', {
            method: 'DELETE'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to clear history", e);
        return false;
    }
}

export async function clearCacheAPI() {
    try {
        const res = await authedFetch('/api/clear-cache', {
            method: 'POST'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to clear cache", e);
        return false;
    }
}

export async function fetchChat(sessionId) {
    try {
        const res = await authedFetch(`/api/history/${sessionId}`);
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load chat", e);
    }
    return null;
}

// Cache GitHub stars in memory to avoid repeated requests
let _githubStarsCache = null;
let _githubStarsCacheTime = 0;
const GITHUB_STARS_CACHE_TTL = 10 * 60 * 1000; // 10 minutes

export async function fetchGitHubStats() {
    // Use in-memory cache to avoid fetching on every settings modal open
    const now = Date.now();
    if (_githubStarsCache && (now - _githubStarsCacheTime) < GITHUB_STARS_CACHE_TTL) {
        return _githubStarsCache;
    }

    try {
        const res = await authedFetch('/api/stats/github');
        if (res.ok) {
            const data = await res.json();
            _githubStarsCache = data;
            _githubStarsCacheTime = now;
            return data;
        }
    } catch (e) {
        console.error("Failed to fetch GitHub stats", e);
    }
    return _githubStarsCache || { stars: 0 };
}

export async function streamChat(query, callbacks) {
    const { onLog, onAnswerChunk, onAnswer, onSources, onError, onDone, onMeta, signal, model } = callbacks;

    const MAX_RETRIES = 2;
    const RETRY_DELAY = 3000; // 3 秒

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        try {
            const response = await authedFetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: query,
                    session_id: state.currentSessionId,
                    base_url: state.settings.base_url,
                    model: model || state.settings.model_id,
                    search_engine: state.settings.search_engine,
                    max_results: state.settings.max_results,
                    max_iterations: state.settings.max_iterations,
                    interactive_search: state.settings.interactive_search
                }),
                signal: signal
            });

            // Handle non-200 responses (e.g. 400 missing API key)
            if (!response.ok) {
                let errMsg = `请求失败 (${response.status})`;
                try {
                    const errData = await response.json();
                    if (errData.detail) errMsg = errData.detail;
                } catch (e) {}
                if (onError) onError(errMsg);
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const dataStr = line.slice(6);
                        if (dataStr === '[DONE]') {
                            if (onDone) onDone();
                            return; // 正常结束，无需重试
                        }

                        try {
                            const event = JSON.parse(dataStr);

                            if (event.type === 'meta' && onMeta) {
                                onMeta(event.session_id);
                            }
                            else if (event.type === 'log' && onLog) {
                                onLog(event.content);
                            }
                            else if (event.type === 'sources' && onSources) {
                                onSources(event.content);
                            }
                            else if (event.type === 'answer_chunk' && onAnswerChunk) {
                                onAnswerChunk(event.content);
                            }
                            else if (event.type === 'answer' && onAnswer) {
                                onAnswer(event.content, event.session_id);
                            }
                            else if (event.type === 'error' && onError) {
                                onError(event.content);
                            }
                        } catch (e) {
                            console.error('Error parsing SSE event', e);
                        }
                    }
                }
            }

            // 流正常结束
            return;

        } catch (e) {
            // AbortError 是用户主动取消，不重试
            if (e.name === 'AbortError') {
                throw e;
            }

            // 网络错误等可重试
            if (attempt < MAX_RETRIES) {
                console.warn(`SSE 连接断开 (尝试 ${attempt + 1}/${MAX_RETRIES + 1})，${RETRY_DELAY / 1000} 秒后重连...`, e);
                if (onLog) {
                    onLog(`网络连接中断，正在重连 (${attempt + 1}/${MAX_RETRIES + 1})...`);
                }
                await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
            } else {
                console.error('SSE 连接重试耗尽:', e);
                throw e;
            }
        }
    }
}
