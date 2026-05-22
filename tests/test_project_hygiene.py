import ast
import re
import struct
import zlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_rgba_png(path):
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")

    offset = 8
    width = height = color_type = None
    compressed = bytearray()
    while offset < len(data):
        chunk_length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + chunk_length]
        offset += 12 + chunk_length

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            assert bit_depth == 8
            assert color_type == 6
            assert compression == 0
            assert filter_method == 0
            assert interlace == 0
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break

    assert width is not None and height is not None
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    rows = []
    previous = bytearray(stride)
    cursor = 0

    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        current = bytearray(raw[cursor : cursor + stride])
        cursor += stride

        for index, value in enumerate(current):
            left = current[index - 4] if index >= 4 else 0
            above = previous[index]
            upper_left = previous[index - 4] if index >= 4 else 0

            if filter_type == 1:
                current[index] = (value + left) & 0xFF
            elif filter_type == 2:
                current[index] = (value + above) & 0xFF
            elif filter_type == 3:
                current[index] = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = left + above - upper_left
                pa = abs(predictor - left)
                pb = abs(predictor - above)
                pc = abs(predictor - upper_left)
                predicted = left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                current[index] = (value + predicted) & 0xFF
            else:
                assert filter_type == 0

        rows.append(bytes(current))
        previous = current

    def pixel(x, y):
        index = x * 4
        return tuple(rows[y][index : index + 4])

    return width, height, pixel


def test_readme_project_structure_matches_current_files():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    stale_references = [
        "chat_manager.py",
        "settings_manager.py",
        "benchmark_freshqa.py",
        "settings.json) 和聊天记录 (`chats/`)",
        "backend/chats",
    ]
    for reference in stale_references:
        assert reference not in readme

    current_references = [
        "database.py",
        "engine_health.py",
        "interaction.py",
        "openai_client.py",
        "crawler/",
        "sections/",
        "source-renderer.js",
        "data/",
    ]
    for reference in current_references:
        assert reference in readme


def test_browser_manager_does_not_import_browser_context_private_search_state():
    source_path = PROJECT_ROOT / "backend/app/browser_manager.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    private_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "browser_context":
            private_imports.extend(alias.name for alias in node.names if alias.name.startswith("_"))

    assert private_imports == []


def test_search_result_cleanup_is_split_from_browser_manager():
    cleanup_path = PROJECT_ROOT / "backend/app/search_result_cleanup.py"
    manager_path = PROJECT_ROOT / "backend/app/browser_manager.py"

    assert cleanup_path.is_file()

    cleanup_source = cleanup_path.read_text(encoding="utf-8")
    manager_source = manager_path.read_text(encoding="utf-8")

    assert "def clean_fallback_title" in cleanup_source
    assert "def is_generic_search_aux_title" in cleanup_source
    assert "def is_search_engine_internal_page" in cleanup_source
    assert "def _clean_fallback_title" not in manager_source
    assert "def _is_generic_search_aux_title" not in manager_source
    assert "def _is_search_engine_internal_page" not in manager_source


def test_captcha_interaction_session_is_registered_before_frontend_notification():
    manager_source = (
        PROJECT_ROOT / "backend/app/browser_manager.py"
    ).read_text(encoding="utf-8")
    captcha_block = manager_source.split("if detected_captcha:", 1)[1].split(
        "# Wait for results", 1
    )[0]

    register_index = captcha_block.index("register_interaction_session(session_id, page, event)")
    notify_index = captcha_block.index("ACTION_REQUIRED: CAPTCHA_DETECTED")

    assert register_index < notify_index


def test_google_engine_uses_official_multicolor_icon():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    chat_source = (
        PROJECT_ROOT / "backend/static/js/modules/chat.js"
    ).read_text(encoding="utf-8")
    google_item_match = re.search(
        r'<div class="quick-dropdown-item" data-value="google">(.*?)</div>',
        index_source,
        re.DOTALL,
    )

    assert google_item_match is not None
    google_markup = google_item_match.group(1)
    assert 'class="engine-icon logo-google"' in google_markup
    assert 'fill="currentColor"' not in google_markup
    assert google_markup.count('<path') >= 4
    assert 'fill="#4285F4"' in google_markup
    assert 'fill="#EA4335"' in google_markup
    assert 'fill="#FBBC05"' in google_markup
    assert 'fill="#34A853"' in google_markup
    assert "appendChild(activeSvg.cloneNode(true))" in chat_source


