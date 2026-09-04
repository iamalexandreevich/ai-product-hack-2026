# Executive Summary

The market for **AI agent security** (protecting coding assistants and autonomous agents) is emerging rapidly.  Industry research projects the *agentic AI security* market at **$55 billion in 2026**, growing at ~36% annually to nearly **$888 billion by 2035**.  Major players include enterprise-focused security platforms (e.g. **Lasso Security**’s Intent Deputy, **Adversa AI**’s runtime controls, **Arcade.dev**’s agent runtime), as well as AI platform providers (Anthropic’s **Claude Code**, Microsoft’s **Copilot CLI/Agents**).  Open-source projects (e.g. **DepScope** for dependency safety, **SecureClaw** for OpenClaw hardening) and coding-agent frameworks (e.g. **Kilo Code**, **OpenClaw**) also compete indirectly.  The main conclusion is that while several products address aspects of the problem – behavior monitoring, permission gating, and dependency checks – *significant gaps remain*.  No single solution fully secures open-source coding agents without impeding productivity. The hackathon team should focus on **coverage gaps** (e.g. slopsquatting detection, low-friction guardrails) and on novel approaches resistant to prompt injection or fatigue.

# Market Map

- **Direct solutions** (“Category A”): Products built specifically for AI agent security. E.g. **Lasso Security (Intent Deputy)**, **Adversa AI Runtime Controls**, and **Anthropic Claude Code (Auto Mode)**. These focus on real-time monitoring and filtering of agent actions.  
- **Adjacent solutions** (“Category B”): Broader frameworks or tools that cover parts of the problem. E.g. **Arcade.dev** (enterprise agent runtime with hooks), **SecureClaw** (OpenClaw security plugin), **GitHub Copilot Agents** (CLI/cloud agents with sandboxing), and **DepScope** (dependency intelligence API).  
- **Substitute approaches** (“Category C”): Traditional or manual methods developers use instead. E.g. manual code review, static SAST/SCA tools (Snyk, Dependabot, etc.), container sandboxes, or simply **asking for human approval** on every step (the “ask permission” mode). These are not designed for AI agents but serve as stopgaps.  
- **Emerging threats and future players** (“Category D”): Upcoming research prototypes and startups. This includes new open-source agents (e.g. **DeepSeek**, **AutoGen**), OS-level agent managers, and advanced LLM safety projects. These could introduce novel solutions or exploit new vulnerabilities in agentic workflows.

# Top Competitors

### Anthropic – *Claude Code (Auto Mode)*  
- **Basic info:** Anthropic (USA, founded 2021, large AI lab). Product: *Claude Code* (CLI-based coding agent).  
- **Problem solved:** Provides a full coding assistant with autonomous execution. The new “**Auto Mode**” automatically classifies agent tool calls as safe or risky to avoid manual permission prompts.  
- **How it works:** Before each step, a classifier checks the intended shell command. Benign actions are executed, dangerous ones are blocked or delayed for user review. This prevents actions like mass-deletion or data exfiltration without user oversight.  
- **Key features:** Extended “chain-of-thought” with tool use (dynamic web/docs lookups), real-time validation of dependencies, built-in safety classifier for actions. Auto mode is default in Claude Code (since Aug 2023).  
- **Technology:** Proprietary LLMs (Claude Sonnet/Opus) integrated with agent framework. Classifier uses Anthropic’s guardrail tech (via model or ML) to flag commands.  
- **Market validation:** Used by developers on Anthropic’s Team/Enterprise plans. Being rolled out as default mode, indicating trust in effectiveness.  
- **Strengths:** State-of-the-art LLM integration; proven high “vibe coding” productivity. Auto-mode reduces fatigue versus manual approval.  
- **Weaknesses:** Closed-source and proprietary. Security relies on Anthropic’s models/datasets (prompt injection could still bypass). Tied to Anthropic’s ecosystem – not available for open-source agent stacks.  

