# Stock Assistant — brand & UI system

This document defines how the product should look and sound in marketing and in the UI.

- **Legacy UI:** Streamlit (`src/streamlit_app.py`) + `.streamlit/config.toml`
- **Current UI:** React (`frontend/`) served by FastAPI (`src/api/app.py`)

## Positioning

**Stock Assistant** is an **intelligent prompt-generation system** that **maximizes LLM capabilities for financial reasoning**, grounded on **reliable data**, **efficient retrieval**, and **professional-grade outputs** (including export to external GPT). The product should feel **precise and readable**, not flashy or “trading bro” hype.

**Technical one-liner (README, decks, specs):** An intelligent prompt-generation system that maximizes LLM capabilities for financial reasoning—grounded on reliable data, efficient retrieval, and professional-grade outputs.

**UI principles (applies to Streamlit + React):**
- **Chat-first**: the assistant message is the source of truth for success/failure (“fetched live data…”, “no tickers found…”, “retrieval unavailable…”).
- **Export-first**: the user’s primary action is **Copy entire prompt** (no scrolling required; keep the copy CTA close to the composer).
- **Prompt-only default**: keep the app fast; local answering is an optional mode for power users.
- **Dev details gated**: technical logs are visible only in **Dev mode**.

**Legacy Streamlit UI (`streamlit_app.py`):** Sidebar controls; chat; technical expander; “Export to GPT” with preview + copy; warm-up spinners.

**Current React UI (`frontend/`):** Single-turn chat experience; horizontal settings bar; sticky export action; Dev mode renders compact “technical cards” (steps, timings, retrieval hits, payload preview).

## Colour system — Option A (default)

| Role | Hex | Usage |
|------|-----|--------|
| **Base** | `#0F172A` | App background; main canvas. Deep slate — analytical, high contrast without harsh black. |
| **Surface** | `#111C33` | Cards, panels, secondary regions — one step above base for subtle layering. |
| **Primary** | `#3B82F6` | Controlled professional blue: primary actions, key links, structural anchors. Use sparingly so it always reads as “important”. |
| **Accent** | `#22D3EE` | Crisp cyan: highlights, data emphasis, “signal moments” in copy or UI (hover on links, badges, positive feedback). Reinforces clarity and intelligence. |
| **Text** | `#E5E7EB` | Primary body and headings — soft off-white for long analytical sessions (avoid pure `#FFFFFF` for comfort). |

### Extended palette (UI implementation)

| Role | Hex | Usage |
|------|-----|--------|
| **Muted text** | `#94A3B8` | Secondary captions, expander header tone (when overridden in CSS). |
| **Subtitle** | `#CBD5E1` | Secondary headings / subtitles when used in custom HTML. |
| **Border / track** | `#1E293B` | Card borders, progress bar track, subtle dividers. |
| **Danger / error** | `#F87171` | Copy failures, destructive feedback (sparingly). |

### Rules

1. **Blue = action / trust / navigation.** Buttons, primary links, focus that must be obvious.
2. **Cyan = signal / insight / emphasis.** Metrics callouts, hover states, success copy for “got it” moments — not for every control.
3. **Surfaces over decoration.** Prefer the base + surface lift to gradients or extra colours.
4. **Accessibility:** keep cyan body text large or on strong backgrounds; small cyan on `#111C33` can be marginal depending on weight — prefer cyan for accents, not long paragraphs.

## Colour system — Option A2 (recommended for the React UI)

Option A is intentionally restrained, but the React UI can feel flat if base/surface are too close. Option A2 keeps the same intent while adding depth.

| Role | Hex | Usage |
|------|-----|--------|
| **Base** | `#0A0F1A` | Near-black slate background. Avoid pure black; this keeps blue/cyan professional, not neon. |
| **Surface** | `#0F172A` | Panels/cards. Creates a visible lift over Base. |
| **Primary** | `#3B82F6` | Primary actions (Ask, Copy). |
| **Accent** | `#22D3EE` | Signal moments only (status “READY”, key highlights). |
| **Text** | `#E5E7EB` | Main text. |
| **Muted** | `#94A3B8` | Secondary text. |
| **Border** | `#243042` | Slightly more neutral than `#1E293B` for a product-like feel. |

