# Component sources

Vendored kit code lives in `components/kits/<kit>/` and is imported by feature code only through `components/ui/` (enforced by `no-restricted-imports` in `eslint.config.mjs`). Kit stylesheets are not imported; `styles/kits/*.css` map each kit's token names onto `styles/tokens.css`. Retrieved 2026-11-20.

## Zeus UI (primary) — MIT, registry v0.4.12

Installed with `pnpm dlx shadcn@latest add https://myzeusui.com/r/<name>.json` (`components.json` aliases components to `components/kits/zeus`).

| Item | Files | Used for |
|---|---|---|
| `button` | `base/buttons/button.tsx` | all buttons; `LinkButton` reuses its style map |
| `chip` | `base/badges/chip.tsx` | type, origin, source-kind and status chips |
| `status-dot` | `base/badges/status-dot.tsx` | client lifecycle and run status |
| `tabs` | `base/tabs/tabs.tsx` | section tabs (Next videos, Radar, Your channel, Competitor detail, Settings). **Fork**: native ARIA tabs (controlled only), so screens with tabs don't load react-aria. |
| `segmented-control` | `base/segmented-control/segmented-control.tsx` | window 7/30/90, lens, sort. **Fork**: a native radio group instead of react-aria's ToggleButtonGroup, so the page header doesn't load react-aria. |
| `task-list` (+ `agent-log`, `agent-thinking`) | `application/task-list`, `application/agent-log`, `application/agent-thinking` | live stage rail, driven by the controlled `revealed` prop from run events |
| `web-search` (+ `avatar`, `social-button`) | `application/web-search`, `base/avatar`, `base/social-button` | source routing trail (pages read per source) |
| `selene-shimmer` | `application/selene/shimmer.tsx` | loading skeletons |
| `use-count-up`, `use-selection-bounds`, `use-hydrated-reduced-motion`, `cx` | `hooks/*`, `lib/cx.ts` | registry dependencies |
| `chart-card` (+ `tooltip`) | `application/charts/{chart-card,card-surface,chart-cell,chart-plot}.tsx`, `base/tooltip/tooltip.tsx` | the shared frame of every chart (headline, legend, plot sizing). Our marks are SVG in `components/charts/*` instead of the recharts-based cards, which would add ~100 KB gz. `components/charts/gap-matrix.tsx` is a **fork** of `heatmap-chart-card` (rows × columns of tinted cells) without its `pro-chart` period picker; cells are links with screen-reader text. Copied from the registry JSON. |
| `artemis-folder-card` | `application/artemis/folder-card.tsx` | **fork** of `components/cards/variants/04-folder.tsx`: props instead of demo copy, our tokens instead of `artemis-tokens`, focus lifts like hover, CSS transitions instead of `motion`. Copied from the registry JSON rather than the CLI, because the item pulls `artemis-controls` (headlessui, lucide, 22 files) for two easing constants. Wrapped by `components/competitors/competitor-folder.tsx`. |

The `tokens` item (theme, typography, 30 KB preflight) is not used; `styles/kits/zeus.css` defines only the semantic names the vendored files reference.

## BoardUI (free tier)

Installed with the BoardUI MCP `install_components` (`drawer`, `theme-toggle`, later `toast`, `command-palette`, `popover`, `select`, `meter`, `switch-card`), then moved from `components/{base,foundations,application}` and `utils/` into `components/kits/boardui/` with imports rewritten.

| Item | Used for |
|---|---|
| `drawer` (+ `modal`, `close-button`, `button`, `spinner`, `scroll-fade`, `direction`, `directional-icon`, `cx`) | activity drawer, mobile navigation |
| `theme-toggle` (+ `switch`, `template-copy`) | light/dark switch; storage key mirrored in `app/layout.tsx` to avoid a flash |
| `toast` (+ `notification`, `avatar`) | saved / dismissed (with Undo) / copied / run started. The store and `toast()` live in `toast-store.ts`; the `Toaster` renderer (which pulls `motion`) loads lazily from `components/ui/toaster.ts`. |
| `command-palette` (+ `kbd`) | ⌘K sections, clients and actions; loaded on first open (`components/shell/command-menu.tsx`) |
| `popover` (+ `use-dismiss-on-outside-press`) | mark done (video link), dismiss reasons, account and workspace menu |
| `select` (+ `menu-styles`, `chevrons`, `label`, `hint-text`) | creator sort, competitor set, refresh hour |
| `meter` (+ `progress`) | description byte budget against YouTube's 5,000 |
| `switch-card` | fallback provider, pause client, notification choices |

BoardUI shares Zeus's semantic token vocabulary, so `styles/kits/zeus.css` covers both.

## Beautiful UI — "MIT License" per site footer (no licence file in the registry)

Installed with `pnpm dlx shadcn@latest add https://www.beautifului.dev/r/<name>.json`, moved to `components/kits/bui/`. `foundation.css` is not imported; `styles/kits/bui.css` aliases its names and copies the keyframes the items use.

| Item | Used for |
|---|---|
| `context-cards` | "What we learned" fact stream in the live panel, badged by `source_kind` |
| `loading-state` | live panel status line; **fork**: elapsed time counts from the run's start, the remote-video variant is removed |
| `task-rows` | run history in the activity drawer |
| `entity-chip`, `value-pill` | channels and ratios inside insight sentences |
| `recommendation-card` | not vendored; its three-bar meter is re-implemented in `components/ui/confidence-meter.tsx` |

## libraries.dev — MIT npm packages

| Package | Used for |
|---|---|
| `thinking-orbs` 0.3.2 | analysis orb; state follows the active stage, paused with reduced motion |
| `border-beam` 1.4.1 | glow on the single active run panel; off with reduced motion |

## Our own (no kit equivalent)

`components/shell/coach-marks.tsx` (three one-time tips; Zeus/BoardUI tooltips close on blur, which suits hover help, not a guided first run) and `components/cards/description-diff.tsx` (insert-only diff read from the backend's unified `preview_diff`; Zeus `review-diff` is a paid item).

## Not used

Rare UI folder (licence requires a visible credit link; Zeus folder used per owner decision O5). Zeus `input` (pulls phone/country pickers; `components/ui/text-field.tsx` instead), `tooltip`, `stat-cards`, `agent-progress`, `counter` (scroll-driven). BoardUI Pro items. Beautiful UI `thinking-state` and `approval-card` (timer-driven demos).
