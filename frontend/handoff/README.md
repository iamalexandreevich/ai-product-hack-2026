# Handoff: OPENMAGI landing (AgentGate gate for coding agents)

## Overview
Single-page marketing/landing site for OPENMAGI — a policy gate (`POST /v1/decide` → `allow | deny | ask`) that sits between any coding agent and the OS. The page sells one idea (the agent can do anything, now it has to ask), lets a visitor copy the install command, shows a scroll-driven session where a `deny` does not break the agent's task, exposes benchmark placeholders, an offline "policy probe" (type a shell command → verdict), supported harnesses and a footer CTA.

Product/repo context: `ai-product-hack-2026` (service = FastAPI gate, profile `default-dev`, contracts in `contracts/`). The page does NOT call the API; the probe is a client-side regex demo.

## About the design files
`OPENMAGI.dc.html` (+ `support.js`) is a **design reference built in HTML** — a prototype of intended look and behaviour, not production code. Recreate it in the target stack (Next.js/Astro/plain Vite — pick what the repo already uses; there is no frontend yet, so Astro or Next static export is a sensible default). Keep the visual spec below pixel-close; reuse `data.js` (dictionaries, rules, terminal script) verbatim.

## Fidelity
**High-fidelity.** Colors, type, spacing, copy and interactions are final. Follow the OPENMAGI design system v1 (summarised in Design tokens). Rules that must hold everywhere: **radius 0, no gradients, no shadows, max two background colors per screen (bg + panel), lime never as a large fill, text on colored plaques is always bg (#0B0714).**

## Page structure (single route, RU default, EN toggle)
Viewport: fluid, 1120px content max-width, side padding `clamp(24px,4vw,48px)`. Sections stack with top padding `clamp(96px,12vh,160px)`. Body font JetBrains Mono 400; display font Big Shoulders Display 900 UPPERCASE; kanji Noto Sans JP 900. Page background: `#0B0714` + repeating hex-grid SVG (56×100px tile, stroke #7B4FD6 @ .16 — exact data-URI in `tokens.css`).

### 0. Header (fixed, 56px)
- Left: wordmark, Big Shoulders 900 22px, letter-spacing .01em — `OPEN` #F2EEFB + `MAGI` #7B4FD6.
- Right (flex, gap 6px): "GitHub ↗" link (13px, #A397C2 → #F2EEFB on hover), language toggle button `EN / RU` (13px, active = #F2EEFB, inactive = #A397C2, slash #2A1F45).
- Transparent at top; after scrolling past y>8: background `color-mix(in srgb,#0B0714 72%,transparent)`, bottom border 1px #2A1F45. Transition .3s.

### 1. Hero (min-height 92vh, centered column, gap 30px, padding 130px 24px 72px)
- H1: Big Shoulders 900, `clamp(56px,9vw,132px)`, line-height .9, letter-spacing .01em, uppercase, centered. Two lines: line 1 #F2EEFB (`h1a`), line 2 #A397C2 (`h1b`) with the last word (`h1c`) in #7B4FD6.
- Lead: JetBrains Mono `clamp(17px,1.5vw,20px)`/1.55, #A397C2, max-width 640px.
- Install command button: bg #0B0714, border 1px #2A1F45, padding 18px 20px, mono 16px; `$` in #A397C2, then the command, then a copy icon (18px, #A397C2) → on success a lime check (#B6FF2E) for 2s. Hover: border #B6FF2E. Click copies `curl -fsSL https://openmagi.dev/install.sh | sh` (clipboard API, fallback = select text + execCommand). aria-live region announces "Скопировано/Copied".
- Note under button: 13px #A397C2 (`note`).
- Appear animation on all three: opacity 0→1, translateY(24px→0), .6s cubic-bezier(.16,1,.3,1), staggered 0/.08/.16s, triggered by IntersectionObserver (threshold .15) once.

### 2. Session terminal (scroll-driven, section height 250vh, sticky 100vh inner)
- Section bg #0B0714. Inner: centered column max-width 760px, gap `clamp(16px,3vh,36px)`.
- Label "01 — СЕССИЯ" Big Shoulders 900 `clamp(20px,2vw,26px)`, letter-spacing .04em, #B6FF2E. H2 Big Shoulders 900 `clamp(28px,min(4vw,5vh),56px)`/.92 uppercase. Sub 14–17px #A397C2 max 560px.
- Terminal window: bg #0B0714, border 1px #2A1F45. Title bar: 3 dots 10px (rgba(255,255,255,.16)), center path text 12px #7D7391 ("~/work/api"). Body: padding 26px `clamp(16px,3vw,28px)`, mono `clamp(12px,1.4vw,14px)`/1.6, #A397C2, overflow-y auto, auto-scrolls to bottom as lines appear.
- Lines (from `LINES` in data.js) reveal by scroll progress `p = -rect.top / (rect.height - innerHeight)`; line i is shown when `p >= LINES[i].th`. Each line fades in .45s (opacity + translateY 8→0).
  - prompt: `> text` (#F2EEFB, ">" #7D7391), margin-bottom 10px
  - step: `● text` (#F2EEFB, bullet #7D7391), margin-top 6px
  - allow: indented 1.4em, flex space-between: `承認 allow` (#B6FF2E, kanji Noto JP 900) … latency (#7D7391)
  - gate (deny plaque): margin 12px 0 12px 1.4em, bg #FF2E4A, text #0B0714, padding 10px 16px, grid `auto 1fr`, gap 4px 14px. Left: `否定` Noto JP 900 22px. Right column: "ЗАПРЕЩЕНО"/"DENIED" Big Shoulders 900 18px ls .04em; then 10px lines `deny · recursive delete outside the declared work scope` and `suggest: move to .trash/ and let the user confirm`.
  - done: `● text` with lime bullet.
- prefers-reduced-motion: section becomes static, all lines visible.

### 3. Benchmark (optional, flag `SHOW_METRICS`)
- Label "02 — БЕНЧМАРК" (lime, as above), H2 centered `clamp(32px,4vw,56px)`.
- Grid `repeat(auto-fit,minmax(160px,1fr))`, gap `clamp(12px,2vw,24px)`. Card: padding 28px 24px, bg #17102A, border 1px #2A1F45. Key 13px uppercase ls .06em #A397C2 (ASR / FP / p95 / Friction); value `clamp(40px,4.5vw,64px)` #F2EEFB — currently the placeholder `——` (no benchmark run yet); label 13px #A397C2.
- Footnote 13px #A397C2 centered: "Методика, размер выборки и дата прогона: benchmark/ — прогона ещё нет…" with `benchmark/` underlined link to the repo folder.
- When real numbers exist: format ASR/FP as `x.x%`, p95 as `NNNms`, Friction as `x.x`; count-up 600ms ease-out on first view (skip under reduced motion).

### 4. Policy (rules + offline probe)
- Label "03 — ПОЛИТИКА", H2, sub (17px #A397C2 max 560px) centered.
- Container: grid `repeat(auto-fit,minmax(360px,1fr))`, bg #17102A, border 1px #2A1F45.
  - Left column (padding `clamp(20px,3vw,32px)`, border-right 1px #2A1F45): 4 groups (Разрушительные / Утечка данных / Выход за область / Необратимые). Group title 13px uppercase #A397C2. Rule button: full width, padding 12px 14px, transparent bg, border 1px #2A1F45, hover bg #0B0714 + border #7B4FD6; left = command label mono 14px #F2EEFB; right = `<kanji> <decision>` 10px ls .1em in verdict color (allow #B6FF2E · ask #FFC93A · deny #FF2E4A), kanji 12px Noto JP 900. Click → fills probe with `rule.example`.
  - Right column (sticky top 72px): input row (bg #0B0714, border 1px #2A1F45, padding 14px 16px, mono 15px, `$` prefix #A397C2, placeholder "попробуйте команду…"). Below, one of three states:
    - idle: dashed 1px #2A1F45 box, padding 18px, 14px #A397C2 "Здесь появится вердикт." + "Попробуйте:" + 3 example chips (mono 13px, padding 5px 10px, border 1px #2A1F45, hover border #7B4FD6): `rm -rf ./build`, `cat .env`, `git push --force`.
    - unknown: same box, solid border, text "Это офлайн-демо знает ~30 команд. Настоящий гейт оценивает любую."
    - result: verdict row — bg #17102A, border-left 6px in verdict color, padding 12px 16px, gap 12px. Inside: verdict plaque (align-self start, bg = verdict color, text #0B0714, padding 10px 16px, grid auto/auto: kanji 22px spanning 2 rows; name Big Shoulders 900 18px ls .04em — РАЗРЕШЕНО / ЗАПРЕЩЕНО / НА РАССМОТРЕНИИ (EN: ALLOWED / DENIED / PENDING); 10px decision word). Then 2-col grid `reason:` / `suggest:` (labels #A397C2, values #F2EEFB, mono 13px/1.6). `suggest` row omitted when empty.
  - Evaluation: normalise input (collapse whitespace, strip leading `$`, strip `sudo`), test `RULES` in order, first regex match wins; `safe-read` rule (allow) is matched but never listed. No match → unknown.

### 5. Harnesses
- Label "04 — ХАРНЕССЫ", H2, sub 17px #A397C2, all centered.
- Grid `repeat(auto-fit,minmax(220px,1fr))`, gap 24px. Tile = link: column, padding 32px 20px, border 1px #2A1F45, color #A397C2; hover color+border #7B4FD6. Content: monogram Big Shoulders 900 40px (KI / OC / PI), name 9px uppercase ls .2em #7D7391 (Kilo Code / OpenCode / Pi), then mono 13px path line — currently placeholder "путь к хуку: уточняется" (replace with real hook config paths when known). No third-party logos.

### 6. Footer
- Centered column gap 40px: the same install-command button as hero (second copy target), then a row (border-top 1px #2A1F45, padding-top 24px, 13px #A397C2, gap 8px 24px): GitHub · Лицензия MIT · Сделано командой AgentGate · © 2026.

## Interactions & behaviour
- Smooth scroll: Lenis (lerp .1) — optional; native scroll is acceptable. Disable under `prefers-reduced-motion`.
- Appear-on-scroll: every section block `[data-appear]` — IO threshold .15, one-shot, .6s cubic-bezier(.16,1,.3,1), opacity+translateY(24px). Reduced motion → visible immediately.
- Header opaque state: IO on a 1px sentinel at page top (rootMargin -8px).
- Terminal progress: rAF loop only while section intersects; `shown = count(LINES.th <= p)`; auto-scroll log container to bottom on change.
- Copy: two independent buttons, 2s "copied" state each, aria-live text.
- Language: `EN/RU` toggle, persisted in `localStorage['mg-lang']` (default `ru`), sets `<html lang>`. All strings in `DICT` (data.js).
- Focus ring: 2px #B6FF2E, offset 3px. Buttons ≥ 44px tall on touch.
- Responsive: single column below ~760px (auto-fit grids), H1 scales via clamp, command text scrolls horizontally inside its box (`max-width: calc(100vw - 140px)`).

## State
`lang: 'ru'|'en'`, `scrolled: bool`, `copied: null|'hero'|'foot'`, `liveMsg: string`, `shown: number` (terminal lines), `probe: string`, `appeared: Set<sectionId>`. Build-time flag `SHOW_METRICS` (default true). No data fetching.

## Design tokens (OPENMAGI DS v1)
Colors — bg #0B0714 · panel #17102A · line #2A1F45 · line-strong #3A2A60 · text #F2EEFB · text-2 #A397C2 · text-3 #7D7391 · on-purple #D9CCFF · brand #7B4FD6 · lime #B6FF2E · orange #FF6A00 · deny #FF2E4A · ask #FFC93A. Verdict colors: allow = lime, deny = #FF2E4A, ask = #FFC93A. Text on colored plaques = bg.
Type — Big Shoulders Display 900 (display, uppercase), JetBrains Mono 400/700 (everything else), Noto Sans JP 900 (kanji 承認/否定/審議中). Google Fonts URL in `tokens.css`.
Radius 0 · shadows none · gradients none. Thick rule = 6px brand. Service strings: 10–11px, letter-spacing .2em, uppercase, ` · ` separator.
Spacing used: 4 / 8 / 12 / 14 / 16 / 18 / 20 / 24 / 28 / 30 / 32 / 40 / 48; section gap clamp(96px,12vh,160px).
Easing: cubic-bezier(.16,1,.3,1); durations .2s (hover) / .3s (header) / .45s (terminal lines) / .6s (appear).

## Assets
No raster images. Hex-grid background is an inline SVG data URI (tokens.css). Copy/check icons are inline 18px SVG strokes. Fonts from Google Fonts.

## Open items (placeholders in the prototype)
- Install domain `openmagi.dev` and GitHub links (`github.com/agentgate`) — confirm.
- Metric values (`——`) — wire to benchmark output.
- Harness hook paths — "уточняется".
- `reason`/`suggest` strings in RULES — replace with real `/v1/decide` responses.

## Files
- `OPENMAGI.dc.html` — the prototype (open in a browser; needs `support.js` next to it).
- `support.js` — prototype runtime, not for production.
- `data.js` — DICT (RU/EN strings), RULES (probe regexes + verdicts), LINES (terminal script), METRICS, HARNESSES, KANJI. Reuse as-is.
- `tokens.css` — CSS custom properties, font import, hex-grid background.
