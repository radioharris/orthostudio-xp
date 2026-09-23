"""Imagery providers: typed registry, tile URL grammar and placeholder recognition.

Origin: Ortho4XP ``src/O4_Imagery_Utils.py:1160-1215`` (URL grammar, kept),
``:1019-1032`` (placeholder by Content-Length, extended per ``docs/specs/net-download.md``
R5) and ``Providers/**/*.lay`` (definitions, converted to ``registry.toml``).
Specification: ``docs/specs/imagery-providers.md``.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections.abc import Mapping
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal

import blake3
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from orthostudio.fsutil import atomic_write_text
from orthostudio.home import osxp_home
from orthostudio.imagery.grid import quadkey

__all__ = [
    "PLACEHOLDERS",
    "REGISTRY_SCHEMA",
    "USER_SOURCES_FILE",
    "PlaceholderRule",
    "Provider",
    "is_placeholder",
    "load_registry",
    "new_source_code",
    "read_user_sources",
    "save_user_sources",
    "tile_url",
    "user_sources_path",
]

log = logging.getLogger("orthostudio.imagery.providers")

REGISTRY_SCHEMA = 1
PLACEHOLDERS = ("{zoom}", "{zoom:02d}", "{x}", "{y}", "{-y}", "{|y|}", "{quadkey}", "{switch:")
"""Substitutions of the URL grammar (``docs/specs/imagery-providers.md`` section 2)."""

USER_SOURCES_FILE = "sources.toml"
"""The imagery sources a user added (``$OSXP_HOME/sources.toml``, the registry's schema).
OrthoStudio XP does not ship them, and their terms of use apply to that user (user request,
2026-09-14)."""
USER_SOURCE_IN_FLIGHT = 16
"""Requests in flight for a source a user added: an unknown host is asked politely."""

_CODE_RE = re.compile(r"^[A-Za-z0-9_@-]{1,32}$")
_LEFTOVER_RE = re.compile(r"\{[^{}]*\}")
_SWITCH_RE = re.compile(r"\{switch:([^{}]*)\}")


class PlaceholderRule(BaseModel):
    """How a provider says "no imagery here" with an HTTP 200 (any signal may be absent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    header_name: str | None = None
    header_value: str | None = None
    size: int | None = Field(default=None, ge=1)
    blake3: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Provider(BaseModel):
    """One web-mercator tile provider of the registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=_CODE_RE.pattern)
    grid_type: Literal["webmercator"] = "webmercator"
    url_template: str = Field(min_length=1)
    max_zl: int = Field(ge=1, le=24)
    tile_size: int = 256
    headers: dict[str, str] = Field(default_factory=dict)
    max_in_flight: int = Field(default=64, ge=1, le=256)
    server_req_per_s: float | None = Field(default=None, gt=0)
    """The requests per second the server itself gave at ``max_in_flight``, where it and not the
    line was the limit (``docs/benchmarks/network.md`` 7); ``None`` for a server that kept up with
    the line (Bing, Esri). The estimates never count downloads faster than this."""
    placeholder: PlaceholderRule | None = None
    extent: str | None = None
    """The country or region the imagery covers (English name), ``None`` for the whole world."""
    extent_bounds: tuple[float, float, float, float] | None = None
    attribution: str = ""
    terms_url: str = ""
    licence: str = ""
    """The licence the imagery is given under, in the few words a pilot needs (``CC BY-NC-SA 4.0,
    non-commercial use only``). Empty when the terms are the provider's own and have no short
    name. It is shown beside the credit in the Plan and written into the pack, so that a tile
    passed to somebody else carries what it is bound by (found in review, 2026-09-23)."""
    name: str = ""
    """Short name for the page's lists (``Bing Maps``); ``attribution`` stays the credit line."""
    same_as: str | None = None
    """Another code of the very same imagery (``NL`` is ``PDOK``): still accepted, listed once."""
    custom: bool = False
    """A source the user added (``sources.toml``), not one OrthoStudio XP ships."""

    @field_validator("url_template")
    @classmethod
    def _template_is_consumable(cls, value: str) -> str:
        probe = _substitute(value, 1, 2, 3, switch=0)
        leftover = _LEFTOVER_RE.findall(probe)
        if leftover:
            raise ValueError(f"placeholders not in the grammar: {' '.join(sorted(set(leftover)))}")
        return value

    @field_validator("extent_bounds")
    @classmethod
    def _bounds_ordered(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None:
            lon0, lat0, lon1, lat1 = value
            if not (-180 <= lon0 < lon1 <= 180 and -90 <= lat0 < lat1 <= 90):
                raise ValueError("extent_bounds must be (lon_min, lat_min, lon_max, lat_max)")
        return value

    def covers(self, lat: int, lon: int) -> bool:
        """Whether the 1-degree tile ``(lat, lon)`` meets the imagery's bounds (a rectangle around
        the country, so a tile near a border may still lack imagery); always true without them."""
        if self.extent_bounds is None:
            return True
        lon0, lat0, lon1, lat1 = self.extent_bounds
        return lon < lon1 and lon + 1 > lon0 and lat < lat1 and lat + 1 > lat0

    @property
    def switch_servers(self) -> tuple[str, ...]:
        """Alternatives of the ``{switch:...}`` placeholder (empty when there is none)."""
        m = _SWITCH_RE.search(self.url_template)
        if m is None:
            return ()
        return tuple(s.strip() for s in m.group(1).split(","))


def _substitute(template: str, x: int, y: int, zl: int, *, switch: int) -> str:
    url = template.replace("{zoom:02d}", f"{zl:02d}")
    url = url.replace("{zoom}", str(zl))
    url = url.replace("{x}", str(x))
    url = url.replace("{y}", str(y))
    url = url.replace("{|y|}", str(abs(y) - 1))
    url = url.replace("{-y}", str(2**zl - 1 - y))
    url = url.replace("{quadkey}", quadkey(x, y, zl))
    if "{switch:" in url:
        url_0, tmp = url.split("{switch:")
        tmp, url_2 = tmp.split("}", 1)
        servers = [s.strip() for s in tmp.split(",")]
        url = url_0 + servers[switch % len(servers)] + url_2
    return url


def tile_url(p: Provider, x: int, y: int, zl: int, *, switch: int | None = None) -> str:
    """URL of tile ``(x, y)`` at ``zl`` (``get_wmts_image``, ``O4_Imagery_Utils.py:1160-1196``).

    ``switch`` selects the ``{switch:a,b,c}`` alternative (modulo their number); by default
    ``(x + y) % n``, deterministic where Ortho4XP used ``random.choice``.
    """
    if not 0 <= zl <= 24:
        raise ValueError(f"zoom level {zl} outside [0, 24]")
    if not 0 <= x < 2**zl or not 0 <= y < 2**zl:
        raise ValueError(f"tile ({x}, {y}) outside the grid at ZL{zl}")
    return _substitute(p.url_template, x, y, zl, switch=(x + y) if switch is None else switch)


def is_placeholder(p: Provider, headers: Mapping[str, str], body: bytes) -> str | None:
    """Signal identifying a "no imagery" answer: ``"header"``, ``"blake3"``, ``"size"`` or None.

    Signals are tested in the order of ``docs/specs/net-download.md`` R5: the header is
    authoritative, the body hash is exact, the byte count is a last resort that the caller
    should log (a provider change becomes visible there). Header names are case-insensitive.
    """
    rule = p.placeholder
    if rule is None:
        return None
    if rule.header_name is not None:
        wanted = rule.header_name.lower()
        for name, value in headers.items():
            if name.lower() == wanted and (
                rule.header_value is None or value.strip().lower() == rule.header_value.lower()
            ):
                return "header"
    if rule.blake3 is not None and blake3.blake3(body).hexdigest() == rule.blake3:
        return "blake3"
    if rule.size is not None and len(body) == rule.size:
        return "size"
    return None


def load_registry(path: Path | None = None) -> dict[str, Provider]:
    """Providers of a TOML registry keyed by code, in the file's order. By default the embedded
    ``registry.toml``, followed by the sources the user added (``$OSXP_HOME/sources.toml``,
    :func:`read_user_sources`): a source of the user never replaces a shipped one."""
    if path is not None:
        return _parse_registry(Path(path).read_text("utf-8"), str(path))
    registry = dict(_embedded_registry())
    for code, provider in read_user_sources()[0].items():
        registry.setdefault(code, provider)
    return registry


@cache
def _embedded_registry() -> dict[str, Provider]:
    text = resources.files("orthostudio.imagery").joinpath("registry.toml").read_text("utf-8")
    return _parse_registry(text, "orthostudio/imagery/registry.toml")


def _parse_registry(text: str, where: str) -> dict[str, Provider]:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{where}: invalid TOML: {exc}") from exc
    if doc.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(f"{where}: schema {doc.get('schema')!r}, expected {REGISTRY_SCHEMA}")
    table = doc.get("providers")
    if not isinstance(table, dict) or not table:
        raise ValueError(f"{where}: no [providers] table")
    registry: dict[str, Provider] = {}
    for code, fields in table.items():
        if not isinstance(fields, dict):
            raise ValueError(f"{where}: provider {code!r} is not a table")
        try:
            provider = Provider(code=code, **fields)
        except ValidationError as exc:
            raise ValueError(f"{where}: provider {code!r}: {exc}") from exc
        registry[code] = provider
    return registry


# ----------------------------------------------------------------- the sources a user added


def user_sources_path() -> Path:
    """``$OSXP_HOME/sources.toml``."""
    return osxp_home() / USER_SOURCES_FILE


_user_cache: dict[Path, tuple[tuple[int, int], dict[str, Provider], list[str]]] = {}


def read_user_sources(path: Path | None = None) -> tuple[dict[str, Provider], list[str]]:
    """The sources the user added, and what is wrong in their file (one line each). A broken
    entry is left out rather than stopping every build; a code the embedded registry already
    has is left out too. Read again only when the file changed."""
    path = Path(path) if path is not None else user_sources_path()
    try:
        stat = path.stat()
    except OSError:
        return {}, []
    key = (stat.st_mtime_ns, stat.st_size)
    hit = _user_cache.get(path)
    if hit is not None and hit[0] == key:
        return dict(hit[1]), list(hit[2])
    sources: dict[str, Provider] = {}
    problems: list[str] = []
    try:
        doc = tomllib.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"{path}: {exc}")
        doc = {}
    table = doc.get("providers") if isinstance(doc.get("providers"), dict) else {}
    shipped = _embedded_registry()
    for code, fields in table.items():
        if code in shipped:
            problems.append(f"{code}: the code of a source OrthoStudio XP ships")
            continue
        try:
            if not isinstance(fields, dict):
                raise ValueError("not a table")
            values = {"max_in_flight": USER_SOURCE_IN_FLIGHT, **fields, "custom": True}
            sources[code] = Provider(code=code, **values)
        except (ValidationError, ValueError, TypeError) as exc:
            problems.append(f"{code}: {str(exc).splitlines()[0]}")
    for line in problems:
        log.warning("imagery source left out: %s", line)
    _user_cache[path] = (key, sources, problems)
    return dict(sources), list(problems)


def save_user_sources(sources: Mapping[str, Provider], path: Path | None = None) -> Path:
    """Write the sources the user added, atomically, in the registry's schema."""
    path = Path(path) if path is not None else user_sources_path()
    lines = [
        "# Imagery sources you added in OrthoStudio XP (Plan, step 1: My sources). OrthoStudio XP",
        "# does not ship them: their terms of use apply to you.",
        "",
        f"schema = {REGISTRY_SCHEMA}",
    ]
    for code, p in sources.items():
        lines += ["", f"[providers.{json.dumps(code)}]"]
        lines.append(f"name = {json.dumps(p.name, ensure_ascii=False)}")
        lines.append(f"url_template = {json.dumps(p.url_template, ensure_ascii=False)}")
        lines.append(f"max_zl = {p.max_zl}")
        lines.append(f"max_in_flight = {p.max_in_flight}")
        if p.attribution:
            lines.append(f"attribution = {json.dumps(p.attribution, ensure_ascii=False)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines) + "\n")
    _user_cache.pop(path, None)
    return path


def new_source_code(name: str, taken: Mapping[str, object]) -> str:
    """A code for a source named ``name``: its letters and digits (at most 24), ``Source`` when it
    has none, and ``_2``, ``_3``... when the code is taken (the shipped codes included)."""
    base = re.sub(r"[^A-Za-z0-9]", "", name)[:24] or "Source"
    code, n = base, 1
    while code in taken or code in _embedded_registry():
        n += 1
        code = f"{base}_{n}"
    return code
