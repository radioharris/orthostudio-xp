# Audit of the Ortho4XP imagery providers

A record of 2026-09-12: the registry of OrthoStudio XP (`src/orthostudio/imagery/registry.toml`) was
chosen from it. The tool that produced it read Ortho4XP's provider files and was removed by
decision 0010.

Generated 2026-09-12T00:56:14+00:00 by `tools/audit/providers.py` from `Ortho4XP/Providers/**/*.lay`
(71 definitions). One HTTP request per provider, sequential, 20 s timeout, 127.4 s total. Machine
load before: `2:54 up 5 days, 7:37, 1 user, load averages: 4.13 4.16 3.54`.

## Method

- The `.lay` grammar of Ortho4XP (`src/O4_Imagery_Utils.py:92-490`, URL construction `:1096-1289`)
  is re-implemented without `eval`: `grid_type=webmercator`, custom TMS grids (`epsg_code`,
  `top_left_corner`, `resolutions` or `scaledenominator`), WMS 1.1.1 / 1.3 (axis swap, `SRS` vs
  `CRS`), WMTS with the shipped `capabilities*.xml`, placeholders
  `{zoom} {x} {y} {-y} {|y|} {quadkey} {xcenter} {ycenter} {size} {switch:a,b}`.
- Zoom policy: `min(max_zl, 15); custom grids: first level with res <= asked*1.1`. Test point: a
  city inside the coverage (Marseille 43.4N 5.2E for global providers), moved to the centre of the
  declared `Extents/**/*.ext` bounds when the city falls outside them.
- Headers: the `fake_headers` of the definition, else the generic Ortho4XP headers. Redirects
  followed (max 5). A TLS failure earns a single retry without certificate verification, flagged in
  the note.
- Placeholder detection: Bing `X-VE-Tile-Info: no-tile` or 1033 bytes, ArcGIS 2521 bytes (Ortho4XP
  rules), fully transparent image, max channel std < 3, at most 16 distinct colours, or fewer than
  one colour per 1000 pixels with std < 10 (near-uniform tile).
- `untested`: providers whose URL Ortho4XP assembles in `Providers/O4_Custom_URL.py` from a token
  scraped out of a web page (DK, DOP40, NIB, Here); only DNS was checked for them. CA_NAIP is in the
  same list but its URL needs no token and was rebuilt and probed.
- Credentials embedded in definitions are redacted as `***` in URLs below.

## Summary

| Status | Count | Meaning |
|---|---:|---|
| alive | 27 | 200 + decodable image, not a placeholder |
| placeholder | 3 | 200 + blank or no-data tile |
| needs_key | 5 | 401/403 or HTML page (key, token or referer required) |
| dead | 27 | DNS / connection failure, 404, 5xx, service exception |
| wms_wmts_unreachable | 3 | WMS/WMTS server unreachable |
| untested | 4 | token scraped by O4_Custom_URL.py |
| invalid | 2 | definition cannot produce a URL |
| **total** | **71** | |

**27 of 71 definitions return imagery today**; 5 need a key, token or referer; 30 are dead or
unreachable; 3 answer with a placeholder; 4 could not be tested without scraping a token;
2 definitions are broken.

## Recommendation for the initial OrthoStudio XP registry

Global providers alive without a key: `Arc`, `Arc@`, `BI`.

National providers alive without a key: `CH`, `CRO_2011`, `Hitta`, `Itris`, `JP`, `Lomba2015`,
`Lux`, `Lux2013`, `NL`, `PCN06`, `PCN12`, `PDOK`, `PDOK18`, `PDOK19`, `PDOK20`, `SE`, `SP`, `USGS`.

Alive but kept out of the default list:

- key, token or referer embedded in the definition: `CH_watermarked`, `CRO_2016`, `FIN`, `OSM`;
- usage policy forbids bulk download: `GO2`, `OSM`;
- duplicate of `Arc`: `USA2`.

Shown in the Ortho4XP GUI (`in_GUI` true) although not alive: `EOX`, `EOX2`, `Govmap`, `Here`,
`Mapbox`, `Maxar`, `SEA`.

Reading: the web-mercator tile providers (`webmercator` type) port directly to the OrthoStudio XP
tile store; WMS and custom-grid providers need the reprojection path of P4b and are listed here only
so P4b knows which ones are worth porting.

## Providers

