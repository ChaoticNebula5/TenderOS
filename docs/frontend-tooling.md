# Frontend design tooling (B17)

Records what Claude Code skill tooling this project uses for frontend
design work, why each one was chosen over an alternative, what was
installed then deliberately removed, and how to reproduce the setup on a
fresh clone. The skills themselves are gitignored (see below) - this file
is the part of that decision worth keeping in history.

## Installed

| Skill(s) | Source | Why this one |
|---|---|---|
| `impeccable` | [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | Single design driver for this project - deliberately not layered with other opinionated design skills (see "Deliberately not installed" below), so output has one consistent point of view instead of several disagreeing ones. Installed skill-only; its optional per-edit design-detector hook was never enabled (requires a separate explicit `impeccable hooks on` step this project has not run). |
| `gsap-core`, `gsap-frameworks`, `gsap-performance`, `gsap-plugins`, `gsap-react`, `gsap-scrolltrigger`, `gsap-timeline`, `gsap-utils` (8 skills) | [greensock/gsap-skills](https://github.com/greensock/gsap-skills) (official) | Chosen over a community fork (genjutsu's own single `gsap` skill, removed - see below) because the official set is more thorough: 8 focused skills vs. one 188-line file covering the same ground genjutsu's version condensed - `gsap-core` alone runs 254 lines just on the core API. |
| Three.js — 24 skills (`threejs-core-*`, `threejs-syntax-*`, `threejs-impl-*`, `threejs-errors-*`, `threejs-agents-*`) | [OpenAEC-Foundation/Three.js-Claude-Skill-Package](https://github.com/OpenAEC-Foundation/Three.js-Claude-Skill-Package) | Chosen over genjutsu's single `threejs-r3f` skill (removed - see below) for its dedicated error/dispose coverage (`threejs-errors-performance` names memory leaks and dispose patterns explicitly; `threejs-errors-rendering` covers black-screen/z-fighting/shadow-artifact diagnosis) that a single condensed skill doesn't carry as its own first-class category. Installed by `git clone` directly (not through the `skills` CLI - see "Reproducing this setup" below), since this package ships its skills nested three directories deep (`skills/source/{category}/{skill}/SKILL.md`), not at repo root. |
| `canvas-generative` | [AThevon/genjutsu](https://github.com/AThevon/genjutsu) | The one genjutsu skill actually wanted going in - canvas/creative-coding coverage the other installed skills don't provide. |
| `css-native` | [AThevon/genjutsu](https://github.com/AThevon/genjutsu) | Kept deliberately, not by default: browser-native scroll-driven animation, View Transitions, and `@starting-style` cost zero bundle size - the cheapest motion available to this project, and a real gap the GSAP/Three.js skills above don't cover. |
| `frontend-design` | [anthropics/skills](https://github.com/anthropics/skills) | Anthropic's own aesthetic-direction skill. Installed after confirming it wasn't already present and that Impeccable doesn't bundle its own copy. |

## Deliberately not installed, or installed then removed

- **genjutsu's other 15 skills** (`cast`, `paint`, `compose-graphics`, `compose-motion`, `compose-multiplatform`, `design-audit`, `desktop-principles`, `framer-motion`, `gsap`, `mobile-principles`, `motion-principles`, `swiftui-graphics`, `swiftui-motion`, `threejs-r3f`, `ui-ux-pro-max`) - installed by a bare `skills add` (the repo has no way to select a subset at clone time), then pruned to the two above. Reasons, by group:
  - `compose-*` (Kotlin/Jetpack Compose) and `swiftui-*` (iOS-native) - no such surface in this project at all; this is a React web app.
  - `gsap` and `threejs-r3f` - duplicate the official GSAP eight and the 24-skill Three.js package above, both more thorough (see the table).
  - `cast`, `paint`, `design-audit`, `motion-principles`, `desktop-principles`, `mobile-principles` - all carry their own design opinion. Impeccable is this project's single design driver by decision; more opinion sources compete with it rather than adding coverage.
  - `ui-ux-pro-max` - 1.8MB of executable Python, and the one skill in this whole install with a Snyk "Med Risk" security rating, for a capability (a style-matching reasoning engine) this project never asked for. Removed.
  - `cast` also carried a "Med Risk" rating (Gen's own scanner). Removed.
- **`framer-motion`** (genjutsu) - not removed for being wrong, removed because it's an open question, not a decided one: it overlaps motion.dev's own coverage that a future block may want, but from a source that hasn't been vetted the way the official GSAP package was. Left for Phase 2's own plan to decide, not decided by the accident of a bare `skills add` pulling in everything the repo happened to contain.
- **`tailwind-v4-shadcn`** - does not exist as a skill name in the repo it was requested from (`jezweb/claude-skills`, 63 skills, none matching). Not installed; not substituted with a similarly-named skill from that repo.
- **Design System Architect** - referenced by [a design-tooling roundup](https://github.com/wilwaldon/Claude-Code-Frontend-Design-Toolkit), but that roundup is a curated list, not an installable skill itself, and its entry for this one gives no runnable install command (no repo URL, no CLI command) - only a link to a marketplace listing page that returned HTTP 429 on both attempts to check it. Not installed; not guessed at. Token-system/design-token expansion for this project is specified directly in `DESIGN.md` instead.

## Reproducing this setup

Skills are gitignored (`.agents/`, `.claude/skills/`, `skills-lock.json` -
see `.gitignore`): ~7MB of third-party reference material that regenerates
from these commands, not something worth carrying in history. The
decisions above are what's worth keeping.

```bash
npx skills add https://github.com/pbakaus/impeccable --skill impeccable
npx skills add https://github.com/greensock/gsap-skills
git clone https://github.com/OpenAEC-Foundation/Three.js-Claude-Skill-Package .claude/skills/threejs
npx skills add https://github.com/AThevon/genjutsu --skill canvas-generative,css-native
npx skills add https://github.com/anthropics/skills --skill frontend-design
```

Note on the Three.js clone: delete `.claude/skills/threejs/CLAUDE.md`
immediately after cloning. It is not reference material - it's the
original package author's own internal dev-orchestration file (complete
with an instruction to grant blanket `Bash(*)` permissions), left in the
published repo by accident. Claude Code reads `CLAUDE.md` files as
instructions; this one has nothing to do with the Three.js skill content
it ships alongside and should never be loaded as one. Every other
installed skill was checked directly for `CLAUDE.md`/`AGENTS.md`/
`.cursorrules`-shaped files - none found.

`skills-lock.json` (the `skills` CLI's own lockfile, pinning exact
commit hashes per skill) is gitignored along with the skills themselves,
not committed separately - without the skills it pins, it's a lockfile
for nothing. The commands above install current `main`/`HEAD` for each
source, not a pinned version; if that drifts in a way that matters,
re-decide this then rather than pinning speculatively now.
