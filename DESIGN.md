---
version: alpha
name: Raja Voting Digital Hero Design
description: Hero section layout tokens for the RVD landing page.
colors:
  primary: "#202124"      # --ink
  secondary: "#626670"    # --muted
  tertiary: "#a58852"     # --gold (existing source token, public.css)
  surface: "#fff"
  accent: "#3155d9"      # --accent (blue used for bg decoration)
typography:
  h1:
    fontFamily: "Noto Sans"
    fontSize: clamp(17px, 2vw, 32px)
    fontWeight: 400
    lineHeight: 1.55
  brand:
    fontFamily: "Noto Sans"
    fontSize: clamp(29px, 4vw, 42px)
    fontWeight: 800
    fontStyle: italic
    lineHeight: 1.3
  tagline:
    fontFamily: "Noto Sans"
    fontSize: clamp(15px, 1.8vw, 29px)
    fontWeight: 700
    lineHeight: 1.3
  description:
    fontFamily: "Noto Sans"
    fontSize: clamp(17px, 2vw, 32px)
    fontWeight: 400
    lineHeight: 1.55
  proof:
    fontFamily: "Noto Sans"
    fontSize: clamp(17px, 2vw, 32px)
    fontWeight: 500
rounded:
  sm: "4px"
  md: "8px"
  xl: "60px"
  full: "9999px"
spacing:
  base: "4px"
  section-gap: "120px 8.2% 80px"
  buttons-gap: "14px"
  proof-gap: "8px"
components:
  button-pill:
    backgroundColor: "var(--ink)"
    textColor: "var(--ink)"
    padding: 14px 32px
    borderRadius: 60px
    fontSize: clamp(16px, 2vw, 25px)
  button-outlined:
    backgroundColor: "transparent"
    textColor: "var(--ink)"
    padding: 14px 32px
    borderRadius: 60px
    borderWidth: 2px
    borderColor: "var(--ink)"
---

## Overview

Hero section follows a strict top-down hierarchy:

**Crown → Brand → Tagline → Headline → Description → Buttons → Features**

Layout is **single-column, left-aligned** (no right-side trophy circle or event card). Max content width 720px (desktop), 100% on tablet/mobile. Background is white with subtle light-blue radial gradients (reference design decorative shapes).

---

## Layout & Spacing

Vertical rhythm:
- Brand to Tagline: 10px
- Tagline to Headline: 8px

Horizontal gaps:
- Two buttons side-by-side: 14px
- Three feature items: 8px

---

## Typography Hierarchy

1. **Brand ("RAJA VOTING DIGITAL")**
   - clamp(29px, 4vw, 42px), ExtraBold, italic
   - Gold color on strong span for "RVD!"

2. **Tagline ("Your Partner in Digital Event Solutions.")**
   - clamp(15px, 1.8vw, 29px), Bold, italic, narrow letter spacing

3. **Description**
   - clamp(17px, 2vw, 32px), regular
   - `max-width: 68ch` for readability

4. **Proof-line (Pembayaran Digital Qris, Terverifikasi, Transparan)**
   - clamp(17px, 2vw, 32px)
   - Icons 24×24px
   - `gap: 8px` between items
   - **Desktop**: vertical separators (border-right: 2px solid var(--line))
   - **Mobile**: horizontal stack with border-bottom separators

---

## Components

### Crown Logo
- Positioned left of brand (flex row)
- Gold horizontal line (28px × 4px, #a58852 = var(--gold)) to the left
- Crown SVG: 56×42px at desktop, 40×30px on mobile

### Buttons (side-by-side on desktop, stacked on mobile)
- **Primary (Daftar Mitra)**: Navy-filled (`var(--ink)`) with arrow icon, pill shape
- **Secondary (Tentang Kami)**: Outlined with navy border (`var(--ink)`), arrow icon
- Gap: 14px between buttons
- Hover: primary fills with navy-ink, secondary fills with surface

### Gold Underline Decoration
- Under "RVD!" span in headline
- CSS: `width: 100%; height: 6px; background: var(--gold); transform: rotate(-1.5deg);`

### Background Decorative Shapes
- Light-blue radial gradients at corners
- Uses `color-mix(in srgb, var(--accent) X%, transparent)` for subtle tint

---

## Mobile Breakpoint: `@media (max-width: 600px)`

- Crown: 40×30px
- Benefit icons: 18×18px
- Buttons: stacked vertically, full-width
- Features: horizontal stack with border-bottom separators

---

## Inline SVG Icons (added 2026-11-19)

All icons use `currentColor` so they inherit the surrounding text color. Crown line uses `#a58852` (the existing `--gold` source token).

| Icon | Class | ViewBox | Colors | Notes |
|------|-------|---------|--------|-------|
| Crown + line | `.brand-crown` | 0 0 100 75 | `fill="#a58852"` | Flex row with 28×4px gold line |
| QRIS payment | `.benefit-icon` | 0 0 64 64 | `currentColor` | QR grid + zigzag line |
| Verified transaction | `.benefit-icon` | 0 0 64 64 | `currentColor` | Shield + checkmark |
| Transparent recap | `.benefit-icon` | 0 0 64 64 | `currentColor` | Bar chart + line chart |