| Code | Country | Type | ZL max | GUI | Auth | Status | HTTP | Latency | Size | Note |
|---|---|---|---|---|---|---|---|---:|---|---|
| `Arc` | Global | webmercator | - | yes | none | **alive** | 200 | 128 ms | 22.0 kB, 256x256 | 256x256 RGB Esri World Imagery: usable through ArcGIS Online terms (attribution required). http upgraded to https by the server |
| `Arc@` | Global | webmercator | - | no | none | **alive** | 200 | 98 ms | 21.5 kB, 256x256 | 256x256 RGB Esri 'Clarity' beta endpoint; same terms as Arc. |
| `BI` | Global | webmercator | - | yes | none | **alive** | 200 | 84 ms | 21.6 kB, 256x256 | 256x256 RGB Bing Maps: free tier ended 2025-06-30, platform end of life announced for 2028. |
| `EOX` | Global | webmercator | 14 | yes | none | needs_key | 403 | 160 ms | 0.1 kB | HTTP 403 s2maps is free with attribution; the 403 is administrative (UA/referer filtering). |
| `EOX2` | Global | webmercator | 14 | yes | none | needs_key | 403 | 141 ms | 0.1 kB | HTTP 403 s2maps is free with attribution; the 403 is administrative (UA/referer filtering). |
| `GO2` | Global | webmercator | - | yes | none | **alive** | 200 | 61 ms | 22.4 kB, 256x256 | 256x256 RGB Google Maps Platform terms forbid tile scraping; kept out of any default list. |
| `Here` | Global | custom (scraped token) | - | yes | scraped token | untested | - | - | - | URL built by O4_Custom_URL.py: APP_KEY scraped from the wego.here.com JS bundle, refreshed every 10000 s token source wego.here.com resolves tile host maps.hereapi.com resolves |
| `Mapbox` | Global | webmercator | - | yes | key in url | needs_key | 401 | 170 ms | 0.0 kB | HTTP 401 (Not Authorized - Invalid Token) Static access_token belongs to the OpenStreetMap project, not to OrthoStudio XP users. |
| `Maxar` | Global | webmercator | 22 | yes | key in url | dead | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known Maxar/DigitalGlobe connectId embedded in the definition (third-party key). host name no longer resolves services.digitalglobe.com is gone (Maxar SecureWatch); key-only service. |
| `OSM` | Global | webmercator | - | yes | referer | **alive** | 200 | 103 ms | 28.1 kB, 256x256 | 256x256 P OSM tile usage policy forbids bulk download: map background only, never textures. |
| `SEA` | Global | webmercator | 12 | yes | none | dead | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known Same IGN endpoint as FRorth capped at ZL12; uses the misspelt key color_filter. host name no longer resolves wxs.ign.fr was decommissioned by IGN (Geoplateforme, data.geopf.fr) in 2024. definition: key 'color_filter' (singular) is ignored by Ortho4XP; 'color_filters' meant |
| `USA2` | Global | webmercator | - | yes | none | **alive** | 200 | 115 ms | 22.0 kB, 256x256 | 256x256 RGB Duplicate of Arc kept by Ortho4XP for backward compatibility only. http upgraded to https by the server |
| `Itris` | Austria | wms 1.1.1 EPSG:3857 | - | no | none | **alive** | 200 | 306 ms | 108.8 kB, 512x512 | 512x512 RGB |
| `OST` | Austria | webmercator | - | no | none | dead | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves maps{1-4}.wien.gv.at: basemap.at moved to mapsneu.wien.gv.at (to verify). |
| `BE_Fr` | Belgium | tms EPSG:31370 | 17 lv, finest ~ZL20.5 | no | none | dead | ReadError | 11712 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `GeoPunt2012` | Belgium | webmercator | - | no | none | dead | ConnectError | 16 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves tile.informatievlaanderen.be is gone; Digitaal Vlaanderen successor to verify. definition: extent 'GeoPunt' has no Extents/**/GeoPunt.ext file |
| `GeoPunt2015` | Belgium | webmercator | - | no | none | dead | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves tile.informatievlaanderen.be is gone; Digitaal Vlaanderen successor to verify. definition: extent 'GeoPunt' has no Extents/**/GeoPunt.ext file |
| `Wallonie2013` | Belgium | tms EPSG:31370 | 17 lv, finest ~ZL20.5 | no | none | dead | ReadError | 11308 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `CRO_2011` | Croatia | wms 1.1.1 EPSG:3765 | - | no | none | **alive** | 200 | 482 ms | 74.0 kB, 512x512 | 512x512 RGB |
| `CRO_2016` | Croatia | wms 1.1.1 EPSG:3765 | - | no | key in url | **alive** | 200 | 862 ms | 93.1 kB, 512x512 | 512x512 RGB |
| `CRO_2017` | Croatia | wmts EPSG:3765 | 13 lv, finest ~ZL19.6 | no | key in url | dead | 400 | 151 ms | 0.0 kB | HTTP 400 (Layer DOF5_2017 was not found!) The WMTS answers 400 'Layer DOF5_2017 was not found': the layer was renamed. |
| `CZ` | Czech Republic and Slovakia | webmercator | - | no | none | dead | ConnectError | 27 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves mapserver.mapy.cz is gone; mapy.cz tiles now require an API key. |
| `DK` | Denmark | custom (scraped token) | - | no | scraped token | untested | - | - | - | URL built by O4_Custom_URL.py: TICKET scraped from sdfekort.dk HTML, refreshed every 3600 s token source sdfekort.dk resolves tile host kortforsyningen.kms.dk resolves kortforsyningen.kms.dk was replaced by dataforsyningen.dk (token-based) in 2023. |
| `EST` | Estonia | tms EPSG:3301 | 16 lv, finest ~ZL18.7 | no | none | dead | ConnectError | 1 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves pump.reach-u.com (Delfi orthophoto mirror) is gone; Maa-amet WMS is the source. |
| `FO` | Faroe | webmercator | - | no | none | needs_key | 200 | 713 ms | 4.9 kB | redirected to an HTML page https://kort.foroyakort.fo/kort/ kortal.fo redirects to the foroyakort.fo portal: the TMS endpoint moved. |
| `FIN` | Finland | wmts EPSG:3857 | 19 lv, finest ~ZL18.0 | yes | referer | **alive** | 200 | 264 ms | 34.0 kB, 256x256 | 256x256 RGB |
| `FR2010_` | France | webmercator | - | no | none | invalid | - | - | - | request_type tms without url_template; no url_template Same decommissioned wxs.ign.fr host as FRorth. |
| `FRorth` | France | webmercator | - | no | none | dead | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves wxs.ign.fr was decommissioned by IGN (Geoplateforme, data.geopf.fr) in 2024. |
| `IAU2008` | France | tms EPSG:2154 | 16 lv, finest ~ZL18.6 | no | none | dead | ReadError | 10993 ms | - | ReadError: [Errno 54] Connection reset by peer definition: extent 'IdF' has no Extents/**/IdF.ext file |
| `LittoV2` | France | wms 1.1.1 EPSG:2154 | - | no | none | wms_wmts_unreachable | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves geolittoral WMS host is gone (Cerema / geoplateforme successor to verify). |
| `DOP40` | Germany | custom (scraped token) | - | no | scraped token | untested | - | - | - | URL built by O4_Custom_URL.py: Set-Cookie of a JS bundle replayed as Cookie header, refreshed every 3600 s token source sg.geodatenzentrum.de resolves tile host sg.geodatenzentrum.de resolves |
| `HU` | Hungary | webmercator | 17 | no | none | dead | 404 | 150 ms | 3.3 kB | HTTP 404 at the test point (Az oldal nem található...) |
| `Govmap` | Israel | tms EPSG:2039 | 11 lv, finest ~ZL18.6 | yes | none | invalid | - | - | - | placeholders not in the grammar: {x_gov} {y_gov} {zoom_gov} |
| `Isrl` | Israel | tms EPSG:4326 | 12 lv, finest ~ZL18.9 | no | none | dead | 404 | 147 ms | 0.2 kB | HTTP 404 at the test point (404 Not Found) 404 on a custom grid: the cell may fall outside the served levels |
| `AltoAdige08` | Italy | webmercator | - | no | none | dead | 404 | 63 ms | 0.1 kB | HTTP 404 at the test point (404 Not Found) |
| `AltoAdige1415` | Italy | tms EPSG:25832 | 11 lv, finest ~ZL19.0 | no | none | dead | 404 | 22 ms | 0.1 kB | HTTP 404 at the test point (404 Not Found) 404 on a custom grid: the cell may fall outside the served levels |
| `Aosta2005` | Italy | tms EPSG:23032 | 10 lv, finest ~ZL19.7 | no | none | dead | ReadError | 11058 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `Aosta2012` | Italy | tms EPSG:23032 | 10 lv, finest ~ZL19.7 | no | none | dead | ReadError | 10676 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `Aosta2015` | Italy | tms EPSG:23032 | 10 lv, finest ~ZL19.7 | no | none | dead | ReadError | 11446 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `Lomba2015` | Italy | tms EPSG:32632 | 14 lv, finest ~ZL18.7 | no | none | **alive** | 200 | 3426 ms | 108.7 kB, 256x256 | 256x256 RGB |
| `PCN06` | Italy | tms EPSG:32633 | 20 lv, finest ~ZL18.7 | no | none | **alive** | 200 | 268 ms | 128.7 kB, 512x512 | 512x512 RGB |
| `PCN12` | Italy | tms EPSG:32633 | 19 lv, finest ~ZL18.7 | no | none | **alive** | 200 | 90 ms | 124.3 kB, 512x512 | 512x512 RGB |
| `Piem2010` | Italy | tms EPSG:3857 | 16 lv, finest ~ZL20.0 | no | none | dead | 200 | 200 ms | 6.9 kB | HTML page instead of an image ArcGIS REST error page: the Mosaico_2010 service no longer exists on this server. |
| `JP` | Japan | webmercator | - | no | none | **alive** | 200 | 366 ms | 20.8 kB, 256x256 | 256x256 RGB GSI (Japan) tiles: free with attribution; 2007 imagery with partial coverage. http upgraded to https by the server |
| `Lux` | Luxembourg | webmercator | - | no | none | **alive** | 200 | 86 ms | 32.4 kB, 256x256 | 256x256 RGB |
| `Lux2013` | Luxembourg | wms 1.1.1 EPSG:2169 | - | no | none | **alive** | 200 | 331 ms | 482.9 kB, 1024x1024 | 1024x1024 RGB |
| `NL` | Netherland | wmts EPSG:3857 | 20 lv, finest ~ZL19.0 | no | none | **alive** | 200 | 211 ms | 32.7 kB, 256x256 | 256x256 RGB |
| `PDOK` | Netherland | wmts EPSG:3857 | 20 lv, finest ~ZL19.0 | yes | none | **alive** | 200 | 40 ms | 32.7 kB, 256x256 | 256x256 RGB |
| `PDOK18` | Netherland | wmts EPSG:3857 | 20 lv, finest ~ZL19.0 | yes | none | **alive** | 200 | 112 ms | 38.9 kB, 256x256 | 256x256 RGB |
| `PDOK19` | Netherland | wmts EPSG:3857 | 20 lv, finest ~ZL19.0 | yes | none | **alive** | 200 | 129 ms | 32.6 kB, 256x256 | 256x256 RGB |
| `PDOK20` | Netherland | wmts EPSG:3857 | 20 lv, finest ~ZL19.0 | yes | none | **alive** | 200 | 51 ms | 33.4 kB, 256x256 | 256x256 RGB |
| `NZ` | New Zealand | webmercator | - | no | key in url | dead | 500 | 142 ms | 0.3 kB | HTTP 500 (Fastly error: unknown domain koordinates-tiles-a.global.ssl.fastly.net) Koordinates key embedded in the definition; partial coverage, .comb use only. Koordinates tile host unknown to Fastly: LINZ tiles moved (basemaps.linz.govt.nz). definition: template uses {z}, which the Ortho4XP grammar never substitutes ({zoom}) |
| `NIB` | Norway | custom (scraped token) | 18 lv, finest ~ZL18.9 | no | scraped token | untested | - | - | - | URL built by O4_Custom_URL.py: nibToken scraped from the norgeibilder.no HTML, refreshed every 3600 s token source www.norgeibilder.no resolves tile host tilecache.norgeibilder.no resolves |
| `PL` | Poland | wms 1.1.1 EPSG:3857 | - | no | none | needs_key | 401 | 87 ms | 0.1 kB | HTTP 401 (Unauthorized.) |
| `SLO` | Slovenia | tms EPSG:102060 | 17 lv, finest ~ZL18.6 | no | none | dead | 200 | 133 ms | 6.9 kB | HTML page instead of an image ArcGIS REST error page: the ARSO service no longer exists on this server. |
| `SLO_2009_2011` | Slovenia | tms EPSG:102060 | 17 lv, finest ~ZL18.6 | no | none | dead | 200 | 103 ms | 6.9 kB | HTML page instead of an image ArcGIS REST error page: the ARSO service no longer exists on this server. |
| `SLO_2016` | Slovenia | tms EPSG:102060 | 17 lv, finest ~ZL18.6 | no | none | dead | 200 | 87 ms | 6.9 kB | HTML page instead of an image ArcGIS REST error page: the ARSO service no longer exists on this server. |
| `SP` | Spain | webmercator | - | no | none | **alive** | 200 | 81 ms | 21.6 kB, 256x256 | 256x256 RGB |
| `SP2012` | Spain | wms 1.1.1 EPSG:3857 | - | no | none | placeholder | 200 | 22 ms | 6.3 kB, 1024x1024 | uniform image (max channel std 0.0) pnoa-historico answers a blank 1024 px image: the PNOA2012 layer name is stale. |
| `SP2015` | Spain | wms 1.1.1 EPSG:3857 | - | no | none | placeholder | 200 | 22 ms | 6.3 kB, 1024x1024 | uniform image (max channel std 0.0) pnoa-historico answers a blank 1024 px image: the PNOA2015 layer name is stale. |
| `Hitta` | Sweden | webmercator | - | no | none | **alive** | 200 | 118 ms | 83.8 kB, 256x256 | 256x256 RGB Commercial Swedish map site; terms of use for bulk download not verified. |
| `SE` | Sweden | webmercator | - | no | none | **alive** | 200 | 368 ms | 25.9 kB, 256x256 | 256x256 RGB Eniro commercial map site (Norwegian host); terms of use not verified. http upgraded to https by the server |
| `SE2` | Sweden | wms 1.1.1 EPSG:3006 | - | no | referer | wms_wmts_unreachable | ConnectError | 2 ms | - | ConnectError: [Errno 8] nodename nor servname provided, or not known host name no longer resolves kso.etjanster.lantmateriet.se is gone; Lantmateriet now requires an account. |
| `CH` | Switzerland | wms 1.1.1 EPSG:21781 | - | no | none | **alive** | 200 | 357 ms | 784.6 kB, 512x512 | 512x512 RGBA WMS in EPSG:21781 (LV03), 800 kB PNG per 512 px request: slow for textures. |
| `CH_VS` | Switzerland | wmts EPSG:2056 | 29 lv, finest ~ZL21.0 | no | none | dead | 404 | 80 ms | 0.3 kB | HTTP 404 at the test point (404 Not Found) 404 on a custom grid: the cell may fall outside the served levels |
| `CH_watermarked` | Switzerland | webmercator | - | yes | referer | **alive** | 200 | 90 ms | 29.4 kB, 256x256 | 256x256 RGB swisstopo tiles are watermarked without an API key (hence the name). |
| `CH_ZH` | Switzerland | wms 1.0.0 EPSG:2056 | - | no | none | placeholder | 200 | 62 ms | 10.5 kB, 1024x1024 | near-uniform image (240 colours, max channel std 7.02): no-data tile MapServer renders 'Invalid layer(s) given in the LAYERS parameter' as an image: the layer 'ortho' was renamed (image inspected by hand). |
| `Alaska` | United States | webmercator | - | no | none | dead | ReadError | 9782 ms | - | ReadError: [Errno 54] Connection reset by peer |
| `CA_NAIP` | United States | custom ArcGIS exportImage EPSG:3857 | - | no | none | wms_wmts_unreachable | ConnectError | 262 ms | - | ConnectError: TLS handshake fails even without verification: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1082) gis.apfo.usda.gov aborts the TLS handshake; NAIP moved to naip.imagery.usda.gov (to verify). |
| `NAIP` | United States | webmercator | - | no | none | dead | ConnectError | 259 ms | - | ConnectError: TLS handshake fails even without verification: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1082) gis.apfo.usda.gov aborts the TLS handshake; NAIP moved to naip.imagery.usda.gov (to verify). |
| `USGS` | United States | webmercator | - | no | none | **alive** | 200 | 122 ms | 30.0 kB, 256x256 | 256x256 RGB USGS Imagery Only basemap: public domain, US coverage. |

