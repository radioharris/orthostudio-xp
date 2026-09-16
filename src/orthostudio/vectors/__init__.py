"""Vector layers to a planar straight-line graph (PSLG) for Triangle4XP.

``noding`` is the kernel (P0); the wave-1 layer builders around it are ``osmdata`` (the OSM
store), ``coast``, ``water``, ``roads`` and ``patches``, all leaning on ``geom`` for the
small ``O4_Vector_Utils`` helpers; ``layers`` wires them, ``grid`` and ``seeds`` add what the
assembly owns, ``assemble`` orders and writes, and ``rule`` declares ``orthostudio.vectors@1``.
Specs: ``docs/specs/vectors-*.md``.
"""

from orthostudio.vectors.assemble import (
    AssembledVectors,
    AssemblyParams,
    VectorLayer,
    VectorLayers,
    assemble_vectors,
    to_vector_layers,
)
from orthostudio.vectors.geom import cut_to_tile, ensure_multipolygon, polygons_of
from orthostudio.vectors.noding import MARKERS, Layer, NodedGraph, check_planar, node_layers
from orthostudio.vectors.osmdata import OsmData
from orthostudio.vectors.tags import LAYER_TAGS, LayerTags

__all__ = [
    "LAYER_TAGS",
    "MARKERS",
    "AssembledVectors",
    "AssemblyParams",
    "Layer",
    "LayerTags",
    "NodedGraph",
    "OsmData",
    "VectorLayer",
    "VectorLayers",
    "assemble_vectors",
    "check_planar",
    "cut_to_tile",
    "ensure_multipolygon",
    "node_layers",
    "polygons_of",
    "to_vector_layers",
]
