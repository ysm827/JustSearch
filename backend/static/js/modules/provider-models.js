export function getSupportedModelItems(modelIds) {
    return String(modelIds || '')
        .split(',')
        .map(s => s.trim())
        .filter(model => model && !isUnsupportedGemini25Model(model));
}

export function isUnsupportedGemini25Model(model) {
    return /(^|[^a-z0-9])gemini[\s._-]*2[\s._-]*5($|[^a-z0-9])/i.test(String(model || ''));
}

export function getModelDisplayName(modelValue) {
    return splitModelItem(modelValue).displayName;
}

export function splitModelItem(modelValue) {
    const raw = String(modelValue || '').trim();
    if (!raw) {
        return { modelId: '', displayName: '' };
    }
    const aliasIdx = raw.indexOf('::');
    if (aliasIdx !== -1) {
        const modelId = raw.substring(0, aliasIdx).trim();
        const displayName = raw.substring(aliasIdx + 2).trim();
        if (modelId && displayName) {
            return { modelId, displayName };
        }
        return {
            modelId: raw,
            displayName: raw.includes('/') ? raw.split('/').pop() : raw,
        };
    }
    const colonIdx = raw.indexOf(':');
    if (colonIdx !== -1) {
        const modelId = raw.substring(0, colonIdx).trim();
        const displayName = raw.substring(colonIdx + 1).trim();
        const compactTag = /^[A-Za-z0-9._-]+$/.test(displayName);
        const repeatedCompactName = compactTag
            && modelId
            && displayName
            && modelId.toLowerCase() === displayName.toLowerCase();
        const suffixCompactName = compactTag
            && modelId
            && displayName
            && modelId.toLowerCase().endsWith(displayName.toLowerCase())
            && /[-_.]/.test(modelId);
        if (modelId && displayName && (
            /\s/.test(displayName)
            || !compactTag
            || repeatedCompactName
            || suffixCompactName
        )) {
            return { modelId, displayName };
        }
    }
    return {
        modelId: raw,
        displayName: raw.includes('/') ? raw.split('/').pop() : raw,
    };
}
