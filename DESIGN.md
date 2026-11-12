# Design Tokens — Nordic Yellow / RVD Gold

Mapping dokumentasi untuk design system RVD setelah restrukturisasi ke gaya Scandinavian (Nordic Yellow + Gold accent).

---

## Warna (Color)

| Token | Nilai | Digunakan untuk |
|-------|-------|-----------------|
| `--gold` | `#a58852` | Brand accent utama (menggantikan IKEA Yellow `#FFDA1A`) |
| `--canvas` | `#f7f8fa` | Page background (light) |
| `--paper` | `#f8f7f3` | Card / surface background (light) |
| `--surface` | `#fff` | Elevated surface, modal bg |
| `--ink` | `#202124` | Primary text |
| `--muted` | `#626670` | Secondary text, metadata |
| `--line` | `#e3e5e9` | Borders, dividers |
| `--brand` | `#24262b` | Primary actions, navigation |
| `--accent` | `#3155d9` | Links, focus rings, interactive |
| `--brand-soft` | `#f0f1f4` | Hover/active bg untuk primary |
| `--control-border` | `#8b8e96` | Input/select border |

**Dark mode** (`html.dark`):

| Token | Nilai |
|-------|-------|
| `--canvas` | `#0B1D3A` |
| `--surface` | `#122A52` |
| `--paper` | `#0f1e2a` |
| `--ink` | `#e4e6eb` |
| `--muted` | `#9ca3af` |
| `--line` | `#2a3548` |
| `--brand` | `#3B82F6` |
| `--accent` | `#60a5fa` |
| `--brand-soft` | `#1e2a4a` |
| `--gold` | `#d8b36a` |

---

## Tipografi (Typography)

Satu font family untuk **semua** teks:

| Role | Font | Weights |
|------|------|---------|
| Display / Heading / Body / UI | **Noto Sans** (Google Fonts) | 400, 500, 600, 700, 800, 900 |

Line height:
- Body: 1.5
- Headings: 1.3

Type scale (CSS variables available in `custom.css`):

| Token | Size / Weight | Usage |
|-------|---------------|-------|
| `--text-display` | 40px / 700 | Hero, large marketing |
| `--text-h1` | 32px / 700 | Page titles |
| `--text-h2` | 24px / 700 | Section headings |
| `--text-h3` | 18px / 700 | Sub-sections |
| `--text-body` | 16px / 400 | Paragraphs |
| `--text-body-sm` | 14px / 400 | Captions, meta |
| `--text-caption` | 12px / 400 | Fine print |
| `--text-product` | 16px / 700 | Product names |
| `--text-price` | 24px / 700 | Prices |
| `--text-price-sm` | 16px / 700 | Small prices |
| `--text-dimension` | 14px / 400 (JetBrains Mono) | Technical dimensions |

---

## Border Radius (Radius Tokens)

Semua `border-radius` hardcoded telah diganti ke token berikut:

| Token | Nilai | Digunakan untuk |
|-------|-------|---------------|
| `--radius-sm` | `2px` | Checkbox, status pill kecil, badge kecil |
| `--radius-md` | `4px` | **DEFAULT**: button, card, input, select, chip, table cell |
| `--radius-lg` | `8px` | Dropdown, tooltip, toast, sheet, panel, banner promo |
| `--radius-xl` | `12px` | Modal, dialog, hero section, overlay |
| `--radius-full` | `9999px` | FAB, avatar, tag/pill, icon-only button, circular elements |

### Mapping nilai lama → token

| Nilai lama | Token baru | Catatan |
|------------|------------|---------|
| `0`, `1px`, `2px` | `var(--radius-sm)` | |
| `3px`, `4px`, `5px`, `6px`, `7px` | `var(--radius-md)` | |
| `8px`, `9px`, `10px`, `11px` | `var(--radius-lg)` | |
| `12px`–`50px` | `var(--radius-xl)` | Semua panel/card besar |
| `999px`, `9999px`, `50%` | `var(--radius-full)` | Circular (avatar, pill, FAB) |

