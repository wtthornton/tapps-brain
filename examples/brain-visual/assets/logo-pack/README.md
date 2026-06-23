# NLTlabs logo pack (brain-visual subset)

Redistributable SVG subset copied from the canonical pack in the **NLTWeb** repository:

- Source: `NLTWeb/assets/logo-pack/` (see `NLTWeb/docs/BRAND-STYLE-GUIDE.md` §Logo & wordmark specification)
- Regenerate upstream: `NLTWeb/scripts/rebuild_logo_pack.py`

## Files in this folder

| File | Use on brain-visual |
|------|---------------------|
| `svg/nltlabs-lockup-fullcolor.svg` | Header lockup — light theme |
| `svg/nltlabs-lockup-fullcolor-on-dark.svg` | Header lockup — dark theme |
| `svg/nltlabs-mark-gold.svg` | Favicon (`/favicon.svg`) |
| `svg/nltlabs-mark-gold-on-dark-transparent.svg` | Compact mark on dark panels (optional) |
| `svg/nltlabs-wordmark-fullcolor.svg` | Wordmark-only, light |
| `svg/nltlabs-wordmark-on-dark.svg` | Wordmark-only, dark |

**Do not reconstruct the wordmark in HTML** — the lockup SVG already contains mark, wordmark, and “NEW LOGIC TECH” subtitle. Theme swap via CSS (`.logo-lockup-img--light` / `--dark`).

Brand colors (v3.1): primary `#bd6510`, secondary `#e8941f`, dim `#98500e`.
