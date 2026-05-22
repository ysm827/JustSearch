import { state, setCurrentSessionId } from './state.js';
import { elements } from './ui.js';
import { updateActiveHistoryItem, getCachedHistory, openHistorySearch } from './history-view.js';

let popoverEl = null;
let popoverTimeout = null;

function removeRecentChatsPopover() {
    if (popoverEl) {
        popoverEl.remove();
        popoverEl = null;
    }
}

function setupHistoryPopover(miniHistoryBtn, loadChat) {
    const showPopover = () => {
        if (popoverTimeout) clearTimeout(popoverTimeout);
        if (popoverEl) return;
        
        const allHistory = getCachedHistory() || [];
        const activeSessionId = state.currentSessionId;
        const recentChats = allHistory
            .filter(chat => chat.id !== activeSessionId)
            .slice(0, 8);
            
        popoverEl = document.createElement('div');
        popoverEl.className = 'recent-chats-popover';
        
        const header = document.createElement('div');
        header.className = 'popover-header';
        header.textContent = '最近对话';
        popoverEl.appendChild(header);
        
        const list = document.createElement('div');
        list.className = 'popover-list';
        
        if (recentChats.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'popover-empty';
            empty.textContent = '暂无其他最近对话';
            list.appendChild(empty);
        } else {
            recentChats.forEach(chat => {
                const item = document.createElement('a');
                item.className = 'popover-item';
                item.href = `/c/${chat.id}`;
                item.textContent = chat.title || '新对话';
                item.title = chat.title || '新对话';
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (typeof loadChat === 'function') {
                        loadChat(chat.id);
                    }
                    removePopover();
                });
                list.appendChild(item);
            });
        }
        popoverEl.appendChild(list);
        
        document.body.appendChild(popoverEl);
        
        const rect = miniHistoryBtn.getBoundingClientRect();
        popoverEl.style.top = `${rect.top}px`;
        popoverEl.style.left = `${rect.right + 8}px`;
        
        // Add events to popover itself so hovering keeps it open
        popoverEl.addEventListener('mouseenter', () => {
            if (popoverTimeout) clearTimeout(popoverTimeout);
        });
        popoverEl.addEventListener('mouseleave', () => {
            startHideTimeout();
        });
    };
    
    const removePopover = () => {
        removeRecentChatsPopover();
    };
    
    const startHideTimeout = () => {
        if (popoverTimeout) clearTimeout(popoverTimeout);
        popoverTimeout = setTimeout(() => {
            removePopover();
        }, 300);
    };
    
    miniHistoryBtn.addEventListener('mouseenter', () => {
        if (popoverTimeout) clearTimeout(popoverTimeout);
        popoverTimeout = setTimeout(showPopover, 150);
    });
    
    miniHistoryBtn.addEventListener('mouseleave', () => {
        startHideTimeout();
    });
    
    miniHistoryBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (popoverEl) {
            removePopover();
        } else {
            showPopover();
        }
    });
    
    document.addEventListener('click', (e) => {
        if (popoverEl && !popoverEl.contains(e.target) && e.target !== miniHistoryBtn) {
            removePopover();
        }
    });
}

