# Nordic Yellow

## Overview
Nordic Yellow is a Scandinavian-inspired design system rooted in function, simplicity, and democratic design. A bold blue-and-yellow palette creates instant recognition, while clean typography and grid-based layouts make complex product catalogs feel organized and accessible. The aesthetic is practical, warm, and optimized for helping people furnish their lives affordably.

## Colors
- **Primary** (#003399): Primary actions, navigation, headers, brand identity — IKEA Blue
- **Primary Hover** (#002B80): Hovered buttons, active navigation states
- **Secondary** (#FFDA1A): Highlights, CTAs, price tags, promotional accents — IKEA Yellow
- **Neutral** (#767676): Secondary text, icons, metadata
- **Background** (#F5F5F5): Page background, section separators
- **Surface** (#FFFFFF): Product cards, panels, room displays
- **Text Primary** (#111111): Headings, product names, prices — Near Black
- **Text Secondary** (#484848): Descriptions, dimensions, material info
- **Border** (#DFDFDF): Card borders, input outlines, dividers
- **Success** (#0A8A00): In stock, delivery available, sustainability badge
- **Warning** (#E87400): Limited availability, assembly note
- **Error** (#CC0008): Out of stock, error states, validation failures

## Typography
- **Display Font**: Noto Sans — loaded from Google Fonts
- **Body Font**: Noto Sans — loaded from Google Fonts
- **Code Font**: JetBrains Mono — loaded from Google Fonts

Noto Sans delivers the universal, functional clarity that Scandinavian design demands. Use weights 400 (body, descriptions), 600 (subheadings, labels), and 700 (headings, prices, product names). Letter-spacing 0em for all sizes. Line height 1.5 for body, 1.3 for headings. Product names use 700 with sentence case. Dimensions and measurements use JetBrains Mono for clarity.

Type scale:
- Display: 40px / 700
- H1: 32px / 700
- H2: 24px / 700
- H3: 18px / 700
- Body: 16px / 400
- Body Small: 14px / 400
- Caption: 12px / 400
- Product Name: 16px / 700
- Price: 24px / 700
- Price Small: 16px / 700
- Unit Price: 12px / 400, #484848
- Dimensions: 14px / 400, JetBrains Mono

## Elevation
Scandinavian minimalism keeps elevation subtle and purposeful. Level 0 (flat) for most content; separation via background color. Level 1 (`0 1px 3px rgba(17,17,17,0.06)`) for product cards at rest. Level 2 (`0 4px 12px rgba(17,17,17,0.08)`) for dropdowns, room planner tools, and hover states. Level 3 (`0 8px 24px rgba(17,17,17,0.12)`) for modals and product quick-view. Prefer flat design with color/border separation over heavy shadows.

## Components
- **Buttons**: 44px height, 20px horizontal padding, 4px border-radius, Noto Sans 700 at 15px. Primary: #003399 bg, white text. Secondary: white bg, #003399 text, 2px #003399 border. Yellow CTA: #FFDA1A bg, #111111 text (used for hero and promotional actions). Rounded: 9999px radius variant for icon-only buttons. Disabled: #DFDFDF bg, #767676 text.
- **Cards**: White bg, 1px #DFDFDF border, 4px border-radius, 0 padding (image flush). Product image: aspect-ratio 1:1, room-setting photography. Content: 16px padding. Name: 16px/700. Description: 14px/400 #484848 1-line clamp. Price: 24px/700 with currency. Rating: 5-star, yellow fill #FFDA1A, 12px count.
- **Inputs**: 44px height, 14px horizontal padding, 4px border-radius, 1px #DFDFDF border. Focus: 2px #003399 border. Error: 1px #CC0008 border with error text below. Quantity stepper: compact 100px, +/- buttons blue, center number.
- **Chips**: 28px height, 12px horizontal padding, 4px border-radius. Category: #F5F5F5 bg, #111111 text. Active: #003399 bg, white text. New: #FFDA1A bg, #111111 text. Sustainability: #0A8A00 bg, white text, leaf icon.
- **Lists**: Product list 80px rows, image 64px left, name + description center, price right-aligned. Hover #F5F5F5 bg. Divider: 1px #DFDFDF. Department list: 52px rows, icon 24px, name, chevron right.
- **Checkboxes**: 20px square, 2px border-radius, 2px #DFDFDF border. Checked: #003399 bg, white checkmark. Focus ring: 2px offset, #003399 at 25% opacity. Used in filter panels and shopping lists.
- **Tooltips**: #111111 bg, white text at 13px, 4px border-radius, 8px 12px padding. Used for assembly info, dimensions, and availability details.
- **Navigation**: Top bar 60px, #003399 bg, white text/icons. Logo left (yellow on blue), search center (white bg, 4px radius, magnifying glass), cart/profile/store-selector right. Secondary nav: 44px, white bg, department links, active: #003399 underline 3px.
- **Search**: 40px height, flex-1, 4px border-radius, white bg, magnifying glass icon right. Focus: 2px #FFDA1A border. Results: product image 48px, name, price, availability badge. Level 2 shadow on dropdown.

## Spacing
- Base unit: 4px
- Scale: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80
- Component padding: Buttons 10px 20px, Cards 0/16px, Inputs 10px 14px
- Section spacing: 64px between room/department sections, 24px between product rows
- Container max width: 1400px, centered with 20px side padding
- Card grid gap: 20px (desktop 4-column), 8px (mobile 2-column)

## Border Radius
- 2px: Checkboxes, small badges
- 4px: Buttons, cards, inputs, chips, all standard elements
- 8px: Panels, dropdowns, promotional banners
- 12px: Modals, hero cards, room display overlays
- 9999px: Icon-only buttons, avatars, circular badges

## Do's and Don'ts
- Do use blue (#003399) as the dominant UI color for navigation and actions
- Do use yellow (#FFDA1A) for emphasis and promotions, but never for text on white backgrounds
- Don't over-decorate; Scandinavian design is about removing the unnecessary
- Do show prices prominently with currency symbol; affordability is a core message
- Don't use rounded corners larger than 4px on standard elements; keep it functional
- Do include product dimensions in a monospace font for technical accuracy
- Don't use gradients or decorative patterns; rely on photography for visual interest
- Do maintain 1:1 aspect ratio product images showing items in room settings
- Don't forget sustainability badges for relevant products; it is a brand pillar
- Do keep the blue header consistent across all pages for strong brand presence