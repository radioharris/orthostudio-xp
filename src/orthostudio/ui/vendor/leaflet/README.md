# Leaflet 1.9.4 (vendored)

Source: the npm registry, `https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz`, verified
against the published integrity
`sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`
(2026-09-13). Files copied unchanged from `package/dist/`: `leaflet.js`, `leaflet.css`.
Licence: BSD-2-Clause, `LICENSE` beside them (compatible with OrthoStudio XP's GPL v3).

Why vendored: the page has no build step and never contacts another origin (`docs/specs/ui.md`
section 1). To upgrade, download the new tarball, check its integrity against
`https://registry.npmjs.org/leaflet/<version>`, replace the two files and this note.