### Rules (Option A2)
- **Do not use pure black (`#000`)** for the main canvas.
- Prefer **neutral gray-blue surfaces** over adding more colors.
- Blue stays “action”; cyan stays “signal”.

## Typography

- Streamlit theme font: **sans serif** (system stack). Keep UI copy concise; analytical tone in product strings.
- Marketing: same family idea — clean geometric sans if custom assets are added later.

## Logo & icon

- **Assets:** `assets/icon.png`, `assets/icon.ico` (page icon / Windows bundle).
- **Guidance:** favicon and sidebar scale need a **simple mark** (monoline spark, chart stroke, or abstract “signal”). Full wordmarks belong on landing/about, not the 16–32px tray.
- **Colour:** mark on slate (`#0F172A`) or surface (`#111C33`); accent cyan (`#22D3EE`) or primary blue (`#3B82F6`) for the glyph — one dominant colour per lockup.

## Feature pillars (messaging)

**Messaging pillars (README / marketing; not rendered as a strip in the classic UI):**

1. **Clear answers** — Straight explanations without drowning the user in jargon.
2. **Live numbers** — Prices and key figures from up-to-date feeds, not guesswork.
3. **Smart context** — Background when it helps; live data always comes first.
4. **Works with your AI** — Export the full prompt to ChatGPT or another assistant in one step.
5. **Careful tone** — No hype; not personal financial advice.

**Technical mapping (for README “how it works”, engineers, prompts):** structured JSON user turns; system hierarchy (live Yahoo metrics vs Chroma RAG vs chat); session warm-up indexing; **Export to GPT** payload; optional **Ollama**; prompt-only mode.

**Disclaimer (always visible where appropriate):** not financial advice.

## Streamlit constraints (what we own vs accept)

Streamlit is not a full design system. We align what we can and avoid fighting the framework.

| Layer | What we control |
|--------|------------------|
| **Theme** | `[theme]` in `.streamlit/config.toml`: base, surface, primary, text — matches the table above. |
| **Accent** | Streamlit has no separate “accent” token; cyan is applied via **small custom CSS** (e.g. link hover) and intentional markdown/HTML where needed. |
| **Layout** | Columns, expanders, chat — good for “structured analytical”; bespoke dashboards need more CSS or custom components. |
| **Widgets** | Most inputs pick up theme colours; some third-party or internal chrome may not match perfectly — acceptable if overall chrome matches Option A. |

When adding new UI, default to **theme tokens** first; introduce **cyan** only for deliberate emphasis.

## React UI constraints (what we own vs accept)

React gives us full control, but we should still keep the product restrained:
- Prefer **layout hierarchy** (spacing, grouping, cards) over decoration.
- Keep most pages at a **max width** for readability; use responsive grids inside Dev mode to avoid wasted space.
- Any new feature should answer: “Does this make export easier, faster, or clearer?”

## Changelog

- **Initial:** Option A palette documented; theme wired in `config.toml` + minimal CSS in `streamlit_app.py`.
- **Positioning:** Intelligent prompt-generation line + five pillars documented here and in `README.md` (classic UI uses the long **caption** under the title instead of a pillar strip).
- **Welcome / boot:** Reverted to classic spinners + `st.rerun()` after each answer (see classic UI note above).
- **Build output:** Publish staging lives under `%TEMP%/StockAssistant_build/` only; `dist/StockAssistant_staging` is removed when present (legacy intermediate folder).
- **Streamlit:** Restored original feature set: **Technical details (last reply)** always available; **Export to GPT (last reply)** + Preview + copy button; minimal link CSS only.