### Lasso Security (Israel) – *Intent Deputy*  
- **Basic info:** Lasso Security (Israel/USA, founded ~2022). Product: *Intent Deputy*, part of Lasso AI Security Platform.  
- **Problem solved:** Detects and blocks malicious or out-of-scope actions by AI agents. Addresses threats that content-only filters miss, by analyzing **behavioral intent** across a session.  
- **How it works:** Continuously monitors agent activity in real time, building “fingerprints” of normal behavior. A semantic engine determines if each action aligns with the agent’s intended purpose. Deviations or known malicious patterns trigger blocks or alerts.  
- **Key features:** Behavior-based detection (99.83% threat catch rate at <50ms latency), anomaly baselining (compares to user/agent history), explainable logs for compliance, and multi-agent analysis (detects coordinated misuse). Focuses on full-session intent rather than isolated prompts.  
- **Technology:** Proprietary ML models for behavioral analysis. Distributed real-time monitoring platform with low overhead. Works as a sidecar or API in an enterprise environment.  
- **Market validation:** Recently launched (Feb 2026) with initial customers (Telit Cinterion endorsement in press release). Positioned for large enterprises worried about agentic AI.  
- **Strengths:** First-of-its-kind in “intent security” category. High reported detection accuracy. Can catch multi-step attacks. Focus on enterprise compliance (audit trails).  
- **Weaknesses:** Very new and unproven in wide use. Complexity of behavioral models may lead to false positives or require tuning. Likely expensive. Focused on big companies – less accessible to individual developers.  

### Adversa AI (USA) – *Agent Control Platform*  
- **Basic info:** Adversa AI (USA, founded ~2022). Product: a runtime security layer for coding agents (open or closed).  
- **Problem solved:** Prevents unsafe agent actions (malicious installs, credential leaks, destructive commands) across any coding agent (Claude, Copilot, Cursor, etc.).  
- **How it works:** Inserts a control layer “between” the agent and system. Monitors every action in the “chain” of an agentic task and blocks dangerous sequences by default. Uses expert-crafted policies and detection models to catch subtle attacks (prompt jailbreaks, supply chain exploits).  
- **Key features:** Real-time policy enforcement; agent-agnostic (works with CLI or IDE agents); deep inspection of commands, inputs, and outputs; protection against known CVEs, malicious npm packages, and abnormal behaviors. Has an open-source plugin (*SecureClaw*) for OpenClaw as example of layering.  
- **Technology:** Combination of static analysis (Vuln/CVE scanning, SBOM analysis) and dynamic runtime checks. Uses a library of ~100 attack patterns and behavioral rules. All processing happens locally (“no third-party AI API calls”) to avoid data leakage.  
- **Market validation:** Early product stage, available as on-prem or cloud service. Pilot customers reported in blog (analysts at Snowflake/others). Some open-source tools (SecureClaw) available on GitHub.  
- **Strengths:** Holistic coverage of coding-agent threats; not tied to one agent platform; open-source heritage (SecureClaw) fosters trust. Designed specifically to handle complex attacks in code generation workflows.  
- **Weaknesses:** Also new and specialized – limited real-world track record. Likely heavy engineering overhead for deployment. May introduce latency on agent actions. Effectiveness against future AI-driven threats remains to be proven.  

### Arcade (USA) – *MCP Runtime & Contextual Access*  
- **Basic info:** Arcade.dev (USA, founded ~2022). Product: secure *runtime* for production AI agents (MCP-based).  
- **Problem solved:** Enables enterprises to deploy agents safely by enforcing identity, permissions, and governance on every agent action. Instead of an agent accessing APIs directly, it goes through Arcade’s layer.  
- **How it works:** Agents authenticate as real users and perform actions through Arcade’s runtime. Arcade provides three security layers: (1) **Auth** – agents use the user’s identity (no static keys in prompts); (2) **Scope** – limit which tool-operations an agent can use; (3) **Contextual Access** – new hooks that intercept tool calls at three points (before agent sees tool, before execution, after execution) for custom security logic. Users can plug in custom webhooks (e.g. enterprise policies) into these hooks.  
- **Key features:** Dynamic OAuth-based auth (no hardcoded creds); policy-driven tool visibility and access controls; run-time filtering of tool calls and outputs; full audit logging. Supports any LLM, any identity provider. Integrates with IDPs like Okta/Entra ID.  
- **Technology:** Proprietary MCP (Model Context Protocol) server and runtime. Built by ex-Okta/Snowflake engineers. Cloud or on-prem deployment. Emphasizes API/SDK integrations.  
- **Market validation:** Trusted by enterprises (partners include Snyk, LangChain) and has paying customers (Aracde mentions references). It’s a live product with tiered pricing (free trial, enterprise plans).  
- **Strengths:** Enterprise-grade solution solving many otherwise hard problems (auth, governance). Reduces burden on dev teams by handling security centrally. Extensible “hooks” let security team plug in custom checks.  
- **Weaknesses:** Heavyweight and complex – possibly overkill for individual developers or smaller teams. Requires adoption of MCP framework (Arcade vendor lock-in). Does not specifically scan for malicious code/package; focuses on infrastructure and policy.  

