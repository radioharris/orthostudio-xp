"""Coded errors with a remedy.

Every failure or silent degradation that OrthoStudio XP can meet has a stable code
(``DOMAIN_SNAKE_CASE``), an English message, a remedy for the user, a severity and
an action. The inventory and its origin in Ortho4XP live in
``docs/specs/errors.md``; this module is the executable registry.

Usage::

    raise OsxpError("DEM_DOWNLOAD_FAILED", context={"tile": "+43+005", "url": url})

    try:
        ...
    except Exception as exc:
        emit(render_json(exc))
"""

from __future__ import annotations

import enum
import json
import re
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePath
from types import MappingProxyType
from typing import Any

__all__ = [
    "CODE_PATTERN",
    "DOMAINS",
    "REGISTRY",
    "SCHEMA_VERSION",
    "Action",
    "ErrorSpec",
    "OsxpError",
    "Severity",
    "by_domain",
    "codes",
    "render_json",
    "spec_for",
    "wrap",
]

SCHEMA_VERSION = 1

DOMAINS: tuple[str, ...] = (
    "OSM",
    "DEM",
    "MESH",
    "MASK",
    "IMG",
    "TEX",
    "DSF",
    "XP",
    "CFG",
    "NET",
    "SYS",
    "ZONE",
)

CODE_PATTERN = re.compile(r"^(" + "|".join(DOMAINS) + r")_[A-Z0-9]+(?:_[A-Z0-9]+)*$")


class Severity(enum.StrEnum):
    """How much the outcome of the node is affected."""

    BLOCKING = "blocking"
    DEGRADED = "degraded"
    INFO = "info"


