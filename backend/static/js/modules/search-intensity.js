/**
 * 搜索强度档位：映射 max_results（广度）与 max_iterations（深度）。
 */

export const INTENSITY_PRESETS = Object.freeze([
    {
        id: 'quick',
        label: '快速',
        max_results: 4,
        max_iterations: 1,
        hint: '约 4 源 · 1 轮',
        description: '事实问答，尽量少轮搜索',
    },
    {
        id: 'balanced',
        label: '均衡',
        max_results: 8,
        max_iterations: 3,
        hint: '约 8 源 · 最多 3 轮',
        description: '日常问题默认强度',
    },
    {
        id: 'deep',
        label: '深入',
        max_results: 12,
        max_iterations: 5,
        hint: '约 12 源 · 最多 5 轮',
        description: '对比调研，多源补充',
    },
    {
        id: 'research',
        label: '研究',
        max_results: 20,
        max_iterations: 8,
        hint: '约 20 源 · 最多 8 轮',
        description: '长报告与多轮穷尽搜索',
    },
]);

const PRESET_BY_ID = Object.freeze(
    Object.fromEntries(INTENSITY_PRESETS.map((preset) => [preset.id, preset]))
);

export function clampMaxResults(value, fallback = 8) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(1, Math.min(50, Math.trunc(parsed)));
}

export function clampMaxIterations(value, fallback = 3) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(1, Math.min(10, Math.trunc(parsed)));
}

export function getIntensityPreset(id) {
    return PRESET_BY_ID[id] || null;
}

export function matchIntensityPreset(maxResults, maxIterations) {
    const results = clampMaxResults(maxResults, NaN);
    const iterations = clampMaxIterations(maxIterations, NaN);
    if (!Number.isFinite(results) || !Number.isFinite(iterations)) {
        return null;
    }
    return INTENSITY_PRESETS.find(
        (preset) => preset.max_results === results && preset.max_iterations === iterations
    ) || null;
}

export function resolveIntensityFromSettings(settings = {}) {
    const maxResults = clampMaxResults(settings.max_results, 8);
    const maxIterations = clampMaxIterations(settings.max_iterations, 3);
    const preset = matchIntensityPreset(maxResults, maxIterations);
    if (preset) {
        return {
            id: preset.id,
            label: preset.label,
            max_results: preset.max_results,
            max_iterations: preset.max_iterations,
            hint: preset.hint,
            isCustom: false,
        };
    }
    return {
        id: 'custom',
        label: '自定义',
        max_results: maxResults,
        max_iterations: maxIterations,
        hint: `约 ${maxResults} 源 · 最多 ${maxIterations} 轮`,
        isCustom: true,
    };
}

export function applyIntensityPresetToSettings(settings, presetId) {
    const preset = getIntensityPreset(presetId);
    if (!preset || !settings || typeof settings !== 'object') {
        return settings;
    }
    return {
        ...settings,
        max_results: preset.max_results,
        max_iterations: preset.max_iterations,
    };
}

export function syncIntensityControls(root = document) {
    const bar = root.getElementById?.('search-intensity-bar') || root.querySelector?.('#search-intensity-bar');
    if (!bar) return null;

    const chips = Array.from(bar.querySelectorAll('.intensity-chip[data-intensity]'));
    const hintEl = root.getElementById?.('search-intensity-hint') || bar.querySelector('#search-intensity-hint');
    const customChip = bar.querySelector('.intensity-chip[data-intensity="custom"]');

    // settings may be injected via data attributes for tests; prefer live state values from caller.
    return { bar, chips, hintEl, customChip };
}

/**
 * 根据当前 max_results / max_iterations 刷新 chip 选中态与提示文案。
 */
export function updateIntensityUI({
    maxResults,
    maxIterations,
    disabled = false,
    root = document,
} = {}) {
    const resolved = resolveIntensityFromSettings({
        max_results: maxResults,
        max_iterations: maxIterations,
    });
    const bar = root.getElementById?.('search-intensity-bar') || root.querySelector?.('#search-intensity-bar');
    if (!bar) return resolved;

    const hintEl = root.getElementById?.('search-intensity-hint') || bar.querySelector('#search-intensity-hint');
    const chips = Array.from(bar.querySelectorAll('.intensity-chip[data-intensity]'));
    const customChip = bar.querySelector('.intensity-chip[data-intensity="custom"]');

    if (customChip) {
        customChip.hidden = !resolved.isCustom;
        customChip.setAttribute('aria-hidden', resolved.isCustom ? 'false' : 'true');
    }

    chips.forEach((chip) => {
        const id = chip.getAttribute('data-intensity');
        const isActive = id === resolved.id;
        chip.classList.toggle('active', isActive);
        chip.setAttribute('aria-checked', isActive ? 'true' : 'false');
        chip.disabled = Boolean(disabled);
        chip.tabIndex = isActive ? 0 : -1;
    });

    if (hintEl) {
        hintEl.textContent = resolved.hint;
    }

    bar.classList.toggle('is-disabled', Boolean(disabled));
    bar.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    // 使用 data-active-intensity，避免与 chip 的 data-intensity 在 querySelector 时冲突
    bar.dataset.activeIntensity = resolved.id;

    return resolved;
}
