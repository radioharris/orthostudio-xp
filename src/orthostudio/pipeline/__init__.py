"""Pipelines composing the OrthoStudio XP modules into user-visible builds.

``build`` declares and runs the graph of a batch of tiles (``docs/specs/pipeline-build.md``),
``textures`` builds the textures of one tile (``docs/specs/pipeline-textures.md``) and ``pack``
writes, installs, uninstalls and deletes the packs (``docs/specs/install.md``).
"""

from orthostudio.pipeline.home import default_chunks_root, default_store_root, osxp_home
from orthostudio.pipeline.textures import (
    TextureJob,
    TextureOutcome,
    TexturesReport,
    TexturesSpec,
    build_textures,
)

__all__ = [
    "TextureJob",
    "TextureOutcome",
    "TexturesReport",
    "TexturesSpec",
    "build_textures",
    "default_chunks_root",
    "default_store_root",
    "osxp_home",
]