> **Aturan**: PR baru **tidak boleh** memperkenalkan `border-radius` dengan nilai px/% hardcoded. Gunakan `var(--radius-*)`. Dikenakan via stylelint custom rule (`local/radius-token`).

---

## Elevation (Shadow)

| Level | Shadow | Penggunaan |
|-------|--------|------------|
| 0 | `none` | Flat content, separation via background |
| 1 | `0 1px 3px rgba(17,17,17,0.06)` | Product cards at rest |
| 2 | `0 4px 12px rgba(17,17,17,0.08)` | Dropdowns, room planner, hover states |
| 3 | `0 8px 24px rgba(17,17,17,0.12)` | Modals, quick-view, overlays |

CSS variables: `--shadow-1`, `--shadow-2`, `--shadow-3` (di `custom.css`).

---

## Spacing

Base unit: **4px**

Scale: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80`

| Context | Nilai |
|---------|-------|
| Section gap (antar "room") | `64px` |
| Row gap (antar product) | `24px` |
| Container max-width | `1400px` |
| Side padding | `20px` (mobile), `28px` (desktop) |
| Card grid gap | `20px` (desktop 4-col), `8px` (mobile 2-col) |

---

## Komponen Utama (Component Specs)

### Button
- Height: `44px` (min)
- Padding: `11px 19px` (`.btn`), `8px 15px` (`.header-cta`)
- Radius: `var(--radius-md)` default, `var(--radius-full)` untuk icon-only
- Font: Noto Sans 700, 15px
- Primary: `--brand` bg, white text
- Secondary: `--surface` bg, `--brand` text, `2px` `--brand` border
- Gold CTA: gradient `#f2dfb2` → `#dbb977` (`.button-gold`), `--ink` text

### Card
- Background: `var(--surface)` / `var(--paper)`
- Border: `1px solid var(--line)`
- Radius: `var(--radius-md)` (default), `var(--radius-xl)` untuk hero/featured
- Image: aspect-ratio 1:1, flush (no padding)
- Content padding: `16px`

### Input / Select
- Height: `44px`
- Padding: `11px 13px`
- Radius: `var(--radius-md)`
- Border: `1px solid var(--control-border)`
- Focus: `2px solid var(--accent)`, outline-offset `4px`
- Error: `1px solid #CC0008` + error text below

### Chip / Tag
- Height: `28px`
- Padding: `0 12px`
- Radius: `var(--radius-md)` default, `var(--radius-full)` untuk pill
- Category: `--canvas` bg, `--ink` text
- Active: `--brand` bg, white text
- Gold/New: `--gold` bg, `--ink` text

### Status Pill
- Radius: `var(--radius-sm)`
- Padding: `4px 9px`
- Font: Noto Sans 700, 12px

---

## Enforcement (Lint)

File konfigurasi:
- `.stylelintrc.json` — extends `stylelint-config-standard`, loads custom plugin
- `stylelint-radius-token.js` — custom rule `local/radius-token`

Rule `local/radius-token`:
- Rejects any `border-radius` declaration that is not a `var(--radius-*)` token (or `inherit`/`initial`/`unset`/`revert`).
- Allows raw px values **only** inside `:root` token definitions (custom properties starting with `--`).

Jalankan:
```bash
npx stylelint "app/static/css/**/*.css"
```

---

## Catatan Migrasi

- `standing` / leaderboard / peringkat page: **nama tidak diubah** (per instruksi user).
- Font legacy yang dihapus: DM Sans, Poppins, Manrope, Rajdhani, Cormorant Garamond, Oswald, Bebas Neue, Plus Jakarta Sans, Inter, Source Sans 3, Outfit.
- Semua font-family di CSS dan template HTML sekarang `Noto Sans`.
- Semua `border-radius` hardcoded di `public.css`, `landing.css`, `vote_modal.css`, `checkout.css`, `admin.css`, `invite.css`, `custom.css` telah di-tokenisasi.
- Gold accent: `--gold: #a58852` (light) / `#d8b36a` (dark).
- Canvas/Paper: `--canvas: #f7f8fa`, `--paper: #f8f7f3` (light).

---

*Generated as part of RVD frontend restructuring to Nordic Yellow / Scandinavian design system.*