# Quantiv Typography

This is the product-wide typography contract for the frontend.

Quantiv should look like one institutional research product. Typography is therefore intentionally restrained: one UI family, one technical data family, eight rendered size steps, three weights, three tracking modes, and four line-height modes.

## Font roles

| Role | Family | Use |
| --- | --- | --- |
| Product UI | **Mulish** | Hierarchy, navigation, controls, explanatory copy, card titles, page titles |
| Data / technical | **JetBrains Mono** | Prices, percentages, timestamps, formulas, aligned technical values |

KaTeX retains its mathematical glyph fonts. Brand images are not part of the text system.

The local heading reference is the About-page **“What is priced?”** treatment: light, compact Mulish rather than a bold dashboard heading.

## Canonical size scale

Only eight rendered size steps belong to the system:

| Size | Role |
| ---: | --- |
| 10px | Uppercase labels, pills, table headers |
| 12px | Metadata, compact controls, secondary technical copy |
| 14px | Normal product copy |
| 16px | Lead/intro copy |
| 20px | Card, panel, and ordinary subsection headings |
| 32px | Major section headings and medium display/stat roles |
| 48px | Primary page/detail titles |
| 64px | True hero or primary quantitative display only |

Compatibility tokens such as `--qv-type-small`, `--qv-type-subhead`, and `--qv-type-page-title` remain available, but they alias one of these eight values rather than creating another visual step.

Responsive rules reuse the same eight values. Mobile does not have a separate parallel type scale.

## Canonical weights

Only three product weights are intentional:

- **400** — normal copy and the “What is priced?” local-heading voice
- **600** — labels, data emphasis, major section headings, bold emphasis
- **700** — primary page/detail titles and true hero hierarchy

There is no 500, 650, or 800 product role.

## Tracking

Only three tracking modes are intentional:

- `0` — normal copy and controls
- `-0.015em` — display/headline text
- `0.12em` — uppercase labels

## Line height

Only four line-height modes are intentional:

- `1.05` — large display/page titles
- `1.1` — card/panel headings
- `1.35` — compact UI and metadata
- `1.5` — body/lead copy

## Product hierarchy

| Level | Treatment | Typical use |
| --- | --- | --- |
| Label | 10px / 600 / uppercase | Table headers, pills, compact labels |
| Metadata | 12px / 400–600 | Sources, timestamps, controls, compact technical text |
| Body | 14px / 400 | Normal product copy |
| Lead | 16px / 400 | Page introductions and explanatory lead text |
| Card/panel | 20px / 400 | “What is priced?”, research cards, operational panels |
| Major section | 32px / 600 | Large research/editorial section breaks |
| Page/detail | 48px / 700 | Screener, Watchlist, ticker/detail titles |
| Hero/data display | 64px / 600–700 | Only the most important quantitative or marketing display |

Primary page titles are not forced uppercase. Hierarchy should come from spacing, alignment, and information density rather than oversized all-caps text.

## Legacy normalization

Older JSX still contains literal values such as 9.5px, 10.5px, 11.5px, 12.5px, 650 weight, 800 weight, and decorative italic helper text.

`apps/frontend/app/typography.css` contains a compatibility bridge that snaps those **rendered** values onto the canonical grid. This gives the product one visual system immediately without mixing typography work with broad, unrelated component rewrites. Headings are governed directly by their semantic hierarchy; non-heading legacy literals are normalized by the bridge.

This bridge is transitional architecture, not permission to add more inline styles. As components are touched for product work, migrate their typography to shared semantic roles and delete obsolete local literals. Removing those literals should not change appearance because the canonical rendered result is already defined here.

## Rules for new UI

1. Use Mulish for ordinary product UI and hierarchy.
2. Use JetBrains Mono only for genuinely technical/data-oriented content.
3. Do not introduce a ninth size step.
4. Do not introduce a fourth weight.
5. Do not add new tracking or line-height values outside the canonical modes.
6. Do not use decorative italics for helper/instruction text.
7. Ordinary `h2`/`h3` headings should inherit the 20px / 400 local-heading role.
8. Use `qv-type-section-title` only for true major section boundaries.
9. Use `qv-type-page-title` for top-level page/detail titles.
10. Use `qv-type-card-title` for card and panel headings.
11. Prefer shared semantic roles over component-local `fontSize`, `fontWeight`, `letterSpacing`, and `lineHeight` combinations.
12. Preserve exceptions only when they represent a genuine information role, not because a single page “looks better” with a custom value.
13. When removing legacy inline typography, preserve the canonical rendered result rather than reintroducing page-specific values.
