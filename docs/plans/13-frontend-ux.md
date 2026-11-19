# 13 — Frontend UX & information architecture

Status: plan (built after the engine). Owner of: screens, flows, navigation, design tokens, component sourcing, client-side state, SSE consumption, UI copy for every state. Consumes the `/api/v1` surface defined in 11 and the domain objects of 03, 04, 06, 07, 08 and 09. Writes no backend code.

Research artefacts (all saved locally, referenced below as `[R:<file>]`, all paths under `/tmp/ux-research/`):

| File | What it shows |
|---|---|
| `spotlit-full.png`, `spotlit-fold.png`, `slices/spotlit-0..6.png` | usespotlit.ai landing, full page (1440 × 12584) and sections |
| `slices/spotlit-2.png` | Spotlit "Your morning brief" card: KPI tiles, numbered next steps, agent panel, Approve |
| `slices/spotlit-3.png` / `slices/spotlit-0.png` | Spotlit analytics panel: tabs, line chart, benchmark table, share-of-voice donut, KPI sparkline tiles |
| `spotlit-signup-fold.png` | Spotlit sign-up: split layout, form left, live preview panel right, step eyebrow |
| `spotlit-css.json` | computed styles + all `:root` / `.dark` CSS custom properties (250 vars) |
| `spotlit-brief-probe.json`, `spotlit-signup-probe.json` | per-element computed styles of the brief card and sign-up form |
| `zeusui-full.png`, `zeus-folder-card-fold.png`, `zeus-task-list-fold.png`, `zeus-agent-progress-fold.png` | Zeus UI home and component pages |
| `zeus-registry.json`, `zeus-llms.txt`, `zeus-agents.json`, `zeus-tokens.json`, `zeus-task-list.json`, `zeus-web-search.json`, `zeus-agent-progress.json` | Zeus registry (v0.4.12, MIT, 336 items), token CSS, component sources |
| `beautifului-full.png`, `beautifului-fold.png`, `slices/beautifului-2..4.png` | Beautiful UI page (dark) |
| `beautifului-registry.json`, `bui-dl/*.json` | Beautiful UI shadcn registry and component sources (found at `/r/registry.json`) |
| `librariesdev-full.png`, `librariesdev-text.txt` | libraries.dev catalogue |
| `boardui-full.png` | boardui.com home (component list taken from the BoardUI MCP `list_components`) |
| `rareui-full.png`, `rareui-folder-fold.png`, `rareui-dl/*` | rareui.com, its Folder component page, source, registry item and LICENSE |
| `rareui-registry.json`, `shitui-registry.json` | the *other* "RareUI" (Codewithswappy, renamed ShitUI): no folder component |

Scripts used: `/tmp/ux-scripts/shot.py` (full-page screenshot + computed-style extraction), `probe.py` (style chain for elements found by text), `input.py` (form focus state). Playwright 1.60 (Python) drove the installed Brave browser because the Chromium download failed (`playwright install chromium` → download error).

---

## 1. Purpose & business value

The engine produces a lot: own-channel performance, competitor scorecards, outliers, trait lift, trending, news opportunities, description refreshes, creators, plus a live pipeline. If all of that lands on one screen, a marketer at a clinic or an agency sees a dashboard, not a decision. This component turns the engine's output into a few concrete actions a day, each backed by evidence the user can check in two clicks.

How the UI drives conversions for onboarded businesses:
- **A daily brief of 3–5 actions** ("make this video", "add this line to that description", "react to this news before Thursday"). Each action has one primary button. The brief is the product. Everything else is drill-down.
- **Conversion lens visible on every card.** The buyer question a card answers is shown verbatim, so the business can see why a video should bring buyers, not just views. Website and OAuth data sit in clearly labelled side panels and are never blended into YouTube numbers.
- **Evidence earns trust.** Reference videos with thumbnail, channel, publish date, views at the same age and outlier ratio are one click away. Honest confidence with sample size stops users from acting on weak signals. When data is thin, the UI says so and offers one fix ("Widen to 30?").
- **Time to first value.** Onboarding streams real discoveries (pages read, offerings found, competitors found) while the run works, so a 1–2 minute wait feels like watching an analyst, and the user confirms competitors while their attention is high.
- **Closing the loop.** Save / dismiss (with reason) / mark done (link the published video) feeds 07's feedback loop and 09's refresh-impact tracking, so recommendations improve per client.

The assignment lists UI design as out of scope for the engine ("Mira provides"). Our Next.js app is our own presentation of the same API. The API stays UI-agnostic (`?format=contract`), so a Spotlit UI can consume it unchanged.

## 2. Scope / non-scope

In scope:
- Information architecture, navigation, routes, and screen-by-screen content with progressive-disclosure rules.
- Onboarding flow, including the live analysis experience driven by SSE `RunEvent`s, confirmation, analysis progress and first brief.
- Card designs: recommendation (all contract types), news opportunity, description refresh diff, creator, competitor folder + competitor detail, unanswered-question note, notices.
- The design system: one token set mapped onto Zeus UI, BoardUI, Beautiful UI, libraries.dev and Rare UI components; the component sourcing map; vendoring and adaptation rules.
- Client architecture: Next.js App Router, TanStack Query, the SSE hooks, the generated API client, URL state, error model handling.
- Empty / loading / error / thin-data / stale states for every screen; accessibility; responsive layout; dark mode; performance budgets.
- Internal screens for Evaluation/Ops (role-gated, read-mostly), phased after the core product.
- UI copy for every user-facing message in §8.

Non-scope:
- Creating the frontend app now (planned only).
- Any number computation. The UI formats numbers; it never derives a metric from other metrics (except trivially formatting, e.g. `3.62 → 3.6×`). The one client-side composition (the brief, §5.1) only orders and selects existing objects.
- The "Ask your analyst" chat (paused by owner).
- Publishing to YouTube, editing descriptions through the API (we give copy buttons; the user pastes in YouTube Studio).
- Email/push notifications (no infra yet; §13).
- Marketing site / pricing pages.

## 3. Inputs & outputs

### 3.1 Inputs (consumed from 11's `/api/v1`, see 11 §12.1)