### GitHub / Microsoft – *Copilot Agents (CLI, Cloud, SDK)*  
- **Basic info:** Microsoft GitHub (USA). Products: *Copilot CLI* (terminal agent) and *Copilot Cloud Agent*.  
- **Problem solved:** Automates coding tasks (file edits, branches, PRs) while enforcing security boundaries. Copilot Cloud Agent runs in ephemeral environments; Copilot CLI requires user permission for actions.  
- **How it works:** The **Cloud Agent** executes multi-step tasks on GitHub.com (branches, PRs) within isolated containers, with firewalls and DAST scanning. The **CLI** tool can execute shell commands and file operations but requires the user to approve each external action. Both use Microsoft’s enforcement model (e.g. firewall, SANDBOX).  
- **Key features:** Ephemeral dev environments (no state persists); firewall prevents unauthorized egress; explicit permission prompts for CLI actions; integrated code-review and PR flows; a robust IDE/plugin ecosystem. The Copilot SDK supports custom agents and lifecycle hooks as well.  
- **Technology:** Built on Codex/GPT-based models integrated with GitHub infrastructure. Uses containers, OAuth flows, webhooks. The permission gating and firewall are part of GitHub’s infrastructure.  
- **Market validation:** Millions of GitHub users have Copilot; the Agents feature is newer but backed by Microsoft. Widely adopted in corporate settings (GitHub user base, GitHub Enterprise customers).  
- **Strengths:** Strong security defaults (sandboxed cloud execution, prompt gating); seamless integration in dev workflow; continuous vulnerability scanning of agent actions. Large customer base and support.  
- **Weaknesses:** Recent analyses show the CLI still had bypassable prompts (prompt injection could skirt user checks). It’s limited to GitHub’s ecosystem. Many security features assume trust (if user approves malicious command, Copilot will run it). Not open-source, so opaque controls.  

### DepScope – *AI Package Intelligence API*  
- **Basic info:** DepScope (open-source/CLI tool and free API by cuttalo). Founders unknown. Available via MCP Market as an API server.  
- **Problem solved:** Prevents coding agents from installing malicious or bogus dependencies (slopsquatting/typosquatting). Helps check packages before `npm install`, etc.  
- **How it works:** Developers or agents call the DepScope API with a package name or repo URL. DepScope returns a risk assessment: maintenance health, known CVEs, malice flags, and hallucination warning. It aggregates real-time info (OSV DB, download trends, malicious registry watchlists) and identifies typosquats or nonexistent packages. It can digest lockfiles and export SBOMs.  
- **Key features:** Multi-ecosystem support (NPM, PyPI, Cargo, Go, etc.), OSV vulnerability scanning, typosquatting detection, real-time malicious package feed, hallucination benchmark data. Free and token-optimized (caches and compresses) to reduce AI prompt tokens.  
- **Technology:** Hosted API with databases of package metadata and security info. Uses GitHub APIs, OpenCV Database, community feeds. Designed to plug into agent pipelines. No AI model itself – purely data-driven.  
- **Market validation:** It appears to be a new community-driven project (a GitHub repo and MCP market listing). Low GitHub stars suggests early stage. Free to use – aimed at developers and AI agents.  
- **Strengths:** Specifically addresses the novel “slopsquatting” threat. Covers 19 package ecosystems. Low-cost/free service that agents can query automatically.  
- **Weaknesses:** Very new and niche – not battle-tested. Limited to dependency checks (does not monitor agent commands beyond installs). Performance and accuracy depend on timely data aggregation.  

