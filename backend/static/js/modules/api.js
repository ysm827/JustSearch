import { state, setSettings } from './state.js';
import { applyTheme } from './utils.js';

export async function fetchSettings() {
    try {
        const res = await fetch('/api/settings');
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
        const res = await fetch('/api/settings', {
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
        const res = await fetch('/api/settings/default');
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
        const res = await fetch('/api/history');
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
        const res = await fetch(`/api/history/${sessionId}`, {
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
        const res = await fetch(`/api/history/${sessionId}`, {
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
        const res = await fetch('/api/history', {
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
        const res = await fetch('/api/clear-cache', {
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
        const res = await fetch(`/api/history/${sessionId}`);
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
        const res = await fetch('/api/stats/github');
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
            const response = await fetch('/api/chat', {
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