## Test URLs

- `Arc` Marseille (43.4, 5.2) ZL~15.0:
  `http://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/15/11990/16857`
- `Arc@` Marseille (43.4, 5.2) ZL~15.0:
  `https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/15/11990/16857`
- `BI` Marseille (43.4, 5.2)
  ZL~15.0: `http://r0.ortho.tiles.virtualearth.net/tiles/a120222133031221.jpeg?g=136`
- `EOX` Marseille (43.4, 5.2) ZL~14.0:
  `https://a.s2maps-tiles.eu/wmts/?layer=s2cloudless_3857&style=default&tilematrixset=GoogleMapsCompatible&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fjpeg&TileMatrix=14&TileCol=8428&TileRow=5995`
- `EOX2` Marseille (43.4, 5.2) ZL~14.0:
  `https://a.s2maps-tiles.eu/wmts/?layer=s2cloudless-2018_3857&style=default&tilematrixset=GoogleMapsCompatible&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fjpeg&TileMatrix=14&TileCol=8428&TileRow=5995`
- `GO2` Marseille (43.4, 5.2) ZL~15.0: `http://mt0.google.com/vt/lyrs=s&x=16857&y=11990&z=15`
- `Here` Marseille (43.4, 5.2) : `(none)`
- `Mapbox` Marseille (43.4, 5.2)
  ZL~15.0: `https://a.tiles.mapbox.com/v4/mapbox.satellite/15/16857/11990.jpg?access_token=***`