class Action(enum.StrEnum):
    """What the scheduler does with the node that raised the error."""

    STOP = "stop"
    CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Registry entry: default message and remedy templates for one code."""

    code: str
    severity: Severity
    action: Action
    message: str
    remedy: str

    @property
    def domain(self) -> str:
        """Domain prefix of the code (``OSM`` for ``OSM_LAYER_UNAVAILABLE``)."""
        return self.code.split("_", 1)[0]


_B, _D, _I = Severity.BLOCKING, Severity.DEGRADED, Severity.INFO
_S, _C = Action.STOP, Action.CONTINUE


def _spec(code: str, severity: Severity, action: Action, message: str, remedy: str) -> ErrorSpec:
    return ErrorSpec(code, severity, action, message, remedy)


_SPECS: tuple[ErrorSpec, ...] = (
    # ---------------------------------------------------------------- OSM
    _spec(
        "OSM_MIRROR_UNREACHABLE",
        _I,
        _C,
        "Overpass mirror {mirror} did not answer ({reason}).",
        "Another mirror is tried automatically. Offline: continue with the land-polygons "
        "coastline.",
    ),
    _spec(
        "OSM_MIRROR_REJECTED",
        _I,
        _C,
        "Overpass mirror {mirror} rejected the query with HTTP {status}.",
        "Waiting {delay} s before retrying on another mirror; reduce parallel OSM requests if "
        "it persists.",
    ),
    _spec(
        "OSM_RESPONSE_TRUNCATED",
        _I,
        _C,
        "Overpass answer for layer {layer} was truncated (no closing tag).",
        "The query is retried; a truncated cached file is deleted and fetched again.",
    ),
    _spec(
        "OSM_RESPONSE_ERROR",
        _I,
        _C,
        "Overpass returned an error body for layer {layer} ({reason}).",
        "The bounding box is split into smaller queries and retried.",
    ),
    _spec(
        "OSM_LAYER_UNAVAILABLE",
        _B,
        _S,
        "OSM layer {layer} for tile {tile} could not be obtained from any mirror.",
        "Check the network and the Overpass mirrors, then retry. For the coastline only, "
        "OrthoStudio XP can continue with the land-polygons fallback (--coast-fallback).",
    ),
    _spec(
        "OSM_CACHE_UNREADABLE",
        _D,
        _C,
        "Cached OSM file {path} is unreadable.",
        "The corrupted cache file is deleted and the layer downloaded again.",
    ),
    _spec(
        "OSM_CACHE_WRITE_FAILED",
        _I,
        _C,
        "OSM cache file {path} could not be written ({reason}).",
        "Free disk space or fix permissions on the cache directory; the build continues.",
    ),
    _spec(
        "OSM_COAST_OPEN_END",
        _B,
        _S,
        "OSM coastline stops inside tile {tile} at {points}.",
        "Fix the coastline in OSM or JOSM at the given positions, or build with the "
        "land-polygons coastline.",
    ),
    _spec(
        "OSM_COAST_ORIENTATION",
        _B,
        _S,
        "OSM coastline of tile {tile} has a way with water on the wrong side.",
        "Reverse the faulty coastline way in OSM/JOSM or use the land-polygons coastline.",
    ),
    _spec(
        "OSM_COAST_TRIPLE_JUNCTION",
        _B,
        _S,
        "OSM coastline of tile {tile} has a triple junction near lat={lat} lon={lon}.",
        "Fix the junction in OSM/JOSM or use the land-polygons coastline.",
    ),
    _spec(
        "OSM_WAY_NOT_CLOSED",
        _I,
        _C,
        "OSM way {osm_id} used as a polygon is not closed; skipped.",
        "Nothing to do; the feature is listed in the decision report.",
    ),
    _spec(
        "OSM_WAY_INVALID",
        _I,
        _C,
        "OSM way {osm_id} is an invalid polygon; skipped.",
        "Nothing to do; correct the geometry in OSM if the feature matters.",
    ),
    _spec(
        "OSM_RELATION_INVALID",
        _I,
        _C,
        "OSM relation {osm_id} yields an invalid polygon; skipped.",
        "Nothing to do; the relation is listed in the decision report.",
    ),
    _spec(
        "OSM_WATER_MERGE_FAILED",
        _B,
        _S,
        "Inland water polygons of tile {tile} could not be merged ({reason}).",
        "Retry with clean_bad_geometries disabled, or provide a custom water file for the tile.",
    ),
    _spec(
        "OSM_LAKE_TREATED_AS_SEA",
        _I,
        _C,
        "Water body {name} ({area_km2} km2) exceeds max_area and is rendered like the sea.",
        "Raise max_area or add the lake to good_imagery_list to keep the orthophoto.",
    ),
    _spec(
        "OSM_AIRPORT_TAG_INVALID",
        _D,
        _C,
        "Aerodrome element near {point} has an unusable geometry; airport skipped.",
        "Check the aerodrome element in OSM near the given point.",
    ),
    _spec(
        "OSM_AIRPORT_BOUNDARY_INVALID",
        _D,
        _C,
        "Boundary of airport {airport} is an invalid polygon; runways only are used.",
        "Fix the aerodrome outline in OSM; runways are still flattened.",
    ),
    _spec(
        "OSM_AIRPORT_TOO_SMALL",
        _I,
        _C,
        "Airport {airport} is below the size threshold and is ignored.",
        "Nothing to do; listed in the decision report.",
    ),
    _spec(
        "OSM_AIRPORT_SMOOTHING_INVALID",
        _D,
        _C,
        "Airport {airport} carries smoothing_pix={value}, outside {range}; the tile setting "
        "apt_smoothing_pix is used instead.",
        "Fix the smoothing_pix tag of the aerodrome in OSM, or remove it.",
    ),
    _spec(
        "OSM_RUNWAY_REJECTED",
        _D,
        _C,
        "A runway of airport {airport} was rejected ({reason}) and is not flattened.",
        "Edit the airports layer in JOSM, or tag the runway way custom=yes to bypass the check.",
    ),
    _spec(
        "OSM_AIRPORT_SURFACE_INVALID",
        _I,
        _C,
        "A {surface} area of airport {airport} is not a valid polygon; skipped.",
        "Nothing to do; listed in the decision report.",
    ),
    _spec(
        "OSM_AIRPORT_INFO_UNAVAILABLE",
        _D,
        _C,
        "Airport list of tile {tile} is unavailable; airport cover and refinement are skipped.",
        "Rebuild the vector stage of the tile.",
    ),
    _spec(
        "OSM_PATCH_INVALID",
        _D,
        _C,
        "Patch file {path} is invalid ({reason}); the tile is built without it.",
        "Fix the patch file and rebuild the tile.",
    ),
    # ---------------------------------------------------------------- DEM
    _spec(
        "DEM_DOWNLOAD_FAILED",
        _B,
        _S,
        "Elevation data for cell {cell} could not be downloaded from {source} ({reason}).",
        "Check the source for this cell; place a .hgt or GeoTIFF file manually in the "
        "elevation directory, or choose another source.",
    ),
    _spec(
        "DEM_TILE_UNAVAILABLE",
        _B,
        _S,
        "No elevation data for tile cell {cell} from source {source} ({reason}); OrthoStudio XP "
        "does not build a flat tile.",
        "With the X-Plane relief (the default), install this region of the X-Plane 12 Global "
        "Scenery with the X-Plane installer. Otherwise choose another relief source, or give "
        "your own elevation file (custom_dem).",
    ),
    _spec(
        "DEM_OVERLAY_UNAVAILABLE",
        _D,
        _C,
        "No elevation data from {source} over cell {cell}: the relief laid under it is used "
        "alone there.",
        "Nothing to do: the source covers part of the country only (Canada's lidar), and the "
        "base relief answers for the rest.",
    ),
    _spec(
        "DEM_OVERLAY_COARSER",
        _D,
        _C,
        "Your own file for cell {cell} ({own}) has {own_m} m between two points, where {source} "
        "has {base_m} m: the finer of the two is used, so {source} answers over this square.",
        "Nothing to do. Put a file at least as fine as the relief you chose in the folder, or "
        "choose a coarser relief, if you want your own file to answer here.",
    ),
    _spec(
        "DEM_NEIGHBOUR_UNAVAILABLE",
        _D,
        _C,
        "Elevation data for neighbouring cell {cell} is unavailable; the tile border uses the "
        "tile's own data.",
        "Provide the neighbour DEM to remove a possible seam at the border.",
    ),
    _spec(
        "DEM_SOURCE_MANUAL_DOWNLOAD",
        _B,
        _S,
        "Elevation source {source} requires a manual download; {expected_name} is missing.",
        "Download the file manually into the elevation directory, or switch to "
        "Viewfinderpanoramas.",
    ),
    _spec(
        "DEM_FILE_UNREADABLE",
        _B,
        _S,
        "Elevation file {path} is unreadable ({reason}).",
        "Delete the file so it is downloaded again, or replace it with a valid one.",
    ),
    _spec(
        "DEM_RASTER_LIBRARY_MISSING",
        _B,
        _S,
        "Raster {path} needs the optional raster library, which is not installed.",
        "Convert the DEM to .hgt, or to an uncompressed GeoTIFF.",
    ),
    _spec(
        "DEM_NODATA_UNDECLARED",
        _I,
        _C,
        "Raster {path} declares no nodata value; -32768 is assumed.",
        "Nothing to do unless holes appear; declare nodata in the raster.",
    ),
    _spec(
        "DEM_EPSG_UNDECLARED",
        _I,
        _C,
        "Raster {path} declares no CRS; EPSG:4326 is assumed.",
        "Nothing to do if the raster is in geographic coordinates.",
    ),
    _spec(
        "DEM_EPSG_UNSUPPORTED",
        _B,
        _S,
        "Raster {path} is in EPSG:{epsg}; only EPSG:4326 is supported.",
        "Reproject the DEM to EPSG:4326 (for example gdalwarp -t_srs EPSG:4326).",
    ),
    _spec(
        "DEM_VOIDS_FILLED_WITH_ZERO",
        _D,
        _C,
        "{count} nodata pixels of cell {cell} were set to 0 m.",
        "Use a DEM without voids for this cell or set fill_nodata to nearest.",
    ),
    _spec(
        "DEM_CELL_ASSUMED_OCEAN",
        _I,
        _C,
        "Cell {cell} is marked as ocean in the world bitmap; elevation is 0 m.",
        "Provide a DEM file for the cell if it is not open sea.",
    ),
    _spec(
        "DEM_CACHE_STALE",
        _B,
        _S,
        "Cached elevation of tile {tile} does not match the current DEM settings.",
        "Rebuild the vector stage of the tile.",
    ),
    # ---------------------------------------------------------------- MESH
    _spec(
        "MESH_INPUT_MISSING",
        _B,
        _S,
        "Input {path} for the mesh stage of tile {tile} is missing.",
        "Run the build; OrthoStudio XP orders the stages itself.",
    ),
    _spec(
        "MESH_TILE_ASSUMED_SEA",
        _D,
        _C,
        "Tile {tile} has no coastline and a flat DEM; the whole tile is seeded as sea.",
        "Check the DEM and the coastline of this tile; force land with sea_seed = none.",
    ),
    _spec(
        "MESH_QUALITY_RELAXED",
        _D,
        _C,
        "Triangle4XP could not meet min_angle={min_angle} on tile {tile}; retried with 0.",
        "Check the OSM layers reported as invalid for this tile.",
    ),
    _spec(
        "MESH_TRIANGULATION_FAILED",
        _B,
        _S,
        "Triangle4XP failed on tile {tile} (exit code {returncode}).",
        "Report with the files exported by osxp debug export-pslg; check available memory.",
    ),
    _spec(
        "MESH_TRIANGLE_BUDGET_REACHED",
        _I,
        _C,
        "Triangle budget of {limit_tris} M reached on tile {tile}; the mesh is coarser than "
        "curvature_tol asks.",
        "Raise limit_tris or curvature_tol for this tile.",
    ),
    _spec(
        "MESH_WEIGHT_MAP_INCOMPLETE",
        _D,
        _C,
        "Coastline refinement map of tile {tile} is incomplete ({reason}).",
        "The coastline layer of the vector stage is reused; nothing to do.",
    ),
    # ---------------------------------------------------------------- MASK
    _spec(
        "MASK_NEIGHBOUR_MESH_MISSING",
        _D,
        _C,
        "Neighbour tile {neighbour} has no mesh; masks of tile {tile} may show a hard edge at "
        "that border.",
        "Build the neighbour first or use the neighbour's OSM water polygon "
        "(--neighbour-water osm).",
    ),
    _spec(
        "MASK_NEIGHBOUR_MESH_UNREADABLE",
        _D,
        _C,
        "Neighbour mesh {path} is unreadable; skipped for the masks of tile {tile}.",
        "Rebuild the neighbour mesh.",
    ),
    _spec(
        "MASK_STALE",
        _D,
        _C,
        "Masks of tile {tile} were built for another mesh or other parameters.",
        "Rebuild the masks of the tile.",
    ),
    _spec(
        "MASK_DISTANCE_MISSING",
        _D,
        _C,
        "Distance mask {mask} is missing; bathymetry of tile {tile} is flat there.",
        "Distance masks are enabled automatically with XP12 water; rebuild the masks.",
    ),
    _spec(
        "MASK_FILE_UNREADABLE",
        _B,
        _S,
        "Mask file {path} is unreadable ({reason}).",
        "Delete the file and rebuild the masks of this tile.",
    ),
    _spec(
        "MASK_CUSTOM_EXTENT_INVALID",
        _B,
        _S,
        "Custom mask extent {extent} is missing or unreadable.",
        "Check the extent name and its PNG in the Extents directory.",
    ),
    # ---------------------------------------------------------------- IMG
    _spec(
        "IMG_TILE_PLACEHOLDER",
        _I,
        _C,
        "Provider {provider} returned a placeholder for chunk {chunk}; parent used.",
        "Nothing to do; the area has no imagery at this zoom level.",
    ),
    _spec(
        "IMG_TILE_PARENT_FALLBACK",
        _I,
        _C,
        "Chunk {chunk} missing at ZL{zl}; replaced by its parent {levels} level(s) above.",
        "Nothing to do; listed in the decision report.",
    ),
    _spec(
        "IMG_TILE_MISSING",
        _D,
        _C,
        "Chunk {chunk} of texture {texture} could not be obtained from {provider}.",
        "The texture is marked incomplete; use Retry missing textures once the provider is back.",
    ),
    _spec(
        "IMG_TILE_CORRUPTED",
        _I,
        _C,
        "Provider {provider} sent an undecodable image for chunk {chunk}.",
        "Retried; if it persists the provider is degraded.",
    ),
    _spec(
        "IMG_BAD_CONTENT_TYPE",
        _D,
        _C,
        "Provider {provider} answered {content_type} instead of an image for chunk {chunk}.",
        "The provider needs a token or is retired; run osxp doctor --providers.",
    ),
    _spec(
        "IMG_CACHE_INCOMPLETE",
        _D,
        _C,
        "Cached texture {texture} contains {count} missing chunk(s).",
        "Retry missing textures downloads only the missing chunks.",
    ),
    _spec(
        "IMG_COLOR_FILTER_FAILED",
        _I,
        _C,
        "Colour filter {filter} failed on texture {texture}; the texture is kept unfiltered.",
        "Check the filter definition.",
    ),
    _spec(
        "IMG_COMBINED_NO_DATA",
        _D,
        _C,
        "Combined provider {provider} has no layer with data for {texture}.",
        "Add a fallback layer to the .comb file or change the provider for this zone.",
    ),
    _spec(
        "IMG_EXTENT_UNTESTABLE",
        _D,
        _C,
        "Coverage of extent {extent} could not be tested ({reason}); treated as no data.",
        "Check the extent files in the Extents directory.",
    ),
    _spec(
        "IMG_EXTENT_DATA_MISSING",
        _B,
        _S,
        "Extent {extent} needs OSM data that is absent or empty.",
        "Download the extent data as documented, or remove the layer.",
    ),
    _spec(
        "IMG_LOCAL_TILE_MISSING",
        _D,
        _C,
        "Local imagery file {path} of provider {provider} is absent.",
        "Check the local imagery directory of the provider.",
    ),
    _spec(
        "IMG_CUSTOM_URL_MODULE_INVALID",
        _D,
        _C,
        "Token plugin {path} could not be loaded ({reason}); its providers are unavailable.",
        "Fix or remove the token plugin.",
    ),
    # ---------------------------------------------------------------- TEX
    _spec(
        "TEX_MISSING",
        _B,
        _S,
        "{count} texture(s) referenced by the DSF of tile {tile} are missing.",
        "Use Retry missing textures; the DSF is installed only once every texture exists.",
    ),
    _spec(
        "TEX_ENCODE_FAILED",
        _B,
        _S,
        "DDS encoding of {texture} failed with {encoder} ({reason}).",
        "The next encoder of the fallback chain is tried; if all fail run osxp doctor --encoder.",
    ),
    _spec(
        "TEX_ENCODER_UNAVAILABLE",
        _B,
        _S,
        "No DDS encoder is available ({reason}).",
        "Run osxp doctor --encoder and install the fallback encoder.",
    ),
    _spec(
        "TEX_GEOTIFF_TOOL_MISSING",
        _D,
        _C,
        "GeoTIFF export needs GDAL, which is not installed.",
        "Install GDAL for GeoTIFF export; DDS output is unaffected.",
    ),
    # ---------------------------------------------------------------- DSF
    _spec(
        "DSF_GLOBAL_SCENERY_MISSING",
        _B,
        _S,
        "X-Plane 12's own scenery for {tile} was not found (its Global Scenery DSF).",
        "Install the region with {tile} with X-Plane's installer (Add or Remove Scenery); if "
        "it is installed, set the X-Plane directory.",
    ),
    _spec(
        "DSF_GLOBAL_SCENERY_COPY_FAILED",
        _B,
        _S,
        "Global Scenery DSF for {tile} could not be copied ({reason}).",
        "Free disk space or fix permissions on the OrthoStudio XP store.",
    ),
    _spec(
        "DSF_SOURCE_DECOMPRESS_FAILED",
        _B,
        _S,
        "Global Scenery DSF for {tile} could not be decompressed ({reason}).",
        "Reinstall Global Scenery for this tile in X-Plane.",
    ),
    _spec(
        "DSF_SOURCE_CORRUPTED",
        _B,
        _S,
        "Global Scenery DSF for {tile} is not a valid DSF file.",
        "Reinstall Global Scenery for this tile in X-Plane.",
    ),
    _spec(
        "DSF_ACTIVATION_FAILED",
        _B,
        _S,
        "Final DSF of tile {tile} could not be moved into place ({reason}).",
        "Close X-Plane or unlock the file, then run Install again.",
    ),
    _spec(
        "DSF_POOL_OVERFLOW",
        _B,
        _S,
        "A vertex pool of tile {tile} exceeds 65535 entries.",
        "Internal error; report the tile and parameters with osxp debug report.",
    ),
    _spec(
        "DSF_MESH_OUTSIDE_TILE",
        _B,
        _S,
        "Mesh cell ({til_x}, {til_y}) at ZL{mesh_zl} of tile {tile} is outside the tile.",
        "The mesh must be clamped to the 1x1 degree tile; rebuild the mesh of this tile.",
    ),
    _spec(
        "DSF_OVERLAY_SOURCE_MISSING",
        _B,
        _S,
        "Overlay source DSF for {tile} was not found at {path}.",
        "Set the X-Plane directory or the overlay source scenery.",
    ),
    _spec(
        "DSF_OVERLAY_TOOL_FAILED",
        _B,
        _S,
        "DSFTool failed on {path} (exit code {returncode}).",
        "Check the DSFTool binary with osxp doctor; report the source DSF if it persists.",
    ),
    # ---------------------------------------------------------------- XP
    _spec(
        "XP_RUNNING",
        _B,
        _S,
        "X-Plane is running; the scenery cannot be installed now.",
        "Quit X-Plane, then run Install again.",
    ),
    _spec(
        "XP_DIR_NOT_FOUND",
        _B,
        _S,
        "X-Plane directory {path} was not found.",
        "Choose the X-Plane 12 folder in Settings (osxp doctor detects it).",
    ),
    _spec(
        "XP_GLOBAL_SCENERY_NOT_FOUND",
        _B,
        _S,
        "Global Scenery is not installed in {path}.",
        "Install Global Scenery from the X-Plane installer.",
    ),
    _spec(
        "XP_SCENERY_PACKS_UNWRITABLE",
        _B,
        _S,
        "scenery_packs.ini at {path} cannot be written ({reason}).",
        "Fix permissions on the file; the pack is built but not activated.",
    ),
    _spec(
        "XP_LINK_FAILED",
        _B,
        _S,
        "Link {link} to the scenery pack could not be created ({reason}).",
        "Enable Developer Mode on Windows or copy the pack into Custom Scenery.",
    ),
    _spec(
        "XP_PACK_CONFLICT",
        _I,
        _C,
        "Tile {tile} is also provided by pack {pack}.",
        "The order in scenery_packs.ini is adjusted; remove the duplicate pack if unwanted.",
    ),
    # ---------------------------------------------------------------- CFG
    _spec(
        "CFG_GLOBAL_FILE_MISSING",
        _I,
        _C,
        "No global configuration found at {path}; defaults are used.",
        "A default configuration is written.",
    ),
    _spec(
        "CFG_LINE_INVALID",
        _B,
        _S,
        "Configuration {path} line {line}: {reason}.",
        "Fix the line; OrthoStudio XP validates the whole configuration before building.",
    ),
    _spec(
        "CFG_TILE_FILE_MISSING",
        _I,
        _C,
        "No configuration found for tile {tile}; the global one is used.",
        "A tile configuration is created from the global one.",
    ),
    _spec(
        "CFG_TILE_WRITE_FAILED",
        _D,
        _C,
        "Configuration {path} could not be written ({reason}).",
        "Fix permissions on the file.",
    ),
    _spec(
        "CFG_ZONE_LIST_INVALID",
        _B,
        _S,
        "Zone {index} of tile {tile} is invalid: {reason}.",
        "Fix the zone definition; zones are validated and drawn before the build.",
    ),
    _spec(
        "CFG_PROVIDER_UNKNOWN",
        _B,
        _S,
        "Provider {provider} is unknown.",
        "Choose a provider from the list; run osxp doctor --providers to see which are alive.",
    ),
    _spec(
        "CFG_PROVIDER_DEFINITION_INVALID",
        _B,
        _S,
        "Provider definition {path} is invalid (field {field}: {reason}).",
        "Fix the provider file or remove the provider.",
    ),
    _spec(
        "CFG_PROVIDER_OUT_OF_COVERAGE",
        _B,
        _S,
        "Imagery source {provider} covers {extent} only, not tile(s) {tiles}.",
        "Choose a source that covers these tiles: Bing Maps and Esri cover the whole world.",
    ),
    _spec(
        "CFG_DATA_DIR_INVALID",
        _B,
        _S,
        "The data folder {path} cannot be used: {reason}.",
        "Choose a folder on a disk formatted APFS or Mac OS Extended (Mac), NTFS (Windows) or "
        "ext4 (Linux).",
    ),
    _spec(
        "CFG_DATA_DIR_MISSING",
        _B,
        _S,
        "The data folder {path} was not found.",
        "Plug in the disk it is on, or choose another folder in Settings.",
    ),
    _spec(
        "CFG_LATLON_INVALID",
        _B,
        _S,
        "Tile coordinates {lat},{lon} are outside the supported range.",
        "Enter integer tile coordinates with latitude in [-85, 84] and longitude in [-180, 179].",
    ),
    _spec(
        "CFG_VALUE_INVALID",
        _B,
        _S,
        "Parameter {name} has an invalid value {value}.",
        "Set {name} to a value of type {type} in range {range}.",
    ),
    _spec(
        "CFG_DEM_SOURCE_INVALID",
        _B,
        _S,
        "Elevation source {source} is unknown or unreadable.",
        "Choose a source from the list or a readable raster path for custom_dem.",
    ),
    _spec(
        "CFG_BUILD_DIR_UNWRITABLE",
        _B,
        _S,
        "Output directory {path} cannot be written ({reason}).",
        "Fix permissions on the directory or choose another output directory.",
    ),
    # ---------------------------------------------------------------- NET
    _spec(
        "NET_OFFLINE",
        _B,
        _S,
        "No network: none of the required hosts can be reached.",
        "Check the network connection; cached data is still usable with --offline.",
    ),
    _spec(
        "NET_CONNECTION_FAILED",
        _I,
        _C,
        "Connection to {host} failed ({reason}).",
        "Retried with backoff.",
    ),
    _spec(
        "NET_TIMEOUT",
        _I,
        _C,
        "Request to {host} timed out after {timeout} s.",
        "Retried; a slow host is hedged with a second request.",
    ),
    _spec(
        "NET_RATE_LIMITED",
        _D,
        _C,
        "Host {host} is rate limiting requests (HTTP {status}).",
        "Requests to the host are slowed down; wait or use another provider.",
    ),
    _spec(
        "NET_FORBIDDEN",
        _B,
        _S,
        "Host {host} refused the request (HTTP 403).",
        "Downloads from the host are paused; wait before retrying and check the provider's "
        "terms of use.",
    ),
    _spec(
        "NET_SERVER_ERROR",
        _I,
        _C,
        "Host {host} answered HTTP {status}.",
        "Retried with backoff.",
    ),
    _spec(
        "NET_UNEXPECTED_STATUS",
        _D,
        _C,
        "Provider {provider} answered HTTP {status} for {url}.",
        "Run osxp doctor --providers to check the provider.",
    ),
    # ---------------------------------------------------------------- SYS
    _spec(
        "SYS_DISK_FULL",
        _B,
        _S,
        "Not enough disk space on {volume}: {needed} needed, {free} free.",
        "Free disk space or lower the cache quota.",
    ),
    _spec(
        "SYS_WRITE_FAILED",
        _B,
        _S,
        "{path} could not be written ({reason}).",
        "Fix permissions on the path.",
    ),
    _spec(
        "SYS_TOOL_MISSING",
        _B,
        _S,
        "Required tool {tool} is missing or not executable.",
        "Run osxp doctor; reinstall the tool or the OrthoStudio XP bundle.",
    ),
    _spec(
        "SYS_ROSETTA_MISSING",
        _B,
        _S,
        "{tool} is an x86_64 binary and Rosetta 2 is not installed.",
        "Install Rosetta 2 only if you opt for this fallback tool; OrthoStudio XP does not need "
        "it.",
    ),
    _spec(
        "SYS_RESOURCE_MISSING",
        _B,
        _S,
        "Bundled resource {path} is missing.",
        "Reinstall OrthoStudio XP; the bundle is incomplete.",
    ),
    _spec(
        "SYS_INTERNAL_ERROR",
        _B,
        _S,
        "Internal error: {type}: {detail}",
        "Report the error with osxp debug report (tile, parameters and traceback included).",
    ),
    _spec(
        "SYS_CANCELLED",
        _I,
        _S,
        "Build cancelled by the user.",
        "Nothing to do; the build resumes from the last completed node.",
    ),
    _spec(
        "SYS_UPSTREAM_FAILED",
        _I,
        _S,
        "Node {node} skipped: {root} failed with {code}.",
        "Fix the upstream failure and relaunch; finished nodes are reused.",
    ),
    _spec(
        "SYS_OUT_OF_MEMORY",
        _B,
        _S,
        "Stage {stage} of tile {tile} ran out of memory.",
        "Close other applications or lower the parallelism (--jobs).",
    ),
    _spec(
        "SYS_WORKING_DIR_INVALID",
        _B,
        _S,
        "Directory {path} is not an Ortho4XP installation.",
        "Point import-ortho4xp to the root of the Ortho4XP directory.",
    ),
    _spec(
        "SYS_PACK_NOT_OSXP",
        _B,
        _S,
        "{path} was not built by OrthoStudio XP ({reason}), so OrthoStudio XP does not delete it.",
        "Nothing was deleted. Uninstall takes the tile out of X-Plane without deleting anything; "
        "to delete the folder itself, delete it by hand.",
    ),
    # ---------------------------------------------------------------- ZONE
    _spec(
        "ZONE_INVALID",
        _B,
        _S,
        "Zone {zone} is invalid: {reason}.",
        "Fix or delete the zone on the map (or in the zones file); nothing that depends on it is "
        "saved or built until it is valid.",
    ),
    _spec(
        "ZONE_TOO_MANY",
        _B,
        _S,
        "Too many zones for {scope}: {count}, at most {limit}.",
        "Delete or merge zones: a tile takes at most 254 zone parts (a zone cut by the tile "
        "edges counts once per part) and the document at most 500 zones.",
    ),
    _spec(
        "ZONE_CONFLICT",
        _B,
        _S,
        "The zones file {path} was saved by another window or program since these zones were "
        "loaded.",
        "Reload the zones to see the saved ones, then make the change again; nothing was written.",
    ),
)


def _build_registry(specs: Iterable[ErrorSpec]) -> Mapping[str, ErrorSpec]:
    registry: dict[str, ErrorSpec] = {}
    for spec in specs:
        if not CODE_PATTERN.match(spec.code):
            raise ValueError(f"malformed error code: {spec.code!r}")
        if spec.code in registry:
            raise ValueError(f"duplicate error code: {spec.code!r}")
        if not spec.message.strip() or not spec.remedy.strip():
            raise ValueError(f"error code {spec.code!r} needs a message and a remedy")
        registry[spec.code] = spec
    return MappingProxyType(registry)


REGISTRY: Mapping[str, ErrorSpec] = _build_registry(_SPECS)


def codes() -> tuple[str, ...]:
    """All registered codes, sorted."""
    return tuple(sorted(REGISTRY))


def by_domain(domain: str) -> tuple[ErrorSpec, ...]:
    """Registered specs of one domain (``"OSM"``), in registry order."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown error domain: {domain!r}")
    return tuple(spec for spec in REGISTRY.values() if spec.domain == domain)


