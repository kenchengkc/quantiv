# Quantiv Typography

This document is the product-wide typography contract for the frontend.

## Font roles

Quantiv uses one primary UI family plus one technical data family, both self-hosted through `next/font`:

| Role | Family | Use |
| --- | --- | --- |
| Product UI | **Mulish** | Page titles, section titles, cards, paragraphs, navigation, controls, explanatory copy, and non-technical values |
| Data / technical | **JetBrains Mono** | Prices, timestamps, table metadata, formulas, and technical labels where monospaced alignment is useful |

The About-page headings **“What is priced?”** and **“See the research move.”** are the reference product treatments. They use the same Mulish family as the rest of the UI, but establish two deliberate hierarchy levels: lighter 400-weight card/panel titles for clean local structure, and 700-weight section titles for stronger page structure. Both use the shared display voice—alternate forms (`ss01`), normal kerning, compact line height, and deliberately tight tracking. That display voice is product-wide; it is not an About-page-only font treatment.

KaTeX keeps its own mathematical glyph fonts. Brand image assets are not part of the text system.

## Why the contract changed

The first typography pass correctly identified Mulish as the display font, but it retained Nunito Sans for body/interface copy. That still produced a visible family switch between the About-page reference headings and the rest of the interface. The current contract removes that split:

- Mulish is the single application voice for both hierarchy and ordinary UI copy.
- JetBrains Mono remains only where technical/data alignment is useful.
- Semantic `h1`–`h3` headings automatically receive the shared Mulish display treatment; explicit role classes control their scale and hierarchy weight.
- Public research/detail card titles use the lighter “What is priced?” treatment instead of making every panel as heavy as a section heading.
- Tailwind `font-sans`, Clerk/auth surfaces, the splash wordmark, and semantic heading roles all resolve to Mulish.
- The unused Nunito Sans font is no longer loaded, reducing one font family from the application bundle.

The goal is still not to make every number or heading on the interface the same size or weight. Data-heavy views need more density, panel titles should stay quiet, and hero metrics need more emphasis. The goal is for each **role** to have a predictable hierarchy and for larger human-readable text to share one coherent display voice.

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

## Shared display voice

Semantic `h1`, `h2`, and `h3` elements automatically use the Mulish display voice: `ss01`, normal kerning, balanced wrapping, and role-appropriate tight tracking. This is intentionally separate from **size and weight**. A 16px operational heading can retain its local hierarchy while still looking like the same product family as a 20px research-card title or a 38px section heading.

For non-heading elements or explicit hierarchy, use the semantic roles:

- `qv-type-display` applies the shared display treatment without forcing a particular size.
- `qv-type-card-title` is the semantic implementation of the **“What is priced?”** treatment: 20px Mulish, 400 weight, 1.05 line height, and `-0.015em` tracking. Public detail-page research cards use this same light panel-title hierarchy.
- `qv-type-subhead` is the 24px compact subsection role at 700 weight.
- `qv-type-section-title` is the semantic implementation of the **“See the research move.”** section treatment: 38px Mulish, 700 weight, 1.0 line height, and `-0.025em` tracking.
- `qv-type-page-title` is the 56px primary page-title role at 800 weight.
- Existing `qv-m-h1` page headings share the page-title treatment except the earnings-calendar `qv-week-heading`, whose font size is dynamically fitted to keep the date range on one line.
- Normal paragraphs, navigation, controls, labels, and Clerk/auth copy inherit Mulish from the product body role.
- Data-oriented content can opt into JetBrains Mono through `.mono` / `.qv-type-data`.
- Large quantitative displays such as countdowns and implied-move hero values remain intentionally larger than ordinary page copy.

`.serif` is now a backwards-compatibility class only. It still resolves to Mulish and preserves the display OpenType feature for older components, but new semantic headings should not need it.

## Rules for new UI

1. Do not add another application typeface without a deliberate product-wide decision.
2. Use Mulish for all ordinary product UI and hierarchy.
3. Use JetBrains Mono only for genuinely technical/data-oriented content where monospaced alignment helps.
4. Let semantic `h1`–`h3` elements inherit the shared display voice instead of recreating font-feature, kerning, or tracking rules locally.
5. Prefer `qv-type-card-title`, `qv-type-subhead`, `qv-type-section-title`, and `qv-type-page-title` for explicit heading hierarchy rather than local `fontSize`/`fontWeight` combinations.
6. Use `qv-type-display` when a non-heading element needs the display voice without a prescribed size.
7. Avoid new half-pixel font sizes. If a role feels wrong, adjust the shared role rather than creating `11.5px` or `20.5px` locally.
8. Page titles should use the page-title role unless they are a deliberate hero/marketing surface or a dynamically fitted calendar heading.
9. Card/panel titles should normally use the lighter 400-weight card-title role; do not promote them to section-level weight without a hierarchy reason.
10. Uppercase eyebrows/pills should normally use the 10px label role with deliberate tracking.
11. Numerical alignment should use tabular numerals and JetBrains Mono only when the content is genuinely technical/data-oriented.
12. SVG charts should inherit Mulish for prose-like labels and use the data family for axes/numeric technical labels.
13. Preserve readability over strict sameness: dense tables and hero quantitative output are intentional exceptions, but exceptions should map to a documented role.