- `Maxar` Marseille (43.4, 5.2) ZL~15.0:
  `https://services.digitalglobe.com/earthservice/tmsaccess/tms/1.0.0/DigitalGlobe:ImageryTileService@EPSG:3857@jpg/15/16857/20777.jpg?connectId=***`
- `OSM` Marseille (43.4, 5.2) ZL~15.0: `https://tile.openstreetmap.org/15/16857/11990.png`
- `SEA` Marseille (43.4, 5.2) ZL~12.0:
  `http://wxs.ign.fr/61fs25ymczag0c67naqvvmap/geoportail/wmts?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX=12&TILEROW=1498&TILECOL=2107`
- `USA2` Marseille (43.4, 5.2) ZL~15.0:
  `http://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/15/11990/16857`
- `Itris` Innsbruck (47.26, 11.39) ZL~15:
  `https://gis.tirol.gv.at/arcgis/services/Service_Public/orthofoto/MapServer/WmsServer?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=Image_Aktuell_RGB&STYLES=&SRS=EPSG:3857&WIDTH=512&HEIGHT=512&BBOX=1266706.0076829935,5983393.438900151,1269151.9925877787,5985839.423804936`
- `OST` Vienna (48.21, 16.37)
  ZL~15.0: `http://maps1.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/15/11362/17874.jpeg`
