# Общая картина OPENMAGI — материалы для Claude Design

Источник: артефакт `https://claude.ai/code/artifact/d21e7785-d6e3-46b2-a5f2-53e9ef211498` («Общая картина OPENMAGI»). Одна страница в редакционном стиле: мастхед с вордмарком, заголовок, вводка, абзац, одна SVG-схема с подписью, одна таблица, заключительный абзац.

## 1. Содержание страницы

**Вордмарк:** `OPEN` (зелёный) + `MAGI` (фиолетовый), Chakra Petch 700, разрядка 0.06em.

**Заголовок:** Что делает инсталлер

**Вводка:** Пять харнессов, три способа, один результат. Ваша команда остаётся вашей — рядом появляется вторая, `<харнесс>-gate`, и только она ходит через гард.

**Абзац 1:** Инсталлер всегда производит две вещи: обёртку с именем `<харнесс>-gate` и место, где зарегистрирован плагин, — такое, куда харнесс попадает только через переменную или флаг, которые выставляет одна лишь обёртка. Дальше различия заканчиваются: за швом идёт общее ядро и один HTTP-контракт.

**Схема (см. §3).** Подпись под схемой: Разница между харнессами заканчивается на плагине. Поэтому «поддержать ещё один харнесс» значит написать один адаптер шва, а не второй гард.

**Таблица:**

| Харнесс | Чем переключается | Шов для решения | Патч |
|---|---|---|---|
| opencode | `OPENCODE_CONFIG_CONTENT` | `permission.ask` | да |
| kilo | `KILO_CONFIG_CONTENT` | `permission.ask` | да |
| codex | `CODEX_HOME` | `PreToolUse` | нет |
| pi | `PI_CODING_AGENT_DIR` | `tool_call` | нет |
| dsh | `--profile gate` | `pre-execute` | нет |

**Абзац 2:** Патч нужен только двоим. У codex, pi и dsh шов есть штатно; у opencode и kilo он тоже объявлен — `permission.ask` описан и в API плагинов, и в документации, — но апстрим его никогда не вызывает. Патч добавляет ровно недостающий вызов, около пятидесяти строк.

## 2. Дизайн-токены

| Токен | Светлая | Тёмная | Роль |
|---|---|---|---|
| `--ground` | `#F5F3F8` | `#131019` | фон страницы |
| `--surface` | `#FFFFFF` | `#1B1725` | карточка figure |
| `--sunk` | `#EEEAF4` | `#17131f` | фон `code` |
| `--ink` | `#1A1622` | `#ECE8F3` | основной текст |
| `--muted` | `#6B647A` | `#9C93AE` | вторичный текст, подписи |
| `--rule` | `#DDD6E8` | `#302941` | линии, рамки |
| `--rule-soft` | `#E9E3F1` | `#262034` | мягкие разделители |
| `--green` | `#2E7D33` | `#8AD96D` | «наше»: обёртка, плагин, ядро |
| `--green-soft` | `#E6F3E4` | `#1D2A1B` | заливка «наших» блоков |
| `--violet` | `#6B41C4` | `#AE8DF2` | «их»: Гард |
| `--violet-soft` | `#EFE8FB` | `#251C3A` | заливка блока Гарда |

**Шрифты:** display — Chakra Petch (500/600/700); body — Source Serif 4 (400/600), 17px / 1.62; mono — JetBrains Mono (400/500).
Ширина контента 1000px, мера текста 66ch. Google Fonts: `https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap`

**Семантика цвета в схеме:** зелёная обводка и заливка — компоненты OPENMAGI (обёртка, плагин, общее ядро, три способа установки); фиолетовая — внешний Гард, отвечающий `allow · ask · deny`. Пунктирная серая линия отделяет дорожку «Установка» от дорожки «Работа».

## 3. Схема (автономный SVG, 980×372)