| Area | Endpoints | Objects |
|---|---|---|
| Session & tenancy | auth endpoints + `GET /me` (proposed, §13.6) | `Me{user, workspaces[{id, name, slug, role}]}` |
| Clients | `GET/POST /clients`, `GET/PATCH/DELETE /clients/{id}` | `Client`, `ClientCreate`, `ClientPatch` |
| Channel resolution | `POST /clients/{id}/channel/resolve` | `ChannelResolveOut.candidates[{channel, ownership_evidence, confidence}]` |
| Onboarding | `POST /clients/{id}/onboarding`, `POST /clients/{id}/confirm-setup` | `PipelineRun`, `ConfirmSetupOut` |
| Profile | `GET/PATCH /clients/{id}/profile`, `POST …/profile/recrawl` | `BusinessProfileView{profile, overrides, effective, version, completeness, partial, sources}` (03's `BusinessProfile` with `ClaimMeta` per fact) |
| Topics | `…/keywords`, `…/buyer-questions` (+ bulk-status) | `KeywordItem`, `BuyerQuestionItem` |
| Competitors | `…/competitors` (+ confirm/reject/add/suggest) | `CompetitorView` (needs 04's fields, §13.4) |
| Runs | `POST /clients/{id}/runs`, `GET /runs/{id}`, cancel, retry, `GET /runs/{id}/events` (SSE), `GET /runs/{id}/events.json`, `GET /clients/{id}/events` (SSE) | `PipelineRun`, `StageRunView`, `RunEvent` with `EventPayload` union, `RunNotice` |
| Analytics | `…/analytics/own-performance`, `/competitors`, `/competitors/{channel_id}`, `/competitors/{channel_id}/videos`, `/trending`, `/outliers`, `/patterns`, `/topics` | `ResultEnvelope[...]` wrapping 06's `OwnPerformanceReport`, `ChannelScorecard`, `TrendingReport`, `TraitLift`, `VideoMetrics` |
| Recommendations | `GET /clients/{id}/recommendations`, `GET /recommendations/{id}`, `PUT /recommendations/{id}/feedback` | `ResultEnvelope[Page[RecommendationOut]]` (+ 202 `cards_run`) |
| News | `…/news/opportunities`, `…/news/signals`, `POST /news/opportunities/{id}/dismiss` | `NewsOpportunityView`, `NewsSignal` |
| Refresh | `…/refresh-suggestions`, `POST /refresh-suggestions/{id}/applied`, `/dismiss` | `RefreshSuggestionView` (09's `DescriptionRefresh`) |
| Creators | `…/creators` | `CreatorView` |
| Integrations | `GET /integrations/youtube/connect`, `DELETE /clients/{id}/integrations/youtube` | redirect URL |
| Internal | `/evaluation/*`, `/ops/*` | 12's report objects |

Every analytics/recommendation read arrives in a `ResultEnvelope{data, window_days, as_of, run_id, generated_at, freshness{data_through, age_hours, stale, stale_reasons}, notices[]}`. The UI renders `freshness` and `notices` generically on every screen (§4.9).

### 3.2 Outputs (owned by 13)

Screens and routes (§4.3), the token set and component library (§4.5–4.6), and these client-side view models. They are TypeScript types derived from the generated OpenAPI types; field lists in pydantic-style notation:

```text
BriefItem                                   # one numbered row in Today
  id: str                                   # source object id
  kind: Literal["video_idea","pattern","news","refresh","creator","watch","setup"]
  rank: int                                 # 1..5
  headline: str                             # = card.summary (LLM-written, evidence-validated by 10) or template
  evidence_line: str                        # first why_it_works entry or template from typed fields
  lens: list[Literal["views","conversion"]]
  confidence: ConfidenceView | None
  urgency: Literal["now","this_week","evergreen_window"] | None
  expires_at: datetime | None
  primary_action: Literal["open","copy_insertion","widen_window","confirm_competitors","connect_oauth"]
  href: str                                 # deep link to the card in its section

ConfidenceView
  label: Literal["High","Medium","Low","First-mover"]
  outliers: int; channels: int
  bars: Literal[0,1,2,3]                    # High 3, Medium 2, Low 1, First-mover 0 (+ text label always)
  caps_applied: list[str]                   # shown in the evidence drawer only

ReferenceVideoView
  video_id: str; url: str; title: str
  thumbnail_url: str                        # i.ytimg.com/vi/{id}/mqdefault.jpg when API omits it
  channel_title: str; channel_id: str; channel_role: Literal["competitor","niche","client","creator"] | None
  published_at: datetime
  age_label: str                            # "views at day 7" | "views at day 3 (interpolated)"
  views_at_age: int | None
  outlier_ratio: float | None               # None → "no baseline yet"
  views_basis: Literal["observed","interpolated","backfilled"] | None

LiveRunState                                # reducer state for one run stream (§5.4)
  run_id: str; status: RunStatus; percent: float; last_seq: int
  stages: dict[str, StageView]              # status, counts, started_at, finished_at, message
  facts: list[FactItem]                     # profile_field / channel_resolved payloads, revealed in order
  pages: list[PageItem]                     # page_read / page_skipped
  candidates: list[CandidateItem]           # competitor_candidate
  insights: list[InsightItem]               # outlier_found, trait_lift, news_found, card_written, refresh_suggested
  notices: list[RunNotice]
  connection: Literal["connecting","live","reconnecting","polling","closed"]
  pending_reveal: list[RunEvent]            # pacing queue (§5.5)

ThinDataPrompt
  message: str                              # "Not enough outliers in the last 7 days."
  found: {outliers, channels, cards}; required: {outliers, channels, cards}
  action: Literal["widen_window","add_competitors","add_keywords","add_buyer_questions","connect_oauth","wait"]
  widen_to: Literal[30,90] | None; projected: {outliers, channels, cards} | None
  cta_label: str                            # "Widen to 30?"

UiPreferences (localStorage only; no API data)
  last_workspace_id: str; last_client_id_by_workspace: dict[str, str]
  sidebar_collapsed: bool; theme: Literal["system","light","dark"]
  dismissed_hints: list[str]; density: Literal["comfortable","compact"]
```

## 4. Internal design

### 4.1 Design language captured from usespotlit.ai (measured, not guessed)

Spotlit runs two type systems: a marketing system (Instrument Serif display + Schibsted Grotesk text) and an app system declared under `.app-light, .dark` that sets `--font-ui: Inter` and `--font-heading: Schibsted Grotesk` `[R:spotlit-css.json]`. The sign-up flow, which is the closest thing to product UI that is public, renders in Inter `[R:spotlit-signup-probe.json]`. We adopt the **app system**, with the serif reserved for one moment of delight (onboarding hero and the brief greeting).

**Typography**
| Role | Spotlit value (computed) | Ours |
|---|---|---|
| Display (marketing H1) | Instrument Serif 400, 87.84px / 93.99px, tracking −1.76px; H2 52–60px, tracking −1.6 to −2px | `font-display` Instrument Serif, used only on onboarding hero (48px) and brief greeting (34px) |
| Card/section title | Schibsted Grotesk 500, 34px / 37.4px, tracking −0.9px ("Your morning brief.") | `text-title` Schibsted Grotesk 500 28/32, −0.6px (page titles); 20/26 −0.3px (section) |
| Item headline | Schibsted 700, 17px / 21.25px, tracking −0.3px ("Publish the comparison AI is looking for") | `text-headline` 600 16/22 −0.2px |
| Body / UI | Inter 400, 16px / 24px; labels Inter 500 14/20 | Inter 400 15/22 (app density), 14/20 secondary |
| Caption / sub | 13px / 19.5px, color #525252 ("vs. prior 7 days"); 12/16 #5c6270 helper text | 13/18 `text-muted` |
| Eyebrow | 10px, weight 500, letter-spacing 2px, uppercase, color #5b5f66 ("PROACTIVE AGENT FOR…"); "01 · YOUR ACCOUNT" with a blue dot | 11px 500, tracking 0.16em, uppercase |
| Metric | Schibsted 500, 40px / 40px, tracking −1.6px ("34%"); delta 14px 700 red rgb(180,35,24) "↓ 4 pp" | `text-metric` Schibsted 500 36/36 −1.4px, `tabular-nums` |

**Colour (light; HSL tokens are Spotlit's own `:root` values, hex computed)**
| Token | Value | Use |
|---|---|---|
| background | hsl(0 0% 98%) #fafafa | page |
| card / popover | #ffffff | surfaces |
| sidebar | hsl(0 0% 96%) #f5f5f5 | nav rail |
| foreground / primary | hsl(0 0% 9%) #171717 | ink, primary buttons |
| secondary / accent | hsl(0 0% 94%) #f0f0f0 | subtle fills |
| muted-foreground | hsl(0 0% 32%) #525252 | secondary text |
| border | hsl(0 0% 90%) #e6e6e6 (app form borders measured #e3e7ee) | hairlines |
| input | hsl(0 0% 54%) #8a8a8a | input stroke when emphasised |
| ring / info / chart-1 | hsl(219 45% 38%) #35548d | focus ring, links, primary series ("ink blue") |
| success | hsl(145 46% 33%) #2d7b4e | positive |
| danger | hsl(4 62% 47%) #c2372e | negative |
| warning | hsl(33 64% 33%) #8a5a1e (ink #7b480a) | caution |
| chart-2..6 | #525252, #6b85b8, #8a8a8a, #2b4678, #65758b | blue/grey categorical ramp |
| vivid-up / down | #21c45d / #dc2828 (ink #137737 / #ba1c1c) | deltas |
| form ink (sign-up) | #0f172a (slate-900), primary CTA bg rgb(15,23,42) | primary CTA |
| focus outline (sign-up) | 2px solid rgb(36,99,235) #2463eb | keyboard focus |
| brand shadow tint | rgba(11,42,107,·) | elevated cards |

Dark (`.dark` values, also Spotlit's): background #171717, card #212121, sidebar #1c1c1c, secondary/accent hsl(0 0% 16%), border hsl(0 0% 18%), muted-foreground hsl(0 0% 69%), ring #95aadf, success #4ec17a, danger #ff6b6b, warning #e2ba4b.

**Radii** (`:root`): `--radius .75rem`, `--sl-radius-control 8px`, `--sl-radius-input 12px`, `--sl-radius-card 20px`, `--sl-radius-tile 24px`, `--sl-radius-button 9999px`. Measured: brief card 28px, brief panel 20px, KPI tiles and list rows 16px, inputs 14px (52px tall), hero hq-cards 14px, pills 99px.

**Shadows** (measured): brief card `0 18px 40px -24px rgba(11,42,107,.45)` with `1px solid rgba(255,255,255,.7)`; sign-up stage `0 40px 80px -50px rgba(11,42,107,.6), inset 0 0 0 1px rgba(255,255,255,.25)`; inputs `0 1px 2px rgba(15,23,42,.04), 0 10px 30px -18px rgba(11,42,107,.25)`; floating menu `0 24px 60px rgba(0,0,0,.18)`; default `--card-shadow: 0 0 #0000` (flat cards with 1px borders are the norm).

**Spacing & layout**: brief card padding 36px; KPI tiles `16px 18px`, gap 12px; list rows `14px 16px`, gap 10–14px; panel header `12px 18px` on #fafafa; footer `14px 20px`. Buttons: primary 44px pill, `0 22px`, Schibsted 600 15px; secondary pill white with 1px #767676 border; app CTA 52px pill slate-900. Hero hq-cards: bg `rgba(14,14,16,.035)`, border `1px solid rgba(14,14,16,.09)`, radius 14px, padding `10px 14px`.

**Patterns we take from Spotlit**
1. The **morning brief** `[R:slices/spotlit-2.png]`: eyebrow with time ("YOUR CMO AGENT · 8:00 AM"), title, one-line subtitle ("Three signals. Three priorities."), 3 KPI tiles each tagged with its source chip and "vs. prior 7 days", then a numbered "What we should do next" list where each row has a bold one-line headline, a one-line evidence sentence, and one pill action. This is our Today screen.
2. **Agent panel with "Why" + "The fix your agent drafted" + Approve** → our expanded recommendation card (why it works / what to make / actions).
3. **Analytics tabs with "LAST 28 DAYS" label** `[R:slices/spotlit-3.png]` → window label always visible top-right of each analytics panel.
4. **Split onboarding**: form left, live illustrative panel right on a sky-blue gradient, with an honest note ("Illustration of the goal, built from your answers. Not a live result.") `[R:spotlit-signup-fold.png]`. Our right panel shows real streamed results, so our note reads "Live: read from your site and YouTube's public data."
5. **Orb mascot** (glassy orb with two eyes) as the agent's presence. We use libraries.dev `thinking-orbs` for the same role without copying Spotlit's asset.
6. Quiet greys, one ink-blue accent, colour used only for meaning (deltas, status), serif only for emotional headlines.

### 4.2 Information architecture

```
Workspace (agency or business)            ← workspace switcher (only if >1 workspace)
└── Client (business being advised)        ← client switcher in sidebar header (⌘K too)
    ├── Today                 /c/[clientId]/today          brief: 3–5 actions + "what changed" tiles
    ├── Next videos           /c/[clientId]/ideas          recommendations: video ideas, patterns, formats
    ├── Competitors           /c/[clientId]/competitors    folder grid → /competitors/[channelId]
    ├── Your channel          /c/[clientId]/channel        performance vs baseline · refresh suggestions · conversion
    ├── Niche radar           /c/[clientId]/radar          news opportunities · trending · outliers · unanswered questions
    ├── Creators              /c/[clientId]/creators       ranked creators with references and collaborations
    ├── Settings              /c/[clientId]/settings/*     business profile · keywords & buyer questions · competitors · integrations · schedule
    └── (Activity drawer)     run history, live run, notices
Workspace level
    ├── Clients               /w/[slug]/clients            agency home: client list with status + last brief
    └── Ops (internal)        /w/[slug]/ops/*              quota · runs · evaluation (review queue, outlier labels, backtests)
Auth: /login, /signup, /verify.  Onboarding: /w/[slug]/new → /c/[clientId]/setup/{analyze,confirm,analysis}
```

Navigation rules:
- **Six primary items**, always in the same order. Labels are plain verbs/nouns a marketer uses. "Next videos" instead of "Recommendations"; "Niche radar" holds everything time-sensitive.
- **Global controls in the page header, right side:** window selector `7d · 30d · 90d` (segmented control) and lens toggle `Views · Conversion · Both` (segmented control). Both live in the URL (`?w=30&lens=both`), apply to every section, and show on Today, Next videos, Competitors, Your channel and Radar. Settings and Creators (creators are ranked independent of lens) hide the lens toggle.
- **Header left:** page title + one-line "what this answers" subtitle (e.g. Competitors: "How are your competitors doing? Cadence, performance, what's working."). This maps each screen to one of the assignment's five questions.
- **Freshness line** under the header on every analytics screen: "Data through 20 Nov, 06:10 IST · refreshed daily · 30-day window" + live-run pill when a run is active.
- **Client switcher**: dropdown with search, lifecycle status dot (analyzing / active / needs confirmation / failed), "Add client". Keyboard `⌘K` → command palette with clients, sections and actions ("Widen to 90", "Re-run analysis").
- **Activity drawer** (bell-less; a small run-status pill in the header opens it): current run progress, last 10 runs (status, duration, notices), and notices from the latest envelope.
- **Deep links**: every card has a stable URL (`/c/[id]/ideas?card=rec_…` opens the evidence drawer), so a brief row, a share link or the command palette lands on the exact card.

### 4.3 Screen-by-screen: what's visible, what's behind drill-down

Disclosure levels used everywhere:
- **L0 glance**: one line per item, one number per tile, one action per row.
- **L1 card**: summary, chips (type, lens, confidence), one evidence line, buyer question, actions.
- **L2 expand in place**: all `why_it_works`, `fit_to_client`, expected range with basis, reference-video strip (3–5 thumbnails).
- **L3 evidence drawer** (right side sheet, 560px): reference-video table with method and maths ("18,400 views at day 7 vs channel median 5,100 → 3.6×"), sources labelled by origin (YouTube public / YouTube Analytics (owner) / Website / News), confidence caps, "How this card was built" trace.
- Never deeper than L3. No modal-on-modal. A drawer can switch content (e.g. from a card to a referenced competitor) with a back arrow.

**Today** (default landing)
- Eyebrow: "YOUR BRIEF · {weekday} {time local}"; title (serif): "Good morning, {first name}." on the first view of the day, "Today for {client}." otherwise. Subtitle: "{n} actions. Evidence from {k} channels in the last {w} days."
- **What changed** — 3 tiles, each from one source, each with its source chip and a comparison window. No merged metric:
  1. "Your videos vs your normal" — `OwnPerformanceReport.median_ratio_window` ("1.3× your baseline", delta vs `median_ratio_prev_window`). Chip: YouTube public. Hidden when the client has no channel; replaced by "Connect your channel" tile.
  2. "Competitor outliers" — count of `is_outlier` competitor videos in the window, "+{n} since yesterday". Chip: YouTube public.
  3. "Open opportunities" — news opportunities with `urgency ∈ {now, this_week}` not expired. Chip: News.
- **What to do next** — numbered list of 3–5 `BriefItem`s (§5.1). Each row: number badge, headline (16px semibold, 2 lines max), evidence line (13px muted, 1 line), right-aligned type pill (Video idea / News / Refresh / Creator) that opens the card. News rows show a countdown chip ("closes in 2 days").
- **Below the fold, collapsed by default**: "Also worth a look" (next 5 items), and the thin-data prompt if any.
- Hidden from Today: charts, tables, scorecards. Today has no chart at all.

**Next videos** (`ideas`)
- Tabs: `All · Video ideas · Title patterns · Thumbnail patterns · Formats` with counts. `news_opportunity` cards appear in Radar and in All; `description_refresh` lives in Your channel; `creator` in Creators.
- Top 3 cards expanded to L1; "Show {n} more" reveals the rest (07 caps per window: 5/8/10).
- Filter row (collapsed behind "Filter"): buyer question, confidence, status (new / saved / done / dismissed).
- Right rail on ≥1280px: "Unanswered on YouTube" panel (07's `WhiteSpaceNote`s): buyer questions with zero coverage, each with "videos checked, channels checked, window". No confidence, no range (07 §5.11). Below 1280px it becomes a collapsed section at the bottom.

**Competitors**
- Folder grid (§4.8.5), 3–4 columns, competitor_set only. Each folder: channel avatar + name, relation chip (Direct / Aggregator / Content), uploads/week, median ratio (their own baseline), outlier hit rate "3 of 12", and the three fanned cards show their top-3 outlier thumbnails in the window.
- Sort: outliers / cadence / median ratio / name. Lens = conversion sorts by buyer-question coverage.
- Collapsed below: "Also watching" (niche_set, benchmark_set) as compact rows with "Benchmark — used for format and title ideas only" label (04 §3.1 rule).
- Header action: "Gap matrix" toggle → heatmap of competitors × the client's buyer questions/keywords (cells = videos and outliers per topic, from `ChannelScorecard.topic_coverage` / 07's `GapReport.cells`). Hidden by default because it is dense.
- Detail `/competitors/[channelId]` (folder opens with a shared-element transition): tabs `Overview · What's working · Their outliers · Gap vs your questions · All videos`.
  - Overview: cadence (uploads/week, longest gap, days since last upload, format mix bar), performance (median views at age per segment with method, median ratio, outlier hit rate with Wilson interval as "3 of 12 (likely 1–6 of 12)" text), subscriber count (or "hidden"), size vs client as text ("about 4× your channel"), collaborations (mention vs disclosed paid, labelled).
  - What's working: `top_traits` (TraitLift) as bar list: "Question titles: 4 of 5 outliers vs 8 of 30 videos (2.1× lift)", status `supported`/`exploratory` shown as a label; exploratory collapsed.
  - Their outliers: video cards sorted by ratio with views at age and ratio; "Not copying titles" note: titles are shown as evidence, and the copy button is intentionally absent.
  - Gap vs your questions: one row per client buyer question/keyword: they cover (videos, outliers, best ratio, latest) vs you cover. "You haven't covered this" badge links to the matching recommendation if one exists.
  - All videos: data table (virtualised) with filters (topic, segment, outlier only).

**Your channel**
- Tabs: `Performance · Refresh suggestions · Conversion` (Conversion visible only with OAuth or website data; otherwise shows a connect prompt as its empty state).
- Performance: line chart of views-at-age ratio per video over publish date with the 1.0× baseline line; segment toggle (long-form / Shorts / live), best and worst lists, by-topic and by-length-band bar lists (collapsed), consistency stats. Every number states its comparison age and method ("views at day 7; 4 values interpolated").
- Refresh suggestions: description diff cards (§4.8.3), sorted by 09 priority. Header text from `RefreshEligibility.reasons` ("Your niche moves fast: 6 relevant news items in 30 days. We check monthly.") or "Refresh rarely needed for your business" with a hygiene-only list.
- Conversion: three separate, labelled panels — "YouTube Analytics (owner): subscribers gained, traffic sources, search terms", "From your descriptions: CTA coverage" (YouTube public), "Website (not from YouTube)" — side by side, never combined.

**Niche radar**
- Tabs: `News opportunities · Trending · Outliers · Unanswered questions`.
- News opportunities first because they decay. Cards sorted by urgency then priority; expired ones disappear (moved to a collapsed "Expired" list).
- Trending: `TrendingReport.items` (ratio, velocity) and `rising_topics` ("Cost explainers: 3 → 7 uploads, 1 → 4 outliers vs prior window"). Chart-ranked items (`chart_items`, mostPopular ∩ client topics) under a separate "On YouTube's trending chart" label.
- Outliers: scope switch `Competitors · Niche · You`, scatter (age vs ratio) collapsed by default; list first.

**Creators**
- Ranked list of `CreatorView` cards (§4.8.4). Filters: subscriber band, collaboration history. No lens toggle.

**Settings** (sub-nav left)
- Business profile: facts grouped (identity, offerings, value propositions, locations, conversion actions, freshness profile) as editable rows; each machine fact shows a source link on hover ("Read on /services/cardiology"). Edits write `overrides` with `If-Match`; on success a banner offers "Re-run analysis" (§8).
- Keywords & buyer questions: two lists with status tabs (Active / Suggested / Rejected), bulk accept/reject, add, priority 1–5, source chip (profile / you / Spotlit AI tracking). Minimum one active topic ref enforced with explanation.
- Competitors: table of all sets with move between sets (competitor / niche / benchmark), confirm/reject with reason, add by URL/@handle, counter "6 of 3–10".
- Integrations: YouTube OAuth connect/disconnect with scope explanation and "owner analytics are shown only to your workspace"; Spotlit AI tracking (stub).
- Schedule: daily refresh hour (local), default window, pause client.

**Clients** (agency home)
- Table/cards: client name, lifecycle, last refresh, top action headline, open opportunities count, quota share today (owner/admin only). Empty state = onboarding CTA.

**Ops (internal, role owner/admin)** — phase 2
- Quota: today's YouTube units and search calls with limits, by client; LLM spend; Tavily credits (`OpsQuotaOut`).
- Runs: table with stage timings (p50/p95, < 2 min target line).
- Evaluation: review queue (rate a card 1–5 on specific / evidenced / relevant / actionable / not obvious, 12's rubric), outlier labelling (video card + "outlier? yes/no"), backtest reports (read-only).

### 4.4 Application architecture

```
frontend/
  app/
    (auth)/login, signup, verify
    (app)/w/[slug]/layout.tsx              # workspace guard, AppShell, QueryClientProvider boundary
    (app)/w/[slug]/clients/page.tsx
    (app)/w/[slug]/new/page.tsx            # onboarding step 1
    (app)/c/[clientId]/layout.tsx          # client guard, header (window/lens), useClientEvents
    (app)/c/[clientId]/setup/{analyze,confirm,analysis}/page.tsx
    (app)/c/[clientId]/{today,ideas,competitors,channel,radar,creators}/page.tsx
    (app)/c/[clientId]/competitors/[channelId]/page.tsx
    (app)/c/[clientId]/settings/{profile,topics,competitors,integrations,schedule}/page.tsx
    (app)/w/[slug]/ops/{quota,runs,evaluation}/page.tsx
  lib/
    api/schema.d.ts                        # generated by openapi-typescript from /api/v1/openapi.json
    api/client.ts                          # openapi-fetch instance: credentials, X-Requested-With, Idempotency-Key, problem+json
    api/queries/*.ts                       # queryOptions factories per resource (keys owned here)
    api/mutations/*.ts                     # mutation hooks with optimistic updates
    sse/useRunStream.ts, useClientEvents.ts, runReducer.ts, revealQueue.ts
    view/                                  # mappers API → view models (BriefItem, ConfidenceView, ReferenceVideoView…)
    format/                                # numbers, ratios, dates, relative time, i18n
    url-state.ts                           # nuqs parsers for w, lens, tab, card, filters
  components/
    ui/                                    # our primitives (Button, Chip, Tabs…) — thin wrappers over vendored kits
    kits/zeus/  kits/boardui/  kits/bui/  kits/rare/  kits/libraries/   # vendored sources, unmodified where possible
    agent/                                 # LiveAnalysis, StageRail, FactStream, PageTrail, CandidateFolders, RunPill
    cards/                                 # RecommendationCard + type renderers, NewsCard, RefreshDiffCard, CreatorCard
    competitors/                           # CompetitorFolder, FolderGrid, GapMatrix, ScorecardPanels
    evidence/                              # EvidenceDrawer, ReferenceStrip, ReferenceTable, SourceChip, MathLine
    states/                                # EmptyState, ErrorState, ThinDataPrompt, StaleBanner, QuotaBanner, NoticeList
    charts/                                # wrappers over Zeus chart cards, lazy-loaded
  styles/
    tokens.css                             # the single source of truth (§4.5)
    kits/zeus.css  kits/boardui.css  kits/bui.css                         # alias layers
    globals.css                            # @import "tailwindcss"; tokens; kit aliases; base
  registries/
    cardRenderers.ts                       # RecType → renderer
    eventAnimations.ts                     # EventPayload.kind → reveal component
    noticeRenderers.ts                     # RunNotice.code / InsufficiencyNotice.kind → message + CTA
    emptyStates.ts                         # screen key → EmptyState config
```

SOLID / open-closed:
- **Card renderer registry**: `registerCard(type, {Summary, Body, Evidence, actions})`. Adding a recommendation type (e.g. 08's future `live_or_premiere`) adds one file; the list, drawer and brief stay untouched. Unknown types render a generic contract card (all contract fields) — the API documents that enums may grow.
- **Event animation registry**: `registerEventKind(kind, Component)`. 11 says the frontend ignores unknown payload kinds; the registry's default is a no-op.
- **Notice registry**: maps `RunNotice.code` and `InsufficiencyNotice.kind` to copy + CTA. New codes fall back to the server `message`.
- **Kit adapters**: our `components/ui/*` are the only imports allowed in feature code (ESLint `no-restricted-imports` on `components/kits/**`). Swapping a vendor component means editing one wrapper.

Server vs client components:
- Layouts and pages are React Server Components that prefetch the screen's first queries with the user's cookie and pass them through `HydrationBoundary` (TanStack Query advanced SSR pattern). Interactive regions (`cards`, `agent`, `charts`) are client components.
- Auth gating: Next.js request interception (`proxy.ts` in Next 16; it was `middleware.ts` before — verify name at build time) only checks the session cookie exists and redirects to `/login`. Real authorization happens in the API (01 tenant scoping); a 401/403/404 from the API is handled in the page (§8).

### 4.5 Design tokens: one source of truth, three alias layers

Problem: the five sources use three token vocabularies.
- Zeus UI and BoardUI share **one semantic vocabulary**: `--color-text-primary|secondary|tertiary`, `--color-background-primary|secondary|tertiary|quaternary-{default,hover}`, `--color-border-*`, `--color-foreground-icon-*`, `--shadow-xs…xl`, composite type utilities `text-<family>-<weight>` (verified in `zeus-tokens.json` `tokens/theme.css` + `typography.css`, and in the BoardUI MCP design rules). Dark mode by `.dark` class flipping semantic tokens.
- Beautiful UI uses its own: `--page --canvas --surface --inset --hover --ink --ink-2 --ink-3 --line --line-strong --accent --accent-tint --green --orange --red` + `--radius-chip|control|card|window` + `--shadow-hairline|btn|card|raised|overlay`, in OKLCH, and imports `shadow-plugin/unprefixed` (`bui-dl/foundation.json`).
- shadcn conventions (`--background --foreground --primary --muted --border --ring --radius`), which **Spotlit itself uses** (its `:root` declares exactly these names).

Decision: **`styles/tokens.css` defines the GrowthHacker tokens once in shadcn naming with Spotlit's values (§4.1)**, plus a few product tokens. Each kit gets an alias stylesheet that maps its names onto ours. No kit stylesheet is imported as-is.

```css
/* tokens.css (sketch) */
:root {
  --background: 0 0% 98%;  --foreground: 0 0% 9%;   --card: 0 0% 100%;  --sidebar: 0 0% 96%;
  --primary: 222 47% 11%;  --primary-foreground: 0 0% 98%;              /* slate-900 CTA, as in Spotlit app */
  --secondary: 0 0% 94%;   --muted: 0 0% 96%;  --muted-foreground: 0 0% 32%;
  --border: 216 22% 91%;   --input: 0 0% 54%;  --ring: 221 83% 53%;     /* #e3e7ee hairline, #2463eb focus */
  --info: 219 45% 38%;     --success: 145 46% 33%; --danger: 4 62% 47%; --warning: 33 64% 33%;
  --chart-1: 219 45% 38%;  --chart-2: 0 0% 32%; --chart-3: 220 35% 57%; --chart-4: 0 0% 54%; --chart-5: 219 47% 32%; --chart-6: 215 16% 47%;
  --up: 142 73% 27%;       --down: 0 74% 42%;                            /* ink variants for text deltas */
  --lens-views: 219 45% 38%; --lens-conversion: 160 45% 30%;            /* two lens hues, both ≥ 4.5:1 on white */
  --radius-control: 8px; --radius-input: 12px; --radius-card: 16px; --radius-panel: 20px; --radius-hero: 28px; --radius-pill: 9999px;
  --shadow-card: 0 0 0 1px hsl(var(--border));
  --shadow-raised: 0 1px 2px rgba(15,23,42,.04), 0 10px 30px -18px rgba(11,42,107,.25);
  --shadow-hero: 0 18px 40px -24px rgba(11,42,107,.45);
  --shadow-overlay: 0 24px 60px rgba(0,0,0,.18);
  --font-sans: var(--font-inter), var(--font-devanagari), ui-sans-serif, system-ui, sans-serif;
  --font-heading: var(--font-schibsted), var(--font-sans);
  --font-display: var(--font-instrument-serif), Georgia, serif;
  --font-mono: var(--font-geist-mono), ui-monospace, monospace;
  --ease-out: cubic-bezier(.2,.8,.2,1); --dur-fast: 120ms; --dur-base: 200ms; --dur-slow: 360ms;
}
.dark { /* Spotlit .dark values (§4.1) + matching lens hues */ }
```

Alias layers (`styles/kits/*.css`), examples:
```css
/* zeus.css + boardui.css (same vocabulary) */
@theme inline {
  --color-text-primary: hsl(var(--foreground));
  --color-text-secondary: hsl(var(--muted-foreground));
  --color-background-primary-default: hsl(var(--card));
  --color-background-secondary-default: hsl(var(--muted));
  --color-border-table: hsl(var(--border));
  --color-border-focus-ring: hsl(var(--ring));
  --shadow-card: var(--shadow-card);
  /* …all 60 semantic names Zeus components use; generated by a script from tokens/theme.css */
}
/* bui.css — scoped so --accent / --shadow-card don't collide with shadcn names */
[data-kit="bui"] { --ink: hsl(var(--foreground)); --ink-2: hsl(var(--muted-foreground)); --surface: hsl(var(--card));
  --line: hsl(var(--border)); --accent: hsl(var(--info)); --green: hsl(var(--success)); --red: hsl(var(--danger)); … }
```
Rules:
- Zeus's own `tokens` item (its `preflight.css`, 30 KB) is **not** imported; Tailwind v4 preflight is enough. We copy only the semantic names it needs and point them at ours. Zeus's colour primitives are not used.
- Beautiful UI's `foundation.css` is not imported (it re-imports Tailwind and `shadow-plugin`). We copy its component-specific keyframes only.
- Artemis (Zeus's scoped dark-first system with its own `artemis-tokens`) is avoided, except where noted, because it brings a second token system and `lucide-react` + `@headlessui/react`.
- Icons: one set, **Remix Icon** (`@remixicon/react`, used by Zeus and BoardUI). Beautiful UI ships inline SVGs (fine). Any vendored file importing `lucide-react` is codemodded to Remix equivalents.
- A **token lint**: Stylelint/ESLint rule forbids raw hex/rgb and Tailwind palette classes (`bg-blue-500`) in `components/**` except `kits/**` alias files. This is what keeps a mixed-source UI from looking mixed.
- A **kit acceptance checklist** before any vendored component ships: renders correctly in light and dark with our tokens; focus ring uses `--ring`; radius from our scale; no extra font; respects `prefers-reduced-motion`; no network calls; licence recorded in `components/kits/NOTICE.md`.

Fonts (all via `next/font/google`, self-hosted at build, OFL): Inter (UI), Schibsted Grotesk (headings), Instrument Serif (display, 2 uses), Geist Mono (IDs, URLs and byte counts in the description diff gutter). Indic coverage: **Noto Sans Devanagari** added to the `--font-sans` stack (Inter and Schibsted have no Devanagari glyphs); other Indic scripts (Tamil, Telugu, Bengali…) fall back to system Noto via `unicode-range` subsets loaded only when such characters render. Numbers use `font-variant-numeric: tabular-nums` everywhere a number can change.

### 4.6 Component sourcing map (exact names)

Install commands (verified to resolve):
- Zeus UI: `npx shadcn@latest add https://myzeusui.com/r/<name>.json` (registry v0.4.12, `"license": "MIT"`, items `provenance: original`). MCP alternative: `claude mcp add zeus -- npx --yes --allow-remote=all --package=https://myzeusui.com/install/zeus-mcp-0.4.12.tgz zeus-mcp` (from `zeus-llms.txt`).
- BoardUI: MCP `install_components` (or `npx boardui@latest add <name>`); free tier only.
- Beautiful UI: `npx shadcn@latest add https://www.beautifului.dev/r/<name>.json` — the site has **no installer button, but it serves a shadcn registry at `/r/registry.json`** (27 items). Copying via Playwright is unnecessary.
- Rare UI: `npx shadcn@latest add https://rareui.com/r/folder-component.json` (registry file exists in the repo at `public/r/folder-component.json`; the site advertises `npx shadcn@latest add swamimalode07/rare-ui/<name>`). Verify the URL form at install time.
- libraries.dev: npm packages `border-beam`, `thinking-orbs` (MIT).

| Product element | Component | Source | Adaptation |
|---|---|---|---|
| Page frame, mobile drawer, breadcrumb header | `app-shell` | Zeus | strip `three` dependency (only used for decorative avatar); our nav items |
| Sidebar with client switcher | `sidebar` (team switcher → client switcher) | Zeus | same; collapsible rail at 768–1279px |
| Breadcrumbs (competitor detail, settings) | `breadcrumb` | Zeus | — |
| Section tabs (Next videos, Radar, Competitor detail, Your channel) | `tabs` (underline variant, counts) | Zeus | — |
| Window selector 7/30/90, lens toggle | `segmented-control` | Zeus | URL-bound |
| Buttons | `button`, `icon-button`, `link-button`, `button-group` | Zeus | pill radius for primary |
| Type/lens/source chips, deltas | `chip` (bold/subtle tones) | Zeus | lens hues from tokens |
| Counts in tabs/nav | `badge` | Zeus | — |
| Status dots (run, lifecycle) | `status-dot` | Zeus | — |
| Tooltips (exact numbers, definitions) | `tooltip` | Zeus | — |
| Forms | `input`, `textarea`, `select`, `switch`, `checkbox`, `radio` | Zeus | — |
| KPI tiles on Today | `stat-cards` (delta pills, info tooltips) | Zeus | our tile layout per §4.3 |
| Tiny trend in tiles | `selene-tiny-line-chart` | Zeus | tokens |
| Own performance chart | `line-chart-card` (dashed comparison = 1.0× baseline) | Zeus | recharts 3.x |
| Topic coverage, trait lift, traffic sources | `bar-list-card` | Zeus | — |
| Gap matrix (competitors × buyer questions) | `heatmap-chart-card` | Zeus | sticky first column, cell = videos/outliers |
| Outlier scatter (age vs ratio) | `scatter-chart-card` | Zeus | lazy |
| Format mix / cadence | `stage-bars-card` or `artemis-segmented-meter` (Artemis) | Zeus | prefer `stage-bars-card` (no Artemis) |
| Videos tables | `data-table` (TanStack Table, sorting, filters, density) | Zeus | add virtualisation (TanStack Virtual) |
| Onboarding stage log | `task-list` (`AgentTask{label, completedLabel, steps[{label, chips}]}`, `revealed`) | Zeus | driven by `revealed` from the SSE reducer, not timers |
| Pages being read + sources | `web-search` (`WebSearchStep{label, query, sources[{title, domain, href}]}`) | Zeus | `page_read` → sources; site marks from domain |
| Compact run progress (header pill, collapsible) | `agent-progress` | Zeus | fork: replace `stepDuration` timer with controlled `activeIndex`/`percent` props |
| Inline "working" label | `agent-thinking` (`AgentThinkingLabel`) + `agent-log` primitives | Zeus | elapsed timer from `run.started` ts |
| Skeleton shimmer | `selene-shimmer` | Zeus | paper tone light, dark pair |
| Rolling numbers as counts arrive | `counter` | Zeus | respects reduced motion |
| Activity inbox (runs, notices) | `notification-center` | Zeus | grouped by run |
| Banners (quota, stale, re-run) | `announcement` | Zeus/BoardUI | dismissible per session |
| Drawers (evidence, run detail) | `drawer` | BoardUI (free) | right sheet 560px, bottom sheet on mobile |
| Confirm dialogs (delete client, disconnect OAuth) | `modal` (alert variant) | BoardUI (free) | — |
| Command palette | `command-palette` | BoardUI (free) | clients, sections, actions |
| Toasts (copied, saved, undo) | `toast` | BoardUI (free) | undo for dismiss |
| Keyword & question editors | `tag-group`, `combobox`, `search-field` | BoardUI (free) | — |
| Quota meter | `meter` | BoardUI (free) | — |
| Choice cards (channel ownership pick) | `radio-card`, `checkbox-card`, `switch-card` | BoardUI (free) | — |
| Skeletons that size to content | `skeleton` | BoardUI (free) | alternative to `selene-shimmer` for lists |
| Settings sections | `accordion` | BoardUI (free) | — |
| Theme toggle | `theme-toggle` | BoardUI (free) | `next-themes`-compatible class |
| Inner scroll fade | `scroll-fade` | BoardUI (free) | long lists in drawers |
| Confidence meter + "alternatives" drawer pattern | `recommendation-card` (3-bar signal meter) | Beautiful UI | we take the meter and layout; content is ours |
| Inline evidence chips inside sentences | `entity-chip`, `value-pill` | Beautiful UI | channel chip, "3.6×" pill in `why_it_works` |
| "How this card was built" trace | `thinking-state` (Steps variant) | Beautiful UI | steps from reason codes, not LLM text |
| Run history rows, stage status rows | `task-rows` | Beautiful UI | statuses map to `StageStatus` |
| Profile facts with sources | `context-cards` | Beautiful UI | `ClaimMeta.source_url` per fact |
| Competitor confirmation questions (ambiguous candidates) | `approval-card` | Beautiful UI | "Is this Fortis Healthcare's channel?" yes/no/skip |
| Long-stage loader with elapsed time | `loading-state` (pixel grid) | Beautiful UI | analysis step "Computing outliers" |
| Diff visual language (insert sweep, row highlight) | `diff-table` | Beautiful UI | colours/animation reused in our `InsertionDiff` |
| Active run card glow | `border-beam` | libraries.dev | only on the single active run card; off with reduced motion |
| Agent orb (onboarding header, empty Today while analyzing) | `thinking-orbs` | libraries.dev | static image when reduced motion |
| Competitor folder → detail | `folder-component` | Rare UI | fork (§4.8.5): controlled `open`, card slots with thumbnails, button semantics, keyboard, reduced motion, token theme |
| Folder fallback (if Rare UI licence rejected) | `artemis-folder-card` | Zeus (Artemis) | static folder-shaped card with media + metadata |

Not used, with reason: Zeus `review-diff` (premium, FoundKeep Pro; code-review oriented); BoardUI Pro (`task-list`, `agent-progress`, `web-search`, chart cards — Zeus serves equivalent items free under MIT with the same token vocabulary); Beautiful UI `insight-cards` (adds `liveline` 0.0.7, pre-1.0; our charts come from Zeus); Beautiful UI `streaming-text`/`chat-composer`/`prompt-bar` (chat paused; the LLM never streams to the UI); libraries.dev voice/gooey/metal/image effects (no use case).

Pinning: Zeus items declare `motion@^12.42.2`, `recharts@^3.9.2`, `@tanstack/react-table@^8.21.3`, `react-aria-components@^1.17.0`. npm latest today: motion 14.14.0, recharts 3.11.0, @tanstack/react-table 9.3.1, react-aria-components 1.22.1. **Pin motion 12.x and react-table 8.x** for vendored code until each kit item is tested on the new majors.

### 4.7 Onboarding flow (live analysis experience)

Layout: Spotlit's split. Left column (480px): step eyebrow ("01 · YOUR BUSINESS"), serif headline, form or stage rail. Right column: a large rounded panel (`--radius-hero`, `--shadow-hero`, soft sky gradient in light, near-black in dark) holding the **live panel**. Mobile: the live panel stacks above the stage rail.

**Step 1 — Start** (`/w/[slug]/new`)
- One field: "Your website or YouTube channel". Detects URL / `@handle` / `UC…` id / youtube.com URL. A link reveals the other field ("Add your channel too" / "Add your website too"). Country and language prefilled from `navigator.language` and timezone, editable in a small "Market: India · English · IST" line.
- Submit → `POST /clients` with `start_onboarding: true` and an `Idempotency-Key` (UUID per form submission) → redirect to `setup/analyze?run=…`.
- 409 `client_exists` → "You already have {name}. Open it?" with a link.

**Step 1b — Channel ownership check** (only when needed)
- If the user gave a channel ref, or the run emits `channel_resolved` with `verified_by` ≠ `website_link|channel_link|oauth`, show ownership candidates (`ChannelResolveOut`) as radio cards: avatar, title, handle, subscribers, the evidence list ("Linked from apollohospitals.com footer", "Channel links back to apollohospitals.com"), confidence. Copy: "We found a few channels. Which one is yours?" plus "None of these" → continue website-only. Never auto-attach a low-confidence match. This is the `@Apollo247` trap from shared context §1.

**Step 2 — Live analysis** (onboarding run; `setup/analyze`)
- Stage rail (Zeus `task-list`), one task per onboarding stage with the code-templated message as the step label: "Reading your website" (crawl), "Understanding your business" (profile), "Checking your channel" (resolve), "Finding competitors" (discover). Each task expands while active and collapses to its `completedLabel` ("Read 24 pages", "Found 7 offerings, 12 buyer questions", "Found 9 candidates").
- Live panel, three stacked zones that fill as payloads arrive:
  1. **Pages read** — `web-search` trail: `page_read{path, title, page_type}` rows with site mark; `page_skipped{reason}` collapsed into a muted "3 pages skipped (robots.txt)" line.
  2. **What we learned** — fact cards (BUI `context-cards`) for each `profile_field{field, count, preview[]}`: "Offerings · 7" with up to 5 preview chips ("Cardiology", "Organ transplant", …). Each new fact animates in (fade + 8px rise, 200ms). Counts roll with `counter`.
  3. **Competitors found** — mini folders (Rare UI fork at `size=sm`, closed) popping in from `competitor_candidate{name, thumbnail_url, source, evidence_count, score}`, max 6 visible + "+3 more".
- Header of the live panel: `thinking-orbs` + elapsed time + a plain status line from the latest `stage.step` message.
- Footer note: "Live: read from your website and YouTube's public data. You can leave; progress is saved." (true: runs continue server-side; returning re-attaches via replay.)
- Accessibility: a visually hidden `aria-live="polite"` region announces stage transitions and a summary every 5 s at most ("Read 12 pages; found 4 offerings"), not every event.

**Step 3 — Confirm** (`run.awaiting_confirmation`; `setup/confirm`)
Three short sections on one page, in order, each collapsible after it's reviewed:
1. "Is this your business?" — profile summary: one-liner, offerings, locations, conversion actions as chips with "Edit". `completeness` < 0.6 or `partial` → callout "We couldn't read everything on your site. Add anything we missed." with inline add.
2. "What do your buyers ask?" — buyer questions and keywords as two tag groups. Suggested ones preselected as active; the user can reject with one click. At least one active topic ref required.
3. "Who do you compete with?" — folder grid of `competitor_set` suggestions, each with relation chip, evidence summary ("Same offerings in Delhi, Hyderabad · 3 sources"), and two separate relevance lines labelled by source — "Business match (from websites): high" and "YouTube activity & topic match (from YouTube): 0.72" (04 §5 rule). Confirm/Reject on each; ambiguous ones show a BUI `approval-card`. Counter "5 selected · need 3–10". "Add a competitor" (URL/@handle → `POST …/competitors`). Niche and benchmark suggestions under a collapsed "Also watch these (optional)".
- Sticky footer CTA "Start analysis" (`POST /confirm-setup`), disabled with the reason inline ("Pick at least 3 competitors").
- If 04 flagged `set_contains_content_competitors`: info line "Few direct competitors post on YouTube, so we added channels that win your buyer questions."

**Step 4 — Analysis** (analysis run; `setup/analysis`)
- Same split layout; the stage rail shows the analysis DAG (fetch videos → compute metrics → find patterns → scan news → write recommendations → refresh suggestions).
- Live panel shows **proof of work with real numbers**: per-channel `videos_fetched` rows ("Fortis · 38 videos in 30 days"), `outlier_found` mini cards (thumbnail from `i.ytimg.com/vi/{video_id}/mqdefault.jpg`, channel, "3.6× their normal at day 7"), `trait_lift` lines ("Question titles: 2.1× more common among outliers"), `news_found` headlines, and finally `card_written` skeletons that fill in with summary + lens chips + confidence. This is the last zone and the moment of payoff.
- Progress: `run.progress.percent` drives a thin bar; ETA text only after 20% ("About a minute left"), derived from elapsed / percent, rounded to 30 s, never shown below 10 s.
- On `run.succeeded` → 600 ms pause → route to Today with `?first=1`.

**Step 5 — First brief**
- Today renders with the serif greeting "Your first brief is ready." and at most three one-time coach marks: window selector ("Change how far back we look"), a confidence label ("How sure we are, and how many videos back it"), the evidence button ("Every number links to the videos behind it"). Dismissed forever after the first close (`dismissed_hints`).

Failure and partial paths in onboarding are in §8 (rows O1–O14).

### 4.8 Card designs

All cards: white surface, 1px border, `--radius-card` 16px, padding 20px (16px mobile), no shadow at rest, `--shadow-raised` on hover/focus-within. Header row: type chip, lens chips, confidence; title; body; footer actions. Max width 760px in single-column lists; grid cells 360px min.

#### 4.8.1 Recommendation card (contract-driven; one renderer per type via registry)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [Video idea]  [● Views] [● Conversion]                       ▮▮▮ High · 6 outliers · 4 channels │
│ Short explainer on cardiac bypass costs in Hyderabad, under 8 minutes         │  ← summary (2 lines max, then …)
│ ❝ How much does bypass surgery cost in Hyderabad? ❞                           │  ← buyer_question (conversion lens)
│ Why: 4 of 6 niche outliers in 30 days are cost explainers                    │  ← why_it_works[0] (L1)
│ ▸ More evidence (3)                                                           │  ← expands L2
│ ─────────────────────────────────────────────────────────────────────────── │
│ L2: • 2 of 5 competitors cover this; you haven't            (all why_it_works)│
│     Fit: Matches 2 tracked buyer questions; your how-tos average 1.4× baseline│
│     Expected: 1.2×–2.0× your baseline (indicative) ⓘ  basis: blended, n=11    │
│     [thumb][thumb][thumb]  ← reference strip: channel · 2 Nov · 18.4K at day 7 · 3.6×                           │
│ ─────────────────────────────────────────────────────────────────────────── │
│ [Save]  [Mark done]  [Dismiss ▾]                      [Evidence →]  window 30d · 20 Nov │
└──────────────────────────────────────────────────────────────────────────────┘
```
Field mapping (contract → UI):
| Field | Where | Rule |
|---|---|---|
| `type` | type chip | label map: video_idea "Video idea", title_pattern "Title pattern", thumbnail_pattern "Thumbnail pattern", format "Format", creator "Creator", news_opportunity "News", description_refresh "Refresh" |
| `summary` | title | 2-line clamp, full text in `title` attr and evidence drawer |
| `lens[]` | lens chips | hue per lens; when the global lens filter is on, the matching chip is emphasised |
| `buyer_question` | quote line | shown when non-null; muted "No tracked buyer question" omitted entirely when null |
| `why_it_works[]` | first at L1, rest at L2 | numbers inside sentences are wrapped in `value-pill`, channels in `entity-chip`, by matching against the card's own reference videos and numbers (pure display; text unchanged) |
| `reference_videos[]` | L2 strip (3–5), L3 table | each: thumbnail (16:9, 120px), channel, `published_at` (date in client tz), views at age with age label, outlier ratio "3.6×"; click opens YouTube in new tab; row in drawer shows baseline and method |
| `fit_to_client` | L2 "Fit" line | — |
| `expected_range_vs_baseline` | L2 "Expected" line + info tooltip | always suffixed "(indicative)"; tooltip explains the basis (`peer`/`blended`/`own`/`peer_only_no_baseline`) and n; `insufficient` → "Not enough comparable videos for a range" |
| `confidence{label, outliers, channels}` | header | 3-bar meter (BUI) + text "High · 6 outliers · 4 channels". Text always present (colour is never the only signal) |
| `window_days`, `generated_at` | footer meta | "window 30d · generated 20 Nov 06:12" |
| `id` | deep link | `?card=` |

Type-specific bodies:
- **title_pattern / thumbnail_pattern**: the pattern as a readable rule ("Question titles with a year: 'How much … in 2026?'" — example skeleton from traits, never a competitor title), lift line, client usage line ("You used this in 1 of 14 videos"). Thumbnail pattern shows trait chips only (text present, word count band, contrast band) — no face language.
- **format**: format + length band, client's own result on that format if any.
- **creator**: see §4.8.4.
- **news_opportunity**: see §4.8.2.

Actions (one row; keyboard `s` save, `d` dismiss, `x` done):
- **Save** (toggle) → status saved; appears in "Saved" filter.
- **Mark done** → small popover: "Link the video you published (optional)" with a URL field validated against the client's own channel uploads; → status done (+ `linked_video_id`).
- **Dismiss ▾** → reasons (07's list: not our business · already covered · can't produce this format · evidence looks wrong · other) → card collapses to a one-line "Dismissed · Undo" for 5 s, then leaves the list (toast with Undo).
- **Evidence →** opens the L3 drawer.
- Optimistic update with rollback on error (§5.7).

Evidence drawer (L3): header (summary + chips); "Reference videos" table (thumbnail, title, channel + role, published, comparison age, views at age, channel baseline at age, ratio, method badge `observed`/`interpolated`/`backfilled`); "Why" (reason list); "Fit"; "Expected range" with the plain-language maths ("Similar videos ran 1.2×–2.0× their own channel's normal at day 7. Based on 11 comparable videos, 4 of them yours."); "Confidence" with caps applied in words ("Capped at Medium: one channel supplies most references"); "How this card was built" (BUI `thinking-state` steps from the reason codes: "Matched 14 videos to your buyer question" → "Found 6 outliers across 4 channels" → "Checked you haven't covered it in 30 days" → "Wrote this card from the evidence above"). Source chips on every block: YouTube public · YouTube Analytics (owner) · Website · News.

#### 4.8.2 News opportunity card

Urgency and decay are the point, so the card leads with time:
```
[News] [● Views] [● Conversion]                         First-mover · 0 reference videos yet
Claude Fable 5.2 launched 14 h ago                                    ◔ closes in ~2 days
60-sec Short: build a pitch deck with Claude Fable 5.2 in Runable      ← summary (angle tied to offering)
Ties to: "AI presentations" · CTA: Start free                          ← offering + conversion_action
Sources: anthropic.com (primary) · 9 outlets                           ← reference_sources, outlet tier chips
Early YouTube demand: 3 videos, 2 outliers, median 1.8K views/hour     ← DemandCheck (YouTube, labelled)
[Save] [Mark done] [Dismiss ▾]                       [Evidence →]
```
- **Decay meter**: a ring that empties from `published_at` to `expires_at` (`half_life_hours` drives the label: "now" < 24 h, "this week", "evergreen window"). Under 6 h left: amber chip "Closing soon". Past `expires_at`: card leaves the active list and moves to "Expired" with no actions except dismiss.
- **First-mover** (08 §5.12: `reference_videos` may be empty): confidence label "First-mover", meter at 0 bars, the reference strip replaced by "No YouTube videos on this yet. Being early is the opportunity; there's no evidence it will perform." Expected range shows 08's text ("insufficient history" allowed). See §13.9 for the quality-bar exception.
- `compliance.review_required` (health): banner on the card "Needs medical/legal review before publishing" + disclaimers list. The primary action becomes "Mark reviewed" before "Mark done" for regulated clients.
- `demand.status = unchecked_quota`: line reads "YouTube demand not checked today (data budget used up)".

#### 4.8.3 Description refresh card (insert-only diff)

```
[Refresh] How to make money using AI in 2026 · published 3 Mar · 48K views    [Open on YouTube ↗]
Why now: "Claude Fable 5.2" is in 4 niche outliers this week; your video doesn't mention it.
┌ Description (current) ───────────────────────────────────────────────────────┐
│ In this video we cover 7 ways to earn with AI tools…                          │
│ + New in 2026: works with Claude Fable 5.2 for faster drafts.   [Copy line]  │ ← insertion, green tint, "+" gutter
│ ⋯ 6 unchanged lines ⋯                                         (expand)       │
│ Links: https://runable.com/…                                                 │
│ - https://old.example.com/pricing   + https://runable.com/pricing [Copy] │ ← link_fix only: strike + replace
│ #AI #MakeMoney                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
Adds 64 characters (4,812 → 4,876 of 5,000). Keywords added: Claude Fable 5.2
Expected effect: Helps this video show up for searches that mention Claude Fable 5.2. No view promise.
[Copy full new description]  [Copy all insertions]  [I applied this]  [Dismiss ▾]
```
Rules:
- Render from `operations[]` (`InsertionOp`): position label (`top_line` "First line", `after_first_paragraph`, `before_hashtag_line`, `before_links_block`, `append`), `anchor_excerpt` shown as the context line above the insertion, `text` in the body font (it's prose, not code), `keywords_added` highlighted inside the inserted text.
- Inserted lines: `+` gutter, success-tint background (6% opacity), left border 2px success. **No deletions are ever drawn**, except `kind = link_fix`, which shows the old URL struck through with the replacement (09 says this is the only non-additive op).
- Unchanged text collapses to "⋯ n unchanged lines ⋯" (expand on click). The current description comes from `preview_after`'s source text rendered at read time (09 doesn't persist it).
- Copy buttons: per insertion ("Copy line") and "Copy full new description" (exact `preview_after`). Clipboard API with fallback `execCommand` for older Safari; toast "Copied. Paste it in YouTube Studio → Details → Description."
- Byte budget: `inserted_bytes`, `resulting_bytes` vs YouTube's 5,000-byte description limit as a mini meter (BoardUI `meter`), turns amber above 90%.
- `advisories[]` render as a collapsed "Also consider" list with risk chips (e.g. "Title still says 2025").
- `review_required` → "Needs review before you paste" banner; for OAuth owners, the "I confirm this applies to {X}" checkbox (09 §13.11) unlocks L3-claim wording; public/demo mode never shows it.
- "I applied this" → `POST /refresh-suggestions/{id}/applied`; card moves to "Applied — we'll track the effect" with 09's `RefreshImpact.verdict` later ("Too early to tell" until data suffices).
- Description text uses `dir="auto"` per line (Hindi/Arabic insertions render correctly) and `white-space: pre-wrap`.

#### 4.8.4 Creator card
```
[avatar] Dr. Asha Health · @drasha · 120K–1M subscribers · posts every 4 days
Relevance: 9 of 14 recent videos match your topics (cardiology, cost)       ← relevance.match_share/topics_covered
Recent performance: median 1.8× their own normal · niche median views at day 7: 12K
Consistency: posted 13 times in 90 days · 46% of videos beat their normal
Worked with: Fortis (mention, 12 Oct) · Max Healthcare (disclosed paid promotion, 2 Sep)
[thumb][thumb][thumb] reference videos (same strip as recommendation cards)
[Save] [Dismiss ▾] [Evidence →]
```
- Subscriber **band**, never an inferred audience ("viewers are mostly…" is forbidden). Country only if creator-declared, labelled "Channel country (self-declared)".
- Collaboration labels exactly "Mention" vs "Disclosed paid promotion".

#### 4.8.5 Competitor folder + detail

Folder tile (Rare UI `folder-component`, forked to `components/competitors/CompetitorFolder.tsx`):
- Visual: the folder back + 3D-tilted flap from Rare UI, themed with tokens (`white` theme in light mode, `black` in dark). The three fanned cards hold the competitor's **top 3 outlier thumbnails in the window** (fallback: 3 most recent uploads; fallback: channel avatar on a neutral card). On hover/focus, cards fan up (Rare UI motion); on activate, the folder "opens" (cards lift) and the route transitions to the detail page using a View Transition (`document.startViewTransition` where supported; plain navigation otherwise).
- Below the folder (outside the SVG, real text): channel name (1 line, ellipsis, full name in tooltip), relation chip, three facts: "2.3 uploads/week", "median 1.4× their normal", "3 outliers in 30d". Status chips: "Inactive 47 days", "Subscribers hidden", "Benchmark".
- Fork changes (the original is a `div` with `onClick` toggling internal state and no keyboard support, `rareui-dl/folder-component.tsx`): wrap in a `<Link>` (focusable, Enter opens), controlled `open` prop, `cards: ReactNode[3]` slot, `aria-label="{name}: open competitor analysis"`, `prefers-reduced-motion` → no fan/tilt, size from container width, theme from tokens (no hard-coded hex), lazy thumbnails.
- Licence: MIT + Commons Clause + Attribution (visible link to rareui.com required; no reselling of the component). We add "Folder component by Rare UI (rareui.com)" to Settings → About/Credits and the repo `NOTICE.md`. If the owner rejects the attribution clause, swap to Zeus `artemis-folder-card` (MIT) behind the same `CompetitorFolder` interface.

Grid with many competitors (10): 4 columns at ≥1440px, 3 at 1024–1439, 2 at 640–1023, 1 below. Ten folders fit two rows at 1440px. Sorting keeps the user's chosen order in the URL.

Detail page (§4.3 Competitors) header: avatar, name, handle, relation chip, "Open on YouTube", "Move to niche / benchmark", "Remove". Window and lens apply.

#### 4.8.6 Notices and prompts (shared)
- **ThinDataPrompt** (from 07 `InsufficiencyNotice`, or 11 `RunNotice` code `thin_data` with `suggest.widen_window_to`): inline card in the list position where cards would be: "Not enough outliers in the last 7 days (2 found across 1 channel; we need 3+). **Widen to 30?** → 9 outliers across 4 channels." One button. Other actions: "Add competitors", "Add buyer questions", "Connect your channel", "Check back tomorrow".
- **StaleBanner**, **QuotaBanner**, **RerunBanner**, **PartialRunBanner**: announcement bars under the header (copy in §8).
- **Unanswered on YouTube** note (07 `WhiteSpaceNote`): "No one has answered '{question}' on YouTube in the last 90 days (checked 412 videos across 14 channels). No reference videos, so no confidence score." Action: "See formats that work in your niche" (links to Format cards).

### 4.9 Data fetching & state

- **Server state**: TanStack Query v5. `queryOptions` factories per resource; keys `['client', clientId, resource, params]`, e.g. `['client', id, 'recommendations', {w, lens, type, status}]`. Defaults: `staleTime` 60 s for analytics/recommendations (data changes at most daily or on `results.published`), 10 s for runs, 0 for run detail during an active run; `gcTime` 30 min; `refetchOnWindowFocus` true only for runs and Today.
- **No browser persistence of API data**: no `persistQueryClient`, no service worker cache, no localStorage of API responses. YouTube data must be refreshed or deleted within 30 days; a browser cache we can't purge would break that. Only `UiPreferences` (§3.2) go to localStorage.
- **URL state** with `nuqs`: `w` (7|30|90, default = client's `window_days`), `lens` (views|conversion|both, default both), `tab`, `card`, filters, sort. Shareable and back-button friendly.
- **Ephemeral UI state**: React state/context. The live run uses a `useReducer` (§5.4) inside a `RunStreamProvider`. No global store library.
- **Invalidation**: `useClientEvents(clientId)` (one SSE per client view) listens for run terminal events and `results.published` → `invalidateQueries(['client', clientId])` minus `profile`/`topics`; a toast "Fresh results are in" with no forced re-render of the card the user is reading (the list updates on next interaction; the open drawer keeps its snapshot and shows "Updated version available · Refresh").
- **Mutations**: `Idempotency-Key` header (UUID v4 per user intent; reused on retry) for creating clients, runs, onboarding, confirm-setup, competitor add, feedback. `If-Match` with the last ETag/version for profile and client patches; 412 → refetch, show "Someone else changed this. We loaded the latest version; re-apply your edit?".
- **Generated API client**: `openapi-typescript` (types from `/api/v1/openapi.json`) + `openapi-fetch` (typed fetch). Chosen over `@hey-api/openapi-ts` (0.99, pre-1.0) for stability and size; query keys stay ours. CI step regenerates types and fails on diff (`schema.d.ts` checked in). Problem+json errors are parsed into a typed `ApiProblem{status, code, title, detail, request_id, errors, retry_after_s}` and routed by `code` (§8).
- **Routing of API and SSE**: in production, one origin; the edge proxy sends `/api/*` straight to FastAPI and everything else to Next.js. SSE never passes through Next.js rewrites (proxy buffering/compression of `text/event-stream` is a known class of problems; see §7). In dev, the browser calls `http://localhost:8000` directly with `credentials: 'include'` (localhost ports are same-site, so the `SameSite=Lax` session cookie is sent; 11's CORS allows credentials). Server Components call the API over the internal network, forwarding the session cookie.

### 4.10 Accessibility, responsive, dark mode

Accessibility (target WCAG 2.2 AA):
- Primitives from Zeus/BoardUI are built on React Aria (keyboard, focus, ARIA). Vendored non-Aria pieces (Rare UI folder, BUI cards) get explicit roles, focus order and keyboard handlers.
- Focus ring: 2px `--ring` outline + 2px offset on every interactive element (Spotlit measured `2px solid #2463eb`).
- Colour is never the only signal: confidence has bars + text; lens chips have text; deltas have arrows + sign; diff insertions have a `+` gutter.
- Contrast: muted text #525252 on #fafafa ≈ 7.4:1; lens hues and status inks chosen ≥ 4.5:1 on white and on #212121 (checked in CI with a token contrast test).
- Live regions: run progress announces stage changes and throttled summaries (§4.7); toasts are `role=status`; errors `role=alert`.
- Reduced motion: `prefers-reduced-motion` disables folder fan/tilt, border beam, orb animation, shimmer sweeps and count-up; reveals become instant.
- Keyboard: `⌘K` palette, `g t / g n / g c / g y / g r` go to sections, `j/k` move between cards, `Enter` expand, `e` evidence, `s/d/x` save/dismiss/done, `?` shortcut sheet. All shortcuts are listed and can be turned off in Settings.
- Text: `lang` per client market on content regions (`lang="hi"` when a title is Devanagari via a cheap script detector), `dir="auto"` on user/API text, `overflow-wrap:anywhere` for long URLs/hashtags.
- Charts have a "View as table" toggle (data table with the same numbers).

Responsive:
| Width | Layout |
|---|---|
| ≥ 1280 | sidebar expanded (240px), content max 1200px, right rail on Next videos |
| 1024–1279 | sidebar rail (64px, icons + tooltips), right rail collapses into the page |
| 640–1023 | sidebar as drawer (hamburger), header controls wrap into a second row |
| < 640 | single column; window/lens under the title as compact segmented controls; drawers become bottom sheets (90vh); tables render as stacked cards; gap matrix scrolls horizontally with sticky first column; onboarding live panel above the stage rail |

Dark mode decision: **light by default, dark supported from v1 through tokens** (`next-themes`, class strategy, `system` default for new users is *off*; we default to light because the brand reference is light and thumbnails read better on light). Spotlit ships `.dark` values, Zeus/BoardUI flip by `.dark`, Beautiful UI has `.dark` values we alias. Every screen gets light and dark visual snapshots in CI; no `dark:` utility classes in feature code (tokens flip instead).

## 5. Algorithms & logic

### 5.1 Today brief composition (deterministic; client-side until 11 offers `/brief`, §13.1)

Inputs: recommendations for the active window (status not dismissed/done), news opportunities (status new, not expired), refresh suggestions (status open), creators (top 1), insufficiency notices, setup gaps (competitors < 3, no topic refs, no channel), lens filter.

```
slots = []
1. setup gaps first (max 1): unconfirmed competitors / zero active buyer questions / channel unverified
2. urgent news (max 1): urgency == "now" or (expires_at - now) < 48h; best by priority
3. video ideas (max 2): type == video_idea, by server order (07 score), lens-filtered
4. refresh (max 1): highest priority open DescriptionRefresh, only if eligibility.decision in {periodic, active}
5. pattern (max 1): title_pattern | thumbnail_pattern | format with confidence ≥ Medium
6. fill to 5 from remaining recommendations by server order (any type except creator), lens-filtered
7. if total < 3 and a thin-data notice exists → append the ThinDataPrompt as an item (kind="setup", action widen_window)
8. never repeat the same reference video as the lead evidence in two items (skip to next candidate)
rank = order of slots; ties broken by server order
```
Constants live in `lib/brief/config.ts` (`max_items=5, min_items=3, news_window_h=48, max_video_ideas=2`). No numbers are computed; the brief only selects and orders.

"What changed" tiles: values are read from the API objects (`median_ratio_window`, `median_ratio_prev_window`, outlier counts from `/analytics/outliers?scope=competitors` `total`, news count). The "since yesterday" delta needs the previous run's count; until 11 returns it (§13.1) the tile shows the window count only.

### 5.2 Lens filter
- `both` (default): show all cards; chips show each card's lenses.
- `views` / `conversion`: show cards whose `lens[]` contains the value. Cards with the other lens only are hidden, with a footer "3 cards hidden by the Conversion lens · Show all".
- Competitor grid: `conversion` sorts folders by count of buyer questions they cover (from `topic_coverage`), `views` by outlier count.
- Your channel: `conversion` opens the Conversion tab by default.

### 5.3 Window selector and thin data
- Changing `w` updates the URL and refetches window-scoped queries. Analytics are precomputed for 7/30/90 (11 §13.5), so they return 200.
- Recommendations for an unwritten window return **202 + `cards_run`**: the list shows skeleton cards with an inline `agent-progress` ("Writing cards for 90 days… about 20 s") subscribed to that run; on success, refetch.
- Thin data: if the envelope has an `InsufficiencyNotice` (or `RunNotice` `thin_data`) for the current window, render the ThinDataPrompt in the first card slot. The "Widen to {n}?" button sets `w={widen_to}` (view only; doesn't change the client default). If `projected` is present, the prompt shows it; otherwise "Widen to 30?" alone.
- The client default window changes only in Settings (`PATCH /clients/{id}` `window_days`).

### 5.4 SSE run stream (reducer)

`useRunStream(runId)`:
```
connect():
  es = new EventSource(`${API}/runs/${runId}/events`, { withCredentials: true })
  es.onmessage / es.addEventListener(type, …): parse RunEvent → dispatch(event)
  es.onerror: if es.readyState == CLOSED → scheduleReconnect()     # browser auto-reconnects with Last-Event-ID while CONNECTING
  on "end" event or terminal run.* → es.close(); state.connection = "closed"
scheduleReconnect(): attempt += 1; delay = min(30 s, 1 s · 2^attempt) + jitter(0–500 ms)
  new EventSource(url + `?after_seq=${state.last_seq}`)                # explicit resume when we created the new object
  after 3 failed attempts → polling mode: GET /runs/{id}/events.json?after_seq=…&limit=200 every 3 s (backoff to 10 s)
  while polling, try SSE again every 60 s; switch back on success
visibility: when the tab is hidden > 10 min with a terminal-free run, keep the stream (cheap); on return, nothing to do (replay covers gaps)

reducer(state, ev):
  if ev.seq <= state.last_seq: return state                         # dedupe (replay + live overlap)
  if ev.seq > state.last_seq + 1 and source == "live": mark gap → fetch events.json for the gap, then apply in order
  state.last_seq = ev.seq
  switch ev.type:
    run.*        → status, percent; run.awaiting_confirmation → route to confirm
    run.progress → percent = max(percent, ev.percent)                 # monotonic
    stage.*      → stages[ev.stage] = {status, counts, message, ts}
    insight/notice → by payload.kind via eventAnimations registry → facts/pages/candidates/insights/notices
  unknown type or kind → ignore (documented contract)
initial load: GET /runs/{id} → seed stages/status/percent; then SSE with after_seq=0 replays history
```
- Replay without theatre: events whose `ts` is older than 10 s at arrival are applied instantly (no reveal pacing), so reloading a long run doesn't replay animations for a minute.
- 204 on connect (run already terminal and fully delivered) → treat as closed; fetch `GET /runs/{id}` for the final state.
- Multiple tabs: each tab has its own stream (HTTP/2 in prod removes the 6-connection limit concern, 11 §5.12). Within a tab, `RunStreamProvider` shares one connection per run across components.

`useClientEvents(clientId)`: same mechanics on `/clients/{id}/events`; handles `run.started` (show RunPill), terminal events and `results.published` (invalidate, §4.9).

### 5.5 Reveal pacing (makes long waits feel valuable without lying)
- Insight events enter `pending_reveal`. A ticker releases one item every `dwell` ms: `dwell = clamp(900 / max(1, backlog/4), 120, 600)` — slow when few events, fast when many, never slower than real time by more than ~3 s.
- If backlog > 40, collapse same-kind items into a summary row ("+18 pages read") and release.
- Stage transitions and run terminal events bypass the queue (shown immediately, flush pending items first).
- Order is always `seq` order. Nothing is invented: every animated row is a real event. If no event arrives for 8 s during an active stage, show the stage's templated waiting line ("Still fetching videos from YouTube · 3 of 6 channels done") from the latest `stage.progress.counts`, never fake progress.

### 5.6 Formatting rules (`lib/format`)
- Views: compact with one decimal in lists ("18.4K", "1.2M"), exact in tooltips with the client market's locale grouping (`Intl.NumberFormat(market.language-country)`, e.g. `en-IN` → "18,400" and "1,12,000"). Compact notation uses `en` style (K/M) everywhere for consistency; Indian lakh/crore appears only in exact tooltips for `IN` markets.
- Ratios: `3.62 → "3.6×"`; < 1 shown as "0.7×"; null → "no baseline yet".
- Ranges: server `display` string as-is (07 formats it); never recomputed.
- Dates: `published_at` as "2 Nov" in the current year, "2 Nov 2025" otherwise; relative ("3 days ago") only in activity and news; tooltips show full date-time in client timezone with zone abbreviation.
- Views at age label: "at day 7" when `comparison_age_days == 7`, "at day {n}" otherwise, with "(interpolated)" / "(backfilled)" suffix from `views_basis`.
- Wilson intervals as words: "3 of 12 (likely 1–6 of 12)".
- Long text: titles clamp to 2 lines (3 for Devanagari, which renders taller); `title` attribute + tooltip full text; never truncate inside a number or a buyer question in the evidence drawer.

### 5.7 Optimistic feedback
- Save/Done/Dismiss mutate the card's status in the cached list immediately (`setQueryData`), call `PUT /recommendations/{id}/feedback` with an Idempotency-Key, and roll back with a toast on failure ("Couldn't save. Try again."). Dismiss shows Undo for 5 s; Undo sends status cleared.
- Rapid toggles: the last intent wins (mutation key per card; earlier in-flight mutations are superseded client-side; the server upsert per user is idempotent).

### 5.8 Staleness logic (30-day policy)
- From `ResultEnvelope.freshness`: `stale=false` → freshness line only. `stale=true` with `age_hours < 48` → amber banner "Data is from {date}. Today's refresh hasn't run yet." `age_hours ≥ 48` → banner with reasons (`stale_reasons`, e.g. quota, failed run) + "Refresh now" (if role allows and no active run).
- Data past 30 days is purged by 01's retention; the API then returns empty data with a notice. The UI never shows cached-in-browser numbers older than the session (§4.9), so policy holds by construction.

### 5.9 Profile edits → re-run prompt
- After a successful `PATCH /profile`, keyword/question/competitor change, or OAuth connect, show a RerunBanner: "Your settings changed. Re-run analysis to update recommendations (about 2 minutes)." → `POST /runs {kind: "analysis"}`. Multiple edits within a session collapse into one banner. If the server returns `RunNotice` `stale_config` on reads, the same banner appears for other users of the workspace.
- If a run is already active, the banner says "We'll use your changes in the next run" and offers "Re-run after this one" (queues; server dedupes, 11 §5.7).

## 6. Data model

The frontend owns no database tables. It stores:

| Store | Keys | Contents | Retention |
|---|---|---|---|
| localStorage `gh:prefs:v1` | per user id | `UiPreferences` (§3.2) | until sign-out; no API data |
| URL | `w, lens, tab, card, q, sort, status, type, bq` | view state | n/a |
| TanStack Query cache (memory) | `['client', id, resource, params]` | API responses | `gcTime` 30 min; cleared on sign-out and client deletion |
| sessionStorage `gh:onboarding:draft` | per workspace | step-1 form inputs before submit (URL, market) | until submit |
| Cookie (set by API, 01) | session | opaque token, HttpOnly | 01's policy |

Proposed server additions to support the UI (owned by 11/01, listed in §13): `GET /me`, `GET /clients/{id}/brief`, and `user_ui_hints` only if cross-device hint dismissal is wanted (not planned).

## 7. External APIs & libraries

Versions checked with `npm view` on 2026-11-20.

| Library | Version | Licence | Use | Source |
|---|---|---|---|---|
| next | 16.4.6 | MIT | App Router, RSC, `next/font`, `next/image` (not for YouTube thumbnails) | https://nextjs.org/docs |
| react | 19.3.1 | MIT | — | https://react.dev |
| tailwindcss | 4.3.5 | MIT | CSS-first `@theme`, `@custom-variant dark` | https://tailwindcss.com/docs |
| shadcn (CLI) | 4.23.0 | MIT | installs registry items from Zeus, Beautiful UI, Rare UI | https://ui.shadcn.com/docs/registry |
| @tanstack/react-query | 5.108.1 | MIT | server state, SSR hydration | https://tanstack.com/query/latest/docs/framework/react/guides/advanced-ssr |
| openapi-typescript | 7.13.0 | MIT | types from OpenAPI | https://openapi-ts.dev |
| openapi-fetch | 0.17.0 | MIT | typed fetch client | https://openapi-ts.dev/openapi-fetch/ |
| nuqs | 2.10.3 | MIT | URL state | https://nuqs.dev |
| next-themes | 0.4.6 | MIT | theme class | https://github.com/pacocoursey/next-themes |
| react-aria-components | 1.22.1 (kits pin ^1.17) | Apache-2.0 | primitives under Zeus/BoardUI | https://react-spectrum.adobe.com/react-aria/ |
| motion | 14.14.0 (pin 12.x for kits) | MIT | animations | https://motion.dev |
| recharts | 3.11.0 | MIT | Zeus chart cards | https://recharts.org |
| @tanstack/react-table | 9.3.1 (pin 8.x for Zeus data-table) | MIT | tables | https://tanstack.com/table |
| @remixicon/react | 4.9.0 | Apache-2.0 (icons) | single icon set | https://remixicon.com |
| border-beam, thinking-orbs | 1.4.1, 0.3.2 | MIT | two effects | https://libraries.dev |
| Zeus UI registry | 0.4.12 | MIT (registry field) | primary components | https://myzeusui.com/r/registry.json, https://myzeusui.com/llms.txt |
| BoardUI (free tier) | MCP | free tier licence per boardui.com | base components | https://www.boardui.com, MCP `list_components` |
| Beautiful UI | registry (no version) | "MIT License" in site footer; no repo, no licence field in registry JSON | AI-native card patterns | https://www.beautifului.dev/r/registry.json |
| Rare UI folder-component | repo HEAD | MIT + Commons Clause + Attribution (LICENSE in repo) | competitor folders | https://rareui.com/components/foldercomponent, https://github.com/swamimalode07/rare-ui |
| MSW | 2.x | MIT | API mocks in tests (SSE handler support: verify version) | https://mswjs.io |
| Playwright | 1.60 | Apache-2.0 | e2e + visual snapshots | https://playwright.dev |
| axe-core / @axe-core/playwright | latest | MPL-2.0 | a11y checks | https://github.com/dequelabs/axe-core |

Platform facts relied on:
- **EventSource** cannot send custom headers; it sends cookies with `withCredentials`; on network errors it reconnects automatically and sends `Last-Event-ID`; an HTTP 204 response stops reconnection. Source: HTML Living Standard, server-sent events, https://html.spec.whatwg.org/multipage/server-sent-events.html (11 §13.13 notes it could not reach the page; re-verify before build).
- **SSE through proxies** is sensitive to buffering and compression. Reports exist for Next.js behind proxies and Next's own proxying (https://github.com/vercel/next.js/discussions/48427, https://caddy.community/t/streaming-breaks-when-caddy-proxies-next-js/31796). Hence path-based routing of `/api/*` straight to FastAPI (§4.9). Not verified as a bug in Next.js rewrites specifically; we avoid the dependency instead of testing it.
- **YouTube thumbnails and branding**: display unaltered thumbnails linked to the video on YouTube and attribute YouTube as the source of YouTube data. YouTube API Services Developer Policies https://developers.google.com/youtube/terms/developer-policies and Branding Guidelines https://developers.google.com/youtube/terms/branding-guidelines. Exact clause numbers not re-verified here.
- **Quota reset**: YouTube Data API daily quota resets at midnight Pacific Time (https://developers.google.com/youtube/v3/getting-started#quota). The quota banner shows the reset in the client's timezone.
- **YouTube description limit**: 5,000 bytes (used by 09; the UI only displays 09's byte counts).
- **Next.js 16 request interception** file is `proxy.ts` (renamed from `middleware.ts`): verify against the Next 16 docs at build time.

Licence obligations collected in `components/kits/NOTICE.md`: Zeus (MIT), Beautiful UI (MIT per footer; record URL and retrieval date because no licence file ships), Rare UI (MIT + Commons Clause + visible attribution link to rareui.com; we may use commercially but must not resell the component), libraries.dev packages (MIT), BoardUI free items (per BoardUI terms).

## 8. Edge cases & failure modes

Copy rule for every message: say what happened, what we're showing instead, and the one thing the user can do. No blame, no jargon (no "stage", "SSE", "quota units" for end users; Ops screens may use them).

### 8.1 Onboarding

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| O1 | Input is neither URL nor channel ref | client-side parse | inline error, keep focus | "Enter a website (example.com) or a YouTube channel (@name or link)." |
| O2 | Client already exists | 409 `client_exists` | link to existing client | "You already added {name}. Open it?" |
| O3 | No channel yet (website only) | `Client.own_channel == null` after resolve | continue; Today hides own-performance tile, Your channel shows connect empty state; recs use `peer_only_no_baseline` | "No YouTube channel yet? That's fine. We'll compare against similar channels until you have your own baseline." |
| O4 | Channel only (no website) | no `website_url` | skip crawl stages; profile from channel About + description; confirm step asks for offerings | "We built your profile from your channel. Add your website for sharper buyer questions." |
| O5 | Ambiguous channel (e.g. @Apollo247 vs @ApolloHospitalsIndia) | `ChannelResolveOut` with no `high` candidate or ≥2 candidates | ownership picker; never auto-attach | "We found a few channels. Which one is yours?" |
| O6 | Channel ownership unverified | 422 `channel_ownership_unverified` | block auto-attach; offer OAuth connect (proves ownership) or continue website-only | "We couldn't confirm this channel belongs to {site}. Connect it with YouTube to prove ownership, or continue without it." |
| O7 | Website blocks crawling (robots/WAF) | `page_skipped{reason: robots}` majority, `notice partial_profile` | show partial profile; confirm step opens manual fields | "Your site limits automated reading, so we read {n} pages. Add your main services below." |
| O8 | Crawl times out / site down | stage `degraded`/`failed` for crawl, run continues with channel or fails | if channel exists → continue; else manual profile form | "We couldn't reach {domain}. Describe your business in a few lines and we'll continue." |
| O9 | Zero competitor candidates | `run.awaiting_confirmation` with 0 `competitor_set` | confirm step shows add-by-handle + niche channels promoted + "Find more" (`POST …/competitors/suggest`) | "We didn't find competitors on YouTube yet. Add 3 you know, or pick from channels that answer your buyer questions." |
| O10 | Fewer than 3 confirmable competitors | count < 3 | CTA disabled with reason; suggest content channels (04 rule) | "Pick at least 3. Channels that answer your buyer questions count too." |
| O11 | More than 10 selected | count > 10 / 422 | prevent the 11th confirm; counter turns amber | "You can track up to 10. Move extras to 'Also watch'." |
| O12 | Run fails mid-way | `run.failed` with `error.retryable` | keep everything already shown; "Try again" → `POST /runs/{id}/retry {from_stage}` | "Something went wrong while {templated stage}. What we found so far is saved. Try again?" |
| O13 | Run fails, not retryable | `run.failed`, `retryable=false` | show request id, contact path, allow manual setup | "We couldn't finish setting up {client}. You can enter details by hand, or contact us with code {request_id}." |
| O14 | Run partial | `run.partial` + notices | proceed to confirm/brief with PartialRunBanner listing notices | "Done, with gaps: {notice summaries}. Your recommendations use what we could fetch." |
| O15 | User leaves during analysis and returns | route load with active run | replay without animation (§5.4) then live | "Picking up where we left off." (only if >30 s away) |
| O16 | User opens a second client's onboarding in parallel | multiple active runs | each page has its own stream; header RunPill shows count | — |
| O17 | Profile extraction produced nothing useful | `completeness < 0.3` | confirm step becomes a short guided form | "We need a bit more about you. Three quick questions." |
| O18 | No buyer questions extracted | `buyer_questions` count 0 | require ≥1 topic ref; offer examples from niche keywords | "What do customers ask before they buy? Add one or two questions." |
| O19 | Market misdetected | user edits market line | PATCH before run start; if after, offer re-run | "Market changed. Re-run discovery for {country}?" |
| O20 | Onboarding run queued behind others | `run.queued` > 10 s | queue state in stage rail | "Waiting for a free analyst… (position {n})" (only if 11 exposes position; otherwise "Starting soon") |

### 8.2 Streaming & runs

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| S1 | SSE drops (network blip) | `onerror`, readyState CONNECTING | browser auto-reconnect with Last-Event-ID; reducer dedupes | small "Reconnecting…" dot on the RunPill after 3 s |
| S2 | SSE closed by server/proxy | readyState CLOSED, no terminal event | manual reconnect with backoff + `after_seq` | "Reconnecting…" |
| S3 | SSE unavailable (corporate proxy buffers streams) | 3 failed reconnects or no event and no heartbeat in 45 s | switch to polling `events.json` every 3–10 s | none (quiet degrade); Ops sees `connection=polling` in console logs |
| S4 | Gap in seq | live event with `seq > last+1` | backfill via `events.json` before applying | — |
| S5 | Duplicate events | `seq ≤ last_seq` | drop | — |
| S6 | Unknown event type/payload kind | registry miss | ignore; log once | — |
| S7 | Run cancelled by another user | `run.cancelled` | stop animations; show who/when if available | "This run was cancelled. Start a new one?" |
| S8 | Run superseded (newer run started) | `run.superseded` | attach to the new run id from client events | "A newer run started. Following it." |
| S9 | 204 on connect | response status | fetch final run state | — |
| S10 | Session expired during stream | 401 on reconnect / events.json | redirect to login with return URL | "Please sign in again to keep watching." |
| S11 | Very long run (> 5 min) | elapsed > 300 s | keep live; show "Taking longer than usual" + leave-safe note | "This one's taking longer than usual. You can leave; we'll keep working." |
| S12 | Event flood (4,000-video channel) | backlog > 40 | collapse into summary rows (§5.5); 11 caps insights at 30/stage | "+212 videos fetched" |
| S13 | Events arrive out of order across stages | seq order is global | apply by seq only | — |
| S14 | Tab in background for hours | visibility change | no special handling; replay on focus if disconnected | — |
| S15 | Cards run for new window fails | `cards_run` terminal failed | keep analytics; show retry in the list slot | "Couldn't write cards for 90 days. Try again?" |

### 8.3 Data & content states

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| D1 | Zero recommendations pass gates | `items=[]` + `suppressed_counts` | ThinDataPrompt if notice; else explain gates; show Unanswered panel and Radar link | "Nothing met our evidence bar for the last {w} days (each idea needs 3+ recent reference videos). {Widen to 30? / Add competitors}" |
| D2 | Thin data in 7-day window | `InsufficiencyNotice{kind: thin_window, suggestion.widen_to: 30}` | ThinDataPrompt first slot | "Not enough outliers in the last 7 days ({found} found; we need 3+). Widen to 30?" |
| D3 | Thin even at 90 days | notice with no widen option | suggest add competitors/keywords | "Your niche is quiet on YouTube. Adding 2–3 more channels to watch will give us more to learn from." |
| D4 | Type-specific thin (no title patterns) | `kind: type_thin` | tab shows empty state with reason | "No title pattern stands out yet. We need at least {n} outliers sharing a trait." |
| D5 | Client has no baseline (new channel) | `basis = peer_only_no_baseline` | expected range text from 07; info chip "No baseline yet" | "No baseline yet; similar videos ran 1.2×–2.0× their own channel's normal (indicative)." |
| D6 | Expected range insufficient | `basis = insufficient` | hide range, show text | "Not enough comparable videos for a range." |
| D7 | Low-confidence card | label Low | shown, ranked lower, muted meter | "Low · 2 outliers · 1 channel" |
| D8 | Reference video deleted/private since computation | thumbnail 404 / API flags | placeholder thumbnail "Video unavailable"; keep numbers with as-of date | "No longer available on YouTube (as of {date})." |
| D9 | Thumbnail fails to load | `img onerror` | neutral 16:9 tile with play icon | — |
| D10 | Very long titles | length / line clamp | 2-line clamp, tooltip, full in drawer | — |
| D11 | Hindi / mixed-script titles and questions | script detection | Devanagari font in stack, 3-line clamp, `lang` attr, `dir=auto` | — |
| D12 | RTL text (Urdu/Arabic channels in niche) | `dir=auto` | renders correctly within LTR layout; numbers isolated with `<bdi>` | — |
| D13 | Emoji/hashtag-heavy titles | — | no special casing; `overflow-wrap:anywhere` | — |
| D14 | Many competitors (10) | count | grid 4/3/2/1 columns; gap matrix scrolls; sort persisted | — |
| D15 | Competitor channel removed/terminated | `DriftEvent channel_missing` / scorecard missing | folder greyed "Channel unavailable"; suggest replacement | "{name}'s channel is no longer available. Replace it?" |
| D16 | Competitor inactive | `days_since_last_upload > 45` / flag | chip "No uploads in {n} days"; excluded from cadence comparisons per 06 | — |
| D17 | Hidden subscriber count | `subscriber_hidden` | "Subscribers hidden" | — |
| D18 | Benchmark channels | role benchmark_set | label "Benchmark — used for format and title ideas only"; excluded from gap matrix | — |
| D19 | Stale data (refresh failed/quota) | `freshness.stale` | StaleBanner (§5.8) | "Data is from {date}. Today's refresh hasn't run yet." |
| D20 | Data purged at 30 days and not refreshed | empty data + notice `data_stale` | empty state with policy explanation + "Refresh now" | "This data expired under YouTube's 30-day storage rule and is being refreshed." |
| D21 | Quota exhausted | notice `quota_limited`; ops quota | QuotaBanner on all screens for that client; refresh actions disabled with reason | "Today's YouTube data budget is used up. You're seeing data from {time}. Fresh data after {reset local time}." |
| D22 | LLM fallback used for wording | `rendered_by = template` / notice `llm_fallback` | no visual difference except a small "Plain wording" chip in the drawer | — |
| D23 | News skipped (Tavily down) | notice `news_skipped` | Radar news tab empty state with reason | "News scan didn't run today. We'll try again at {time}." |
| D24 | News opportunity expired while viewing | `expires_at < now` (client clock check every minute) | card flips to expired state | "This window has closed." |
| D25 | First-mover news (0 references) | empty `reference_videos`, label First-mover | §4.8.2 copy | "No YouTube videos on this yet…" |
| D26 | Regulated client (health) | `compliance.review_required` / `review_required` | review banner, "Mark reviewed" | "Needs medical/legal review before publishing." |
| D27 | No refresh warranted | eligibility `none` | Refresh tab empty state with reasons | "Your descriptions don't need updates right now. Businesses like yours rarely need them; we check every {cadence} days." |
| D28 | Refresh suggestion out of date (description changed) | 09 `superseded`/hash mismatch | card shows "Your description changed; we'll re-check tonight" | — |
| D29 | Clipboard blocked | `navigator.clipboard` rejects | fallback select-all textarea | "Press ⌘C to copy." |
| D30 | OAuth not connected | `oauth_connected=false` | Conversion tab empty state with connect CTA; never prompt elsewhere more than once | "Connect your channel to see subscribers gained, traffic sources and search terms. Only your workspace sees them." |
| D31 | OAuth revoked | notice `oauth_revoked` | banner + disconnect state; owner data hidden (01 purges) | "YouTube access was removed, so owner analytics are hidden. Reconnect?" |
| D32 | OAuth callback error / channel mismatch | `?oauth=error` / `oauth_channel_mismatch` | toast + settings message | "That Google account doesn't own {channel}. Sign in with the account that manages it." |
| D33 | Creator list empty | `items=[]` | empty state | "No creators matched your topics with enough recent videos." |
| D34 | Unanswered questions present | `white_space[]` | right rail panel | §4.8.6 copy |
| D35 | Recommendation repeats a dismissed idea | server dedupes by `stable_key`; if seen | client hides any card whose `stable_key` matches a dismissed one in this session | — |
| D36 | Profile edited → results outdated | PATCH success / `stale_config` | RerunBanner (§5.9) | "Your settings changed. Re-run analysis to update recommendations (about 2 minutes)." |
| D37 | Concurrent edit conflict | 412 | refetch + re-apply prompt | "Someone else changed this. We loaded the latest version." |
| D38 | Enum value unknown (new rec type) | registry miss | generic contract card | — |
| D39 | Window not yet written | 202 + `cards_run` | §5.3 | "Writing cards for 90 days…" |
| D40 | Lens hides everything | filtered list empty, unfiltered not | footer "Show all" | "No {Conversion} cards in this window. 4 Views cards are hidden." |

### 8.4 Tenancy, auth, platform

| # | Case | Detection | Handling | User-facing message |
|---|---|---|---|---|
| P1 | Multiple clients | `GET /clients` > 1 | client switcher; last client remembered per workspace; URLs carry clientId | — |
| P2 | Client switch while a run streams | route change | stream belongs to the page; the old run continues server-side; RunPill shows active runs across clients | — |
| P3 | Many clients (agency, 50+) | count | switcher with search + recent; Clients page paginated | — |
| P4 | Client deleted elsewhere | 404 / `client_deleting` | redirect to Clients with toast; purge its query cache | "{name} was deleted." |
| P5 | Viewer role | `role=viewer` | hide settings, run and competitor mutations; show save/dismiss/done only if the role may post feedback (§13.5) | tooltip "Viewers can't change settings." |
| P6 | Session expired | 401 | redirect to login with `next=` | "Please sign in again." |
| P7 | Forbidden / other tenant id in URL | 404 (01 returns 404 for other tenants) | not-found page | "We couldn't find that. It may have been removed or you may not have access." |
| P8 | Rate limited | 429 + `retry_after_s` | back off; disable button with countdown | "Too many requests. Try again in {n} s." |
| P9 | API down / 5xx | network error / 503 | page-level ErrorState with retry; keep cached data visible with banner | "We can't reach GrowthHacker right now. Retrying…" |
| P10 | Overloaded | `overloaded` problem | queue message on run start | "We're busy. Your run is queued and will start shortly." |
| P11 | Idempotency replay | `Idempotent-Replayed` header | treat as success | — |
| P12 | Duplicate run request | 200 + `X-Run-Deduplicated` | attach to the existing run | "Already running. Showing progress." |
| P13 | Offline | `navigator.onLine=false` | banner; pause polling; mutations disabled | "You're offline. Showing the last loaded data." |
| P14 | Clock skew affects countdowns | compare `Date` header vs local | compute countdowns from server time offset | — |
| P15 | Browser without View Transitions | feature detect | plain navigation | — |
| P16 | Old browser without EventSource | feature detect | polling mode | — |
| P17 | JS disabled | — | RSC renders read-only pages with data; interactive parts degrade | — |
| P18 | Ops screens accessed by non-admin | role check (server and client) | 404 | — |

## 9. Policy & guardrail check

| Guardrail (assignment / shared context §2) | UI rule | How it's enforced |
|---|---|---|
| Official APIs only | The UI calls only our API; thumbnails load from `i.ytimg.com` URLs the API returns or derives from video ids; links go to youtube.com | no scraping code in the frontend; CSP `img-src` allows `i.ytimg.com`, `yt3.ggpht.com` only |
| Metrics = API data + simple maths | the UI never computes metrics; it formats server numbers | lint rule bans arithmetic on metric fields outside `lib/format` (review checklist; unit tests on formatters) |
| Never merge YouTube with other sources | separate labelled panels (YouTube public / YouTube Analytics (owner) / Website / News); KPI tiles are single-source with source chips; brief selects items, doesn't score across sources | `SourceChip` required prop on every metric component |
| No revenue/monetisation estimates | no revenue words or currency next to channel metrics; collaborations show labels only ("Disclosed paid promotion"), never value | copy review |
| No inferred demographics | creator cards show subscriber band and creator-declared country only, labelled | type-level: `CreatorView` has no audience fields |
| No inferred content category | topics shown are the client's own keywords/buyer questions; no "category" UI; review-flagged topic matches show "match under review" | — |
| Refresh/delete within 30 days | no browser persistence of API data; freshness shown on every screen; purged data shows policy message | §4.9, §5.8 |
| Thumbnails: simple traits only | thumbnail patterns use trait chips (text present, word count, contrast); no face language anywhere | copy review |
| Conversion lens honesty | "Conversion" = answers a tracked buyer question; own-channel conversion data (OAuth) shown separately with source label | §4.3 Your channel |
| LLM wording only | the UI renders LLM text as-is from the API (validated by 10) and never asks an LLM; no streaming LLM text | — |
| ≥3 reference videos, same-age comparisons, honest confidence, ranges | every card shows refs with views at age + ratio; confidence text includes sample size; range always "(indicative)"; news first-mover exception labelled (§13.9) | card renderer tests fail if a required contract field is missing |
| Thin data → say so | ThinDataPrompt with "Widen to 30?" | §5.3 |
| No copying | no "copy title" action on competitor videos; pattern cards show trait skeletons, not titles | — |
| YouTube branding / attribution | "Data from YouTube" attribution in the footer of analytics screens; thumbnails unaltered (no crops beyond aspect-fit, no overlays except a duration/ratio badge outside the image area) and linked to YouTube | visual review; verify exact branding requirements before launch |
| Owner-only analytics visibility | OAuth panels visible only inside the owning workspace, marked "Only your workspace sees this" | API enforces; UI labels |
| Image optimisation cache | YouTube thumbnails are **not** passed through `next/image` optimisation (it would store derived copies on our server); use `<img loading="lazy" decoding="async">` with width/height | `next.config` `images.remotePatterns` excludes ytimg; lint rule on `next/image` with ytimg src |

## 10. Performance, cost & scaling

Budgets (CI-enforced with `@next/bundle-analyzer` + Lighthouse CI on fixture data):
| Metric | Target |
|---|---|
| Today route JS (gzip, first load) | ≤ 180 KB |
| Competitors route JS | ≤ 220 KB (folders + motion) |
| LCP (Today, cached API, desktop) | ≤ 1.8 s; mobile 4G ≤ 2.5 s |
| INP | ≤ 200 ms |
| CLS | ≤ 0.05 (fixed-size thumbnail boxes, skeletons sized to content) |
| SSE event → pixel | ≤ 100 ms p95 (excluding pacing) |

Tactics:
- RSC prefetch of the first screen's queries; client components hydrate with data (no waterfall). Streaming with `Suspense` per panel so the brief shows before charts.
- Charts (`recharts`), scatter, heatmap, data tables and the folder motion code are `next/dynamic` lazy chunks loaded on tab open or viewport entry.
- Strip `three` from Zeus `sidebar`/`app-shell` (declared dependency, ~150 KB gz) — the plan of record is the fork in `components/kits/zeus` with the 3D bits removed.
- Virtualise lists over 100 rows (videos tables, all-videos tab).
- Thumbnails: `mqdefault` (320×180) in strips and folders, `hqdefault` only in the drawer; `loading=lazy`; fixed aspect boxes.
- Fonts: `next/font` with `display: swap`, subsets `latin` + `devanagari` (lazy via unicode-range), Instrument Serif loaded only on the two routes that use it.
- Query dedupe: one fetch per key; the window switch reuses cached windows.
- SSE: one stream per client view + one per watched run; heartbeat-based liveness, no polling when SSE is healthy.

Cost: the frontend adds no YouTube quota or LLM cost. It must not trigger runs implicitly: window switches use the lazy `cards` run only when the API returns 202 (server decides); "Refresh now" is rate-limited server-side (11 §5.14) and the UI disables it during an active run. Supports the < 2 min/client target by making the analysis wait visible, not by any compute.

Hosting: Next.js on Node (container next to the API in docker compose; 01 owns compose). Static assets via CDN in prod.

## 11. Testing strategy

- **Unit (Vitest + React Testing Library)**: formatters (ratios, compact numbers with `en-IN`, dates in client tz, age labels), brief composition (§5.1) with fixtures covering every slot rule, run reducer (dedupe, gaps, monotonic percent, unknown kinds, terminal handling), reveal queue timing (fake timers), lens/window filters, notice registry mapping.
- **Contract tests**: `openapi-typescript` regeneration in CI must produce no diff; MSW handlers are generated from OpenAPI examples (Apollo fixture from 11), so a schema change breaks tests, not production. A renderer test asserts every contract field (`id … generated_at`) is rendered somewhere for each `RecType`.
- **Golden UI states (Storybook or Ladle)**: each card type in High/Medium/Low/First-mover, long Hindi title, no buyer question, insufficient range, peer-only baseline, refresh with link_fix, review_required; each screen in empty/loading/error/thin/stale/quota states. Visual snapshots in light and dark via Playwright.
- **SSE replay tests**: 11's recorded `run_events` for an Apollo onboarding and analysis (JSONL) replayed through an MSW SSE handler (or a tiny Node SSE server if MSW's SSE support is insufficient — verify) at 1× and 50× speed; assertions on rendered facts, folders, cards; injected faults: dropped connection at random seq, duplicate events, gap, unknown kind, 204, 401.
- **E2E (Playwright)** against the local stack in fixture mode (11's fixture adapters, no real quota): onboarding website-only, channel-only, ambiguous channel, zero competitors; confirm → analysis → first brief; window switch with 202; save/dismiss/undo/done; refresh copy; OAuth connect mocked.
- **Accessibility**: `@axe-core/playwright` on every route and state story (zero serious/critical violations); keyboard-only e2e for onboarding and the folder grid; reduced-motion run; token contrast test (all text/background pairs ≥ 4.5:1, large text ≥ 3:1) in light and dark.
- **Performance**: Lighthouse CI budgets (§10) on fixture data; bundle size check per route.
- **Live smoke (manual, gated)**: one Apollo onboarding with real keys alongside 11's smoke test, watching the stream in the browser; record timings and any copy that reads wrong.
- **Human review**: the 12 rubric reviewers use the real UI (cards + evidence drawer) so "evidenced" and "actionable" scores reflect what users see.

## 12. Implementation task breakdown

Sizes: S ≤ 0.5 d, M ≈ 1–2 d, L ≈ 3 d. Starts after 11's OpenAPI and fixture mode exist.

| # | Task | Size | Depends on |
|---|---|---|---|
| F1 | Scaffold Next 16 + Tailwind 4 + TS strict + ESLint (import restrictions, token lint) + Vitest + Playwright; folder layout §4.4 | M | — |
| F2 | `tokens.css` from §4.1/§4.5, fonts (`next/font`), light/dark, contrast test | M | F1 |
| F3 | Vendor Zeus items (app-shell, sidebar w/o three, tabs, segmented-control, button set, chip, badge, status-dot, tooltip, inputs, stat-cards, tiny-line-chart, data-table, task-list, web-search, agent-progress fork, agent-thinking, agent-log, selene-shimmer, counter, notification-center, chart cards) + `kits/zeus.css` alias generator script | L | F2 |
| F4 | Vendor BoardUI free items (drawer, modal, command-palette, toast, tag-group, combobox, search-field, meter, radio/checkbox/switch-card, skeleton, accordion, theme-toggle, scroll-fade) | M | F2 |
| F5 | Vendor Beautiful UI items (recommendation-card meter, entity-chip, value-pill, thinking-state, task-rows, context-cards, approval-card, loading-state, diff-table styles) + scoped `kits/bui.css`; NOTICE | M | F2 |
| F6 | Rare UI folder fork (`CompetitorFolder`) with a11y + reduced motion + token themes; attribution; Zeus folder-card fallback behind same interface | M | F2, owner licence OK (§13.10) |
| F7 | libraries.dev `border-beam`, `thinking-orbs` wrappers with reduced-motion fallbacks | S | F2 |
| F8 | API layer: openapi-typescript generation + CI diff check, `openapi-fetch` client (credentials, CSRF header, Idempotency-Key, problem+json), query/mutation factories | M | 11 OpenAPI |
| F9 | Auth pages + session guard (`proxy.ts`), `GET /me` integration, workspace/client switcher, command palette | M | F3, F4, F8, 01/11 auth + `/me` |
| F10 | AppShell, header (window/lens via nuqs), freshness line, notice/banner system (registries) | M | F3, F8 |
| F11 | SSE: `useRunStream`, `useClientEvents`, reducer, reveal queue, polling fallback, RunPill + Activity drawer | L | F8, 11 SSE |
| F12 | Onboarding step 1 + channel ownership picker | M | F9, F11 |
| F13 | Live analysis panel (stage rail, page trail, fact stream, candidate folders, orb) | L | F5, F6, F7, F11 |
| F14 | Confirm step (profile, topics, competitors folder grid, validation, confirm-setup) | L | F12, F6 |
| F15 | Analysis progress screen (insights stream, card skeleton fill) + first-brief handoff + coach marks | M | F13 |
| F16 | Card system: registry, RecommendationCard (all types), EvidenceDrawer, ReferenceStrip/Table, feedback actions with optimistic updates | L | F3–F5, F8 |
| F17 | Today: brief composer (§5.1), KPI tiles, thin-data prompt | M | F16 |
| F18 | Next videos screen (tabs, show more, filters, Unanswered rail) + window 202 handling | M | F16 |
| F19 | News opportunity card + Radar (news, trending, outliers, unanswered) | L | F16 |
| F20 | Your channel: performance charts, refresh diff cards (InsertionDiff, copy, applied), conversion panels | L | F16 |
| F21 | Competitors: folder grid, gap matrix, detail page (5 tabs) | L | F6, F16 |
| F22 | Creators screen | M | F16 |
| F23 | Settings: profile editor with sources, topics editor, competitors table, integrations (OAuth), schedule; re-run banner | L | F9, F8 |
| F24 | States pass: empty/loading/error/thin/stale/quota for every screen (§8 copy), offline, 401/404/429 handling | M | F17–F23 |
| F25 | Accessibility pass (axe, keyboard, live regions, reduced motion), responsive pass (4 breakpoints) | M | F24 |
| F26 | Performance pass (lazy chunks, virtualisation, budgets in CI) | M | F24 |
| F27 | SSE replay + e2e suites, visual snapshots light/dark | L | F11–F23, 11 fixtures |
| F28 | Ops (internal): quota, runs, evaluation review queue + outlier labelling | L | 12 endpoints |

Critical path: F1 → F2 → F3 → F8 → F11 → F13 → F14 → F16 → F17 (first demo-able slice: onboarding Apollo → first brief). Estimated 6–7 weeks for one engineer through F27; F28 adds ~1 week.

## 13. Open questions / proposed changes to shared context

1. **Brief endpoint.** Propose 11 add `GET /clients/{id}/brief?window_days=&lens=` returning ordered brief items + the three "what changed" values with their comparison values (including "since previous run" counts). Selection is deterministic and belongs next to the other numbers; it also lets the Spotlit UI reuse it. Until then the client composes it (§5.1).
2. **`RecommendationOut.reference_videos` is thinner than 07's `ReferenceVideo`.** The UI needs `thumbnail_url`, `comparison_age_days`, `views_at_age`, `views_basis`, `channel_role`, `channel_baseline_views_at_age`, `is_outlier` in `format=full`. Without `comparison_age_days`, a card whose refs compare at day 3 would be mislabelled "day 7". Also expose `confidence.caps_applied`, `expected_range.basis/n_peer/n_own` and `rendered_by`.
3. **Feedback vocabulary mismatch.** 07: `done | irrelevant | save | snooze | more_like_this` + reasons + `linked_video_id`. 11: `rating up/down` + `status saved|dismissed|done`. Proposal: one `FeedbackIn{status: saved|dismissed|done|snoozed|null, reason?: 07's list, linked_video_id?, note?}`; drop thumbs (the human-review rubric covers quality scoring).
4. **`CompetitorView` is too thin for the folder grid and confirm step.** Needs 04's `relation`, `role`, `confidence`, `flags`, `business` (BusinessRelevance) and `youtube` (YouTubeRelevance) as **separate** objects, `size_band`, `subscriber_count`, `uploads_90d`, `last_upload_at`, `evidence_summary`, and `preview_video_ids` (top-3 outliers in window, fallback recent) for folder thumbnails. Also a move-between-sets endpoint (`PATCH …/competitors/{cid} {role}`).
5. **Roles disagree**: 01 `owner | admin | analyst | viewer`, 11 `owner | editor | viewer`. Pick one; the UI needs to know whether viewers may save/dismiss (feedback is per user).
6. **Missing endpoints for the app shell**: auth (`POST /auth/login|logout|signup`, email verify), `GET /me` (user + workspaces + roles), workspace switch. Not in 11's catalogue.
7. **`InsufficiencyNotice` and `WhiteSpaceNote` need an API surface.** Propose `ResultEnvelope` for `/recommendations` include `insufficiency: InsufficiencyNotice[]` and `white_space: WhiteSpaceNote[]`, and `suppressed_counts`. 11's `RunNotice{code: thin_data, suggest: {widen_window_to}}` lacks `found/required/projected`, which the "Widen to 30? → 9 outliers across 4 channels" prompt uses.
8. **`results.published` and `run.started` on the client stream** are mentioned in 11's prose but not in `RunEventType`. Add `results.published{run_id, window_days[], kinds[]}` to the enum so invalidation is a contract.
9. **Quality-bar exception for news.** 08 allows `news_opportunity` with zero reference videos ("First-mover") while the assignment says a card failing ≥3 refs is not shown. Shared context should state the exception explicitly (news cards cite primary sources + early demand instead), or 08 must suppress first-mover cards. The UI supports both and labels it honestly.
10. **Shared context §3 corrections from this research**:
    - Beautiful UI **is installable**: it serves a shadcn registry at `https://www.beautifului.dev/r/registry.json` (27 items: loading-state, thinking-state, streaming-text, approval-card, tool-chips, task-rows, chat-composer, prompt-bar, recommendation-card, context-cards, diff-table, records-table, filter-table, sidebar-nav, search, flowchart, insight-cards, code-block, fine-tune-card, selection-actions, plus atoms). No Playwright copying needed. Licence: "MIT License" in the site footer, but no licence field in the registry and no public repo; record provenance in NOTICE and get owner sign-off.
    - Zeus UI and BoardUI share the same semantic token vocabulary. Zeus serves free (MIT) versions of items BoardUI sells as Pro (task-list, agent-progress, web-search, chart cards). Proposal: Zeus primary, BoardUI free tier for base pieces Zeus lacks (drawer, command palette, toast, tag-group, combobox, meter), no BoardUI Pro purchase.
    - The correct "Rare UI" is **rareui.com** (`github.com/swamimalode07/rare-ui`, ~2.2k stars), which has the Folder component. The similarly named `Codewithswappy/RareUI` was renamed ShitUI and has no folder component. Rare UI's licence is **MIT + Commons Clause + Attribution**: a visible credit link to rareui.com is mandatory. Owner to accept, or we use Zeus `artemis-folder-card` (MIT).
    - libraries.dev is a small set of MIT effect packages (Beam, Orbs, Gooey, Metal, Image, voice effects), not a component library. It isn't a fallback for Beautiful UI; we use two effects.
    - Zeus `review-diff` is premium (FoundKeep Pro). The description diff is our own component.
11. **Edge routing for SSE (01 deploy).** Serve the app and API on one origin with path routing (`/api/*` → FastAPI) at the edge (nginx/ingress with `proxy_buffering off` for `/api/v1/**/events`). Don't send SSE through Next.js rewrites. Dev uses direct `localhost:8000` with credentials.
12. **Thumbnail handling rule (add to shared context §2).** YouTube thumbnails are hotlinked from YouTube's CDN, unaltered and linked to the video; never proxied, cached or re-encoded by our servers (including Next.js image optimisation). This keeps the 30-day rule trivially true for images.
13. **Browser storage rule (add to §2).** No API-derived data in browser persistent storage (localStorage, IndexedDB, service worker caches). Memory cache only.
14. **Indic typography.** Inter/Schibsted lack Devanagari; we add Noto Sans Devanagari. Confirm which Indian scripts the demo niches need (Apollo uploads include Hindi, Telugu, Tamil titles?) so we preload the right subsets.
15. **Number locale.** Proposal: compact K/M everywhere; Indian grouping only in exact-value tooltips for `IN` markets. Owner to confirm (Indian marketers may prefer lakh/crore in compact form).
16. **Ops/Evaluation UI scope.** 12 plans CLI + static HTML for v1. The human review (2 raters × 30 cards) is easier in the real UI. Propose F28 (review queue + outlier labelling) as optional; 12 to confirm endpoints are stable enough.
17. **Notifications.** Long runs say "you can leave". Without email/push, users must come back on their own. Propose a later `Notifier` port (email on first brief ready, daily brief digest) owned by 11/01.
18. **Assignment says "UI design (Mira provides)".** Our UI is an internal presentation and demo surface. Keep the API contract UI-agnostic (`format=contract`) so a Spotlit UI can consume it without our frontend; flag in the design review that we built our own UI on top.
19. **Next.js 16 specifics to verify at build time**: `proxy.ts` naming, `cacheComponents`/caching defaults affecting RSC prefetch of per-user data (all our fetches must be `no-store` or keyed by session).
20. **"Since yesterday" deltas** on Today require the previous run's counts. 06/11 should expose `prev_run` values (counts only, same source) in the brief payload so the UI never subtracts metrics itself.
