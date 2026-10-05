# Bundled application fonts

Mulish and JetBrains Mono are loaded with `next/font/local`. Builds and page
loads do not depend on the Google Fonts service. Both normal variable fonts
retain the complete upstream glyph set; the layout declares the application's
existing weight ranges. The adjacent SIL Open Font License files cover each font.

The WOFF2 files were losslessly compressed from these pinned Google Fonts sources
using FontTools 4.66.1 (`TTFont(source).flavor = 'woff2'`, then `save(destination)`).
No glyphs or variation axes were removed.

| Font | Pinned source | Source SHA256 | Bundled WOFF2 SHA256 |
| --- | --- | --- | --- |
| Mulish | [Mulish\[wght\].ttf](https://github.com/google/fonts/blob/8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5/ofl/mulish/Mulish%5Bwght%5D.ttf) | `00f1105796291a2fdda117a0fc7f25d8e68f8010cdbb34a411f60b3bd57717ac` | `9918ea468025f4ffdfa44ec1ff947dfe7d8832be8398109b107471fde3fe8190` |
| JetBrains Mono | [JetBrainsMono\[wght\].ttf](https://github.com/google/fonts/blob/6e4b84c976cadb3c49a40fd9a1c203e4f7fcf2da/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf) | `48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda` | `5b177d80fec7bb29846c450124bd80c0c81cc539dd6dd266394658e3a40437c9` |

When updating a font, retain its license, record the new immutable source and
hashes, and run the frontend typography, browser and first-contentful-paint checks.
