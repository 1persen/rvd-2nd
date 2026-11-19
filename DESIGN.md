---
version: alpha
name: Raja Voting Digital Hero Design
description: Hero section layout tokens for the RVD landing page.
colors:
  primary: "#202124"      # --ink
  secondary: "#626670"    # --muted
  tertiary: "#a58852"     # --gold (existing source token, public.css)
  surface: "#fff"
typography:
  h1:
    fontFamily: "Noto Sans"
    fontSize: 11px
    fontWeight: 800
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  brand:
    fontFamily: "Noto Sans"
    fontSize: 28px
    fontWeight: 900
    lineHeight: 1.1
    letterSpacing: "-0.01em"
  subtitle:
    fontFamily: "Noto Sans"
    fontSize: 11px
    fontWeight: 400
    lineHeight: 1.6
  subtitleStrong:
    fontFamily: "Noto Sans"
    fontSize: 14px
    fontWeight: 700
    lineHeight: 1.4
  proof:
    fontFamily: "Noto Sans"
    fontSize: 0.72rem
    fontWeight: 400
rounded:
  sm: "4px"
  md: "8px"
  xl: "16px"
  full: "9999px"
spacing:
  base: "4px"
  section-gap: "18px"
  buttons-gap: "10px"
  proof-gap: "8px"
  container-padding: "24px"
components:
  button-gold:
    backgroundColor: "{colors.tertiary}"
    textColor: "#111827"
    padding: 8px 18px
    rounded: "{rounded.full}"
    typography: "{typography.h1}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    padding: 8px 16px
    rounded: "{rounded.md}"
    borderWidth: 1px
  proof-line:
    display: flex
    gap: 8px
    alignItems: center
---

## Overview

Hero section follows a strict top-down hierarchy:

**Crown → Brand → Tagline → Headline → Description → Buttons → Features**

---

## Layout & Spacing

Vertical rhythm (all values in px at 1280px viewport):

| Element Pair | Spacing |
|-------------|---------|
| Brand to Tagline | 10px |
| Tagline to Headline | 8px |
| Headline to Description (strong to p) | 4px |
| Description to Buttons | 18px |

Horizontal gaps:

| Component | Gap |
|-------------|-----|
| Two buttons stacked in row | 10px |
| Three feature items | 8px |

---

## Typography Hierarchy

1. **Brand ("RAJA VOTING DIGITAL")**
   - 28px, ExtraBold weight + uppercase
   - 1 line only (`white-space: nowrap`)

2. **Tagline ("Your Partner in Digital Event Solutions.")**
   - 11px, Bold weight
   - Narrow letter spacing
   - Right after brand, no break

3. **Description**
   - Strong (headline): 14px, bold
   - Normal (sub): 11px, regular
   - `max-width: 55ch` for readability

4. **Proof-line (Pembayaran Digital Qris, Terverifikasi, Transparan)**
   - 0.72rem ≈ 11px
   - Inline-flex, icons 12×12px
   - `gap: 8px` between items

---

## Components

### Button Primary (Daftar Mitra)
- Gold-filled rectangle (darker gold for contrast)
- Pill shape (border-radius: 9999px)
- Padding: 8px 18px

### Button Secondary (Tentang Kami)
- Outline style
- Same font size, half padding
- Border: 1px solid primary
- Hover: subtle surface background

---

## Non-Existent (from reference image)

- **Crown logo** above brand – not part of current HTML/CSS
- **Background shapes** – white background only
- **Underline decoration** under "RVD!" – not implemented