def spec_for(code: str) -> ErrorSpec:
    """Spec of a registered code; raises ``ValueError`` for an unknown one."""
    try:
        return REGISTRY[code]
    except KeyError:
        raise ValueError(f"unknown error code: {code!r}") from None


class _SafeContext(dict[str, Any]):
    """Formatting mapping that leaves unknown placeholders in place."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


_FORMATTER = string.Formatter()


def _format(template: str, context: Mapping[str, Any]) -> str:
    return _FORMATTER.vformat(template, (), _SafeContext(context))


def _sanitize(value: Any) -> Any:
    """Turn a context value into plain JSON-compatible data."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(_sanitize(v) for v in value)
    if isinstance(value, list | tuple):
        return [_sanitize(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


class OsxpError(Exception):
    """An error with a stable code, a remedy and a machine-readable context.

    ``message`` and ``remedy`` default to the registry templates rendered with
    ``context``; ``severity`` and ``action`` default to the registry values and may
    be overridden when the same situation is more or less serious in context (a
    missing neighbour DEM versus the tile's own DEM, for example).
    """

    __slots__ = ("action", "code", "context", "message", "remedy", "severity")

    def __init__(
        self,
        code: str,
        *,
        context: Mapping[str, Any] | None = None,
        message: str | None = None,
        remedy: str | None = None,
        severity: Severity | None = None,
        action: Action | None = None,
    ) -> None:
        spec = spec_for(code)
        self.code: str = spec.code
        self.context: dict[str, Any] = {str(k): _sanitize(v) for k, v in (context or {}).items()}
        self.message: str = message if message is not None else _format(spec.message, self.context)
        self.remedy: str = remedy if remedy is not None else _format(spec.remedy, self.context)
        self.severity: Severity = Severity(severity) if severity is not None else spec.severity
        self.action: Action = Action(action) if action is not None else spec.action
        super().__init__(self.message)

    @property
    def domain(self) -> str:
        """Domain prefix of the code."""
        return self.code.split("_", 1)[0]

    @property
    def spec(self) -> ErrorSpec:
        """Registry entry of this error."""
        return REGISTRY[self.code]

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"OsxpError({self.code!r}, severity={self.severity.value!r}, context={self.context!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Stable, JSON-compatible representation (schema version ``SCHEMA_VERSION``)."""
        cause = self.__cause__
        return {
            "schema": SCHEMA_VERSION,
            "code": self.code,
            "domain": self.domain,
            "severity": self.severity.value,
            "action": self.action.value,
            "message": self.message,
            "remedy": self.remedy,
            "context": dict(self.context),
            "cause": None if cause is None else f"{type(cause).__name__}: {cause}",
        }

    def to_json(self) -> str:
        """Compact JSON, keys sorted, UTF-8 kept as is."""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def wrap(exc: BaseException) -> OsxpError:
    """Return ``exc`` if it is an ``OsxpError``, else wrap it as ``SYS_INTERNAL_ERROR``."""
    if isinstance(exc, OsxpError):
        return exc
    wrapped = OsxpError(
        "SYS_INTERNAL_ERROR",
        context={"type": type(exc).__name__, "detail": str(exc)},
    )
    wrapped.__cause__ = exc
    return wrapped


def render_json(exc: BaseException) -> str:
    """JSON rendering of any exception for the API and the event stream."""
    return wrap(exc).to_json()
