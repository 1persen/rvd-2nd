# Approved design and production status

The ivory, navy and gold theme now covers public pages, the admin login and all organizer pages. The owner-supplied RV logo appears in site branding and browser icons. The QRIS/Midtrans footer badge is removed. Shared inline SVG icons, trophy artwork and gold/silver/bronze top-three podiums remain in place.

Production is live at https://rajavotedigital.my.id. See [deployment and verification](deploy/ivory-gold-20261010.md) for the release directory, backup, test evidence and limits.

The design was integrated into the newer, hardened Tencent source before deployment. Existing backend behavior, payment-disabled controls, safe HTML rendering, authenticated exports and native checkout dialogs are preserved. Production serves prebuilt Tailwind rather than the development CDN.

The isolated local fixture preview is available at http://127.0.0.1:8766 while its temporary runner remains active. It uses a separate SQLite database under `/tmp/rvd-public-preview`; no preview data or credentials were deployed. Temporary runners and screenshots are not deployment tooling.

## Event templates

`/invite/{event_slug}` now renders one of three stored templates from the same database row: `classic` (the previous portal layout), `poster` (poster-led editorial hero) and `cinematic` (full-bleed backdrop with the tab content in document flow). Theme tokens (accent, background mode, overlay, text colors) come from a validated `events.theme_config` and are emitted as scoped CSS custom properties in `app/static/css/invite.css`.

Only the countdown and the Peserta tab can be hidden; Beranda, Klasemen and Sultan always remain. When Peserta is hidden, the "Dukung sekarang" call to action opens Klasemen instead. Organizers edit templates, colors and optional assets in the event dialog, where `Pratinjau Halaman` renders the unsaved form in a new tab through an authenticated, no-store preview endpoint.

## UI consistency repair

The current application retains support pages, event pricing and Duitku checkout. Its shared header is back in document flow, headings use concise display copy with serif accents, and a first visit uses ivory even when the operating system prefers dark mode. Explicit dark-mode choices remain available. The four-column footer fits its current contact content, and checkout keeps readable dark surfaces.

Browser regression checks cover 320, 390, 820 and 1440 px, centered and elevated first place, public navigation, checkout, and authenticated organizer pages. Browser fixtures refuse databases whose names do not end in `test`; captured previews are written to the ignored `tests/browser/artifacts/` directory.
