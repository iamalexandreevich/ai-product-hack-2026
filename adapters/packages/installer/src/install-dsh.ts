/**
 * DeepSeek Harness: gated by a profile of its own.
 *
 * dsh composes its runtime from a named profile — an ordered stack of plugin
 * bundles. That is the harness's own mechanism for "a different set of
 * plugins", so gating is a profile rather than a relocated home: the user's
 * profiles are never touched and `dsh-gate` just boots ours.
 */
import { execFileSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { adaptersRoot } from "./bundle.ts"
import type { Detected } from "./detect.ts"
import { ensureLink } from "./home.ts"
import type { GatePaths } from "./paths.ts"
import { removeWrapper, writeHomeWrapper } from "./wrapper.ts"

export type DshInstallOptions = {
  guardUrl: string
  token?: string
  profileId?: string
  log: (line: string) => void
  /**
   * The checkout the plugin is linked from. Overridable because installing dsh
   * necessarily writes a symlink into the repo — Node dereferences symlinks, so
   * the plugin's own import of the shared core resolves from the real path.
   * Tests point this at a temp dir so they never touch the working tree.
   */
  root?: string
}

export type DshRecord = { profile: string; links: string[]; wrapper: string }

export const PROFILE_NAME = "gate"

/**
 * `insert` is load-bearing: a bare `- id: gate` is read as a patch of an
 * existing entry and fails with `patch: entry "gate" not found`.
 */
const PATCH_YAML = `# AgentGate: gate every tool call and filter every tool result.
- insert:
    - id: gate
      name: '@agentgate/dsh-gate'
`

const PROFILE_FILES = ["cordis.yml", "package.json", "pnpm-workspace.yaml"]

/**
 * A profile is scaffolded by dsh itself, never by us: `package.json` names
 * version-specific bundle packages, and inventing them yields a profile that
 * composes to nothing. So we copy an existing one and add our layer.
 */
function pickDonorProfile(profilesDir: string): string | null {
  if (!fs.existsSync(profilesDir)) return null
  const preferred = ["headless", "tui", "web"]
  const names = fs.readdirSync(profilesDir).filter((n) => n !== "node_modules" && n !== PROFILE_NAME)
  const ordered = [...preferred.filter((n) => names.includes(n)), ...names.filter((n) => !preferred.includes(n))]
  for (const name of ordered) {
    const dir = path.join(profilesDir, name)
    if (PROFILE_FILES.every((f) => fs.existsSync(path.join(dir, f)))) return dir
  }
  return null
}

export function installDsh(target: Detected, paths: GatePaths, options: DshInstallOptions): DshRecord | null {
  const profilesDir = target.configDir
  const profile = path.join(profilesDir, PROFILE_NAME)

  if (!fs.existsSync(path.join(profile, "cordis.yml"))) {
    let donor = pickDonorProfile(profilesDir)
    if (!donor) {
      // A fresh install has no profile yet, and asking the user to run one
      // command and start over is a poor first impression when we can ask for
      // the same thing ourselves. This is still dsh scaffolding its own
      // profile -- `--help` makes it materialise one and exit -- which is what
      // the comment above forbids us from faking, not from requesting.
      try {
        execFileSync(target.binary, ["--profile", "headless", "--help"], {
          stdio: "ignore",
          timeout: 60_000,
        })
      } catch {
        // Nothing to add: the retry below reports the outcome either way.
      }
      donor = pickDonorProfile(profilesDir)
    }
    if (!donor) {
      options.log("  ! dsh has no profile to copy from, and `dsh --profile headless --help` did not create one")
      return null
    }
    fs.mkdirSync(profile, { recursive: true })
    for (const file of PROFILE_FILES) fs.copyFileSync(path.join(donor, file), path.join(profile, file))
    // Two packages with one name confuse the workspace the profile lives in.
    try {
      const pkgFile = path.join(profile, "package.json")
      const pkg = JSON.parse(fs.readFileSync(pkgFile, "utf8"))
      pkg.name = `dsh-profile-${PROFILE_NAME}`
      fs.writeFileSync(pkgFile, JSON.stringify(pkg, null, 2) + "\n")
    } catch {
      // A profile we cannot rename still works; the name only matters to pnpm.
    }
    options.log(`  copied dsh profile from ${path.basename(donor)}`)
  }

  // Written whole: this is YAML, and none of the JSON helpers may touch it.
  const patchFile = path.join(profile, "cordis.patch.yml")
  const existing = fs.existsSync(patchFile) ? fs.readFileSync(patchFile, "utf8") : ""
  if (!existing.includes("@agentgate/dsh-gate")) fs.writeFileSync(patchFile, PATCH_YAML)

  // One link is enough: the plugin imports the shared core by relative path, and
  // Node resolves symlinks to their real location, so the import lands inside
  // the checkout either way. Linking core separately would also mean uninstall
  // deleting something the repo's own tests rely on.
  const root = options.root ?? adaptersRoot()
  const links: string[] = []
  const pluginLink = path.join(profilesDir, "node_modules", "@agentgate", "dsh-gate")
  if (ensureLink(pluginLink, path.join(root, "packages", "plugin-dsh"))) links.push(pluginLink)

  const wrapper = writeHomeWrapper("dsh", {
    binDir: paths.binDir,
    guardUrl: options.guardUrl,
    token: options.token,
    profileId: options.profileId,
    extraArgs: ["--profile", PROFILE_NAME],
  })
  return { profile, links, wrapper }
}

export function uninstallDsh(
  target: Detected,
  paths: GatePaths,
  log: (line: string) => void,
  root = adaptersRoot(),
): void {
  removeWrapper(paths.binDir, target.id)
  const profile = path.join(target.configDir, PROFILE_NAME)
  const patchFile = path.join(profile, "cordis.patch.yml")

  // A profile the user has taken over for their own use is left alone.
  const ours = fs.existsSync(patchFile) && fs.readFileSync(patchFile, "utf8").includes("@agentgate/dsh-gate")
  if (fs.existsSync(profile) && !ours) {
    log(`  left ${profile} alone — it no longer looks like ours`)
  } else if (fs.existsSync(profile)) {
    fs.rmSync(profile, { recursive: true, force: true })
  }

  void root
  for (const link of [path.join(target.configDir, "node_modules", "@agentgate", "dsh-gate")]) {
    try {
      if (fs.lstatSync(link).isSymbolicLink()) fs.unlinkSync(link)
    } catch {
      // Never there, or not ours to remove.
    }
  }
}