- `BE_Fr` Namur (50.47, 4.87) ZL~15.2:
  `http://geoservices.wallonie.be/arcgis/rest/services/IMAGERIE/ORTHO_LAST/MapServer/tile/11/30482/26617`
- `GeoPunt2012` Ghent (51.05, 3.72) ZL~15.0:
  `http://tile.informatievlaanderen.be/ws/raadpleegdiensten/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=omzrgb12vl&STYLE=&FORMAT=image/png&TILEMATRIXSET=GoogleMapsVL&TILEMATRIX=15&TILEROW=10962&TILECOL=16722`
- `GeoPunt2015` Ghent (51.05, 3.72) ZL~15.0:
  `http://tile.informatievlaanderen.be/ws/raadpleegdiensten/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=omzrgb15vl&STYLE=&FORMAT=image/png&TILEMATRIXSET=GoogleMapsVL&TILEMATRIX=15&TILEROW=10962&TILECOL=16722`
- `Wallonie2013` Namur (50.47, 4.87) ZL~15.2:
  `http://geoservices.wallonie.be/arcgis/rest/services/IMAGERIE/ORTHO_2012_2013/MapServer/tile/11/30482/26617`
- `CRO_2011` Zagreb (45.81, 15.98) ZL~15:
  `http://geoportal.dgu.hr/ows?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=DOF&STYLES=&SRS=EPSG:3765&WIDTH=512&HEIGHT=512&BBOX=458733.01561818906,5073739.0591749,460437.9648575291,5075444.00841424`