### SecureClaw (OpenClaw plugin by Adversa AI)  
- **Basic info:** SecureClaw (GitHub project by Adversa AI). Open-source security plugin and skill set for the OpenClaw agent (and forks like Moltbot).  
- **Problem solved:** Hardens OpenClaw, a popular open-source agent, by adding safety checks at both runtime and agent-instruction levels.  
- **How it works:** SecureClaw is a *dual-layer* solution. An external “plugin” component audits and hardens the OpenClaw installation (e.g. checking config, dependency health). A companion “skill” (agent plugin) enforces behavioral rules at runtime (e.g. blocking dangerous tool calls). Together they prevent many OWASP ASI Top10 threats.  
- **Key features:** 55 automated system checks (permissions, vulnerable dependencies, UIX) plus 15 in-agent behavioral rules (e.g. detect malicious prompts or exfil patterns). Aligns with MITRE ATLAS/OWASP ASI guidance. Emphasizes minimal token usage.  
- **Technology:** Mixed approach: Python scripts for system auditing; OpenClaw skill defined via prompts and rules. All logic is visible and extensible (open-source).  
- **Market validation:** Being a free, GitHub-available project, adoption depends on OpenClaw users. It’s cited in security blogs as the first comprehensive attempt to secure OpenClaw.  
- **Strengths:** Free and transparent. Comprehensive coverage of known agent threats. Developed by security experts. Works directly inside the agent (less prone to prompt injection) and outside (OS-level checks).  
- **Weaknesses:** Only protects OpenClaw (and similar) – not applicable to other agents. OpenClaw itself is already considered extremely insecure, so SecureClaw is playing catch-up. Community adoption is uncertain.  

### Kilo Code (USA, open-source)  
- **Basic info:** Kilo (acquired by Anaconda 2026). Product: *Kilo Code*, an open-source AI coding assistant (CLI, VS Code, JetBrains).  
- **Problem solved:** Facilitates AI-assisted coding (writing, refactoring, reviewing code) across platforms. Does not natively secure agent actions.  
- **How it works:** Acts as a single “agent” interface for multiple LLMs. Users write natural prompts; Kilo generates code or shell commands via chosen model. It supports modes (code, debug, etc.) and isolates each agent’s worktree. Kilo does not execute commands on its own; it relies on the user or environment to run code.  
- **Key features:** 500+ model support (local & cloud); isolated parallel agents; visibility into prompts/decisions; switchable modes (Code vs Debug vs Architect); integration with many IDEs; open MIT license.  
- **Technology:** Mostly Python orchestrator calling LLM APIs. No specialized security model. It can integrate with dependency checkers or other tools via its “gateway” plugin framework, but security is user’s responsibility.  
- **Market validation:** Very popular among developers (5M+ coders as of 2026). Active community (GitHub, Discord). “Open Source Product of the Month” award. Used by teams for productivity.  
- **Strengths:** Completely open and transparent – users can inspect and modify it. Broad model support and rich feature set make it very flexible. Active ecosystem.  
- **Weaknesses:** Does not include built-in auto-mode or security checks. If used in dangerous mode (allowing command execution), it inherits all the agent security risks the hackathon is addressing. Users currently must use Kilo’s permission prompts or external safeguards (e.g. sandboxing) themselves.

### OpenClaw (open-source agent)  
- **Basic info:** OpenClaw (OpenAI, community; star project on GitHub). Product: A multi-domain AI agent framework (runs as personal assistant via chat tools).  
- **Problem solved:** Enables users to use LLMs to perform actions on chat platforms (retrieve data, send messages, etc.). Not designed for secure code execution.  
- **Relevance:** Many organizations adopted it informally. Its explosive growth (180k stars) highlighted the risk of “shadow AI” in enterprises.  
- **Strengths:** Highly flexible and extensible (supports many chat integrations). Shows what modern agents can do when unchecked.  
- **Weaknesses:** Extremely insecure by default. Analysts have found multiple critical vulnerabilities (RCEs, plugin hijacks) and dozens of malicious “skills” in the ecosystem. Security experts warn that running OpenClaw as-is is unsafe.  
- **Note:** OpenClaw itself is not a security solution – it’s an example of the problem. It underscores the demand for solutions like SecureClaw or Intent Deputy.  

