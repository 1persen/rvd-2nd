---
version: alpha
name: Simpaskor Layout Model
description: Landing page layout with horizontal pill navigation, 7 vertical slides, card grids, and pricing packages. Single-page app style with slide transitions.
colors:
  primary: "#dc2626"
  primary-hover: "#b91c1c"
  surface: "#ffffff"
  background: "#fafafa"
  border: "#e5e7eb"
  text-primary: "#111827"
  text-secondary: "#6b7280"
  text-muted: "#9ca3af"
typography:
  heading:
    fontFamily: "Noto Sans, system-ui, sans-serif"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Noto Sans, system-ui, sans-serif"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
  button:
    fontFamily: "Noto Sans, system-ui, sans-serif"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "0.01em"
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  full: "9999px"
spacing:
  base: "4px"
  scale: [4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80]
  section-gap: "64px"
  card-gap: "24px"
  container-max: "1280px"
  container-padding: "24px"
elevation:
  level-0: "none"
  level-1: "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)"
  level-2: "0 4px 12px rgba(0,0,0,0.1), 0 2px 4px rgba(0,0,0,0.06)"
  level-3: "0 10px 25px rgba(0,0,0,0.15), 0 4px 6px rgba(0,0,0,0.1)"
components:
  header:
    height: "56px"
    background: "transparent"
    backdropFilter: "blur(8px)"
    borderBottom: "1px solid {colors.border}"
    display: "flex"
    alignItems: "center"
    justifyContent: "space-between"
    padding: "0 {spacing.container-padding}"
    position: "fixed"
    top: 0
    left: 0
    right: 0
    zIndex: 40
  logo:
    size: "36px"
    borderRadius: "{rounded.full}"
    background: "#000000"
    border: "1px solid rgba(255,255,255,0.1)"
    display: "flex"
    alignItems: "center"
    justifyContent: "center"
  nav-pills:
    display: "flex"
    gap: "4px"
    background: "rgba(255,255,255,0.8)"
    borderRadius: "{rounded.full}"
    padding: "4px"
    backdropFilter: "blur(8px)"
    border: "1px solid {colors.border}"
  nav-pill:
    display: "flex"
    alignItems: "center"
    gap: "6px"
    padding: "8px 16px"
    borderRadius: "{rounded.full}"
    fontSize: "13px"
    fontWeight: 500
    color: "{colors.text-secondary}"
    background: "transparent"
    border: "none"
    cursor: "pointer"
    transition: "all 0.2s ease"
  nav-pill-active:
    background: "{colors.primary}"
    color: "#ffffff"
    boxShadow: "{elevation.level-1}"
  nav-pill-hover:
    color: "{colors.text-primary}"
    background: "rgba(0,0,0,0.04)"
  cta-button:
    height: "40px"
    padding: "0 20px"
    borderRadius: "{rounded.full}"
    fontSize: "13px"
    fontWeight: 600
    background: "{colors.primary}"
    color: "#ffffff"
    border: "none"
    cursor: "pointer"
    transition: "background 0.2s ease"
  cta-button-hover:
    background: "{colors.primary-hover}"
  slide-container:
    display: "flex"
    flexDirection: "column"
    minHeight: "100vh"
  slide:
    display: "none"
    flexDirection: "column"
    minHeight: "calc(100vh - 56px)"
    paddingTop: "56px"
  slide-active:
    display: "flex"
  slide-inner:
    flex: 1
    display: "flex"
    flexDirection: "column"
    maxWidth: "{spacing.container-max}"
    margin: "0 auto"
    padding: "0 {spacing.container-padding}"
    width: "100%"
  slide-header:
    marginBottom: "32px"
  slide-title:
    fontSize: "32px"
    fontWeight: 700
    lineHeight: 1.2
    color: "{colors.text-primary}"
    marginBottom: "8px"
  slide-subtitle:
    fontSize: "16px"
    color: "{colors.text-secondary}"
  section-title:
    fontSize: "20px"
    fontWeight: 700
    color: "{colors.text-primary}"
    marginBottom: "16px"
    display: "flex"
    alignItems: "center"
    justifyContent: "space-between"
  view-all-link:
    fontSize: "14px"
    fontWeight: 500
    color: "{colors.primary}"
    textDecoration: "none"
  view-all-link-hover:
    textDecoration: "underline"
  card-grid:
    display: "grid"
    gridTemplateColumns: "repeat(4, 1fr)"
    gap: "{spacing.card-gap}"
  card-grid-3:
    gridTemplateColumns: "repeat(3, 1fr)"
  card-grid-2:
    gridTemplateColumns: "repeat(2, 1fr)"
  card-grid-1:
    gridTemplateColumns: "1fr"
  card:
    background: "{colors.surface}"
    borderRadius: "{rounded.lg}"
    border: "1px solid {colors.border}"
    overflow: "hidden"
    transition: "transform 0.2s ease, box-shadow 0.2s ease"
    display: "flex"
    flexDirection: "column"
  card-hover:
    transform: "translateY(-4px)"
    boxShadow: "{elevation.level-2}"
  card-image:
    aspectRatio: "16/10"
    width: "100%"
    objectFit: "cover"
    background: "{colors.background}"
  card-content:
    padding: "16px"
    display: "flex"
    flexDirection: "column"
    gap: "8px"
    flex: 1
  card-badge:
    display: "inline-flex"
    alignItems: "center"
    fontSize: "11px"
    fontWeight: 600
    padding: "2px 8px"
    borderRadius: "{rounded.full}"
    background: "{colors.primary}"
    color: "#ffffff"
    width: "fit-content"
  card-title:
    fontSize: "16px"
    fontWeight: 600
    color: "{colors.text-primary}"
    lineHeight: 1.3
    display: "-webkit-box"
    webkitLineClamp: 2
    webkitBoxOrient: "vertical"
    overflow: "hidden"
  card-meta:
    fontSize: "13px"
    color: "{colors.text-muted}"
    display: "flex"
    alignItems: "center"
    gap: "8px"
  card-price:
    fontSize: "18px"
    fontWeight: 700
    color: "{colors.text-primary}"
  card-price-from:
    fontSize: "13px"
    fontWeight: 400
    color: "{colors.text-muted}"
  card-button:
    marginTop: "auto"
    width: "100%"
    padding: "10px 16px"
    borderRadius: "{rounded.md}"
    fontSize: "13px"
    fontWeight: 600
    background: "{colors.primary}"
    color: "#ffffff"
    border: "none"
    cursor: "pointer"
    transition: "background 0.2s ease"
  card-button-hover:
    background: "{colors.primary-hover}"
  pricing-grid:
    display: "grid"
    gridTemplateColumns: "repeat(4, 1fr)"
    gap: "24px"
  pricing-card:
    background: "{colors.surface}"
    borderRadius: "{rounded.xl}"
    border: "1px solid {colors.border}"
    padding: "24px"
    display: "flex"
    flexDirection: "column"
    position: "relative"
  pricing-card-popular:
    borderColor: "{colors.primary}"
    boxShadow: "{elevation.level-2}"
  pricing-badge:
    position: "absolute"
    top: "-12px"
    left: "50%"
    transform: "translateX(-50%)"
    background: "{colors.primary}"
    color: "#ffffff"
    fontSize: "11px"
    fontWeight: 600
    padding: "4px 12px"
    borderRadius: "{rounded.full}"
  pricing-name:
    fontSize: "18px"
    fontWeight: 700
    color: "{colors.text-primary}"
    marginBottom: "4px"
  pricing-desc:
    fontSize: "13px"
    color: "{colors.text-secondary}"
    marginBottom: "16px"
  pricing-price:
    fontSize: "28px"
    fontWeight: 700
    color: "{colors.text-primary}"
    marginBottom: "4px"
  pricing-price-note:
    fontSize: "12px"
    color: "{colors.text-muted}"
    marginBottom: "20px"
  pricing-features:
    listStyle: "none"
    padding: 0
    margin: 0
    flex: 1
    display: "flex"
    flexDirection: "column"
    gap: "10px"
  pricing-feature:
    display: "flex"
    alignItems: "flex-start"
    gap: "10px"
    fontSize: "13px"
    color: "{colors.text-secondary}"
  pricing-feature-check:
    flexShrink: 0
    width: "18px"
    height: "18px"
    borderRadius: "{rounded.full}"
    background: "{colors.primary}"
    color: "#ffffff"
    display: "flex"
    alignItems: "center"
    justifyContent: "center"
    fontSize: "10px"
  pricing-button:
    marginTop: "20px"
    width: "100%"
    padding: "12px 20px"
    borderRadius: "{rounded.md}"
    fontSize: "14px"
    fontWeight: 600
    background: "transparent"
    color: "{colors.primary}"
    border: "2px solid {colors.primary}"
    cursor: "pointer"
    transition: "all 0.2s ease"
  pricing-button-primary:
    background: "{colors.primary}"
    color: "#ffffff"
  pricing-button-hover:
    background: "{colors.primary}"
    color: "#ffffff"
  pricing-button-primary-hover:
    background: "{colors.primary-hover}"
    borderColor: "{colors.primary-hover}"
  footer:
    padding: "40px {spacing.container-padding}"
    background: "{colors.background}"
    borderTop: "1px solid {colors.border}"
  footer-links:
    display: "flex"
    flexWrap: "wrap"
    gap: "24px"
    maxWidth: "{spacing.container-max}"
    margin: "0 auto"
    justifyContent: "center"
  footer-link:
    fontSize: "14px"
    color: "{colors.text-secondary}"
    textDecoration: "none"
  footer-link-hover:
    color: "{colors.primary}"
    textDecoration: "underline"