- `CRO_2016` Zagreb (45.81, 15.98) ZL~15:
  `http://geoportal.dgu.hr/services/auth/inspire/orthophoto_2014-2016/wms?requesttype=from_browser&authKey=***&SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=OI.OrthoImagery&STYLES=&SRS=EPSG:3765&WIDTH=512&HEIGHT=512&BBOX=458733.01561818906,5073739.0591749,460437.9648575291,5075444.00841424`
- `CRO_2017` Zagreb (45.81, 15.98) ZL~15.3:
  `https://geoportal.dgu.hr/services/auth/dof_hok_tk/wmts?requesttype=from_browser&authKey=***&&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=DOF5_2017&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG3765:256x256&TILEMATRIX=8&TILEROW=483&TILECOL=924`
- `CZ` Prague (50.09, 14.42) ZL~15.0: `http://m1.mapserver.mapy.cz/ophoto-m/15-17696-11100`
- `DK` Copenhagen (55.68, 12.57) : `(none)`
- `EST` Tallinn (59.44, 24.75) ZL~15.7: `https://pump.reach-u.com/delfi-orto/12/2009/2286`
- `FO` Torshavn (62.01, -6.77) ZL~15.0:
  `http://www.kortal.fo/background/gwc/service/tms/1.0.0/FDS:Mynd_Verdin@EPSG:900913@jpg/15/15767/23629.jpeg`
- `FIN` Helsinki (60.17, 24.94) ZL~15.0:
  `https://karttamoottori.maanmittauslaitos.fi/maasto/wmts?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ortokuva&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=WGS84_Pseudo-Mercator&TILEMATRIX=15&TILEROW=9484&TILECOL=18654`
- `FR2010_` Marseille (43.3, 5.37) : `(none)`
- `FRorth` Marseille (43.3, 5.37) ZL~15.0:
  `http://wxs.ign.fr/61fs25ymczag0c67naqvvmap/geoportail/wmts?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX=15&TILEROW=12003&TILECOL=16872`
- `IAU2008` Paris (48.86, 2.35) ZL~15.2:
  `http://geoviz.iau-idf.fr/arcgis/sharing/servers/7fa31e9e1b4c41b5803c4106692c5f09/rest/services/orthophoto/ortho2008/MapServer/tile/12/62141/53518`
- `LittoV2` La Rochelle (46.16, -1.15) ZL~15:
  `http://geolittoral.application.developpement-durable.gouv.fr/wms2/metropole?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=ortholittorale_v2_rvb&STYLES=&SRS=EPSG:2154&WIDTH=512&HEIGHT=512&BBOX=379053.4029919795,6569808.472588706,380747.60682669864,6571502.676423426`
- `DOP40` Berlin (52.52, 13.4) : `(none)`
- `HU` Budapest (47.5, 19.04)
  ZL~15.0: `http://a.tile.openstreetmap.hu/ortofoto2005/15/18117/11458.jpg`
- `Govmap` Tel Aviv (32.08, 34.78) : `(none)`
- `Isrl` Tel Aviv (32.08, 34.78) ZL~15.9:
  `http://www10.emap.co.il/ArcGIS/rest/services/Clients/Bezeq_Ortho/MapServer/tile/8/60399/71375`
- `AltoAdige08` Bolzano (46.5, 11.35) ZL~15.0:
  `http://geoservices.buergernetz.bz.it/geoserver/gwc/service/tms/1.0.0/P_BZ_OF_2008_EPSG3857@GoogleMapsCompatible@jpeg/15/17417/21176.jpeg`
- `AltoAdige1415` Bolzano (46.5, 11.35) ZL~15.0:
  `http://geoservices.buergernetz.bz.it/geoserver/gwc/service/tms/1.0.0/P_BZ_OF_2014_2015@EPSG%3A25832_20cm@jpeg/6/98/100.jpeg`
