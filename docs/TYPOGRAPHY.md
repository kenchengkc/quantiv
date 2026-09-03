# Quantiv Typography

This is the product-wide typography and text-color contract for the frontend.

Quantiv should look like one institutional research product. The visual language is therefore intentionally restrained: one UI family, one technical data family, eight rendered size steps, three weights, three tracking modes, four line-height modes, and three neutral text-color roles.

## Font roles

| Role | Family | Use |
| --- | --- | --- |
| Product UI | **Mulish** | Hierarchy, navigation, controls, explanatory copy, card titles, page titles |
| Data / technical | **JetBrains Mono** | Prices, percentages, timestamps, formulas, aligned technical values |

KaTeX retains its mathematical glyph fonts. Brand images are not part of the text system.

The local heading reference is the About-page **“What is priced?”** treatment: light, compact Mulish rather than a bold dashboard heading. True page/hero anchors intentionally use the strong role so the restrained system still has clear hierarchy.

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
- **800** — primary page/detail titles and true hero hierarchy

There is no 500, 650, or 700 product role. The strong 800 role is deliberately narrow: it is for primary anchors such as **“See what options imply.”**, **Screener**, Watchlist, and ticker/detail titles, not ordinary card or section headings.

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

## Text color

Ordinary interface text has only three neutral foreground roles:

| Role | Token | Use |
| --- | --- | --- |
| Primary | `--qv-text-primary` | Page titles, card headings, primary values and high-priority copy |
| Secondary | `--qv-text-secondary` | Supporting copy and normal secondary information |
| Muted | `--qv-text-muted` | Metadata, timestamps, source labels, placeholders and low-priority annotations |

The historical `--ink-4` token is a compatibility alias of `--qv-text-muted`; it is no longer a fourth rendered neutral level.

Color outside those three neutrals must communicate meaning rather than hierarchy:

- `--qv-text-accent` — interactive or selected emphasis
- `--qv-text-positive` — positive/up state
- `--qv-text-negative` — negative/down state
- `--qv-text-warning` — warning/caution state

Do not introduce new gray values, near-white variants, or local neutral `color-mix()` values simply to make one block feel more or less important. Use the three neutral roles. Disabled state should normally be represented by opacity on an existing role rather than another foreground color. Brand-blue variants may still be used for surfaces, borders, charts, and brand treatments; ordinary text should use the semantic text-color tokens.

## Product hierarchy

| Level | Treatment | Typical use |
| --- | --- | --- |
| Label | 10px / 600 / uppercase | Table headers, pills, compact labels |
| Metadata | 12px / 400–600 | Sources, timestamps, controls, compact technical text |
| Body | 14px / 400 | Normal product copy |
| Lead | 16px / 400 | Page introductions and explanatory lead text |
| Card/panel | 20px / 400 | “What is priced?”, research cards, operational panels |
| Major section | 32px / 600 | Large research/editorial section breaks |
| Page/detail | 48px / 800 | Screener, Watchlist, ticker/detail titles |
| Hero/data display | 64px / 600–800 | Only the most important quantitative or marketing display; marketing/page anchors use 800 |

Primary page titles are not forced uppercase. Hierarchy should come from spacing, alignment, information density, and the small set of documented text roles rather than oversized all-caps text or extra shades of gray.

## Legacy normalization

Older JSX still contains literal values such as 9.5px, 10.5px, 11.5px, 12.5px, 650 weight, 700 weight, decorative italic helper text, and the older fourth neutral `--ink-4`.

`apps/frontend/app/typography.css` contains a compatibility bridge that snaps legacy **rendered typography** onto the canonical grid. `apps/frontend/app/text-colors.css` performs the equivalent neutral-color consolidation by aliasing the old fourth neutral to the muted role. Together they give the product one visual system immediately without mixing design-system work with broad, unrelated component rewrites.

These bridges are transitional architecture, not permission to add more inline styles or legacy color tokens. As components are touched for product work, migrate them to shared semantic roles and delete obsolete local literals. Removing those literals should not change appearance because the canonical rendered result is already defined here.

## Rules for new UI

1. Use Mulish for ordinary product UI and hierarchy.
2. Use JetBrains Mono only for genuinely technical/data-oriented content.
3. Do not introduce a ninth size step.
4. Do not introduce a fourth weight.
5. Do not add new tracking or line-height values outside the canonical modes.
6. Do not use decorative italics for helper/instruction text.
7. Use only primary, secondary, or muted for neutral text hierarchy.
8. Use accent, positive, negative, and warning colors only when the color carries semantic meaning.
9. Do not add page-specific gray values or neutral text `color-mix()` variants.
10. Ordinary `h2`/`h3` headings should inherit the 20px / 400 local-heading role.
11. Use `qv-type-section-title` only for true major section boundaries.
12. Use `qv-type-page-title` for top-level page/detail titles; this is the strong 800 role.
13. Use `qv-type-card-title` for card and panel headings.
14. Prefer shared semantic roles over component-local `fontSize`, `fontWeight`, `letterSpacing`, `lineHeight`, and `color` combinations.
15. Preserve exceptions only when they represent a genuine information role, not because a single page “looks better” with a custom value.
16. When removing legacy inline typography or color, preserve the canonical rendered result rather than reintroducing page-specific values.
