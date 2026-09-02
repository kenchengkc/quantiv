# Quantiv Typography

This document is the product-wide typography contract for the frontend.

## Font roles

Quantiv uses one primary UI family plus one technical data family, both self-hosted through `next/font`:

| Role | Family | Use |
| --- | --- | --- |
| Product UI | **Mulish** | Page titles, section titles, cards, paragraphs, navigation, controls, explanatory copy, and non-technical values |
| Data / technical | **JetBrains Mono** | Prices, timestamps, table metadata, formulas, and technical labels where monospaced alignment is useful |

The About-page heading **“See the research move.”** is the reference product treatment: Mulish, tight tracking, strong but clean hierarchy. All ordinary interface text now stays in the same Mulish family instead of switching to a second sans-serif family.

KaTeX keeps its own mathematical glyph fonts. Brand image assets are not part of the text system.

## Why the contract changed

The first typography pass correctly identified Mulish as the display font, but it retained Nunito Sans for body/interface copy. That still produced a visible family switch between the About-page reference headings and the rest of the interface. The current contract removes that split:

- Mulish is the single application voice for both hierarchy and ordinary UI copy.
- JetBrains Mono remains only where technical/data alignment is useful.
- Tailwind `font-sans`, Clerk/auth surfaces, the splash wordmark, and semantic heading roles all resolve to Mulish.
- The unused Nunito Sans font is no longer loaded, reducing one font family from the application bundle.

The goal is still not to make every number on the interface the same size. Data-heavy views need more density and hero metrics need more emphasis. The goal is for each **role** to have a predictable size while the product keeps one coherent visual voice.

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

## Shared treatments

- `qv-type-section-title` is the semantic implementation of the **“See the research move.”** reference treatment: 38px Mulish, 700 weight, 1.0 line height, and `-0.025em` tracking.
- Page titles use the 56px Mulish role unless they are a deliberate marketing hero.
- Card titles use the 20px Mulish role.
- Normal paragraphs, navigation, controls, labels, and Clerk/auth copy inherit Mulish from the product body role.
- Data-oriented content can opt into JetBrains Mono through `.mono` / `.qv-type-data`.
- Large quantitative displays such as countdowns and implied-move hero values remain intentionally larger than ordinary page copy.

## Rules for new UI

1. Do not add another application typeface without a deliberate product-wide decision.
2. Use Mulish for all ordinary product UI and hierarchy.
3. Use JetBrains Mono only for genuinely technical/data-oriented content where monospaced alignment helps.
4. Prefer the named type tokens or semantic classes in `typography.css` over local `fontSize` values.
5. Avoid new half-pixel font sizes. If a role feels wrong, adjust the shared role rather than creating `11.5px` or `20.5px` locally.
6. Page titles should use the page-title role unless they are a deliberate hero/marketing surface.
7. Section titles should use `qv-type-section-title`, which is anchored to the About-page reference treatment.
8. Card titles should normally use the 20px card-title role.
9. Uppercase eyebrows/pills should normally use the 10px label role with deliberate tracking.
10. Numerical alignment should use tabular numerals and JetBrains Mono only when the content is genuinely technical/data-oriented.
11. SVG charts should inherit Mulish for prose-like labels and use the data family for axes/numeric technical labels.
12. Preserve readability over strict sameness: dense tables and hero quantitative output are intentional exceptions, but exceptions should map to a documented role.