const STORAGE_KEY = 'justsearch_auth_token';

let authState = {
    token: '',
    authEnabled: false,
    clientIsLoopback: false,
};

export function normalizeSettings(settings) {
    return settings && typeof settings === 'object' ? settings : {};
}

export function resolveClientAuth({ bootstrapToken = '', storedToken = '', url }) {
    const parsed = new URL(url, 'http://localhost');
    const queryToken = (parsed.searchParams.get('token') || '').trim();

    parsed.searchParams.delete('token');
    const cleanedQuery = parsed.searchParams.toString();
    const cleanedPath = `${parsed.pathname}${cleanedQuery ? `?${cleanedQuery}` : ''}${parsed.hash || ''}`;

    return {
        token: queryToken || bootstrapToken || storedToken || '',
        shouldPersist: Boolean(queryToken),
        cleanedPath,
    };
}

export function buildAuthHeaders(token, headers = {}) {
    const merged = {};
    for (const [key, value] of new Headers(headers || {}).entries()) {
        merged[key] = value;
    }
    if (token) {
        delete merged.authorization;
        merged.Authorization = `Bearer ${token}`;
    }
    return merged;
}

export function buildBrowserWebSocketUrl(locationLike, sessionId, token = getAuthToken()) {
    const protocol = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = new URL(`${protocol}//${locationLike.host}/ws/browser/${sessionId}`);
    if (token) {
        url.searchParams.set('token', token);
    }
    return url.toString();
}

export function buildAuthenticatedUrl(path, token = getAuthToken()) {
    if (!token) {
        return path;
    }
    const baseOrigin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
    const url = new URL(path, baseOrigin);
    url.searchParams.set('token', token);
    return `${url.pathname}${url.search}${url.hash}`;
}

export function initializeAuth(win = window) {
    const bootstrap = win.__JUSTSEARCH_BOOTSTRAP__ || {};
    const storedToken = (() => {
        try {
            return win.localStorage.getItem(STORAGE_KEY) || '';
        } catch (e) {
            return '';
        }
    })();

    const resolved = resolveClientAuth({
        bootstrapToken: bootstrap.authToken || '',
        storedToken,
        url: win.location.href,
    });

    if (resolved.token) {
        try {
            win.localStorage.setItem(STORAGE_KEY, resolved.token);
        } catch (e) {
            // Ignore storage errors and continue using in-memory token.
        }
    }

    const currentPath = `${win.location.pathname}${win.location.search}${win.location.hash}`;
    if (resolved.cleanedPath !== currentPath && win.history?.replaceState) {
        win.history.replaceState(win.history.state, '', resolved.cleanedPath);
    }

    authState = {
        token: resolved.token,
        authEnabled: Boolean(bootstrap.authEnabled),
        clientIsLoopback: Boolean(bootstrap.clientIsLoopback),
    };

    return authState;
}

export function getAuthToken() {
    return authState.token;
}

export function authFetch(input, init = {}) {
    const headers = buildAuthHeaders(getAuthToken(), init.headers);
    return fetch(input, { ...init, headers });
}
