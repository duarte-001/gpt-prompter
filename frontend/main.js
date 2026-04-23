import React, { useEffect, useMemo, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";

function safeJsonParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

function App() {
  const [question, setQuestion] = useState("");
  const [promptOnly, setPromptOnly] = useState(true);
  const [useRag, setUseRag] = useState(true);
  const [indexRag, setIndexRag] = useState(false);
  const [promptSize, setPromptSize] = useState("large"); // small | medium | large (server default is large)
  const [devMode, setDevMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resp, setResp] = useState(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState("");
  const [status, setStatus] = useState(null);
  const [stepsLive, setStepsLive] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [chat, setChat] = useState([]);
  const localMode = !promptOnly;

  const exportMsgs = resp?.export_messages ?? [];
  const exportJsonText = useMemo(() => {
    if (!exportMsgs?.length) return "";
    return JSON.stringify(exportMsgs, null, 2);
  }, [exportMsgs]);

  const chatGptPasteText = useMemo(() => {
    if (!exportMsgs?.length) return "";
    const lines = [];
    for (const m of exportMsgs) {
      const role = (m?.role || "").toUpperCase();
      const content = m?.content || "";
      if (!role || !content) continue;
      lines.push(`${role}:\n${content}\n`);
    }
    return lines.join("\n").trim();
  }, [exportMsgs]);

  const chatGptOptimizedText = useMemo(() => {
    if (!exportMsgs?.length) return "";
    const sys = exportMsgs.find((m) => m?.role === "system")?.content || "";
    const lastUserIdx = (() => {
      for (let i = exportMsgs.length - 1; i >= 0; i -= 1) {
        if (exportMsgs[i]?.role === "user") return i;
      }
      return -1;
    })();
    const lastUser = lastUserIdx >= 0 ? exportMsgs[lastUserIdx]?.content || "" : "";

    // Include the same context as “Copy entire prompt”, but in a cleaner single-block paste.
    // Keep recent turns (if any) because they often contain the user's intent refinements.
    const recent = lastUserIdx > 0 ? exportMsgs.slice(1, lastUserIdx) : []; // drop system, drop final user
    const recentLines = [];
    for (const m of recent) {
      const role = m?.role;
      const content = String(m?.content || "").trim();
      if (!content) continue;
      if (role === "user") recentLines.push(`User: ${content}`);
      else if (role === "assistant") recentLines.push(`Assistant: ${content}`);
      else recentLines.push(content);
    }

    const chunks = [];
    if (sys.trim()) chunks.push(`INSTRUCTIONS\n${sys.trim()}`);
    if (recentLines.length) chunks.push(`RECENT_CONTEXT\n${recentLines.join("\n\n")}`);
    if (lastUser.trim()) chunks.push(`CURRENT_PAYLOAD\n${lastUser.trim()}`);
    return chunks.join("\n\n").trim();
  }, [exportMsgs]);

  const promptPreview = useMemo(() => {
    // Preview should be user-facing and readable: show only the last USER payload
    // (the structured JSON block), not the system prompt.
    const user = [...exportMsgs].reverse().find((m) => m?.role === "user");
    const text = user?.content || "";
    if (!text) return "";
    const maxChars = 1800;
    if (text.length <= maxChars) return text;
    return text.slice(0, maxChars).trimEnd() + "\n\n…(preview truncated)";
  }, [exportMsgs]);

  const liveJson = useMemo(() => {
    if (!resp?.context_json) return null;
    return safeJsonParse(resp.context_json);
  }, [resp]);

  const asOfBySymbol = useMemo(() => {
    const out = {};
    const data = liveJson;
    if (!data) return out;
    // summaries_to_json emits a JSON array of per-ticker summaries (when tickers exist).
    if (Array.isArray(data)) {
      for (const row of data) {
        const sym = row?.symbol || row?.ticker || row?.metadata?.ticker;
        const asOf = row?.last_date || row?.as_of || row?.metadata?.as_of;
        if (sym && asOf) out[String(sym)] = String(asOf);
      }
    }
    return out;
  }, [liveJson]);

  const isPromptOnlyPlaceholder = useMemo(() => {
    return (resp?.mode || "") === "prompt_export";
  }, [resp]);

  const exportAvailable = !!chatGptPasteText;

  const exportChars = useMemo(() => {
    const t = chatGptOptimizedText || "";
    return t.length;
  }, [chatGptOptimizedText]);

  const exportTokEst = useMemo(() => {
    // Cheap, consistent heuristic. Good enough for warning thresholds.
    return Math.max(1, Math.round(exportChars / 4));
  }, [exportChars]);

  const exportTooLarge = useMemo(() => {
    // Conservative default threshold; user can still copy.
    return exportTokEst >= 12000;
  }, [exportTokEst]);

  const fetchErrorSymbols = useMemo(() => {
    const data = liveJson;
    if (!Array.isArray(data)) return [];
    const bad = [];
    for (const row of data) {
      const sym = row?.symbol || row?.ticker;
      const summ = row?.summary;
      const errMsg = row?.error || summ?.error;
      if (sym && errMsg) bad.push(String(sym));
    }
    return [...new Set(bad)];
  }, [liveJson]);

  const timingEntries = useMemo(() => {
    const t = resp?.timings || {};
    const order = ["match", "fetch", "rag_index", "rag_retrieve", "llm", "total"];
    return Object.entries(t)
      .filter(([, v]) => v != null)
      .map(([k, v]) => ({ k, v }))
      .sort((a, b) => {
        const ia = order.indexOf(String(a.k));
        const ib = order.indexOf(String(b.k));
        if (ia !== -1 || ib !== -1) return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
        return String(a.k).localeCompare(String(b.k));
      });
  }, [resp]);

  const ragHitCards = useMemo(() => {
    const hits = resp?.rag_hits || [];
    if (!Array.isArray(hits)) return [];
    return hits.slice(0, 8).map((h) => {
      const meta = h?.metadata || {};
      const ticker = meta.ticker || meta.symbol || "";
      const asOf = meta.as_of || "";
      const docType = meta.doc_type || "";
      const dist = h?.distance;
      return {
        title: [ticker, docType, asOf].filter(Boolean).join(" · ") || "hit",
        distance: dist,
        excerpt: (h?.excerpt || "").trim(),
        metadata: meta,
      };
    });
  }, [resp]);

  const [stepsExpanded, setStepsExpanded] = useState(false);

  function stepKind(label) {
    const low = String(label || "").toLowerCase();
    if (low.includes("match")) return "match";
    if (low.includes("fetch") || low.includes("market")) return "fetch";
    if (low.includes("rag") || low.includes("retriev") || low.includes("index")) return "rag";
    if (low.includes("llm") || low.includes("answer") || low.includes("draft") || low.includes("generat"))
      return "llm";
    return "other";
  }

  function normalizeStepLabel(label) {
    const s = String(label || "");
    return s.replaceAll("…", "...").replaceAll("\uFFFD", "").trim();
  }

  function buildAssistantSummary(r) {
    if (!r) return "";
    const syms = r.symbols_used || [];
    const asOfs = syms
      .map((s) => (asOfBySymbol && asOfBySymbol[s] ? `${s} (as-of ${asOfBySymbol[s]})` : s))
      .filter(Boolean);

    if (r.error) {
      return `I couldn't complete the fetch.\n\nError: ${r.error}\n\nIf this persists, try another network or disable retrieval context.`;
    }

    if (!syms.length) {
      // Use pipeline’s own guidance when no tickers were found.
      const a = (r.answer || "").trim();
      return a || "I couldn't identify any tickers in that question. Try adding a ticker (e.g. NVDA) or a sector.";
    }

    const ragNote =
      r.rag_error && String(r.rag_error).trim()
        ? `\n\nNote: retrieval context had an issue (${String(r.rag_error).trim()}). Prompt will still work.`
        : "";

    if ((r.mode || "") === "prompt_export") {
      return `Fetched live data for ${asOfs.join(", ")}.\n\nPrompt is ready — click “Copy entire prompt” to paste into ChatGPT.${ragNote}`;
    }

    // local_answer mode
    const a = (r.answer || "").trim();
    return a || `Fetched live data for ${asOfs.join(", ")}.${ragNote}`;
  }

  async function onAsk() {
    const q = question.trim();
    if (!q) return;
    setLoading(true);
    setErr("");
    setCopied("");
    setStepsLive([]);
    // No chat memory: keep a single-turn chat UX
    setChat([
      { role: "user", text: q },
      { role: "assistant", text: "Working…", pending: true },
    ]);
    setQuestion("");
    try {
      const r = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: q,
          prompt_only: promptOnly,
          prompt_size: promptSize,
          use_rag: localMode ? useRag : false,
          index_metrics_to_rag: localMode ? indexRag : false,
          recent_messages: [],
        }),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`API error (${r.status}): ${t}`);
      }
      const created = await r.json();
      const jobId = created?.job_id;
      // Backwards-compatible path: if server is still on sync /api/ask, it returns AskResponse directly.
      if (!jobId) {
        if (created?.mode && typeof created?.answer === "string") {
          setResp(created);
          if (created?.error) setErr(String(created.error));
          return;
        }
        throw new Error("API error: missing job_id");
      }

      let tries = 0;
      while (true) {
        tries += 1;
        await new Promise((res) => setTimeout(res, 650));
        const jr = await fetch(`/api/ask/${encodeURIComponent(jobId)}`, {
          method: "GET",
        });
        if (!jr.ok) {
          const t = await jr.text();
          throw new Error(`API error (${jr.status}): ${t}`);
        }
        const js = await jr.json();
        if (js?.status === "done" && js?.result) {
          setResp(js.result);
          if (js.result?.error) setErr(String(js.result.error));
          break;
        }
        if (js?.status === "error") {
          throw new Error(js?.error || "Job failed.");
        }
        if (tries > 240) throw new Error("Timed out waiting for result.");
      }
    } catch (e) {
      setErr(e?.message ?? String(e));
      setResp(null);
      setChat((prev) => {
        const msg = e?.message ?? String(e);
        const base = Array.isArray(prev) ? prev : [];
        const out = base.map((m) =>
          m?.role === "assistant" ? { ...m, pending: false, text: `Request failed.\n\n${msg}` } : m
        );
        if (!out.some((m) => m?.role === "assistant")) out.push({ role: "assistant", text: `Request failed.\n\n${msg}` });
        return out;
      });
    } finally {
      setLoading(false);
    }
  }

  async function refreshStatus() {
    try {
      const r = await fetch("/api/status", {
        method: "GET",
      });
      if (!r.ok) return;
      const data = await r.json();
      setStatus(data);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    refreshStatus();
    const t = setInterval(refreshStatus, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const steps = resp?.steps ?? [];
    if (Array.isArray(steps) && steps.length) setStepsLive(steps);
  }, [resp]);

  useEffect(() => {
    if (!resp) return;
    const finalText = buildAssistantSummary(resp);
    setChat((prev) => {
      const base = Array.isArray(prev) ? prev : [];
      const out = base.map((m) => (m?.role === "assistant" ? { ...m, pending: false, text: finalText } : m));
      // If no assistant bubble exists (edge case), create it.
      if (!out.some((m) => m?.role === "assistant")) out.push({ role: "assistant", text: finalText });
      return out;
    });
  }, [resp, isPromptOnlyPlaceholder, asOfBySymbol]);

  useEffect(() => {
    if (!localMode) {
      // Keep state consistent: advanced toggles only apply to local-answer mode.
      if (useRag) setUseRag(false);
      if (indexRag) setIndexRag(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localMode]);

  async function onCopyChatGpt() {
    if (!chatGptPasteText) return;
    await copyToClipboard(chatGptPasteText);
    setCopied("Copied ChatGPT-ready prompt to clipboard.");
    setTimeout(() => setCopied(""), 2500);
  }

  async function onCopyChatGptOptimized() {
    if (!chatGptOptimizedText) return;
    await copyToClipboard(chatGptOptimizedText);
    setCopied("Copied ChatGPT-optimized prompt to clipboard.");
    setTimeout(() => setCopied(""), 2500);
  }

  async function onCopyDebugJson() {
    if (!exportJsonText) return;
    await copyToClipboard(exportJsonText);
    setCopied("Copied export_messages JSON (debug) to clipboard.");
    setTimeout(() => setCopied(""), 2500);
  }

  return React.createElement(
    "div",
    { className: "app" },
    React.createElement(
      "div",
      { className: "topbar" },
      React.createElement(
        "div",
        { className: "title" },
        React.createElement(
          "div",
          { className: "titleRow" },
          React.createElement("h1", null, "Stock Assistant"),
          React.createElement(
            "label",
            { className: "devToggle" },
            React.createElement("input", {
              type: "checkbox",
              checked: devMode,
              onChange: (e) => setDevMode(e.target.checked),
            }),
            "Dev mode"
          )
        ),
        React.createElement(
          "p",
          null,
          "React UI + FastAPI backend. Prompt-only mode matches the Streamlit “Export to GPT” workflow."
        )
      ),
      React.createElement(
          "div",
          { className: "statusStrip" },
          React.createElement(
            "button",
            { className: "btn", onClick: () => setSettingsOpen((v) => !v) },
            settingsOpen ? "Close settings" : "Settings"
          ),
          React.createElement(
            "div",
            { className: "statusPill" },
            "API ",
            React.createElement(
              "span",
              { className: (status?.api_ok ?? false) ? "ok" : "bad" },
              (status?.api_ok ?? false) ? "OK" : "DOWN"
            )
          ),
          React.createElement(
            "div",
            { className: "statusPill" },
            "Ollama ",
            React.createElement(
              "span",
              { className: (status?.ollama_reachable ?? false) ? "ok" : "warn" },
              (status?.ollama_reachable ?? false) ? "READY" : "NOT RUNNING"
            )
          )
      )
    ),

    settingsOpen
      ? React.createElement(
          "div",
          { className: "panel settingsBar" },
          React.createElement("div", { className: "settingsTitle" }, "Settings"),
          React.createElement(
            "div",
            { className: "settingsRow" },
            React.createElement(
              "div",
              { className: "settingItem" },
              React.createElement("div", { className: "settingLabel" }, "Response mode"),
              React.createElement(
                "label",
                { className: "switch" },
                React.createElement("input", {
                  type: "checkbox",
                  checked: promptOnly,
                  onChange: (e) => setPromptOnly(e.target.checked),
                }),
                React.createElement("span", null, promptOnly ? "Prompt export only" : "Local answer + export")
              )
            ),
          React.createElement(
            "div",
            { className: "settingItem" },
            React.createElement("div", { className: "settingLabel" }, "Prompt size"),
            React.createElement(
              "select",
              {
                className: "select",
                value: promptSize,
                onChange: (e) => setPromptSize(String(e.target.value || "large")),
                disabled: loading,
              },
              React.createElement("option", { value: "small" }, "Small (compact live data)"),
              React.createElement("option", { value: "medium" }, "Medium"),
              React.createElement("option", { value: "large" }, "Large (default)")
            )
          ),
            localMode
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(
                    "div",
                    { className: "settingItem" },
                    React.createElement("div", { className: "settingLabel" }, "Retrieval context"),
                    React.createElement(
                      "label",
                      { className: "switch" },
                      React.createElement("input", {
                        type: "checkbox",
                        checked: useRag,
                        onChange: (e) => setUseRag(e.target.checked),
                      }),
                      React.createElement("span", null, useRag ? "Enabled (RAG)" : "Disabled")
                    )
                  ),
                  React.createElement(
                    "div",
                    { className: "settingItem" },
                    React.createElement("div", { className: "settingLabel" }, "Indexing"),
                    React.createElement(
                      "label",
                      { className: "switch" },
                      React.createElement("input", {
                        type: "checkbox",
                        checked: indexRag,
                        onChange: (e) => setIndexRag(e.target.checked),
                      }),
                      React.createElement("span", null, indexRag ? "Index metrics to retrieval store" : "No indexing")
                    )
                  )
                )
              : React.createElement(
                  "div",
                  { className: "settingItem" },
                  React.createElement("div", { className: "settingLabel" }, "Advanced"),
                  React.createElement(
                    "div",
                    { className: "emptySub" },
                    "Retrieval and indexing are available only when Local answer + export is enabled."
                  )
                )
          )
        )
      : null,

    // For normal users, avoid extra warnings outside the chat.
    // Dev mode still surfaces this under the technical cards.

    React.createElement(
      "div",
      { className: "panel chatPanel" },
          chat.length
            ? React.createElement(
                "div",
                { className: "chatList" },
                chat.map((m, i) =>
                  React.createElement(
                    "div",
                    { key: i, className: m.role === "user" ? "msg user" : "msg assistant" },
                    React.createElement(
                      "div",
                      { className: "bubble", "data-pending": m?.pending ? "true" : "false" },
                      m.text
                    )
                  )
                )
              )
            : React.createElement(
                "div",
                { className: "emptyState" },
                React.createElement("div", { className: "emptyTitle" }, "Ask a question to generate a grounded prompt."),
                React.createElement(
                  "div",
                  { className: "emptySub" },
                  "You can export the full prompt to ChatGPT in one click. Not financial advice."
                )
              ),
          copied ? React.createElement("div", { className: "meta" }, copied) : null,
          exportAvailable
            ? React.createElement(
                "div",
                { className: "meta" },
                `Prompt size: ${promptSize}. Export length: ${exportChars.toLocaleString()} chars (~${exportTokEst.toLocaleString()} tokens).`,
                exportTooLarge ? React.createElement("span", { className: "warnText", style: { marginLeft: 10 } }, "Large prompt — may be slow or hit limits.") : null
              )
            : null,
          fetchErrorSymbols.length
            ? React.createElement(
                "div",
                { className: "meta" },
                React.createElement(
                  "span",
                  { className: "warnText" },
                  `Live fetch had issues for: ${fetchErrorSymbols.join(", ")}. Prompt may be incomplete for those symbols.`
                )
              )
            : null,
          exportAvailable
            ? React.createElement(
                "div",
                { className: "exportBar" },
                React.createElement(
                  "button",
                  { className: "btn primary", onClick: onCopyChatGpt, disabled: !exportAvailable },
                  "Copy entire prompt"
                ),
                React.createElement(
                  "button",
                  { className: "btn", onClick: onCopyChatGptOptimized, disabled: !chatGptOptimizedText },
                  "Copy ChatGPT prompt"
                ),
                React.createElement(
                  "span",
                  { className: "exportHint" },
                  "“Copy entire” includes role headers; “Copy ChatGPT prompt” is a cleaner single-block paste."
                )
              )
            : null,
          React.createElement(
            "div",
            { className: "composer" },
            React.createElement("textarea", {
              value: question,
              placeholder: "Type your question…",
              onChange: (e) => setQuestion(e.target.value),
              onKeyDown: (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onAsk();
                }
              },
            }),
            React.createElement(
              "button",
              { className: "btn primary", disabled: loading || !question.trim(), onClick: onAsk },
              loading ? "Working…" : "Ask"
            )
          )
    ),

    // Below-chat details are for dev mode only.
    devMode && resp
      ? React.createElement(
          "div",
          { className: "panel", style: { marginTop: 14 } },
          React.createElement(
            "div",
            { className: "devHeader" },
            React.createElement("div", { className: "calloutTitle" }, "Developer details"),
            React.createElement(
              "div",
              { className: "devSub" },
              "Step-by-step pipeline trace, timings, retrieval hits, and raw export payloads."
            )
          ),
          useRag && status && !status.ollama_reachable
            ? React.createElement(
                "div",
                { className: "meta" },
                React.createElement(
                  "span",
                  { className: "warnText" },
                  "RAG is ON but Ollama is not reachable (embeddings may fail)."
                )
              )
            : null,
          React.createElement(
            "div",
            { className: "cardGrid" },
            React.createElement(
              "div",
              { className: "card span8" },
              React.createElement("div", { className: "cardTitle" }, "Steps"),
              (stepsLive || []).length
                ? React.createElement(
                    React.Fragment,
                    null,
                    React.createElement(
                      "div",
                      { className: "stepList" },
                      (stepsExpanded ? stepsLive : (stepsLive || []).slice(0, 6)).map((s, i) =>
                      React.createElement(
                        "div",
                        {
                          key: i,
                          className: "stepItem",
                          "data-kind": stepKind(s?.label),
                        },
                        React.createElement(
                          "div",
                          { className: "stepLabelRow" },
                          React.createElement("span", { className: "stepIdx" }, String(i + 1)),
                          React.createElement("span", { className: "stepLabel" }, normalizeStepLabel(s?.label) || "(step)")
                        ),
                        s?.detail ? React.createElement("div", { className: "stepDetail" }, s.detail) : null
                      )
                      )
                    ),
                    (stepsLive || []).length > 6
                      ? React.createElement(
                          "div",
                          { className: "controls" },
                          React.createElement(
                            "button",
                            { className: "btn", onClick: () => setStepsExpanded((v) => !v) },
                            stepsExpanded ? "Show fewer" : "Show all"
                          )
                        )
                      : null
                  )
                : React.createElement("div", { className: "emptySub" }, "No steps recorded.")
            ),
            React.createElement(
              "div",
              { className: "card span4" },
              React.createElement("div", { className: "cardTitle" }, "Timings"),
              timingEntries.length
                ? React.createElement(
                    "div",
                    { className: "chipRow" },
                    timingEntries.map((e) =>
                      React.createElement(
                        "span",
                        { key: e.k, className: "chip" },
                        React.createElement("span", { className: "chipKey" }, e.k),
                        React.createElement("span", { className: "chipVal" }, String(e.v), "s")
                      )
                    )
                  )
                : React.createElement("div", { className: "emptySub" }, "No timings.")
            ),
            React.createElement(
              "div",
              { className: "card span12" },
              React.createElement("div", { className: "cardTitle" }, "RAG hits"),
              ragHitCards.length
                ? React.createElement(
                    "div",
                    { className: "ragGrid" },
                    ragHitCards.map((h, i) =>
                      React.createElement(
                        "div",
                        { key: i, className: "ragCard" },
                        React.createElement(
                          "div",
                          { className: "ragTop" },
                          React.createElement("div", { className: "ragTitle" }, h.title),
                          h.distance != null
                            ? React.createElement(
                                "div",
                                { className: "ragBadge" },
                                "d=",
                                String(h.distance).slice(0, 6)
                              )
                            : null
                        ),
                        h.distance != null
                          ? null
                          : null,
                        h.excerpt ? React.createElement("div", { className: "ragExcerpt" }, h.excerpt) : null
                      )
                    )
                  )
                : React.createElement("div", { className: "emptySub" }, "No hits.")
            ),
            React.createElement(
              "div",
              { className: "card span6" },
              React.createElement("div", { className: "cardTitle" }, "Prompt preview (payload only)"),
              React.createElement("pre", { className: "promptPreview" }, promptPreview || "")
            )
          )
        )
      : null
  );
}

createRoot(document.getElementById("root")).render(React.createElement(App));