# Competitive Matrix

| **Company**        | **Product**                    | **Target user**             | **Problem solved**                                     | **Main features**                                     | **Technology**                   | **Pricing**          | **Adoption**                           | **Strengths**                                       | **Weaknesses**                                     |
|--------------------|--------------------------------|-----------------------------|--------------------------------------------------------|-------------------------------------------------------|----------------------------------|----------------------|----------------------------------------|-----------------------------------------------------|-----------------------------------------------------|
| *Anthropic*        | Claude Code (Auto Mode)       | Developers/DevOps           | Safe autonomous coding agent execution                | Extended “think” with web/docs lookup; action classifier blocks harmful commands | Proprietary LLMs (Claude 4.6)     | SaaS (team/enterprise) | Growing (new auto mode default)        | Best-in-class LLM; high productivity; Auto mode reduces prompts | Closed; risk still present for new attacks; expensive. |
| *Lasso Security*   | Intent Deputy                | Enterprise AI security teams | Runtime behavioral monitoring of AI agents            | Real-time intent analysis (99.83% detection @ <50ms); user/agent baselines; compliance logs | Custom ML behavior models        | Enterprise SaaS      | Early adopters (launched Feb’26)       | First behavioral intent solution; high reported accuracy | New, unproven; complex setup; may have false alerts.        |
| *Adversa AI*       | Agent Control Platform       | Enterprises/teams          | Prevents malicious commands, supply-chain attacks      | Chain-of-thought monitoring; policy/rule engine; malicious package detection | Hybrid static+runtime analysis    | Enterprise/SaaS      | In early rollout                        | Comprehensive coding-agent focus; open-source skill (SecureClaw) | New startup; effectiveness vs unknown attacks TBD. |
| *Arcade.dev*       | Agent Runtime & Contextual Access | Enterprise IT/DevOps       | Auth, authorization, and governance for agents        | Delegated identity auth; scoped tool access; runtime hooks to inject custom checks | MCP standard runtime             | Cloud/SaaS (tiered)  | Notable enterprise customers (e.g. Snyk) | Solves core security gaps (no static creds); auditable controls | Heavyweight solution; vendor lock-in; not free.          |
| *Microsoft/GitHub* | Copilot Agents (CLI/Cloud)   | Developers (GitHub users)   | Autonomous coding assistants with permission checks    | Ephemeral sandboxed env; firewall; explicit user prompts for commands | OpenAI/GPT-based models; containers | Subscription (Copilot plans) | Very high (GitHub user base)         | Tight GitHub integration; known infrastructure; permission gating | Found design flaws (injection bypass); GitHub lock-in. |
| *DepScope*         | DepScope (MCP API)          | Developers/AI agents        | Safe dependency management for AI coding              | Dependency scanning; vulnerability and typosquat checks; free API; SBOM support | Data aggregation, no LLM        | Free (open API)     | Early community (demo-level)           | Addresses AI-specific threats (slopsquatting) | Very early project; limited ecosystem.                   |
| *SecureClaw*       | SecureClaw (OpenClaw plugin) | OpenClaw developers/users    | Hardens OpenClaw agent security                      | 55 system checks; 15 behavioral rules; OWASP ASI compliance | Python plugin + agent skill     | Free/Open-source    | Very niche (OpenClaw users)           | Comprehensive two-layer guardrails for OpenClaw | Only covers OpenClaw; OpenClaw itself is risky.   |
| *Kilo (Anaconda)*  | Kilo Code                   | Developers                  | General AI coding assistant (flexible model use)     | Multi-IDE/CLI agent; switchable modes (code/debug/arch); open-source support | Multiple LLMs (open & closed)   | Free / Bring-your-key | High (5M+ users)      | Open-source, no vendor lock-in; very flexible | No built-in safety checks (relies on “ask user” by default). |

# Market Gaps

