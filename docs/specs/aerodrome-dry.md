# The water an aerodrome holds, taken out of it

Status: in branch `aerodrome-dry` (2026-10-14). Code: `vectors/layers.py` (`dry_aerodromes`),
`vectors/water.py` (`DryAerodromes`, `touched_pieces`, `left_after`, `without`,
`build_water_layers(dry=)`), `vectors/coast.py` (`build_sea_layers(dry=)`), `vectors/rule.py`
(version 9). Tests:
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
  multipolygons, that meet a land runway (`aeroway=runway`, line or area) and no water runway
  (`dry_aerodromes`). A runway is water when its `surface` is `water` or when its ends are named
  as water runways are, a `W` after the number (`02W/20W`): Kenai's is tagged `surface=unpaved`.
  A seaplane base keeps its water, and so does an airport with a float pond beside its runways:
  Fairbanks' and Kenai's taxiways run into theirs (census, 2026-10-14). An aerodrome drawn as a
  point has no outline and changes nothing.
* **Only the water its surfaces touch.** The water inside the outlines is cut into its pieces, and
  a piece is taken out only where the aerodrome's runways, taxiways, taxilanes or aprons
  (`aeroway`, lines and areas, not `surface=water`) touch it (`touched_pieces`). The first form
  took all the water inside the outline: the census found 419 ha of the lake of Cazaux inside the
  air base's outline, far from any of its surfaces, and X-Plane showed them as a flat meadow
  under Bing's blurred photo of a military site; Lake Hood, the seaplane base of Anchorage, which
  also has a dirt strip and whose water lanes OpenStreetMap does not tag, lost 80 ha of its lake.
* **A touch is not a crossing.** A line that enters a piece of water by less than 20 m in all, or
  an area that covers less than 400 m2 of it, does not count (`TOUCH_MIN_M`): two maps that hardly
  meet. Lake Hood's lake is entered by 9 m of a line; the bridges and runways the rule is for cross
  600 m (Haneda) to 1.6 km (Macau) of water.
* **Out of the lakes and rivers.** Those pieces are taken out of the merged WATER and SEA_EQUIV
  polygons before they are encoded and seeded (`water.without`). A polygon with no such piece is
  the same object.
* **Out of the sea.** `build_sea_layers` takes them out of the sea area before its seeds are
  placed, and the outline of what it took joins the SEA lines, so the sea stops there.
* **What is left, on the noder's grid.** The noder puts every node on a grid of 9 decimals, so
  a shore the cut passes a hair's breadth from touches it there: the outline of Homey Airport
  (KXTA) passes so near a point of Groom Lake's shore, and what the cut left was one polygon of
  two lobes joined at that point, with one seed; Triangle4XP made the lobe without it land
  (12.8 ha, 2026-10-14). What a cut leaves is put on that grid first (`left_after`), so the
  lobes come apart and each gets its seed, in the lakes and in the sea.
* **Surfaces only**, on either side of a cut: an intersection can come back with a line beside
  its surfaces, and the outline of what was taken is then no outline (13 tiles of the pavement rule
  failed so). That is the one thing the shelved rule taught that this one keeps; its other
  safeguards answered the shapes of X-Plane's pavement and are left out until a measure asks for
  them.

The ground taken out of the water is land like any other: the relief, smoothed over the aerodrome
as Ortho4XP smooths it. The photo of the water stays on it; at Schiphol, where XPME shows that
ground blue, the photo of the place shows.

## 3. Isolation and the cache

* `orthostudio.vectors` goes to version 9; 5 is taken by the dry basins of the `ideas` branch, 6
  to 8 by first forms of this rule, whose artefacts may be in a store. Every tile runs its vector
  stage again once; a tile without such an aerodrome writes the same files, so its mesh, masks
  and DSF come from the store.
* `stats.json` says what happened (`water.dry`, `coastline.dry`), only when something did.

## 4. Measured

World census (2026-10-14): the library's 11 914 tiles with airports through the stage's own
functions, no error. Taking all the water inside the outlines touched 2 228 aerodromes on 1 518
tiles, 9 578 ha; the surfaces that touch it, 64 aerodromes and 1 799 ha; the rule as it stands,
35 aerodromes on 33 tiles. The census counts a piece of water that crosses a tile edge on both
tiles: the salt pan of Bitterwasser in Namibia, whose runways lie on it, is 535 ha, 488 on one tile
and 47 on the other, not 508 on each. The largest besides are the ice runway of McMurdo in
Antarctica (241 ha of sea ice), Macau (123 ha), Shenzhen (52 ha), Groom Lake (31 ha), Avranches
(19 ha) and Haneda (14 ha).

Built with 1.0.2 and with the branch, up to the DSF (2026-10-14 and 15): the 33 tiles where the rule
acts, the 27 that version 7 dried and version 8 spares, and the 72 witness tiles, 120 in all; 118
built, the other two (+76-120, and McMurdo's -78+166) having no X-Plane scenery to build on. 31
changed, each one a tile where the rule acts, and no other; the 26 spared tiles built are the same
as 1.0.2's. Trivandrum's piece (473 m2) is under the smallest water a tile keeps, so its tile is
the same too. The mesh loses the water the rule takes and no more, 861 ha on the 31 tiles; before
`left_after`, Groom Lake lost 12.8 ha more, the only such lobe in the 33 tiles. Land triangles
steeper than 45 degrees within 300 m of what was taken: 0 to 4 a tile, under 2 m high, but at
Avranches (185, the 6 m step) and Groom Lake (280, 0.3 m high).

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
* **Runways X-Plane does not have.** The rule follows OpenStreetMap's runways. At Homey Airport
  (KXTA) it draws a second pair of runways 03/21 on Groom Lake, 1.2 km north-west of X-Plane's,
  one of them closed, and the aerodrome's outline wraps them in a narrow strip: that strip of the
  lake is dried, a white band across it with no runway on it in X-Plane (2026-10-14).
* **X-Plane's own airport surfaces under the water left.** Where they lie in water OpenStreetMap
  draws and no OpenStreetMap runway touches, they show under its surface, as in 1.0.2: the rest of
  Groom Lake, which is dry and which X-Plane paves as a lakebed.
* **A blue patch at KXTA, not understood.** X-Plane draws bare water, about 250 by 40 m, beside the
  cut, where large water triangles meet near the point the cut shares with the shore; 1.0.2 has
  none there. The DSF holds the photo, the heights and the values there as elsewhere, and the
  same shape of triangle, large and touching the shore, is in 1.0.2 at Macau (17) and Haneda (8)
  with no such patch seen. Open (2026-10-15).