def test_sidebar_brand_uses_split_google_style_colors():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    sidebar_source = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    assert '<span class="brand-text-blue">Just</span>' in index_source
    assert '<span class="brand-text-cyan">Search</span>' in index_source
    assert "--brand-google-blue: #0024bb;" in sidebar_source
    assert "--brand-google-cyan: #00a5a5;" in sidebar_source
    assert "color: var(--brand-google-blue);" in sidebar_source
    assert "color: var(--brand-google-cyan);" in sidebar_source


def test_sidebar_brand_toggles_sidebar_like_collapse_button():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    sidebar_js = (
        PROJECT_ROOT / "backend/static/js/modules/sidebar.js"
    ).read_text(encoding="utf-8")
    sidebar_source = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    assert 'id="sidebar-brand-toggle"' in index_source
    assert 'role="button"' in index_source
    assert 'tabindex="0"' in index_source
    assert "const sidebarBrandToggle = document.getElementById('sidebar-brand-toggle');" in sidebar_js
    assert "sidebarBrandToggle?.addEventListener('click', toggleSidebar);" in sidebar_js
    assert "event.key === 'Enter' || event.key === ' '" in sidebar_js
    assert ".sidebar-brand-name:hover" in sidebar_source
    assert "cursor: pointer;" in sidebar_source


def test_crawler_security_and_redirect_helpers_are_split_out():
    crawler_dir = PROJECT_ROOT / "backend/app/crawler"
    security_path = crawler_dir / "security.py"
    redirects_path = crawler_dir / "redirects.py"
    page_crawler_path = PROJECT_ROOT / "backend/app/page_crawler.py"

    assert (crawler_dir / "__init__.py").is_file()
    assert security_path.is_file()
    assert redirects_path.is_file()

    page_crawler_source = page_crawler_path.read_text(encoding="utf-8")
    assert "from .crawler.security import is_private_url" in page_crawler_source
    assert "from .crawler.redirects import resolve_redirect_url" in page_crawler_source
    assert "def is_private_url" not in page_crawler_source
    assert "def resolve_redirect_url" not in page_crawler_source


def test_crawler_content_helpers_are_split_out():
    content_path = PROJECT_ROOT / "backend/app/crawler/content.py"
    page_crawler_path = PROJECT_ROOT / "backend/app/page_crawler.py"

    assert content_path.is_file()

    content_source = content_path.read_text(encoding="utf-8")
    page_crawler_source = page_crawler_path.read_text(encoding="utf-8")

    assert "async def extract_page_content" in content_source
    assert "async def extract_og_metadata" in content_source
    assert "async def install_resource_blocker" in content_source
    assert "from .crawler.content import" in page_crawler_source
    assert "_JS_EXTRACT_CONTENT" not in page_crawler_source
    assert "def _extract_og_metadata" not in page_crawler_source
    assert "def _install_resource_blocker" not in page_crawler_source


def test_legacy_migration_is_split_from_database_module():
    migration_path = PROJECT_ROOT / "backend/app/legacy_migration.py"
    database_path = PROJECT_ROOT / "backend/app/database.py"

    assert migration_path.is_file()

    migration_source = migration_path.read_text(encoding="utf-8")
    database_source = database_path.read_text(encoding="utf-8")

    assert "async def migrate_legacy_data" in migration_source
    assert "async def _migrate_chats_dir" in migration_source
    assert "async def _migrate_settings_file" in migration_source
    assert "def _migrate_chats_dir" not in database_source
    assert "def _migrate_settings_file" not in database_source


def test_legacy_chats_directory_is_not_part_of_source_tree():
    assert not (PROJECT_ROOT / "backend/chats/.gitkeep").exists()


def test_browser_context_does_not_keep_obsolete_chat_router_comment():
    source = (PROJECT_ROOT / "backend/app/browser_context.py").read_text(encoding="utf-8")

    assert "chat router references this module" not in source
    assert "Legacy compat import" not in source


def test_frontend_interaction_modules_are_split_out():
    modules_dir = PROJECT_ROOT / "backend/static/js/modules"
    expected_modules = [
        "browser-modal.js",
        "history-view.js",
        "settings-modal.js",
        "sidebar.js",
    ]

    for filename in expected_modules:
        assert (modules_dir / filename).is_file()