| **Gap**                                      | **Evidence**                                                                                     | **Opportunity**                                                                                       |
|----------------------------------------------|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| *Lack of open-source, low-friction solutions* | Most advanced security tools (Lasso, Arcade) target enterprises with complex setups. Open-source agent users (e.g. Kilo, OpenClaw communities) have no equivalent built-in guardrail. | Develop light-weight, open solutions or libraries that individual developers can easily adopt (e.g. Kilo plugin, VSCode extension) with minimal ops overhead. |
| *Incomplete threat coverage*                 | Even advanced agents admit “validation cannot catch every edge case”. Current solutions rely heavily on ML/heuristics (which adversaries can evade). | Combine static verification (SBOM, static analysis) with runtime checks. Leverage external data (SBOM tracking, threat feeds) to catch hallucinated or unknown threats. |
| *High user friction / approval fatigue*      | Excessive prompts degrade productivity. Research notes blocking everything (strict denial) is unrealistic; user fatigue is a critical problem. | Create tiered or adaptive assurance: auto-approve clearly safe actions, batch confirmations, or use human-in-the-loop more smartly. Improve UX (contextual explainers) to reduce fatigue. |
| *Vulnerable guardrails to prompt injection*  | Built-in “guardrails” (rules, keyword scans) are easily bypassed. Many tools filter only at content-level, missing multi-step attacks. | Design architecture that isolates the security logic from the agent’s context (e.g. external validators, dedicated processes) so that a malicious prompt can’t trick the check. |
| *Dependency risks (slopsquatting)*            | Studies show open-source models hallucinate packages frequently (slopsquatting). Most solutions don’t prevent AI from installing bogus libs. | Integrate real-time dependency intelligence (like DepScope) into the agent loop. Automatically verify package names against registries/SBOMs before install. |

# Strategic Conclusions

- **Target the underserved “hobbyist/dev-team” segment.**  Many existing solutions cater to large enterprises. An open or freemium solution for small teams (e.g. via a VSCode plugin or CLI tool) could capture a wide user base that currently just disables safety.  
- **Focus on slopsquatting and supply-chain threats.**  This is a novel, high-impact gap. Integrating a fast package-check API (or even on-device dependency validator) will set a solution apart, as only a few (like DepScope) address it.  
- **Architect for invulnerability to prompt injection.**  Solutions should avoid relying on the agent’s LLM for security decisions. For example, perform action classification or policy checks outside the model context or using deterministic code, so malicious prompts can’t corrupt them.  
- **Minimize unnecessary friction.**  Balance safety with autonomy. Avoid “block everything” and consider semi-autonomous workflows: e.g. auto-approve simple safe commands, use smart defaults, and batch user confirmations. This reduces approval fatigue and encourages adoption.  
- **Leverage behavioral context judiciously.**  Many “next-gen” tools use full-session analysis (fingerprinting) which is powerful but complex. The team could selectively use simpler contextual cues (e.g. unexpected tool usage, command patterns) that are easier to validate and explain.  
- **Provide transparent, user-controlled trust.**  Open-source or at least inspectable security logic will build trust (developers should know *why* something is blocked). Allow users to customize policies (whitelists/blacklists for commands, directories, packages) to reduce false positives and fit their workflow.  
- **Integrate with existing dev workflows.**  Combine agent security with tools devs already use (SAST/SCA, CI pipelines). For instance, report agent actions to the same dashboards as code quality metrics, or allow the user’s existing linting tools to vet generated code in the loop.  
- **Plan for future AI advances.**  New LLMs may change threat models (some are less prone to hallucination, some more powerful attacks will emerge). Build a modular design where core policies and checks can be updated independently of any specific AI model.  
- **Monitor and audit agent behavior.**  Even with controls, agents may slip by. The solution should log every decision (action allowed/blocked) with rationale. Over time, this data can improve rules and show compliance, an important selling point for organizations.  
- **Collaborate on benchmarks and datasets.**  There is little public data on coding-agent threats. Contributing a benchmark suite of malicious prompts/actions (as the case suggests) will not only help the hackathon but also distinguish the team as thought leaders.

By understanding where current tools fall short (citations above) and emphasizing these strategic areas, a new solution can carve out a position even in a rapidly evolving competitive field. The goal is to fill the gaps – enabling secure autonomy without killing the developer’s productivity or making the system so complex that it defeats its purpose.  