- `Aosta2005` Aosta (45.74, 7.32) ZL~15.3:
  `http://cachesct.partout.it/arcgis/rest/services/Foundation/Ortofoto2005/MapServer/tile/5/3640/4053`
- `Aosta2012` Aosta (45.74, 7.32) ZL~15.3:
  `http://cachesct.partout.it/arcgis/rest/services/Foundation/Ortofoto/MapServer/tile/5/3640/4053`
- `Aosta2015` Aosta (45.74, 7.32) ZL~15.3:
  `http://cachesct.partout.it/arcgis/rest/services/Foundation/Ortofoto2005/MapServer/tile/5/3640/4053`
- `Lomba2015` Milan (45.46, 9.19) ZL~15.3:
  `https://www.cartografia.servizirl.it/arcgis2/rest/services/BaseMap/ortofoto2015UTM32N/ImageServer/tile/10/7328/8320`
- `PCN06` Rome (41.9, 12.5)
  ZL~15.4: `http://www.pcn.minambiente.it/arcgis/rest/services/base_new/MapServer/tile/13/3954/3996`
- `PCN12` Rome (41.9, 12.5) ZL~15.4:
  `http://www.pcn.minambiente.it/arcgis/rest/services/immagini/ortofoto_colore_12/MapServer/tile/12/3954/3996`
- `Piem2010` Turin (45.07, 7.69) ZL~15.0:
  `http://webgis.arpa.piemonte.it/ags101free/rest/services/topografia_dati_di_base/Mosaico_2010_jpg/MapServer/tile/10/20121/17084`
- `JP` Tokyo (35.68, 139.77) ZL~15.0: `http://cyberjapandata.gsi.go.jp/xyz/ort/15/29106/12903.jpg`
- `Lux` Luxembourg (49.61, 6.13) ZL~15.0:
  `http://wmts1.geoportail.lu/opendata/wmts/ortho_latest/GLOBAL_WEBMERCATOR_4_V3/15/16941/11168.jpeg`
- `Lux2013` Luxembourg (49.61, 6.13) ZL~15:
  `http://wmts1.geoportail.lu/opendata/service?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=ortho_2013&STYLES=&SRS=EPSG:2169&WIDTH=1024&HEIGHT=1024&BBOX=75659.72913895117,73456.90458143718,78829.66187121681,76626.83731370282`
- `NL` Utrecht (52.09, 5.12) ZL~15.0:
  `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=Actueel_ortho25&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=15&TILEROW=10810&TILECOL=16850`
- `PDOK` Utrecht (52.09, 5.12) ZL~15.0:
  `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=Actueel_ortho25&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=15&TILEROW=10810&TILECOL=16850`
- `PDOK18` Utrecht (52.09, 5.12) ZL~15.0:
  `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=2018_ortho25&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=15&TILEROW=10810&TILECOL=16850`
- `PDOK19` Utrecht (52.09, 5.12) ZL~15.0:
  `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=2019_ortho25&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=15&TILEROW=10810&TILECOL=16850`
- `PDOK20` Utrecht (52.09, 5.12) ZL~15.0:
  `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=2020_ortho25&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=15&TILEROW=10810&TILECOL=16850`
- `NZ` Wellington (-41.29, 174.78) ZL~15.0:
  `https://koordinates-tiles-a.global.ssl.fastly.net/services;key=***/tiles/v4/layer=51769,style=auto;layer=88131,style=auto;layer=95497,style=auto/15/32292/20517.png`
- `NIB` Oslo (59.91, 10.75) : `(none)`
- `PL` Warsaw (52.23, 21.01) ZL~15:
  `http://mapy.geoportal.gov.pl/wss/service/img/guest/ORTO/MapServer/WMSServer?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=Raster&STYLES=&SRS=EPSG:3857&WIDTH=512&HEIGHT=512&BBOX=2337599.5091142855,6840596.728736934,2340045.49401907,6843042.713641719`
- `SLO` Ljubljana (46.05, 14.51) ZL~15.3:
  `http://gis.arso.gov.si/arcgis/rest/services/AO_DOF_2009_2011_AG101/MapServer/tile/13/168/173`
- `SLO_2009_2011` Ljubljana (46.05, 14.51) ZL~15.3:
  `http://gis.arso.gov.si/arcgis/rest/services/AO_DOF_2009_2011_AG101/MapServer/tile/13/168/173`
- `SLO_2016` Ljubljana (46.05, 14.51)
  ZL~15.3: `http://gis.arso.gov.si/arcgis/rest/services/DOF_2016/MapServer/tile/13/168/173`
- `SP` Madrid (40.42, -3.7) ZL~15.0:
  `http://www.ign.es/wmts/pnoa-ma/geoserver/gwc/service/wmts?FORMAT=image/jpeg&VERSION=1.0.0&SERVICE=WMTS&REQUEST=GetTile&LAYER=OI.OrthoimageCoverage&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX=15&TILEROW=12355&TILECOL=16047`
