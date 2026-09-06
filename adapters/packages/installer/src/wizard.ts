/**
 * The interactive install flow.
 *
 * `gate install` with no arguments should be able to answer for itself what it
 * is about to do to the machine: which agents it found, which of them to gate,
 * where the guard is, how strict the rules are — and show the whole plan before
 * touching anything.
 *
 * Every answer has a flag, and every flag skips its question. `--yes` or a
 * non-interactive shell takes the defaults and asks nothing.
 */
import { detectAll, type Detected } from "./detect.ts"
import { ask, confirm, isInteractive, multiSelect, select, ui } from "./prompt.ts"
import { mark, paint } from "./brand.ts"

export type WizardAnswers = {
  targets: string[]
  guardUrl?: string
  token?: string
  startGuard?: boolean
  llmUrl?: string
  llmModel?: string
  llmKey?: string
  level?: string
}

export type WizardOptions = {
  only?: string[]
  guardUrl?: string
  token?: string
  startGuard?: boolean
  llmUrl?: string
  llmModel?: string
  llmKey?: string
  level?: string
  yes?: boolean
  detected?: Detected[]
}

const LEVELS = [
  { value: "low", label: "Низкий", hint: "запрещено только заведомо разрушительное" },
  { value: "medium", label: "Средний", hint: "плюс секреты и конфиги агентов; установка пакетов — вопрос" },
  { value: "high", label: "Высокий", hint: "плюс всё, что выходит за каталог или лезет в сеть" },
]

/** What each harness is called when we talk to a person about it. */
const TITLES: Record<string, string> = {
  opencode: "opencode",
  kilo: "Kilo CLI",
  opencode2: "opencode 2.0",
  pi: "Pi",
  codex: "Codex CLI",
  dsh: "DeepSeek Harness",
}

export async function runWizard(options: WizardOptions): Promise<WizardAnswers | null> {
  const found = options.detected ?? detectAll()

  if (found.length === 0) {
    console.log("Не нашёл ни одного кодинг-агента. Поддерживаются: " + Object.values(TITLES).join(", "))
    return null
  }

  console.log("")
  console.log(`${paint("lime", "●")} Найдено агентов: ${ui.bold(String(found.length))} — ` +
    found.map((t) => `${TITLES[t.id] ?? t.id} ${ui.dim(t.version)}`).join(", "))
  console.log("")

  // Non-interactive, or the user already said what they wanted: no questions.
  const silent = options.yes || !isInteractive()

  let targets = options.only ?? found.map((t) => t.id)
  if (!silent && !options.only) {
    const all = await confirm("Поставить во все найденные?")
    if (!all) {
      targets = await multiSelect(
        "Выберите агентов",
        found.map((t) => ({ value: t.id, label: TITLES[t.id] ?? t.id, hint: t.version })),
        false,
      )
    }
    console.log("")
  }

  const answers: WizardAnswers = { targets, level: options.level }

  // The guard: point at one, or bring one up.
  if (options.startGuard || options.guardUrl) {
    answers.startGuard = options.startGuard
    answers.guardUrl = options.guardUrl
    answers.token = options.token
  } else if (silent) {
    answers.guardUrl = "http://127.0.0.1:8400"
  } else {
    const own = await select("Гард-сервис", [
      { value: false, label: "Подключиться к уже поднятому", hint: "нужен адрес и ключ" },
      { value: true, label: "Поднять свой локально", hint: "docker compose; нужна модель для ступени 2" },
    ])
    console.log("")
    if (own) {
      answers.startGuard = true
      answers.llmUrl = options.llmUrl ?? (await ask("Адрес LLM (OpenAI-совместимый)", { default: "https://openrouter.ai/api/v1" }))
      answers.llmModel = options.llmModel ?? (await ask("Название модели"))
      answers.llmKey = options.llmKey ?? (await ask("Ключ к модели", { secret: true }))
    } else {
      answers.guardUrl = await ask("Адрес гарда", { default: "http://127.0.0.1:8400" })
      answers.token = options.token ?? (await ask("Ключ доступа", { secret: true }))
    }
    console.log("")
  }

  // Stage 1 policy.
  if (!answers.level && !silent) {
    answers.level = await select("Уровень защиты", LEVELS, 1)
    console.log("")
  }
  answers.level = answers.level ?? "medium"

  if (silent) return answers

  // Everything the run is about to do, before it does any of it.
  console.log(`${paint("lime", "◆")} ${ui.bold("Что будет сделано")}`)
  for (const id of targets) {
    console.log(`  ${mark.next()} ${TITLES[id] ?? id}: команда ${ui.bold(`${id}-gate`)}, ваш ${id} не тронут`)
  }
  console.log(`  ${mark.next()} правила уровня ${ui.bold(answers.level)} → ~/.config/gate/rules.json`)
  console.log(
    answers.startGuard
      ? `  ${mark.next()} поднять гард локально (docker compose)`
      : `  ${mark.next()} гард: ${answers.guardUrl}`,
  )
  console.log("")

  if (!(await confirm("Продолжить?"))) {
    console.log("Отменено, ничего не изменено.")
    return null
  }
  console.log("")
  return answers
}