responsive:
  mobile-breakpoint: "640px"
  tablet-breakpoint: "1024px"
  desktop-breakpoint: "1280px"
  mobile:
    nav-pills:
      gap: "2px"
      padding: "2px"
    nav-pill:
      padding: "6px 12px"
      fontSize: "12px"
    slide-title:
      fontSize: "24px"
    card-grid:
      gridTemplateColumns: "repeat(2, 1fr)"
      gap: "16px"
    pricing-grid:
      gridTemplateColumns: "1fr"
    container-padding: "16px"
  tablet:
    card-grid:
      gridTemplateColumns: "repeat(3, 1fr)"
    pricing-grid:
      gridTemplateColumns: "repeat(2, 1fr)"
---

## Overview

Simpaskor landing page uses a **single-page vertical slide model** with 7 content sections navigated via a fixed horizontal pill navigation. Each "slide" is a full-viewport section that shows/hides via display toggling (not scroll-snap). This creates an app-like experience where users click tabs to reveal content panels.

## Layout Architecture

### Slide Model
- **7 slides**: `hero` | `events` | `ticketing` | `voting` | `berita` | `marketplace` | `paket`
- Each slide has `data-slide` attribute matching nav pill
- Only one slide active at a time (`.is-active` class = `display: flex`)
- Slides maintain `min-height: 100vh` for visual consistency
- Header is fixed (`position: fixed`, `z-index: 40`) with transparent bg + backdrop blur