- `SP2012` Madrid (40.42, -3.7) ZL~15:
  `http://www.ign.es/wms/pnoa-historico?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=PNOA2012&STYLES=&SRS=EPSG:3857&WIDTH=1024&HEIGHT=1024&BBOX=-414328.1008398974,4924718.5794800855,-409436.13103032706,4929610.549289655`
- `SP2015` Madrid (40.42, -3.7) ZL~15:
  `http://www.ign.es/wms/pnoa-historico?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=PNOA2015&STYLES=&SRS=EPSG:3857&WIDTH=1024&HEIGHT=1024&BBOX=-414328.1008398974,4924718.5794800855,-409436.13103032706,4929610.549289655`
- `Hitta` Stockholm (59.33, 18.07) ZL~15.0: `https://static.hitta.se/tile/v3/1/15/18028/23131`
- `SE` Stockholm (59.33, 18.07)
  ZL~15.0: `http://map04.eniro.no/geowebcache/service/tms1.0.0/aerial/15/18028/23131.jpeg`
- `SE2` Stockholm (59.33, 18.07) ZL~15:
  `https://kso.etjanster.lantmateriet.se/karta/ortofoto/wms/v1.3?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/png&REQUEST=GetMap&LAYERS=Ortofoto_0.5,Ortofoto_0.4,Ortofoto_0.25,Ortofoto_0.16&STYLES=&SRS=EPSG:3006&WIDTH=512&HEIGHT=512&BBOX=674024.0426982817,6580200.736156,675271.7215723371,6581448.415030055`
- `CH` Bern (46.95, 7.45) ZL~15:
  `https://wms.geo.admin.ch/?SERVICE=WMS&VERSION=1.1.1&FORMAT=image/png&REQUEST=GetMap&LAYERS=ch.swisstopo.swissimage&STYLES=&SRS=EPSG:21781&WIDTH=512&HEIGHT=512&BBOX=600030.5255472722,199044.82175699034,601700.243698316,200714.5399080342`
- `CH_VS` Sion (46.23, 7.36) ZL~15.7:
  `http://mapproxy.vsgis.ch/proxy/service?FORMAT=image/png&&SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile&LAYER=7_swissimage2017&STYLE=&FORMAT=image/jpeg&TILEMATRIXSET=epsg&TILEMATRIX=22&TILEROW=113&TILECOL=90`
- `CH_watermarked` Bern (46.95, 7.45) ZL~15.0:
  `https://wmts100.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/15/17062/11532.jpeg`
- `CH_ZH` Zurich (47.37, 8.54) ZL~15:
  `http://wms.zh.ch/OGDOrthoZH?SERVICE=WMS&VERSION=1.0.0&FORMAT=image/jpeg&REQUEST=GetMap&LAYERS=ortho&STYLES=&SRS=EPSG:2056&WIDTH=1024&HEIGHT=1024&BBOX=2681529.7146723866,1245500.1731832405,2684842.8564880947,1248813.314998949`
- `Alaska` Anchorage (61.22, -149.9) ZL~15.0:
  `http://gis1.dnr.alaska.gov/terrapixel/ips/tilesets/SDMI_ORTHO_RGB/SDMI_ORTHO_RGB/default/smerc/15/9289/2739.png?INSTANCE=ortho`
- `CA_NAIP` Sacramento (38.58, -121.49) ZL~15:
  `https://gis.apfo.usda.gov/arcgis/rest/services/NAIP_Historical/CA_NAIP/ImageServer/exportImage?f=image&bbox=-13526650.92137959%2C4659241.512939806%2C-13521758.951570021%2C4664133.4827493755&imageSR=102100&bboxSR=102100&size=1024%2C1024`
- `NAIP` Kansas City (39.1, -94.58) ZL~15.0:
  `https://gis.apfo.usda.gov/arcgis/rest/services/NAIP/USDA_CONUS_PRIME/ImageServer/tile/15/12511/7775`
- `USGS` Kansas City (39.1, -94.58) ZL~15.0:
  `https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/15/12511/7775`

## Extra probe: Bing placeholder signature (open ocean, ZL15, 30N 40W)

| URL | HTTP | Bytes | Content-Length | X-VE-Tile-Info | Image | Colours |
|---|---|---:|---|---|---|---:|
| `http://r0.ortho.tiles.virtualearth.net/tiles/a033020133002333.jpeg?g=136` | 200 | 1033 | 1033 | no-tile | 256x256 | 8 |
| `https://ecn.t0.tiles.virtualearth.net/tiles/a033020133002333.jpeg?g=1` | 200 | 1033 | 1033 | no-tile | 256x256 | 8 |

## Re-run

No longer possible from this repository: `tools/audit/providers.py`, its raw data and
`tests/test_providers_audit.py` read Ortho4XP's provider definitions and left with decision 0010.