def test_frontend_uses_generated_logo_asset():
    logo_path = PROJECT_ROOT / "backend/static/assets/justsearch-logo.png"
    dark_logo_path = PROJECT_ROOT / "backend/static/assets/justsearch-logo-dark.png"
    favicon_path = PROJECT_ROOT / "backend/static/assets/justsearch-favicon.png"
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    manifest_source = (PROJECT_ROOT / "backend/static/manifest.json").read_text(
        encoding="utf-8"
    )

    assert logo_path.is_file()
    assert dark_logo_path.is_file()
    assert favicon_path.is_file()
    assert dark_logo_path.read_bytes()[25] in {4, 6}
    # PNG color type 6 is truecolor with alpha, 4 is grayscale with alpha.
    assert favicon_path.read_bytes()[25] in {4, 6}
    assert "/static/assets/justsearch-logo.png" in index_source
    assert "/static/assets/justsearch-logo-dark.png" in index_source
    assert "/static/assets/justsearch-favicon.png?v=7" in index_source
    assert "/static/assets/justsearch-favicon.png" in manifest_source
    assert "class=\"brand-logo\"" not in index_source
    assert "sidebar-logo" not in index_source
    assert "hero-brand-logo-light" in index_source
    assert "hero-brand-logo-dark" in index_source


def test_favicon_has_white_rounded_square_background_for_dark_browser_tabs():
    favicon_path = PROJECT_ROOT / "backend/static/assets/justsearch-favicon.png"
    width, height, pixel = _read_rgba_png(favicon_path)

    assert (width, height) == (512, 512)

    for point in [(0, 0), (511, 0), (0, 511), (511, 511), (16, 16), (495, 16), (16, 495), (495, 495)]:
        assert pixel(*point)[3] == 0

    for point in [(256, 16), (16, 256), (496, 256), (256, 496), (80, 80), (431, 80), (80, 431), (431, 431)]:
        red, green, blue, alpha = pixel(*point)
        assert red >= 245
        assert green >= 245
        assert blue >= 245
        assert alpha >= 245

    icon_pixels_x = []
    icon_pixels_y = []
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixel(x, y)
            is_visible_icon_pixel = alpha > 20 and not (
                red >= 245 and green >= 245 and blue >= 245
            )
            if not is_visible_icon_pixel:
                continue

            icon_pixels_x.append(x)
            icon_pixels_y.append(y)

    assert min(icon_pixels_x) >= 16
    assert max(icon_pixels_x) <= 496
    assert min(icon_pixels_y) >= 32
    assert max(icon_pixels_y) <= 480
    assert max(icon_pixels_x) - min(icon_pixels_x) + 1 >= 440
    assert max(icon_pixels_y) - min(icon_pixels_y) + 1 >= 350


def test_frontend_relative_imports_resolve_to_files():
    js_root = PROJECT_ROOT / "backend/static/js"
    import_pattern = re.compile(
        r"(?:from\s+['\"](?P<static>[^'\"]+)['\"]|import\(\s*['\"](?P<dynamic>[^'\"]+)['\"]\s*\))"
    )

    missing = []
    for source_path in sorted(js_root.rglob("*.js")):
        source = source_path.read_text(encoding="utf-8")
        for match in import_pattern.finditer(source):
            import_path = match.group("static") or match.group("dynamic")
            if not import_path.startswith("."):
                continue

            resolved = (source_path.parent / import_path).resolve()
            candidates = [resolved]
            if resolved.suffix == "":
                candidates.append(resolved.with_suffix(".js"))

            if not any(candidate.is_file() for candidate in candidates):
                missing.append(f"{source_path.relative_to(PROJECT_ROOT)} -> {import_path}")

    assert missing == []


def test_app_surface_suppresses_context_menu_outside_editable_controls():
    source = (PROJECT_ROOT / "backend/static/js/main.js").read_text(encoding="utf-8")

    assert "setupContextMenuSuppression();" in source
    assert "function setupContextMenuSuppression()" in source
    assert "document.addEventListener('contextmenu'" in source
    assert ".hero-header, .hero-brand-logo, .hero-container" in source
    assert "event.preventDefault();" in source
    assert "target.closest('input, textarea, select, [contenteditable=\"true\"]')" in source
    assert "{ capture: true }" in source


