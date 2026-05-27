import { state, setSettings } from './state.js?v=2';
import { authFetch } from './auth.js?v=1';
import { applyTheme } from './utils.js';

function encodePathSegment(value) {
    return encodeURIComponent(String(value ?? ''));
}

export async function fetchSettings() {
    try {
        const res = await authFetch('/api/settings');
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
        const res = await authFetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newSettings)
        });
        if (res.ok) {
            const data = await res.json();
            // Update state with server response (contains masked provider api keys)
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
        const res = await authFetch('/api/settings/default');
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load default settings", e);
    }
    return null;
}

export async function checkEnginesAPI() {
    try {
        const res = await authFetch('/api/settings/check-engines', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to check search engines", e);
    }
    return null;
}

export async function fetchHistory() {
    try {
        const res = await authFetch('/api/history');
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load history", e);
    }
    return [];
}

export async function fetchChatGroups() {
    try {
        const res = await authFetch('/api/history/groups');
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load chat groups", e);
    }
    return [];
}

export async function createChatGroupAPI(title = '新分组') {
    try {
        const res = await authFetch('/api/history/groups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to create chat group", e);
    }
    return null;
}

export async function updateChatGroupAPI(groupId, changes) {
    try {
        const res = await authFetch(`/api/history/groups/${encodePathSegment(groupId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes)
        });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to update chat group", e);
    }
    return null;
}

export async function deleteChatGroupAPI(groupId) {
    try {
        const res = await authFetch(`/api/history/groups/${encodePathSegment(groupId)}`, {
            method: 'DELETE'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to delete chat group", e);
        return false;
    }
}

export async function moveChatToGroupAPI(sessionId, groupId) {
    try {
        const res = await authFetch(`/api/history/${encodePathSegment(sessionId)}/group`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_id: groupId })
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to move chat to group", e);
        return false;
    }
}

export async function deleteChatAPI(sessionId) {
    try {
        const res = await authFetch(`/api/history/${encodePathSegment(sessionId)}`, {
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
        const res = await authFetch(`/api/history/${encodePathSegment(sessionId)}`, {
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
        const res = await authFetch('/api/history', {
            method: 'DELETE'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to clear history", e);
        return false;
    }
}

export async function exportHistoryAPI() {
    try {
        const res = await authFetch('/api/history/export/all?format=json');
        if (!res.ok) {
            return false;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        link.href = url;
        link.download = `justsearch-history-${today}.json`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        return true;
    } catch (e) {
        console.error("Failed to export history", e);
        return false;
    }
}

export async function importHistoryAPI(payload) {
    try {
        const res = await authFetch('/api/history/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            return await res.json();
        }
        let detail = '导入失败';
        try {
            const data = await res.json();
            detail = data.detail || detail;
        } catch (e) {
            // Keep generic error.
        }
        return { status: 'error', detail };
    } catch (e) {
        console.error("Failed to import history", e);
        return { status: 'error', detail: '导入请求失败' };
    }
}

export async function deleteMessageAPI(sessionId, messageIndex) {
    try {
        const res = await authFetch('/api/chat/message', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, message_index: messageIndex })
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to delete message", e);
        return false;
    }
}

export async function clearCacheAPI() {
    try {
        const res = await authFetch('/api/clear-cache', {
            method: 'POST'
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to clear cache", e);
        return false;
    }
}

export async function searchHistory(query) {
    try {
        const res = await authFetch(`/api/history/search?q=${encodeURIComponent(query)}`);
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to search history", e);
    }
    return [];
}

export async function fetchChat(sessionId) {
    try {
        const res = await authFetch(`/api/history/${encodePathSegment(sessionId)}`);
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load chat", e);
    }
    return null;
}

export const __apiTestHooks = {
    encodePathSegment,
};

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
        const res = await authFetch('/api/stats/github');
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
    const { onLog, onAnswerChunk, onAnswer, onSources, onStats, onError, onDone, onMeta, signal, model, providerId, liveArtifactsMode } = callbacks;

    const MAX_RETRIES = 2;
    const RETRY_DELAYS = [2000, 5000]; // 渐进式重试延迟
    let doneEmitted = false;

    const emitDone = () => {
        if (doneEmitted) return;
        doneEmitted = true;
        if (onDone) onDone();
    };

    const handleSsePayload = (dataStr) => {
        if (dataStr === '[DONE]') {
            emitDone();
            return true;
        }

        try {
            const event = JSON.parse(dataStr);

            if (event.type === 'meta' && onMeta) {
                onMeta(event);
            }
            else if (event.type === 'log' && onLog) {
                onLog(event.content);
            }
            else if (event.type === 'sources' && onSources) {
                onSources(event.content);
            }
            else if (event.type === 'stats' && onStats) {
                onStats(event.content);
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
        return false;
    };

    const processSseBlock = (block) => {
        const dataLines = String(block || '')
            .split(/\r?\n/)
            .filter(line => line.startsWith('data: '))
            .map(line => line.slice(6));
        if (dataLines.length === 0) return false;
        return handleSsePayload(dataLines.join('\n'));
    };

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        let responseStarted = false;
        try {
            const response = await authFetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: query,
                    session_id: state.currentSessionId,
                    provider_id: providerId || state.settings.default_provider_id,
                    model: model,
                    search_engine: state.settings.search_engine,
                    max_results: state.settings.max_results,
                    max_iterations: state.settings.max_iterations,
                    interactive_search: state.settings.interactive_search,
                    max_concurrent_pages: state.settings.max_concurrent_pages,
                    live_artifacts_mode: Boolean(liveArtifactsMode)
                }),
                signal: signal
            });
            responseStarted = true;

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
                const blocks = buffer.split(/\r?\n\r?\n/);
                buffer = blocks.pop() || '';

                for (const block of blocks) {
                    if (processSseBlock(block)) {
                        return; // 正常结束，无需重试
                    }
                }
            }

            if (buffer.trim() && processSseBlock(buffer)) {
                return;
            }
            emitDone();
            return;

        } catch (e) {
            // AbortError 是用户主动取消，不重试
            if (e.name === 'AbortError') {
                throw e;
            }

            // 已经开始执行的聊天请求不是幂等操作，中途断流不能重发同一请求。
            if (responseStarted) {
                console.error('SSE 连接在响应开始后中断，不重试以避免重复生成:', e);
                throw e;
            }

            // 网络错误等可重试
            if (attempt < MAX_RETRIES) {
                const delay = RETRY_DELAYS[attempt] || 5000;
                console.warn(`SSE 连接断开 (尝试 ${attempt + 1}/${MAX_RETRIES + 1})，${delay / 1000} 秒后重连...`, e);
                if (onLog) {
                    onLog(`网络连接中断，正在重连 (${attempt + 1}/${MAX_RETRIES + 1})...`);
                }
                await new Promise(resolve => setTimeout(resolve, delay));
            } else {
                console.error('SSE 连接重试耗尽:', e);
                throw e;
            }
        }
    }
}
