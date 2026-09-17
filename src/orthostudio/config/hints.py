"""The author's hints of Ortho4XP, verbatim (``O4_Config_Utils.py:16-352``, ``cfg_vars``).

Generated from Ortho4XP's sources; the UI shows them as tooltips
(``docs/specs/settings.md`` section 1). Keys are the Ortho4XP variable names. Whitespace is
normalised (the Ortho4XP strings carry line-continuation runs of spaces); wording is untouched.
"""

from __future__ import annotations

__all__ = ["ORTHO4XP_HINTS"]

ORTHO4XP_HINTS: dict[str, str] = {
    "apt_smoothing_pix": (
        "How much gaussian blur is applied to the elevation raster for the look up of altitude "
        "over airports. Unit is the evelation raster pixel size."
    ),
    "road_level": (
        "Allows to level the mesh along roads and railways. Zero means nothing such is included; "
        '"1" looks for banking ways among motorways, primary and secondary roads and railway '
        'tracks; "2" adds tertiary roads; "3" brings residential and unclassified roads; "4" '
        "takes service roads, and 5 finishes with tracks. Purge the small_roads.osm cached data "
        "if you change your mind in between the levels 2-5."
    ),
    "road_banking_limit": (
        "How much sloped does a roads need to be to be in order to be included in the mesh "
        "levelling process. The value is in meters, measuring the height difference between a "
        "point in the center of a road node and its closest point on the side of the road."
    ),
    "lane_width": (
        "With (in meters) to be used for buffering that part of the road network that requires "
        "levelling."
    ),
    "max_levelled_segs": (
        "This limits the total number of roads segments included for mesh levelling, in order to "
        "keep triangle count under control in case of abundant OSM data."
    ),
    "water_simplification": (
        "In case the OSM data for water areas would become too large, this parameter (in meter) "
        "can be used for node simplification."
    ),
    "min_area": (
        "Minimum area (in km^2) a water patch needs to be in order to be included in the mesh as "
        "such. Contiguous water patches are merged before area computation."
    ),
    "max_area": (
        "Any water patch larger than this quantity (in km^2) will be masked like the sea."
    ),
    "mesh_zl": (
        "The mesh will be preprocessed to accept later any combination of imageries up to and "
        "including a zoomlevel equal to mesh_zl. Lower value could save a few tens of thousands "
        "triangles, but put a limitation on the maximum allowed imagery zoomlevel."
    ),
    "curvature_tol": (
        "This parameter is intrinsically linked the mesh final density. Mesh refinement is mostly "
        "based on curvature computations on the elevation data (the exact decision rule can be "
        "found in _ triunsuitable() _ in Utils/Triangle4XP.c). A higher curvature tolerance "
        "yields fewer triangles."
    ),
    "apt_curv_tol": ("If smaller, it supersedes curvature_tol over airports neighbourhoods."),
    "apt_curv_ext": ("Extent (in km) around the airports where apt_curv_tol applies."),
    "coast_curv_tol": ("If smaller, it supersedes curvature_tol along the coastline."),
    "coast_curv_ext": ("Extent (in km) around the coastline where coast_curv_tol applies."),
    "limit_tris": (
        "If non zero, approx upper bound _in millions_ on the number of final triangles in the "
        "mesh. Note: When 0 we impose a hard limit of 5M, to keep X-Plane comfortable. For high "
        "resolution DEMS you _should_ use it."
    ),
    "min_angle": (
        "The mesh algorithm will try to not have mesh triangles with (smallest for water / second "
        "smallest for regular land) angle less than the value (in deg) of min_angle."
    ),
    "sea_smoothing_mode": (
        "Zero means that all nodes of sea triangles are set to zero elevation. With mean, some "
        "kind of smoothing occurs (triangles are levelled one at a time to their mean elevation), "
        "None (a value mostly appropriate for DEM resolution of 10m and less), positive altitudes "
        "of sea nodes are kept intact, only negative ones are brought back to zero, this avoids "
        "to create unrealistic vertical cliffs if the coastline vector data was lower res."
    ),
    "water_smoothing": (
        "Number of smoothing passes over all inland water triangles (sequentially set to their "
        "mean elevation)."
    ),
    "mask_zl": (
        "The zoomlevel at which the (sea) water masks are built. Masks are used for alpha "
        "channel, and this channel usually requires less resolution than the RGB ones, the reason "
        "for this (VRAM saving) parameter. If the coastline and elevation data are very detailed, "
        "it might be interesting to lift this parameter up so that the masks can reproduce this "
        "complexity."
    ),
    "masks_width": (
        "Maximum extent of the masks perpendicularly to the coastline (rough definition). NOTE: "
        "The value is now in meters, it used to be in ZL14 pixel size in earlier verions, the "
        "scale is roughly one to ten between both."
    ),
    "masking_mode": (
        "A selection of three tentative masking algorithms (still looking for the Holy Grail...). "
        "The first two (sand and rocks) requires masks_width to be a single value; the third one "
        '(3steps) requires a list of the form [a,b,c] for masks width: "a" is the length in '
        "meters of a first transition from plain imagery at the shoreline towards ratio_water "
        'transparency, "b" is the second extent zone where transparency level is kept constant '
        'equal to ratio_water, and "c" is the last extent where the masks eventually fade to '
        "nothing. The transition with rocks is more abrupt than with sand."
    ),
    "use_masks_for_inland": (
        "Will use masks for the inland water (lakes, rivers, etc) too, instead of the default "
        "constant transparency level determined by ratio_water. This is VRAM expensive and "
        "presumably not really worth the price."
    ),
    "imprint_masks_to_dds": (
        "Will apply masking directly to dds textures (at the Build Imagery/DSF step) rather than "
        "using external png files. This doubles the file size of masked textures (dxt5 vs dxt1) "
        "but reduce the overall VRAM footprint (a matter of choice!)"
    ),
    "distance_masks_too": (
        "This will additionally build distance to coastline masks that are used in Step 3 in "
        "order to improve the bathymetric profile (otherwise too low res) and avoid steep walls "
        "close to piers or rocks. Masks_zl should not be too low to grab these details."
    ),
    "masks_use_DEM_too": (
        "If you have acces to high resolutions DEMs (really shines with 5m or lower), you can use "
        "the elevation in addition to the vector data in order to draw masks with higher "
        "precision. If the DEM is not high res, this option will yield unpleasant pixellisation."
    ),
    "masks_custom_extent": (
        "Yet another tentative to draw masks with maximizing the use of the good imagery part. "
        'Requires to draw (JOSM) the "good imagery" threshold first, but it could be one order of '
        "magnitude faster to do compared to hand tweaking the masks and the imageries one by one."
    ),
    "cover_airports_with_highres": (
        "When set, textures above airports will be upgraded to a higher zoomlevel, the imagery "
        "being the same as the one they would otherwise receive. Can be limited to airports with "
        'an ICAO code for tiles with so many airports. Exceptional: use "Existing" to (try to) '
        "derive custom zl zones from the textures directory of an existing tile."
    ),
    "cover_extent": (
        "The extent (in km) past the airport boundary taken into account for higher ZL. Note that "
        "for VRAM efficiency higher ZL textures are fully used on their whole extent as soon as "
        "part of them are needed."
    ),
    "cover_zl": (
        "The zoomlevel with which to cover the airports zone when high_zl_airports is set. Note "
        "that if the cover_zl is lower than the zoomlevel which would otherwise be applied on a "
        "specific zone, the latter is used."
    ),
    "water_tech": (
        "Water tech type. XP12 uses a new (partly in construction) rendering tech, XP11 + bathy "
        "uses a more traditionnal blend. Both allows for 3D water."
    ),
    "ratio_bathy": ("Bathymetry multiplier for near shore vertices. In the range [0,1]."),
    "ratio_water": (
        'Inland water rendering is made of two layers : one bottom layer of "X-Plane water" and '
        "one overlay layer of orthophoto with constant level of transparency applied. The "
        "parameter ratio_water (values between 0 and 1) determines how much transparency is "
        "applied to the orthophoto. At zero, the orthophoto is fully opaque and X-Plane water "
        "cannot be seen ; at 1 the orthophoto is fully transparent and only the X-Plane water is "
        "seen."
    ),
    "overlay_lod": (
        "Distance until which overlay imageries (that is orthophotos over water) are drawn. Lower "
        "distances have a positive impact on frame rate and VRAM usage, and IFR flyers will "
        "probably need a higher value than VFR ones."
    ),
    "sea_texture_blur": (
        'For layers of type "mask" in combined providers imageries, determines the extent (in '
        "meters) of the blur radius applied. This allows to smoothen some sea imageries where the "
        "wave or reflection pattern was too much present."
    ),
    "normal_map_strength": (
        "Orthophotos by essence already contain the part of the shading burned in (here by "
        "shading we mean the amount of reflected light in the camera direction as a function of "
        "the terrain slope, not the shadows). This option allows to tweak the normal coordinates "
        'of the mesh in the DSF to avoid "overshading", but it has side effects on the way '
        "X-Plane computes scenery shadows. Used to be 0.3 by default in earlier versions, the "
        "default is now 1 which means exact normals.},"
    ),
    "terrain_casts_shadows": (
        "If unset, the terrain itself will not cast (but still receive!) shadows. This option is "
        "only meaningful if scenery shadows are opted for in the X-Plane graphics settings."
    ),
    "use_decal_on_terrain": (
        "Terrain files will contain the decal maquify_2_green_key.dcl, which X-Plane 12 ships. "
        "The effect is noticeable at very low altitude and helps to overcome the "
        "orthophoto blur at such levels. Can be slightly distracting at higher altitude."
    ),
    "custom_dem": (
        "Path to an elevation data file to be used instead of the default Viewfinderpanoramas.org "
        "ones (J. de Ferranti). The raster must be in geopgraphical coordinates (EPSG:4326) but "
        "the extent need not match the tile boundary (requires Gdal). Regions of the tile that "
        "are not covered by the raster are mapped to zero altitude (can be useful for high "
        "resolution data over islands in particular)."
    ),
    "fill_nodata": (
        "When set, the no_data values in the raster will be filled by a nearest neighbour "
        "algorithm. If unset, they are turned into zero (can be useful for rasters with no_data "
        "over the whole oceanic part or partial LIDAR data)."
    ),
    "ovl_exclude_pol": (
        "Indices of polygon types which one would like to left aside in the extraction of "
        "overlays. The list of these indices in front of their name can be obtained by running "
        'the "extract overlay" process with verbosity = 2 (skip facades that can be numerous) or '
        "3. Index 0 corresponds to beaches in Global and HD sceneries. Strings can be used in "
        "places of indices, in that case any polygon_def that contains that string is excluded, "
        'and the string can begin with a ! to invert the matching. As an exmaple, ["!.for"] would '
        "exclude everything but forests."
    ),
    "ovl_exclude_net": (
        "Indices of road types which one would like to left aside in the extraction of overlays. "
        "The list of these indices is can be in the roads.net file within X-Plane Resources, but "
        "some sceneries use their own corresponding net definition file. Powerlines have index "
        "22001 in XP11 roads.net default file."
    ),
    "custom_scenery_dir": (
        'Your X-Plane Custom Scenery. Used only for "1-click" creation (or deletion) of symbolic '
        "links from Ortho4XP tiles to there."
    ),
}
