/**
 * JustSearch — Custom Model Dropdown Selector Module
 */

export function initCustomModelSelect() {
    const container = document.getElementById('custom-model-select-container');
    const trigger = document.getElementById('model-select-trigger');
    const menu = document.getElementById('model-dropdown-menu');
    const nativeSelect = document.getElementById('model-select');

    if (!container || !trigger || !menu || !nativeSelect) return;

    let highlightedIndex = -1;

    function openDropdown() {
        container.classList.add('open');
        menu.classList.add('active');
        trigger.setAttribute('aria-expanded', 'true');
        // Pre-highlight current selection
        const items = menu.querySelectorAll('.model-dropdown-item');
        const selectedIdx = Array.from(items).findIndex(item => item.classList.contains('selected'));
        if (selectedIdx !== -1) {
            highlightItem(selectedIdx);
        } else {
            highlightItem(0);
        }
    }

    function closeDropdown() {
        container.classList.remove('open');
        menu.classList.remove('active');
        trigger.setAttribute('aria-expanded', 'false');
        removeHighlight();
        highlightedIndex = -1;
    }

    function toggleDropdown() {
        const isOpen = container.classList.contains('open');
        if (isOpen) {
            closeDropdown();
        } else {
            openDropdown();
        }
    }

    function highlightItem(index) {
        const items = menu.querySelectorAll('.model-dropdown-item');
        if (items.length === 0) return;

        removeHighlight();

        // Wrap around index
        if (index < 0) index = items.length - 1;
        if (index >= items.length) index = 0;

        highlightedIndex = index;
        const targetItem = items[highlightedIndex];
        targetItem.classList.add('highlighted');
        targetItem.setAttribute('aria-selected', 'true');
        targetItem.scrollIntoView({ block: 'nearest' });
    }

    function removeHighlight() {
        const items = menu.querySelectorAll('.model-dropdown-item');
        items.forEach(item => {
            item.classList.remove('highlighted');
            item.setAttribute('aria-selected', 'false');
        });
    }

    function selectItem(index) {
        const items = menu.querySelectorAll('.model-dropdown-item');
        if (index >= 0 && index < items.length) {
            const targetItem = items[index];
            const val = targetItem.dataset.value;
            nativeSelect.value = val;
            nativeSelect.dispatchEvent(new Event('change'));
            syncCustomModelSelect();
            closeDropdown();
            trigger.focus();
        }
    }

    // Trigger Click
    trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDropdown();
    });

    // Keyboard Events on Container
    container.addEventListener('keydown', (e) => {
        const isOpen = container.classList.contains('open');

        switch (e.key) {
            case 'Enter':
            case ' ':
                e.preventDefault();
                if (!isOpen) {
                    openDropdown();
                } else {
                    if (highlightedIndex !== -1) {
                        selectItem(highlightedIndex);
                    } else {
                        closeDropdown();
                    }
                }
                break;
            case 'ArrowDown':
                e.preventDefault();
                if (!isOpen) {
                    openDropdown();
                } else {
                    highlightItem(highlightedIndex + 1);
                }
                break;
            case 'ArrowUp':
                e.preventDefault();
                if (!isOpen) {
                    openDropdown();
                } else {
                    highlightItem(highlightedIndex - 1);
                }
                break;
            case 'Escape':
                e.preventDefault();
                if (isOpen) {
                    closeDropdown();
                    trigger.focus();
                }
                break;
            case 'Tab':
                if (isOpen) {
                    closeDropdown();
                }
                break;
        }
    });

    // Close when clicking outside
    document.addEventListener('click', (e) => {
        if (!container.contains(e.target)) {
            closeDropdown();
        }
    });
}