### Grid System
- **Container**: max-width 1280px, centered, 24px side padding
- **Card grids**: CSS Grid with 4/3/2/1 column responsive breakpoints
- **Pricing grid**: 4-col desktop, 2-col tablet, 1-col mobile
- **Gap**: 24px desktop, 16px mobile

## Components

### Navigation Pills
Horizontal pill bar beneath logo in header. Each pill is a `<button>` with `data-slide` target. Active state uses primary red background (`#dc2626`) with white text. Hover uses subtle gray. Full border-radius (9999px). 4px gap between pills, 4px container padding.

### Cards
Unified card component used across events, ticketing, voting, berita, marketplace:
- Image top (16:10 aspect ratio), content below
- Content: optional badge → title (2-line clamp) → meta (date/location) → price → full-width CTA button
- Hover: translateY(-4px) + elevation level-2
- Border: 1px solid #e5e7eb, radius 12px

### Pricing Cards
Feature-list cards for package selection:
- Name, description, price, feature list with checkmarks, CTA button
- "Popular" variant: primary border + badge + elevation
- Primary CTA: filled primary; Secondary CTA: outline primary
- Feature checkmarks: 18px circles with primary bg + check icon

### Hero Slide
Split layout (text left, image right on desktop; stacked on mobile):
- Left: eyebrow → h1 → h2 tagline → 2 CTAs (primary + ghost)
- Right: decorative glow elements + main hero image
- Decorative: "hero-foto-cahaya" glow span + "hero-foto-glitch" image overlay

## Responsive Behavior

| Breakpoint | Nav Pills | Card Grid | Pricing Grid | Container Padding |
|------------|-----------|-----------|--------------|-------------------|
| Mobile (<640) | 12px padding, 12px font | 2-col | 1-col | 16px |
| Tablet (640-1024) | default | 3-col | 2-col | 24px |
| Desktop (>1024) | default | 4-col | 4-col | 24px |

## Interaction Model

- **Slide switching**: Click nav pill → toggle `.is-active` on target slide, remove from others
- **No scroll-snap**: Standard document flow; each slide is a section
- **Header fixed**: Content starts below header (56px padding-top on slide)
- **Theme toggle**: Button in header switches light/dark via `html.dark` class
- **CTA buttons**: Navigate to detail pages (`/events`, `/e-ticketing`, etc.)

## CSS Architecture Notes

- Design tokens defined in `:root` as CSS custom properties
- Component styles use token references (`var(--color-primary)`)
- Border radius centralized: `--radius-sm/md/lg/xl/full`
- Shadow elevation: 3 levels via `--shadow-1/2/3`
- Spacing scale: 4px base unit
- Single font family: Noto Sans (weights 400-700)
- No utility frameworks — custom CSS with token system