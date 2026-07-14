/**
 * Runs in the tab's isolated world after Defuddle is injected.
 * Last expression value is returned to chrome.scripting.executeScript.
 *
 * Output shape matches JustSearch backend _coerce_extract_result expectations:
 * { text, strategy, useful, title, author, thin, ok }
 */
(() => {
  function resolveDefuddle() {
    const candidates = [
      globalThis.Defuddle,
      typeof self !== "undefined" ? self.Defuddle : null,
      typeof window !== "undefined" ? window.Defuddle : null,
    ];
    for (const g of candidates) {
      if (!g) continue;
      if (typeof g === "function") return g;
      if (g && typeof g.default === "function") return g.default;
    }
    return null;
  }

  function usefulLen(s) {
    return (s || "").replace(/\s+/g, "").length;
  }

  function isProbablyMarkdown(text) {
    if (!text || typeof text !== "string") return false;
    return (
      /^#{1,6}\s/m.test(text) ||
      /^\s*[-*+]\s/m.test(text) ||
      /\[.+?\]\(.+?\)/.test(text) ||
      /^```/m.test(text) ||
      !/<[a-z][\s\S]*>/i.test(text.slice(0, 500))
    );
  }

  try {
    const Defuddle = resolveDefuddle();
    if (!Defuddle) {
      return {
        ok: false,
        text: "",
        strategy: "defuddle-missing",
        useful: 0,
        title: document.title || "",
        error: "Defuddle library failed to load",
      };
    }

    const url = location.href;
    const pageTitle = document.title || "";

    const parsed = new Defuddle(document, {
      markdown: true,
      url,
      useAsync: false, // fully local — no third-party fallbacks
    }).parse();

    let body =
      (parsed && (parsed.contentMarkdown || parsed.content)) || "";

    // Fallback: strip HTML to plain text if MD conversion did not apply
    if (body && !isProbablyMarkdown(body) && /<[a-z][\s\S]*>/i.test(body)) {
      const tmp = document.createElement("div");
      tmp.innerHTML = body;
      body = (tmp.innerText || tmp.textContent || "").trim();
    }

    body = (body || "").trim();
    const title = (parsed && parsed.title) || pageTitle || "";
    const author = (parsed && parsed.author) || "";
    const wordCount = (parsed && parsed.wordCount) || 0;
    const useful = usefulLen(body);
    const thin = useful < 40;

    // Prefer markdown body for the LLM; prepend a light title line when useful.
    let text = body;
    if (title && body && !body.startsWith("# ")) {
      text = `# ${String(title).replace(/\s+/g, " ").trim()}\n\n${body}`;
    }

    return {
      ok: true,
      text,
      strategy: "defuddle",
      useful: usefulLen(text),
      title,
      author,
      wordCount,
      thin,
      url,
    };
  } catch (err) {
    return {
      ok: false,
      text: "",
      strategy: "defuddle-error",
      useful: 0,
      title: document.title || "",
      error: err && err.message ? err.message : String(err),
    };
  }
})();