Стили встроены, внешний CSS не нужен. Две дорожки: сверху «Установка» (`openmagi install` → три способа → обёртка `~/.local/bin/<харнесс>-gate`), снизу «Работа» (`<харнесс>-gate` → шов → плагин харнесса → общее ядро → HTTP → Гард).

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 980 372" role="img" aria-label="Инсталлер находит харнессы и применяет один из трёх способов подключения плагина, создавая обёртку с именем харнесс-гейт; при запуске обёртка ведёт через шов харнесса в общее ядро и к гарду, который отвечает allow, ask или deny">
  <style>
    .bx-ours { fill: #E6F3E4; stroke: #2E7D33; stroke-width: 1.4; }
    .bx-theirs { fill: #EFE8FB; stroke: #6B41C4; stroke-width: 1.4; }
    .ln-ours { stroke: #2E7D33; stroke-width: 1.6; fill: none; }
    .ln-off { stroke: #DDD6E8; stroke-width: 1.4; fill: none; stroke-dasharray: 5 4; }
    .hd-ours { fill: #2E7D33; }
    .tx { font-family: "Chakra Petch", "Trebuchet MS", sans-serif; font-weight: 600; font-size: 13px; fill: #1A1622; }
    .tm { font-family: "JetBrains Mono", Menlo, monospace; font-size: 11px; fill: #1A1622; }
    .tmm { font-family: "JetBrains Mono", Menlo, monospace; font-size: 10px; fill: #6B647A; }
    .ts { font-family: "Source Serif 4", Georgia, serif; font-size: 11.5px; fill: #6B647A; }
    .lane { font-family: "Chakra Petch", "Trebuchet MS", sans-serif; font-weight: 600; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; fill: #6B647A; }
    .lane-ours { fill: #2E7D33; }
    .edge { font-family: "Chakra Petch", "Trebuchet MS", sans-serif; font-weight: 600; font-size: 10.5px; fill: #6B647A; }
    .edge-ours { fill: #2E7D33; }
  </style>

        <text class="lane" x="0" y="11">Установка</text>
        <rect class="bx-ours" x="340" y="20" width="300" height="46" rx="4"/>
        <text class="tm" x="490" y="42" text-anchor="middle">openmagi install</text>
        <text class="ts" x="490" y="58" text-anchor="middle">находит харнессы, выбирает способ</text>

        <path class="ln-ours" d="M490 66 V 84 H 150 V 100"/>
        <path class="ln-ours" d="M490 84 V 100"/>
        <path class="ln-ours" d="M490 84 H 830 V 100"/>
        <polygon class="hd-ours" points="146,100 150,108 154,100"/>
        <polygon class="hd-ours" points="486,100 490,108 494,100"/>
        <polygon class="hd-ours" points="826,100 830,108 834,100"/>

        <rect class="bx-ours" x="0" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="150" y="132" text-anchor="middle">переменная окружения</text>
        <text class="ts" x="150" y="152" text-anchor="middle">opencode · kilo — плюс патч сборки</text>

        <rect class="bx-ours" x="340" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="490" y="132" text-anchor="middle">отдельный каталог настроек</text>
        <text class="ts" x="490" y="152" text-anchor="middle">codex · pi</text>

        <rect class="bx-ours" x="680" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="830" y="132" text-anchor="middle">отдельный профиль</text>
        <text class="ts" x="830" y="152" text-anchor="middle">dsh</text>

        <path class="ln-ours" d="M150 166 V 186 H 490"/>
        <path class="ln-ours" d="M830 166 V 186 H 490"/>
        <line class="ln-ours" x1="490" y1="166" x2="490" y2="204"/>
        <polygon class="hd-ours" points="486,204 490,212 494,204"/>

        <rect class="bx-ours" x="290" y="212" width="400" height="46" rx="4"/>
        <text class="tmm" x="490" y="234" text-anchor="middle">~/.local/bin/&lt;харнесс&gt;-gate</text>
        <text class="ts" x="490" y="250" text-anchor="middle">ваша команда осталась вашей</text>

        <line class="ln-off" x1="0" y1="278" x2="980" y2="278"/>

        <text class="lane lane-ours" x="0" y="296">Работа</text>
        <rect class="bx-ours" x="0" y="304" width="200" height="46" rx="4"/>
        <text class="tmm" x="100" y="332" text-anchor="middle">&lt;харнесс&gt;-gate</text>

        <text class="edge edge-ours" x="230" y="322" text-anchor="middle">шов</text>
        <line class="ln-ours" x1="200" y1="328" x2="252" y2="328"/>
        <polygon class="hd-ours" points="252,324 260,328 252,332"/>

        <rect class="bx-ours" x="260" y="304" width="210" height="46" rx="4"/>
        <text class="tm" x="365" y="326" text-anchor="middle">плагин харнесса</text>
        <text class="ts" x="365" y="342" text-anchor="middle">перехват вызова</text>

        <line class="ln-ours" x1="470" y1="328" x2="522" y2="328"/>
        <polygon class="hd-ours" points="522,324 530,328 522,332"/>

        <rect class="bx-ours" x="530" y="304" width="200" height="46" rx="4"/>
        <text class="tm" x="630" y="326" text-anchor="middle">общее ядро</text>
        <text class="ts" x="630" y="342" text-anchor="middle">одно на все пять</text>

        <text class="edge edge-ours" x="760" y="322" text-anchor="middle">HTTP</text>
        <line class="ln-ours" x1="730" y1="328" x2="782" y2="328"/>
        <polygon class="hd-ours" points="782,324 790,328 782,332"/>

        <rect class="bx-theirs" x="790" y="304" width="190" height="46" rx="4"/>
        <text class="tx" x="885" y="324" text-anchor="middle">Гард</text>
        <text class="ts" x="885" y="341" text-anchor="middle">allow · ask · deny</text>
      </svg>
```

## 4. Полная HTML-страница

Оригинальная разметка артефакта с его собственным `<style>` (поддерживает светлую и тёмную тему через `prefers-color-scheme` и `data-theme`). Runtime-скрипты платформы удалены.

```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Общая картина OPENMAGI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">

<style>
  :root {
    --ground: #F5F3F8;
    --surface: #FFFFFF;
    --sunk: #EEEAF4;
    --ink: #1A1622;
    --muted: #6B647A;
    --rule: #DDD6E8;
    --rule-soft: #E9E3F1;
    --green: #2E7D33;
    --green-soft: #E6F3E4;
    --violet: #6B41C4;
    --violet-soft: #EFE8FB;

    --display: "Chakra Petch", "Trebuchet MS", sans-serif;
    --body: "Source Serif 4", Georgia, "Times New Roman", serif;
    --mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;

    --measure: 66ch;
    --wide: 1000px;
  }

  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground: #131019;
      --surface: #1B1725;
      --sunk: #17131f;
      --ink: #ECE8F3;
      --muted: #9C93AE;
      --rule: #302941;
      --rule-soft: #262034;
      --green: #8AD96D;
      --green-soft: #1D2A1B;
      --violet: #AE8DF2;
      --violet-soft: #251C3A;
    }
  }

  :root[data-theme="dark"] {
    --ground: #131019;
    --surface: #1B1725;
    --sunk: #17131f;
    --ink: #ECE8F3;
    --muted: #9C93AE;
    --rule: #302941;
    --rule-soft: #262034;
    --green: #8AD96D;
    --green-soft: #1D2A1B;
    --violet: #AE8DF2;
    --violet-soft: #251C3A;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--ground);
    color: var(--ink);
    font-family: var(--body);
    font-size: 17px;
    line-height: 1.62;
    -webkit-font-smoothing: antialiased;
  }

  .page { max-width: var(--wide); margin: 0 auto; padding: 56px 24px 88px; }

  .masthead {
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding-bottom: 36px;
    border-bottom: 1px solid var(--rule);
  }

  .wordmark {
    font-family: var(--display);
    font-weight: 700;
    font-size: clamp(30px, 6vw, 44px);
    letter-spacing: 0.06em;
    line-height: 1;
    margin: 0;
  }
  .wordmark .open { color: var(--green); }
  .wordmark .magi { color: var(--violet); }

  h1 {
    font-family: var(--display);
    font-weight: 600;
    font-size: clamp(24px, 4.4vw, 34px);
    line-height: 1.2;
    letter-spacing: -0.01em;
    text-wrap: balance;
    margin: 0;
    max-width: 22ch;
  }

  .standfirst { margin: 0; color: var(--muted); font-size: 18px; max-width: var(--measure); }

  section { padding-top: 44px; }

  p { margin: 0 0 16px; max-width: var(--measure); }

  code {
    font-family: var(--mono);
    font-size: 0.85em;
    background: var(--sunk);
    border: 1px solid var(--rule-soft);
    border-radius: 3px;
    padding: 0.08em 0.32em;
    white-space: nowrap;
  }

  figure {
    margin: 26px 0 0;
    padding: 20px 20px 14px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: 6px;
    overflow-x: auto;
  }

  figure svg { display: block; width: 100%; min-width: 700px; height: auto; }

  figcaption {
    margin-top: 14px;
    padding-top: 11px;
    border-top: 1px solid var(--rule-soft);
    font-size: 14.5px;
    line-height: 1.55;
    color: var(--muted);
    min-width: 700px;
  }

  .bx-ours { fill: var(--green-soft); stroke: var(--green); stroke-width: 1.4; }
  .bx-theirs { fill: var(--violet-soft); stroke: var(--violet); stroke-width: 1.4; }

  .ln-ours { stroke: var(--green); stroke-width: 1.6; fill: none; }
  .ln-off { stroke: var(--rule); stroke-width: 1.4; fill: none; stroke-dasharray: 5 4; }

  .hd-ours { fill: var(--green); }

  .tx { font-family: var(--display); font-weight: 600; font-size: 13px; fill: var(--ink); }
  .tm { font-family: var(--mono); font-size: 11px; fill: var(--ink); }
  .tmm { font-family: var(--mono); font-size: 10px; fill: var(--muted); }
  .ts { font-family: var(--body); font-size: 11.5px; fill: var(--muted); }
  .lane {
    font-family: var(--display); font-weight: 600; font-size: 10px;
    letter-spacing: 0.14em; text-transform: uppercase; fill: var(--muted);
  }
  .lane-ours { fill: var(--green); }
  .edge { font-family: var(--display); font-weight: 600; font-size: 10.5px; fill: var(--muted); }
  .edge-ours { fill: var(--green); }

  .table-wrap { overflow-x: auto; margin: 30px 0 0; }
  table { border-collapse: collapse; width: 100%; min-width: 700px; font-size: 14.5px; }
  th, td {
    text-align: left;
    padding: 10px 14px;
    border-bottom: 1px solid var(--rule-soft);
    vertical-align: top;
  }
  th {
    font-family: var(--display);
    font-weight: 600;
    font-size: 10.5px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--rule);
    white-space: nowrap;
  }
  td:first-child { font-family: var(--mono); font-size: 13px; white-space: nowrap; }
  tbody tr:last-child td { border-bottom: none; }
</style>
</head>
<body>
<div class="page">

  <header class="masthead">
    <p class="wordmark"><span class="open">OPEN</span><span class="magi">MAGI</span></p>
    <h1>Что делает инсталлер</h1>
    <p class="standfirst">
      Пять харнессов, три способа, один результат. Ваша команда остаётся вашей — рядом появляется
      вторая, <code>&lt;харнесс&gt;-gate</code>, и только она ходит через гард.
    </p>
  </header>

  <section>
    <p>
      Инсталлер всегда производит две вещи: обёртку с именем <code>&lt;харнесс&gt;-gate</code> и
      место, где зарегистрирован плагин, — такое, куда харнесс попадает только через переменную
      или флаг, которые выставляет одна лишь обёртка. Дальше различия заканчиваются: за швом идёт
      общее ядро и один HTTP-контракт.
    </p>

    <figure>
      <svg viewBox="0 0 980 372" role="img" aria-label="Инсталлер находит харнессы и применяет один из трёх способов подключения плагина, создавая обёртку с именем харнесс-гейт; при запуске обёртка ведёт через шов харнесса в общее ядро и к гарду, который отвечает allow, ask или deny">
        <text class="lane" x="0" y="11">Установка</text>
        <rect class="bx-ours" x="340" y="20" width="300" height="46" rx="4"/>
        <text class="tm" x="490" y="42" text-anchor="middle">openmagi install</text>
        <text class="ts" x="490" y="58" text-anchor="middle">находит харнессы, выбирает способ</text>

        <path class="ln-ours" d="M490 66 V 84 H 150 V 100"/>
        <path class="ln-ours" d="M490 84 V 100"/>
        <path class="ln-ours" d="M490 84 H 830 V 100"/>
        <polygon class="hd-ours" points="146,100 150,108 154,100"/>
        <polygon class="hd-ours" points="486,100 490,108 494,100"/>
        <polygon class="hd-ours" points="826,100 830,108 834,100"/>

        <rect class="bx-ours" x="0" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="150" y="132" text-anchor="middle">переменная окружения</text>
        <text class="ts" x="150" y="152" text-anchor="middle">opencode · kilo — плюс патч сборки</text>

        <rect class="bx-ours" x="340" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="490" y="132" text-anchor="middle">отдельный каталог настроек</text>
        <text class="ts" x="490" y="152" text-anchor="middle">codex · pi</text>

        <rect class="bx-ours" x="680" y="108" width="300" height="58" rx="4"/>
        <text class="tm" x="830" y="132" text-anchor="middle">отдельный профиль</text>
        <text class="ts" x="830" y="152" text-anchor="middle">dsh</text>

        <path class="ln-ours" d="M150 166 V 186 H 490"/>
        <path class="ln-ours" d="M830 166 V 186 H 490"/>
        <line class="ln-ours" x1="490" y1="166" x2="490" y2="204"/>
        <polygon class="hd-ours" points="486,204 490,212 494,204"/>

        <rect class="bx-ours" x="290" y="212" width="400" height="46" rx="4"/>
        <text class="tmm" x="490" y="234" text-anchor="middle">~/.local/bin/&lt;харнесс&gt;-gate</text>
        <text class="ts" x="490" y="250" text-anchor="middle">ваша команда осталась вашей</text>

        <line class="ln-off" x1="0" y1="278" x2="980" y2="278"/>

        <text class="lane lane-ours" x="0" y="296">Работа</text>
        <rect class="bx-ours" x="0" y="304" width="200" height="46" rx="4"/>
        <text class="tmm" x="100" y="332" text-anchor="middle">&lt;харнесс&gt;-gate</text>

        <text class="edge edge-ours" x="230" y="322" text-anchor="middle">шов</text>
        <line class="ln-ours" x1="200" y1="328" x2="252" y2="328"/>
        <polygon class="hd-ours" points="252,324 260,328 252,332"/>

        <rect class="bx-ours" x="260" y="304" width="210" height="46" rx="4"/>
        <text class="tm" x="365" y="326" text-anchor="middle">плагин харнесса</text>
        <text class="ts" x="365" y="342" text-anchor="middle">перехват вызова</text>

        <line class="ln-ours" x1="470" y1="328" x2="522" y2="328"/>
        <polygon class="hd-ours" points="522,324 530,328 522,332"/>

        <rect class="bx-ours" x="530" y="304" width="200" height="46" rx="4"/>
        <text class="tm" x="630" y="326" text-anchor="middle">общее ядро</text>
        <text class="ts" x="630" y="342" text-anchor="middle">одно на все пять</text>

        <text class="edge edge-ours" x="760" y="322" text-anchor="middle">HTTP</text>
        <line class="ln-ours" x1="730" y1="328" x2="782" y2="328"/>
        <polygon class="hd-ours" points="782,324 790,328 782,332"/>

        <rect class="bx-theirs" x="790" y="304" width="190" height="46" rx="4"/>
        <text class="tx" x="885" y="324" text-anchor="middle">Гард</text>
        <text class="ts" x="885" y="341" text-anchor="middle">allow · ask · deny</text>
      </svg>
      <figcaption>
        Разница между харнессами заканчивается на плагине. Поэтому «поддержать ещё один харнесс» значит написать один адаптер шва, а не второй гард.
      </figcaption>
    </figure>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Харнесс</th>
            <th>Чем переключается</th>
            <th>Шов для решения</th>
            <th>Патч</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>opencode</td><td><code>OPENCODE_CONFIG_CONTENT</code></td><td><code>permission.ask</code></td><td>да</td></tr>
          <tr><td>kilo</td><td><code>KILO_CONFIG_CONTENT</code></td><td><code>permission.ask</code></td><td>да</td></tr>
          <tr><td>codex</td><td><code>CODEX_HOME</code></td><td><code>PreToolUse</code></td><td>нет</td></tr>
          <tr><td>pi</td><td><code>PI_CODING_AGENT_DIR</code></td><td><code>tool_call</code></td><td>нет</td></tr>
          <tr><td>dsh</td><td><code>--profile gate</code></td><td><code>pre-execute</code></td><td>нет</td></tr>
        </tbody>
      </table>
    </div>

    <p style="margin-top:30px">
      Патч нужен только двоим. У codex, pi и dsh шов есть штатно; у opencode и kilo он тоже
      объявлен — <code>permission.ask</code> описан и в API плагинов, и в документации, — но
      апстрим его никогда не вызывает. Патч добавляет ровно недостающий вызов, около пятидесяти
      строк.
    </p>
  </section>

</div>

</body></html>
```