def test_sidebar_does_not_expose_export_all_button():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    sidebar_source = (
        PROJECT_ROOT / "backend/static/js/modules/sidebar.js"
    ).read_text(encoding="utf-8")

    assert "export-all-btn" not in index_source
    assert "导出全部对话" not in index_source
    assert "export-all-btn" not in sidebar_source
    assert "/api/history/export/all" not in sidebar_source


def test_settings_history_reset_uses_history_renderer_cache_path():
    source = (PROJECT_ROOT / "backend/static/js/modules/settings-modal.js").read_text(
        encoding="utf-8"
    )

    assert "renderHistory" in source
    assert "renderHistory([], state.currentSessionId" in source
    assert "historyList.innerHTML = ''" not in source


def test_settings_modal_closes_mobile_sidebar_before_opening():
    source = (PROJECT_ROOT / "backend/static/js/modules/settings-modal.js").read_text(
        encoding="utf-8"
    )

    open_settings_source = source.split(
        "const openSettings = async () => {", 1
    )[1].split("};", 1)[0]
    assert "const sidebar = document.getElementById('sidebar');" in open_settings_source
    assert "const mobileOverlay = document.getElementById('mobile-overlay');" in open_settings_source
    assert "sidebar.classList.remove('mobile-open');" in open_settings_source
    assert "mobileOverlay.classList.remove('active');" in open_settings_source


def test_settings_modal_uses_amc_inspired_layout_tokens():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    settings_css = (
        PROJECT_ROOT / "backend/static/css/sections/input-modal.css"
    ).read_text(encoding="utf-8")
    polish_css = (
        PROJECT_ROOT / "backend/static/css/sections/polish.css"
    ).read_text(encoding="utf-8")

    expected_markup_hooks = [
        "settings-modal-content amc-settings-modal",
        "settings-sidebar-close-btn",
        "settings-mobile-title",
        "settings-section-kicker",
        "settings-card",
        "settings-field-row",
        "settings-danger-zone",
    ]
    for hook in expected_markup_hooks:
        assert hook in index_source

    expected_style_tokens = [
        "--amc-modal-width",
        "--amc-sidebar-width",
        "background: var(--bg)",
        "width: var(--amc-sidebar-width)",
        "max-width: var(--amc-content-width)",
        ".settings-danger-zone",
        "@media (max-width: 760px)",
    ]
    for token in expected_style_tokens:
        assert token in settings_css

    assert "#settings-modal .settings-modal-content" in polish_css
    assert "width: 100vw;" in polish_css
    assert "border-radius: 0;" in polish_css


def test_settings_modal_has_amc_inspired_about_page():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    settings_css = (
        PROJECT_ROOT / "backend/static/css/sections/input-modal.css"
    ).read_text(encoding="utf-8")
    settings_js = (
        PROJECT_ROOT / "backend/static/js/modules/settings-modal.js"
    ).read_text(encoding="utf-8")

    expected_markup = [
        'data-tab="about"',
        'id="tab-about"',
        'class="about-hero"',
        'class="about-logo"',
        'id="about-version"',
        'class="about-version-pill"',
        'class="about-action-link github-primary"',
        'id="about-stars-count"',
        'class="about-meta-grid"',
    ]
    for hook in expected_markup:
        assert hook in index_source

    expected_styles = [
        ".about-hero",
        ".about-version-pill",
        ".about-action-row",
        ".about-meta-grid",
        ".about-action-link.github-primary",
    ]
    for token in expected_styles:
        assert token in settings_css

    assert "about-version" in settings_js
    assert "about-stars-count" in settings_js


def test_settings_modal_avoids_internal_divider_lines():
    settings_css = (
        PROJECT_ROOT / "backend/static/css/sections/input-modal.css"
    ).read_text(encoding="utf-8")

    assert "border-top: 1px solid var(--border);" not in settings_css
    assert "border-right: 1px solid var(--border);" not in settings_css
    assert "border-bottom: 1px solid var(--border);" not in settings_css
    assert "border-top: 1px solid color-mix(in srgb, var(--border) 70%, transparent);" not in settings_css
    assert "border-top: 1px solid rgba(255, 255, 255, 0.12);" not in settings_css
    assert "border-bottom: 1px solid rgba(255, 255, 255, 0.15);" not in settings_css


