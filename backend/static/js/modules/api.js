import { state, setSettings } from './state.js';
import { applyTheme } from './utils.js';

function getAuthHeaders() {
    const headers = {};
    if (state.authToken) {
        headers['Authorization'] = `Bearer ${state.authToken}`;
    }
    return headers;
}

export async function fetchSettings() {
    try {
        const res = await fetch('/api/settings', { headers: getAuthHeaders() });
        if (res.ok) {
            const settings = await res.json();
            setSettings(settings);
            applyTheme(settings.theme);
            return settings;
        }
        if (res.status === 401) {
            showAuthPrompt();
        }
    } catch (e) {
        console.error("Failed to load settings", e);
    }
    return null;
}

function showAuthPrompt() {
    const token = prompt('请输入访问令牌 (Authorization Token)：\n令牌在服务端启动时打印到控制台');
    if (token) {
        state.authToken = token.trim();
        localStorage.setItem('auth_token', token.trim());
        // Retry the last request by reloading
        window.location.reload();
    }
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

export async function saveSettingsAPI(newSettings) {
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
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
        const res = await fetch('/api/settings/default', { headers: getAuthHeaders() });
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
        const res = await fetch('/api/history', { headers: getAuthHeaders() });
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
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to delete chat", e);
        return false;
    }
}

export async function clearHistoryAPI() {
    try {
        const res = await fetch('/api/history', {
            method: 'DELETE',
            headers: getAuthHeaders()
        });
        return res.ok;
    } catch (e) {
        console.error("Failed to clear history", e);
        return false;
    }
}

export async function fetchChat(sessionId) {
    try {
        const res = await fetch(`/api/history/${sessionId}`, { headers: getAuthHeaders() });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to load chat", e);
    }
    return null;
}

export async function fetchGitHubStats() {
    try {
        const res = await fetch('/api/stats/github', { headers: getAuthHeaders() });
        if (res.ok) {
            return await res.json();
        }
    } catch (e) {
        console.error("Failed to fetch GitHub stats", e);
    }
    return { stars: 0 };
}

export async function streamChat(query, callbacks) {
    const { onLog, onAnswerChunk, onAnswer, onSources, onError, onDone, onMeta, signal, model } = callbacks;
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
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
                        return;
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
    } catch (e) {
        throw e; // Let caller handle AbortError etc.
    }
}