export function syncCustomModelSelect() {
    const triggerText = document.getElementById('model-select-current-text');
    const menu = document.getElementById('model-dropdown-menu');
    const nativeSelect = document.getElementById('model-select');

    if (!triggerText || !menu || !nativeSelect) return;

    menu.innerHTML = '';
    const options = nativeSelect.options;

    if (options.length === 0) {
        triggerText.textContent = 'Default';
        return;
    }

    Array.from(options).forEach((opt) => {
        const item = document.createElement('div');
        item.className = 'model-dropdown-item';
        item.dataset.value = opt.value;
        item.setAttribute('role', 'option');
        item.setAttribute('tabindex', '-1');

        // Create Left Container
        const leftDiv = document.createElement('div');
        leftDiv.className = 'model-item-left';

        // Icon Wrapper
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'model-item-icon-wrapper';
        iconWrapper.innerHTML = getModelIconSvg(opt.value);
        leftDiv.appendChild(iconWrapper);

        // Details Block
        const detailsDiv = document.createElement('div');
        detailsDiv.className = 'model-item-details';

        const nameDiv = document.createElement('div');
        nameDiv.className = 'model-item-name';
        nameDiv.textContent = opt.text;
        nameDiv.title = opt.title || opt.value;
        detailsDiv.appendChild(nameDiv);

        const idDiv = document.createElement('div');
        idDiv.className = 'model-item-id';
        idDiv.textContent = opt.value;
        detailsDiv.appendChild(idDiv);

        leftDiv.appendChild(detailsDiv);
        item.appendChild(leftDiv);

        // Check Icon Wrapper (Right Block)
        const rightDiv = document.createElement('div');
        rightDiv.className = 'model-item-right';
        rightDiv.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="check-icon">
            <polyline points="20 6 9 17 4 12"/>
        </svg>
        `;
        item.appendChild(rightDiv);

        if (opt.value === nativeSelect.value) {
            item.classList.add('selected');
            item.setAttribute('aria-selected', 'true');
            triggerText.textContent = opt.text;
            triggerText.title = opt.title || opt.value;
        } else {
            item.setAttribute('aria-selected', 'false');
        }

        // Click handler
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            nativeSelect.value = opt.value;
            nativeSelect.dispatchEvent(new Event('change'));
            syncCustomModelSelect();
            // Close dropdown
            const container = document.getElementById('custom-model-select-container');
            if (container && triggerText) {
                container.classList.remove('open');
                menu.classList.remove('active');
                const triggerBtn = document.getElementById('model-select-trigger');
                if (triggerBtn) {
                    triggerBtn.setAttribute('aria-expanded', 'false');
                    triggerBtn.focus();
                }
            }
        });

        // Hover handler
        item.addEventListener('mouseenter', () => {
            const items = menu.querySelectorAll('.model-dropdown-item');
            items.forEach((it) => {
                if (it === item) {
                    it.classList.add('highlighted');
                } else {
                    it.classList.remove('highlighted');
                }
            });
        });

        menu.appendChild(item);
    });

    // Make sure trigger text is updated correctly.
    const activeOption = nativeSelect.options[nativeSelect.selectedIndex];
    if (activeOption) {
        triggerText.textContent = activeOption.text;
        triggerText.title = activeOption.title || activeOption.value;
    }
}

function getModelIconSvg(value) {
    const normalized = (value || '').toLowerCase();
    if (normalized.includes('gemini')) {
        return `
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="model-item-icon-svg icon-gemini">
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275Z"/>
            <path d="m5 3 1 2.5L8.5 6 6 7 5 9.5 4 7 1.5 6 4 5.5Z"/>
            <path d="m19 17 1 2.5 2.5.5-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1Z"/>
        </svg>`;
    } else if (normalized.includes('gpt') || normalized.includes('openai') || normalized.includes('claude') || normalized.includes('anthropic') || normalized.includes('deepseek')) {
        return `
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="model-item-icon-svg icon-gpt-claude">
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275Z"/>
            <path d="m5 3 1 2.5L8.5 6 6 7 5 9.5 4 7 1.5 6 4 5.5Z"/>
            <path d="m19 17 1 2.5 2.5.5-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1Z"/>
        </svg>`;
    } else {
        return `
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="model-item-icon-svg icon-other">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
            <line x1="12" y1="22.08" x2="12" y2="12"/>
        </svg>`;
    }
}