def test_validate_api_key_button_shows_loading_state():
    settings_js = (
        PROJECT_ROOT / "backend/static/js/modules/settings-modal.js"
    ).read_text(encoding="utf-8")
    settings_css = (
        PROJECT_ROOT / "backend/static/css/sections/input-modal.css"
    ).read_text(encoding="utf-8")

    assert "validateBtn.classList.add('is-validating')" in settings_js
    assert "validateBtn.classList.remove('is-validating')" in settings_js
    assert "validateBtn.disabled = true" in settings_js
    assert "validateBtn.disabled = false" in settings_js
    assert "progress_activity" in settings_js
    assert ".password-toggle-btn.is-validating" in settings_css
    assert ".password-toggle-btn.is-validating span" in settings_css


def test_settings_modal_has_search_engine_availability_check():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    settings_js = (
        PROJECT_ROOT / "backend/static/js/modules/settings-modal.js"
    ).read_text(encoding="utf-8")
    api_js = (PROJECT_ROOT / "backend/static/js/modules/api.js").read_text(
        encoding="utf-8"
    )
    settings_css = (
        PROJECT_ROOT / "backend/static/css/sections/input-modal.css"
    ).read_text(encoding="utf-8")

    assert 'id="check-engines-btn"' in index_source
    assert 'id="engine-check-results"' in index_source
    assert 'export async function checkEnginesAPI' in api_js
    assert "API.checkEnginesAPI" in settings_js
    assert "checkEnginesBtn.disabled = true" in settings_js
    assert "checkEnginesBtn.disabled = false" in settings_js
    assert "renderEngineCheckResults" in settings_js
    assert ".engine-check-results" in settings_css
    assert ".engine-check-result.available" in settings_css
    assert ".engine-check-result.unavailable" in settings_css


def test_default_max_search_results_is_fifty_across_app():
    database_source = (PROJECT_ROOT / "backend/app/database.py").read_text(
        encoding="utf-8"
    )
    chat_source = (PROJECT_ROOT / "backend/app/routers/chat.py").read_text(
        encoding="utf-8"
    )
    settings_source = (PROJECT_ROOT / "backend/app/routers/settings.py").read_text(
        encoding="utf-8"
    )
    browser_manager_source = (PROJECT_ROOT / "backend/app/browser_manager.py").read_text(
        encoding="utf-8"
    )
    workflow_source = (PROJECT_ROOT / "backend/app/workflow.py").read_text(
        encoding="utf-8"
    )
    settings_js = (
        PROJECT_ROOT / "backend/static/js/modules/settings-modal.js"
    ).read_text(encoding="utf-8")
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    settings_example = (PROJECT_ROOT / "backend/settings.json.example").read_text(
        encoding="utf-8"
    )

    assert '"max_results": "50"' in database_source
    assert "max_results: Optional[int] = 50" in chat_source
    assert 'defaults.get("max_results", 50)' in chat_source
    assert "max_results: Optional[int] = 50" in settings_source
    assert 'min(50, int(update["max_results"]))' in settings_source
    assert 'max_results: int = 50' in browser_manager_source
    assert 'max_results: int = 50' in workflow_source
    assert "settings.max_results || 50" in settings_js
    assert "max_results: parseInt(document.getElementById('max-results-input').value) || 50" in settings_js
    assert 'id="max-results-input" placeholder="50" min="1" max="50"' in index_source
    assert '"max_results": 50' in settings_example


def test_input_area_has_no_character_counter():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    chat_source = (
        PROJECT_ROOT / "backend/static/js/modules/chat.js"
    ).read_text(encoding="utf-8")
    css_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "backend/static/css").rglob("*.css"))
    )

    assert "char-count" not in index_source
    assert "charCount" not in chat_source
    assert "Update character count" not in chat_source
    assert ".char-count" not in css_source


