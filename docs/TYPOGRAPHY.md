# Quantiv Typography

This document is the product-wide typography contract for the frontend.

## Font roles

Quantiv intentionally uses three self-hosted families through `next/font`:

| Role | Family | Use |
| --- | --- | --- |
| Display / hierarchy | **Mulish** | Page titles, section titles, card titles, large KPI values |
| Body / interface | **Nunito Sans** | Paragraphs, navigation, controls, explanatory copy |
| Data / technical | **JetBrains Mono** | Prices, timestamps, table metadata, formulas, technical labels |

The About-page heading **“See the research move.”** is the reference display treatment: Mulish, tight tracking, strong but clean hierarchy.

KaTeX keeps its own mathematical glyph fonts. Brand image assets are not part of the text system.

## Audit findings

The platform already had the right three core typefaces, but their roles and sizes had drifted over time:

- `.serif` was actually **Mulish**, so the class name obscured the intended display role.
- `body` correctly used **Nunito Sans** and `.mono` correctly used **JetBrains Mono**.
- Tailwind still declared **Inter**, even though Inter is not loaded by the app.
- The splash wordmark used a generic system sans stack instead of the product display font.
- Some SVG chart labels explicitly requested generic `ui-monospace` instead of JetBrains Mono.
- Clerk surfaces inherited their own default family instead of Quantiv's body family.
- Similar roles accumulated near-duplicate sizes: `9`, `9.5`, `10`, `10.5`, `11`, `11.5`, `12`, `12.5`, plus neighboring title sizes such as `20`, `21`, `22`, `36`, `38`, `46`, `56`, and `66`.
- Page-level titles diverged: Earnings and Watchlist centered around `56px`, while Screener had grown to a one-off `66px`.

The goal is not to make every number on the interface the same size. Data-heavy views need more density and hero metrics need more emphasis. The goal is for each **role** to have a predictable size and family.

## Type scale

Defined in `apps/frontend/app/typography.css`:

| Token | Size | Intended role |
| --- | ---: | --- |
| `--qv-type-label` | 10px | Eyebrows, pills, compact table headers |
| `--qv-type-meta` | 11px | Timestamps, source metadata, footer text |
| `--qv-type-small` | 12px | Secondary copy, compact controls |
| `--qv-type-ui` | 13px | Navigation, buttons, table UI |
| `--qv-type-body` | 14px | Default body copy |
| `--qv-type-lead` | 16px | Page introductions |
| `--qv-type-card-title` | 20px | Research/card headings |
| `--qv-type-subhead` | 24px | Compact section/subsection headings |
| `--qv-type-stat` | 32px | Medium KPI/stat values |
| `--qv-type-section` | 38px | Major section headings; About reference |
| `--qv-type-detail-title` | 48px | Ticker/detail title |
| `--qv-type-page-title` | 56px | Earnings, Screener, Watchlist page titles |
| `--qv-type-data-display` | 64px | Hero quantitative output |
| `--qv-type-hero` | 76px | Marketing/About hero only |

Responsive rules intentionally reduce page, section, and data-display roles on small screens rather than creating separate per-page mobile sizes.

## Normalizations in the first pass

- About section headings: `36–38px` → **38px Mulish**.
- About story titles: `21px` → **20px Mulish**.
- About lead: `17px` → **16px Nunito Sans**.
- Screener page title: `66px` → **56px Mulish**, matching Earnings and Watchlist.
- Ticker detail symbol: `46px` → **48px Mulish**.
- Common pills / metric labels: `9.5–10.5px` → **10px**.
- Metric explainer copy: common `11.5–12.5px` steps → **12px** where the role is secondary copy.
- Footer metadata: `11.5px` → **11px**.
- Splash wordmark: system sans → **Mulish**.
- Tailwind `font-sans`: stale Inter stack → **Nunito Sans**; `font-heading` now maps to **Mulish**.
- Clerk/auth surfaces: default family → **Nunito Sans**.
- Generic SVG monospace labels → **JetBrains Mono**.

Large quantitative displays such as countdowns and implied-move hero values remain intentionally larger than ordinary page copy.

## Rules for new UI

1. Do not add a fourth application typeface without a deliberate product-wide decision.
2. Use Mulish for hierarchy, Nunito Sans for prose/UI, and JetBrains Mono for data/technical text.
3. Prefer the named type tokens or semantic classes in `typography.css` over local `fontSize` values.
4. Avoid new half-pixel font sizes. If a role feels wrong, adjust the shared role rather than creating `11.5px` or `20.5px` locally.
5. Page titles should use the page-title role unless they are a deliberate hero/marketing surface.
6. Card titles should normally use the 20px card-title role.
7. Uppercase eyebrows/pills should normally use the 10px label role with deliberate tracking.
8. Numerical alignment should use tabular numerals (`.tnum`) and JetBrains Mono when the content is genuinely technical/data-oriented.
9. SVG charts should inherit the body family for prose-like labels and use the data family for axes/numeric technical labels.
10. Preserve readability over strict sameness: dense tables and hero quantitative output are intentional exceptions, but exceptions should map to a documented role.
