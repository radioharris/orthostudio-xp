# The water an aerodrome holds, taken out of it

Status: in branch `aerodrome-dry` (2026-10-14). Code: `vectors/layers.py` (`dry_aerodromes`),
`vectors/water.py` (`DryAerodromes`, `touched_pieces`, `without`, `build_water_layers(dry=)`),
`vectors/coast.py` (`build_sea_layers(dry=)`), `vectors/rule.py` (version 7). Tests:
`tests/test_aerodrome_dry.py`.

## 1. The problem

X-Plane lays the pavement of its airports on whatever ground the tile has. Where OpenStreetMap has
water inside an airport, the bridges and piers of an airport built over the sea, a canal under a
runway, an aerodrome in a tidal bay, that pavement sank under the water's surface: Tokyo Haneda's
taxiway bridges (forum report, 2026-10-13, in Ortho4XP's tiles too), Macau's basin between its
runway and its terminal, Avranches (LFRW), whose whole aerodrome lies in the sea of OpenStreetMap's
coastline.

A first rule took X-Plane's own pavement out of the water (`xp-pavement.md`, shelved): the ground
it took out stood in walls above the water all along the cut, and Avranches' runway became an
island the shape of its pavement (seen in X-Plane, 2026-10-14).

## 2. The rule

XPME, a mesh many pilots use, turns water inside the aerodrome OpenStreetMap draws into land.
Measured on its tiles (2026-10-14), random points of that water are land at Schiphol 120 of 120,
Macau 120 of 120, Chennai 120 of 120, Haneda 111 of 120, Avranches 85 of 120, while the water just
outside stays water; against X-Plane's own airport boundary (`apt.dat` row 130) the match is far
weaker (Haneda 45 of 120). Not all of it: it keeps the lake inside the outline of the air base of
Cazaux (1 point of 120 is land) and most of Shenzhen's ponds; how it chooses is not known, and the
distance to the runways does not explain it. The vector stage takes out the water the airport
itself stands on:

* **Which aerodromes.** The `aeroway=aerodrome` polygons of the airports layer, closed ways and
  multipolygons, that meet a runway (`aeroway=runway`, line or area) whose `surface` is not
  `water` (`dry_aerodromes`). A seaplane base, whose runways are water or which has none, keeps its
  water; an aerodrome drawn as a point has no outline and changes nothing.
* **Only the water its surfaces touch.** The water inside the outlines is cut into its pieces, and
  a piece is taken out only where the aerodrome's runways, taxiways, taxilanes or aprons
  (`aeroway`, lines and areas, not `surface=water`) touch it (`touched_pieces`). The first form
  took all the water inside the outline: the census found 419 ha of the lake of Cazaux inside the
  air base's outline, far from any of its surfaces, and X-Plane showed them as a flat meadow
  under Bing's blurred photo of a military site; Lake Hood, the seaplane base of Anchorage, which
  also has a dirt strip and whose water lanes OpenStreetMap does not tag, lost 80 ha of its lake.
* **Out of the lakes and rivers.** Those pieces are taken out of the merged WATER and SEA_EQUIV
  polygons before they are encoded and seeded (`water.without`). A polygon with no such piece is
  the same object.
* **Out of the sea.** `build_sea_layers` takes them out of the sea area before its seeds are
  placed, and the outline of what it took joins the SEA lines, so the sea stops there.
* **Surfaces only**, on either side of a cut: an intersection can come back with a line beside
  its surfaces, and the outline of what was taken is then no outline (13 tiles of the pavement rule
  failed so). That is the one thing the shelved rule taught that this one keeps; its other
  safeguards answered the shapes of X-Plane's pavement and are left out until a measure asks for
  them.

The ground taken out of the water is land like any other: the relief, smoothed over the aerodrome
as Ortho4XP smooths it. The photo of the water stays on it; at Schiphol, where XPME shows that
ground blue, the photo of the place shows.

## 3. Isolation and the cache

* `orthostudio.vectors` goes to version 7; 5 is taken by the dry basins of the `ideas` branch and
  6 by the first form of this rule, whose artefacts may be in a store. Every tile runs its vector
  stage again once; a tile without such an aerodrome writes the same files, so its mesh, masks
  and DSF come from the store.
* `stats.json` says what happened (`water.dry`, `coastline.dry`), only when something did.

## 4. Measured

Five tiles built with 1.0.2 and with the branch, up to the DSF (2026-10-14): the water taken out is
the census' (Macau's tile 199 ha, Shenzhen's 76 ha among them; Haneda 16 ha; Avranches 20.5 ha;
Schiphol's tile 27 ha; Chennai 9 ha); no crossing of edges, needles and fans the same or fewer;
land triangles steeper than 45 degrees as many as before but at Avranches (778 to 992, the edge of
the aerodrome). In X-Plane: Haneda's bridges are dry, Macau's runway and basin are land, Schiphol
looks better than XPME.

## 5. Limits

* **The outline is OpenStreetMap's.** An aerodrome drawn into a bay dries that water too; one not
  drawn leaves its water: Copalis Beach (S16), a beach runway with no aerodrome outline, stays in
  the sea. Shenzhen's outline holds 76 ha of ponds that XPME keeps as water and the rule dries.
* **A step at the edge** where OpenStreetMap's water covers high ground: at Avranches its sea covers
  240 m of salt meadows the relief puts at 6 m, and the aerodrome stands 6 m above it (XPME's water
  lies at 2.5 m there, a 4 m step).