def test_initial_release_version_uses_semver_and_v_display_prefix():
    version_source = (PROJECT_ROOT / "backend/app/version.py").read_text(encoding="utf-8")
    dockerfile_source = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    settings_js = (
        PROJECT_ROOT / "backend/static/js/modules/settings-modal.js"
    ).read_text(encoding="utf-8")

    assert '__version__ = "1.0.0"' in version_source
    assert 'org.opencontainers.image.version="1.0.0"' in dockerfile_source
    assert "formatVersionText" in settings_js
    assert "`v${rawVersion}`" in settings_js


def test_confirm_dialog_resolves_when_dismissed_without_buttons():
    source = (PROJECT_ROOT / "backend/static/js/modules/ui.js").read_text(encoding="utf-8")
    show_confirm = source.split("export function showConfirm", 1)[1].split(
        "export const elements", 1
    )[0]

    assert "function onKeyDown" in show_confirm
    assert "event.key === 'Escape'" in show_confirm
    assert "document.addEventListener('keydown', onKeyDown)" in show_confirm
    assert "modal.addEventListener('click', onBackdropClick)" in show_confirm
    assert "document.removeEventListener('keydown', onKeyDown)" in show_confirm
    assert "modal.removeEventListener('click', onBackdropClick)" in show_confirm


def test_source_rendering_helpers_are_split_from_ui_module():
    source_renderer_path = PROJECT_ROOT / "backend/static/js/modules/source-renderer.js"
    ui_path = PROJECT_ROOT / "backend/static/js/modules/ui.js"
    chat_path = PROJECT_ROOT / "backend/static/js/modules/chat.js"

    assert source_renderer_path.is_file()

    renderer_source = source_renderer_path.read_text(encoding="utf-8")
    ui_source = ui_path.read_text(encoding="utf-8")
    chat_source = chat_path.read_text(encoding="utf-8")

    assert "export function extractSources" in renderer_source
    assert "export function renderWithCitations" in renderer_source
    assert "function getFaviconUrl" in renderer_source
    assert "from './source-renderer.js'" in ui_source
    assert "from './source-renderer.js'" in chat_source
    assert "export function extractSources" not in ui_source
    assert "export function renderWithCitations" not in ui_source
    assert "function getFaviconUrl" not in ui_source


def test_css_is_split_into_named_sections():
    css_dir = PROJECT_ROOT / "backend/static/css"
    sections_dir = css_dir / "sections"
    style_source = (css_dir / "style.css").read_text(encoding="utf-8")
    expected_sections = [
        "base.css",
        "sidebar.css",
        "chat.css",
        "input-modal.css",
        "markdown.css",
        "overlays.css",
        "responsive.css",
        "polish.css",
    ]

    for filename in expected_sections:
        assert (sections_dir / filename).is_file()
        assert f"./sections/{filename}" in style_source

    assert len(style_source.splitlines()) < 40


def test_sidebar_stylesheet_changes_are_cache_busted():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    style_source = (
        PROJECT_ROOT / "backend/static/css/style.css"
    ).read_text(encoding="utf-8")

    assert 'href="/static/css/style.css?v=9"' in index_source
    assert "@import url('./sections/sidebar.css?v=8');" in style_source


def test_history_rename_updates_cached_history_source():
    source = (PROJECT_ROOT / "backend/static/js/modules/history-view.js").read_text(
        encoding="utf-8"
    )

    assert "function updateCachedHistoryTitle" in source
    assert "updateCachedHistoryTitle(chatId, newTitle)" in source


def test_history_empty_state_uses_dom_helper_instead_of_html_strings():
    source = (PROJECT_ROOT / "backend/static/js/modules/history-view.js").read_text(
        encoding="utf-8"
    )
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "backend/static/css").rglob("*.css"))
    )

    assert "function renderEmptyHistory" in source
    assert "icon.setAttribute('aria-hidden', 'true')" in source
    assert ".history-no-results .history-no-results-icon" in css
    assert "history-no-results\"><span" not in source
    assert "style=\"font-size" not in source


def test_sidebar_history_groups_have_frontend_controls():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    history_source = (
        PROJECT_ROOT / "backend/static/js/modules/history-view.js"
    ).read_text(encoding="utf-8")
    api_source = (PROJECT_ROOT / "backend/static/js/modules/api.js").read_text(
        encoding="utf-8"
    )
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    assert 'id="new-group-btn"' in index_source
    assert "fetchChatGroups" in api_source
    assert "createChatGroupAPI" in api_source
    assert "moveChatToGroupAPI" in api_source
    assert "renderChatGroups" in history_source
    assert "setupHistoryDragAndDrop" in history_source
    assert "data-group-id" in history_source
    assert ".chat-group-header" in sidebar_css
    assert ".chat-group-drop-target" in sidebar_css


