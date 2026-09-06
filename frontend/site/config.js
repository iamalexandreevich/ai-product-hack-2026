// Everything on the page that is a link, an address or a flag lives here.
// Change these without touching index.html or app.js.

export const CONFIG = {
  // Install one-liner shown in the hero and the footer. Empty hides the
  // copy buttons until the real command is decided.
  installCommand: 'curl -fsSL https://openmagi.ru/install.sh | sh',
  githubUrl: 'https://github.com/agentgate',
  benchmarkUrl: 'https://github.com/agentgate/agentgate/tree/main/benchmark',
  // Benchmark numbers by metric key (ASR, FP, p95, Friction). null = no run yet.
  metricValues: { ASR: null, FP: null, p95: null, Friction: null },
  showMetrics: true,
  title: {
    ru: 'OPENMAGI — шлюз политик для кодинг-агентов',
    en: 'OPENMAGI — a policy gate for coding agents',
  },
  description: {
    ru: 'Шлюз политик между открытыми кодинг-агентами и вашей машиной. Каждый вызов инструмента проверяется до выполнения.',
    en: 'A policy gate between open-source coding agents and your machine. Every tool call is checked before it runs.',
  },
};