export function setupSidebar(loadChat) {
    if (window.innerWidth > 768) {
        const sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
        if (sidebarCollapsed) {
            elements.sidebar.classList.add('collapsed');
        }
    }

    const toggleSidebar = () => {
        removeRecentChatsPopover();
        if (window.innerWidth <= 768) {
            elements.sidebar.classList.add('mobile-open');
            elements.mobileOverlay.classList.add('active');
        } else {
            elements.sidebar.classList.toggle('collapsed');
            localStorage.setItem('sidebarCollapsed', elements.sidebar.classList.contains('collapsed'));
        }
    };

    elements.expandSidebarBtn?.addEventListener('click', toggleSidebar);
    elements.collapseSidebarBtn?.addEventListener('click', toggleSidebar);

    const sidebarBrandToggle = document.getElementById('sidebar-brand-toggle');
    sidebarBrandToggle?.addEventListener('click', toggleSidebar);
    sidebarBrandToggle?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggleSidebar();
        }
    });
    
    const miniToggleBtn = document.getElementById('mini-toggle-btn');
    miniToggleBtn?.addEventListener('click', toggleSidebar);

    // Expand when clicking empty space on collapsed pane (except on buttons)
    const collapsedPane = document.querySelector('.sidebar-collapsed-pane');
    if (collapsedPane) {
        collapsedPane.addEventListener('click', (e) => {
            if (elements.sidebar.classList.contains('collapsed')) {
                toggleSidebar();
            }
        });
        
        // Prevent button clicks in collapsed pane from propagating to the pane click handler
        collapsedPane.querySelectorAll('button, a, input').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
            });
        });
    }

    // Collapse when clicking empty space on history list (expanded pane)
    if (elements.historyList) {
        elements.historyList.addEventListener('click', (e) => {
            if (!elements.sidebar.classList.contains('collapsed') && e.target === e.currentTarget) {
                toggleSidebar();
            }
        });
    }

    elements.closeSidebarBtn.addEventListener('click', closeMobileSidebar);
    elements.mobileOverlay.addEventListener('click', closeMobileSidebar);

    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            closeMobileSidebar();
        }
    });

    const themeBtn = document.getElementById('quick-theme-btn');
    if (themeBtn) {
        const updateThemeIcon = () => {
            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            themeBtn.title = isDark ? '切换至浅色模式' : '切换至深色模式';
            if (isDark) {
                // Sun Icon (switching to light mode)
                themeBtn.innerHTML = `<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>`;
            } else {
                // Moon Icon (switching to dark mode)
                themeBtn.innerHTML = `<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path></svg>`;
            }
        };

        // Initial setup
        setTimeout(updateThemeIcon, 100);

        // Update when HTML attribute data-theme changes
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.attributeName === 'data-theme') {
                    updateThemeIcon();
                }
            });
        });
        observer.observe(document.documentElement, { attributes: true });

        themeBtn.addEventListener('click', async () => {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

            const { applyTheme } = await import('./utils.js');
            applyTheme(newTheme);
            updateThemeIcon();

            const { saveSettingsAPI } = await import('./api.js');
            const { state } = await import('./state.js');
            if (state.settings) {
                const newSettings = { ...state.settings, theme: newTheme };
                await saveSettingsAPI(newSettings);
                const themeSelect = document.getElementById('theme-select');
                if (themeSelect) {
                    themeSelect.value = newTheme;
                }
            }
        });
    }

    elements.newChatBtn.addEventListener('click', resetToNewChat);
    
    const miniNewChatBtn = document.getElementById('mini-new-chat-btn');
    miniNewChatBtn?.addEventListener('click', resetToNewChat);

    const miniSearchBtn = document.getElementById('mini-search-btn');
    miniSearchBtn?.addEventListener('click', () => {
        if (elements.sidebar.classList.contains('collapsed')) {
            elements.sidebar.classList.remove('collapsed');
            localStorage.setItem('sidebarCollapsed', 'false');
        }
        setTimeout(() => {
            openHistorySearch();
        }, 300);
    });

    const miniHistoryBtn = document.getElementById('mini-history-btn');
    if (miniHistoryBtn) {
        setupHistoryPopover(miniHistoryBtn, loadChat);
    }
}

export function closeMobileSidebar() {
    removeRecentChatsPopover();
    elements.sidebar.classList.remove('mobile-open');
    elements.mobileOverlay.classList.remove('active');
}

export function toggleSidebarFromShortcut() {
    removeRecentChatsPopover();
    if (window.innerWidth <= 768) {
        elements.sidebar.classList.toggle('mobile-open');
        elements.mobileOverlay.classList.toggle('active');
    } else {
        elements.sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', elements.sidebar.classList.contains('collapsed'));
    }
}

function resetToNewChat() {
    removeRecentChatsPopover();
    setCurrentSessionId(null);
    elements.chatContainer.innerHTML = '';
    elements.heroSection.style.display = 'block';
    elements.chatContainer.appendChild(elements.heroSection);
    updateActiveHistoryItem(null);
    elements.userInput.value = '';
    elements.userInput.style.height = '40px';
    elements.userInput.style.overflowY = 'hidden';
    elements.userInput.focus();

    if (window.location.pathname !== '/') {
        window.history.pushState(null, '', '/');
    }
}