def test_sidebar_search_action_matches_amc_layout():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    history_source = (
        PROJECT_ROOT / "backend/static/js/modules/history-view.js"
    ).read_text(encoding="utf-8")
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    action_stack = index_source.split('<div class="new-chat-wrapper">', 1)[1].split(
        '<div class="history-list"',
        1,
    )[0]

    assert action_stack.index('id="new-chat-btn"') < action_stack.index(
        'id="history-search-open-btn"'
    )
    assert action_stack.index('id="history-search-open-btn"') < action_stack.index(
        'id="new-group-btn"'
    )
    assert 'id="history-search-box"' in action_stack
    assert 'id="history-search-close-btn"' in action_stack
    assert 'class="history-search-btn"' in action_stack

    assert ".history-search-btn" in sidebar_css
    assert ".history-search-box[hidden]" in sidebar_css
    assert ".history-search-close-btn" in sidebar_css

    assert "export function openHistorySearch" in history_source
    assert "historySearchOpenBtn.hidden = true" in history_source
    assert "historySearchBox.hidden = false" in history_source
    assert "historySearchInput.value = ''" in history_source


def test_sidebar_collapse_uses_crossfade_instead_of_display_toggle():
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")
    responsive_css = (
        PROJECT_ROOT / "backend/static/css/sections/responsive.css"
    ).read_text(encoding="utf-8")

    expanded_collapsed_rule = re.search(
        r"#sidebar\.collapsed\s+\.sidebar-expanded-pane\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )
    mini_base_rule = re.search(
        r"\.sidebar-collapsed-pane\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )
    mini_collapsed_rule = re.search(
        r"#sidebar\.collapsed\s+\.sidebar-collapsed-pane\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )
    responsive_expanded_rule = re.search(
        r"#sidebar\.collapsed\s+\.sidebar-expanded-pane\s*\{(?P<body>.*?)\}",
        responsive_css,
        re.DOTALL,
    )
    responsive_mini_rule = re.search(
        r"#sidebar\.collapsed\s+\.sidebar-collapsed-pane\s*\{(?P<body>.*?)\}",
        responsive_css,
        re.DOTALL,
    )

    assert expanded_collapsed_rule is not None
    assert mini_base_rule is not None
    assert mini_collapsed_rule is not None
    assert "display: none" not in expanded_collapsed_rule.group("body")
    assert "display: none" not in mini_base_rule.group("body")
    assert "display: flex" not in mini_collapsed_rule.group("body")
    assert "visibility:" in expanded_collapsed_rule.group("body")
    assert "pointer-events: none" in expanded_collapsed_rule.group("body")
    assert "transform:" in expanded_collapsed_rule.group("body")
    assert "transform:" in mini_collapsed_rule.group("body")
    assert responsive_expanded_rule is not None
    assert responsive_mini_rule is not None
    assert "display: flex !important" not in responsive_expanded_rule.group("body")
    assert "display: none !important" not in responsive_mini_rule.group("body")


def test_sidebar_mini_icons_match_expanded_icon_size_and_direction():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(
        encoding="utf-8"
    )
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    collapsed_markup = index_source.split('<div class="sidebar-collapsed-pane">', 1)[
        1
    ].split('    </div>\n\n    <div id="main">', 1)[0]
    assert 'width="20" height="20"' not in collapsed_markup
    assert collapsed_markup.count('width="18" height="18"') >= 5
    assert 'class="icon-svg sidebar-collapse-icon"' in index_source
    assert 'class="icon-svg sidebar-expand-icon"' in index_source
    assert ".sidebar-expand-icon" in sidebar_css
    assert "transform: scaleX(-1);" in sidebar_css


def test_sidebar_polish_uses_precise_cursor_hover_and_stable_shortcuts():
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    assert "cursor: ew-resize" not in sidebar_css
    assert "#sidebar.collapsed:hover" not in sidebar_css
    assert "transition: all" not in sidebar_css
    assert "min-width:" in re.search(
        r"\.sidebar-kbd\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    ).group("body")
    assert "text-align: right" in sidebar_css
    assert ".new-chat-btn:hover .sidebar-kbd" in sidebar_css


def test_sidebar_header_primary_controls_are_twenty_percent_larger():
    sidebar_css = (
        PROJECT_ROOT / "backend/static/css/sections/sidebar.css"
    ).read_text(encoding="utf-8")

    brand_rule = re.search(
        r"\.sidebar-brand-name\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )
    header_button_rule = re.search(
        r"\.sidebar-header-buttons\s+\.icon-btn\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )
    header_icon_rule = re.search(
        r"\.sidebar-header-buttons\s+\.icon-svg\s*\{(?P<body>.*?)\}",
        sidebar_css,
        re.DOTALL,
    )

    assert brand_rule is not None
    assert header_button_rule is not None
    assert header_icon_rule is not None
    assert "font-size: 19.8px;" in brand_rule.group("body")
    assert "width: 41px !important;" in header_button_rule.group("body")
    assert "height: 41px;" in header_button_rule.group("body")
    assert "width: 21.6px;" in header_icon_rule.group("body")
    assert "height: 21.6px;" in header_icon_rule.group("body")


def test_model_selector_trigger_has_no_provider_icon():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(encoding="utf-8")
    selector_source = (
        PROJECT_ROOT / "backend/static/js/modules/model-selector.js"
    ).read_text(encoding="utf-8")

    trigger_match = re.search(
        r'<div class="model-select-trigger"[^>]*>(.*?)</div>',
        index_source,
        re.DOTALL,
    )

    assert trigger_match is not None
    assert "model-icon-svg" not in trigger_match.group(1)
    assert "model-item-icon-svg" in selector_source


def test_deep_search_toggle_uses_material_symbol_icon():
    index_source = (PROJECT_ROOT / "backend/static/index.html").read_text(encoding="utf-8")

    button_match = re.search(
        r'<button id="quick-interactive-btn"[^>]*>(.*?)</button>',
        index_source,
        re.DOTALL,
    )

    assert button_match is not None
    button_markup = button_match.group(1)
    assert 'class="material-symbols-rounded toolbar-symbol"' in button_markup
    assert "travel_explore" in button_markup
    assert '<svg class="icon-svg"' not in button_markup


def test_browser_modal_queries_status_inside_its_modal():
    source = (PROJECT_ROOT / "backend/static/js/modules/browser-modal.js").read_text(
        encoding="utf-8"
    )

    assert "modal.querySelector('.browser-status-overlay')" in source
    assert "document.querySelector('.browser-status-overlay')" not in source


def test_escape_shortcut_closes_topmost_modal_only():
    source = (PROJECT_ROOT / "backend/static/js/main.js").read_text(encoding="utf-8")

    assert "document.querySelectorAll('.modal.active')" in source
    assert "activeModals[activeModals.length - 1]" in source
    assert "document.querySelector('.modal.active')" not in source


def test_openai_clients_use_project_factory_with_user_agent():
    factory_path = PROJECT_ROOT / "backend/app/openai_client.py"
    source = factory_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "JustSearch/" in source
    assert "__version__" in source

    factory_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "AsyncOpenAI"
    ]
    assert len(factory_calls) == 1

    call = factory_calls[0]
    keyword_names = {keyword.arg for keyword in call.keywords}
    assert "default_headers" in keyword_names

    headers_keyword = next(
        keyword for keyword in call.keywords if keyword.arg == "default_headers"
    )
    assert isinstance(headers_keyword.value, ast.Dict)
    header_keys = [
        key.value
        for key in headers_keyword.value.keys
        if isinstance(key, ast.Constant)
    ]
    assert "User-Agent" in header_keys


def test_openai_clients_are_not_constructed_outside_project_factory():
    direct_usages = []
    for source_path in sorted((PROJECT_ROOT / "backend/app").rglob("*.py")):
        if source_path.name == "openai_client.py":
            continue

        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "openai":
                imported_names = {alias.name for alias in node.names}
                if "AsyncOpenAI" in imported_names:
                    direct_usages.append(str(source_path.relative_to(PROJECT_ROOT)))
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "AsyncOpenAI"
            ):
                direct_usages.append(str(source_path.relative_to(PROJECT_ROOT)))

    assert direct_usages == []
