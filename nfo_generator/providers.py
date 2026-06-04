"""Metadata providers: TMDB, AniList, TVDB (uniform schema).

See ``nfo_generator.core`` for shared constants and helpers.
"""

from __future__ import annotations

# Re-export everything from core so we can write code at the same "level
# of abstraction" the single-file version did. Star-import is safe here:
# ``core`` is the only module that does it and is well-curated.
from .core import *  # noqa: F401, F403
from .core import (   # explicit names also bring in private helpers used inline
    logger,
    _setup_logger,
    _HEAVY_RULE,
    _EMPTY_ROW,
    MEDIAINFO_LIB_CANDIDATES,)




# =============================================================================
# 9a. TMDB INTEGRATION (optional synopsis lookup)
# =============================================================================
# Fetches a release synopsis from The Movie Database. Entirely optional and
# off by default: it only runs when the user supplies their own TMDB API key
# (stored locally in the config, never committed). All network access lives
# here; the rest of the program stays offline.

TMDB_API_BASE = "https://api.themoviedb.org/3"

# In-session memoisation of tmdb_lookup results, keyed by the call arguments
# (see tmdb_lookup for the exact key shape). Avoids re-hitting TMDB when the
# user re-opens the picker or fetches the same ID twice.
TMDB_CACHE_MAX: int = 64
_TMDB_LOOKUP_CACHE: dict = {}


class TMDBError(RuntimeError):
    """Raised when a TMDB lookup fails (bad key, no result, network error)."""


def _tmdb_request(path: str, api_key: str, **params: Any) -> dict:
    """Perform a GET against the TMDB v3 API and return the parsed JSON."""
    params["api_key"] = api_key
    url = f"{TMDB_API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise TMDBError("Invalid TMDB API key.") from exc
        raise TMDBError(f"TMDB request failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TMDBError(humanize_network_error(exc, "TMDB")) from exc
    except json.JSONDecodeError as exc:
        raise TMDBError("TMDB returned an unreadable response.") from exc


# Release "junk" tokens (resolution, source, codec, audio, language, HDR,
# season words…). The title is everything *before* the first such token, so a
# messy folder like "Frieren Saison 1 MULTI VFF 1080P 10BIT BLURAY OPUS 2 0"
# cleanly reduces to "Frieren".
_RELEASE_JUNK_RE = re.compile(
    r"\b(?:"
    r"\d{3,4}[pi]|2160p|1080[pi]|720p|480p|576p|4k|8k|uhd|"            # resolution
    r"blu-?ray|bdrip|brrip|bdmv|remux|web-?dl|web-?rip|web|hdtv|"     # source
    r"dvdrip|hdrip|hdlight|4klight|"
    r"x ?26[45]|h\.?26[45]|hevc|avc|xvid|divx|av1|"                   # video codec
    r"aac|ac-?3|e-?ac-?3|eac3|dts(?:-hd)?|true-?hd|flac|opus|"        # audio
    r"ddp?5?(?:\.1)?|dd ?5\.1|mp3|atmos|"
    r"\d{1,2}-?bit|"                                                  # bit depth
    r"hdr10\+?|hdr|dovi|dolby ?vision|sdr|"                           # HDR
    r"multi|vostfr|vosta|true-?french|french|english|vff?|vfq|vfi|"  # language
    r"vf2|vo|subfrench|"
    r"saison|season|complete|int[ée]grale|integrale|coffret"         # season/edition
    r")\b",
    re.IGNORECASE,
)

# Season/episode markers used both for the title cut and TV detection.
_SEASON_MARKER_RE = re.compile(
    r"[Ss]\d{1,2}(?:[Ee]\d{1,3})?|\b\d{1,2}x\d{1,3}\b|\b(?:saison|season)\s*\d{1,2}\b",
    re.IGNORECASE,
)


def guess_title_and_year(name: str) -> tuple[str, Optional[str], bool]:
    """
    Heuristically extract (title, year, is_tv) from a release filename/folder.

    The title is cut at the earliest of: the year, a season/episode marker, or
    the first release "junk" token (quality / source / codec / language …).
    Example: "[Grp] 91 Days S01 2016 1080p BluRay-XYZ" → ("91 Days", "2016", True);
    "Frieren Saison 1 MULTI VFF 1080P 10BIT BLURAY OPUS 2 0" → ("Frieren", None, True).
    """
    base = os.path.splitext(os.path.basename(os.path.normpath(name)))[0]

    is_tv = bool(_SEASON_MARKER_RE.search(base))

    year_match = re.search(r"(?:19|20)\d{2}", base)
    year = year_match.group(0) if year_match else None

    cut = len(base)
    if year_match:
        cut = min(cut, year_match.start())
    season_match = _SEASON_MARKER_RE.search(base)
    if season_match:
        cut = min(cut, season_match.start())
    junk_match = _RELEASE_JUNK_RE.search(base)
    if junk_match:
        cut = min(cut, junk_match.start())
    title = base[:cut]

    # Drop a leading [group] or (group) tag, normalise separators.
    title = re.sub(r"^\s*\[[^\]]*\]", "", title)
    title = re.sub(r"^\s*\([^)]*\)", "", title)
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -")
    return title, year, is_tv


def guess_season_episode(name: str) -> tuple[Optional[int], Optional[int]]:
    """
    Extract (season, episode) from a name. Covers the usual scene forms plus
    the looser conventions common in anime / single-cour shows where only
    the episode number is given (defaults to season 1 in that case):

        S02E04 / S02.E04        → (2, 4)
        2x04                    → (2, 4)
        "Episode 04" / "Ep 04"  → (1, 4)
        "E04"  (bare, ≥2 digits → (1, 4)
    """
    base = os.path.basename(os.path.normpath(name))

    match = re.search(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})", base)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"\b(\d{1,2})x(\d{1,3})\b", base)
    if match:
        return int(match.group(1)), int(match.group(2))

    # "Episode 04" / "Ep 04" / "Ep.04"
    match = re.search(r"\b(?:Episode|Ep)\s*\.?\s*(\d{1,3})\b", base, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))

    # Bare "E04" / "E125" — require ≥ 2 digits so we don't trip on random
    # words starting with "E" followed by a digit.
    match = re.search(r"\bE(\d{2,3})\b", base)
    if match:
        return 1, int(match.group(1))

    return None, None


# Folder names that are just a season marker ("Saison 1", "Season 01", "S1") —
# skipped when climbing the tree to find the real series title.
SEASON_FOLDER_RE = re.compile(r"^(?:saison|season|s)\s*\.?\s*\d{1,3}\b", re.IGNORECASE)


def resolve_title_year_from_path(path: str) -> tuple[str, Optional[str], bool]:
    """
    Best-effort (title, year, is_tv) for a file or folder.

    Climbs up the directory tree when the item's own name isn't descriptive
    enough — e.g. a file literally named "s01e01.mkv" inside
    ".../Summer Time Rendering/Saison 1/" resolves to ("Summer Time Rendering").
    Season-only folders ("Saison 1", "Season 01", "S1") are skipped.
    """
    norm = os.path.normpath(path)
    season, _ = guess_season_episode(norm)
    is_tv = season is not None

    # Candidate names: the item itself, then up to three ancestor folders.
    candidates: list[str] = []
    if os.path.isfile(norm):
        candidates.append(os.path.splitext(os.path.basename(norm))[0])
    else:
        candidates.append(os.path.basename(norm))
    parent = os.path.dirname(norm)
    for _ in range(3):
        if not parent or parent == os.path.dirname(parent):
            break
        candidates.append(os.path.basename(parent))
        parent = os.path.dirname(parent)

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if SEASON_FOLDER_RE.match(candidate):
            # A "Saison N" / "Season N" / "S1" folder in the path means this
            # is a series, even if the eventual title folder doesn't say so.
            is_tv = True
            continue
        title, year, tv = guess_title_and_year(candidate)
        if title and len(title) >= 2:
            return title, year, (is_tv or tv)

    return "", None, is_tv


def _tmdb_resolve(api_key: str, *, tmdb_id, media_kind, query, year, language):
    """Resolve a movie/show to (kind, id, details dict). Internal helper."""
    if tmdb_id:
        kind = "tv" if media_kind == "tv" else "movie"
        base_id = str(tmdb_id).strip()
        return kind, base_id, _tmdb_request(f"{kind}/{base_id}", api_key, language=language)

    if not query:
        raise TMDBError("Nothing to search for (empty title).")
    search_kind = "tv" if media_kind == "tv" else "movie"
    params: dict = {"query": query, "language": language, "include_adult": "false"}
    if year:
        params["first_air_date_year" if search_kind == "tv" else "year"] = year
    results = _tmdb_request(f"search/{search_kind}", api_key, **params).get("results", [])
    if not results:
        multi = _tmdb_request(
            "search/multi", api_key, query=query, language=language, include_adult="false",
        ).get("results", [])
        results = [r for r in multi if r.get("media_type") in ("movie", "tv")]
    if not results:
        raise TMDBError(f"No TMDB result for “{query}”.")
    best = results[0]
    kind = best.get("media_type", search_kind)
    base_id = best.get("id")
    return kind, base_id, _tmdb_request(f"{kind}/{base_id}", api_key, language=language)


def _tmdb_external_links(api_key: str, kind: str, base_id) -> dict:
    """Build {label: url} for TMDB / TVDB / iMDB from a title's external IDs."""
    links = {"TMDB...........:": f"https://www.themoviedb.org/{kind}/{base_id}"}
    try:
        ext = _tmdb_request(f"{kind}/{base_id}/external_ids", api_key)
    except TMDBError:
        return links
    imdb_id = ext.get("imdb_id")
    tvdb_id = ext.get("tvdb_id")
    if kind == "tv" and tvdb_id:
        links["TVDB...........:"] = f"https://thetvdb.com/dereferrer/series/{tvdb_id}"
    if imdb_id:
        links["iMDB...........:"] = f"https://www.imdb.com/title/{imdb_id}/"
    return links


# Base URL for poster thumbnails embedded in the candidate picker.
# w92 keeps each thumbnail under ~15 KB.
TMDB_POSTER_BASE = "https://image.tmdb.org/t/p/w92"


def tmdb_search_candidates(api_key: str,
                           *,
                           query: str,
                           year: Optional[str] = None,
                           media_kind: str = "movie",
                           language: str = "en-US",
                           limit: int = 8) -> list[dict]:
    """
    Search TMDB and return up to `limit` candidate dicts:
        {"id", "kind" ("movie"/"tv"), "title", "year", "overview", "poster_path"}

    Falls back to a `search/multi` query if the typed kind yields nothing,
    so a movie misclassified as a series (or vice versa) still surfaces.
    Used by the GUI picker so the user can override a wrong auto-detect.
    """
    if not api_key:
        raise TMDBError("No TMDB API key configured.")
    if not query:
        raise TMDBError("Nothing to search for (empty title).")

    search_kind = "tv" if media_kind == "tv" else "movie"
    params: dict = {"query": query, "language": language, "include_adult": "false"}
    if year:
        params["first_air_date_year" if search_kind == "tv" else "year"] = year
    results = _tmdb_request(f"search/{search_kind}", api_key, **params).get("results", [])

    if not results:
        multi = _tmdb_request(
            "search/multi", api_key, query=query, language=language, include_adult="false",
        ).get("results", [])
        results = [r for r in multi if r.get("media_type") in ("movie", "tv")]

    candidates: list[dict] = []
    for raw in results[:limit]:
        kind = raw.get("media_type", search_kind)
        if kind not in ("movie", "tv"):
            continue
        date = raw.get("release_date") or raw.get("first_air_date") or ""
        poster_path = raw.get("poster_path")
        candidates.append({
            "id":          raw.get("id"),
            "kind":        kind,
            "title":       raw.get("title") or raw.get("name") or "",
            "year":        date[:4] if len(date) >= 4 and date[:4].isdigit() else "",
            "overview":    (raw.get("overview") or "").strip(),
            "poster_path": poster_path,
            "poster_url":  f"{TMDB_POSTER_BASE}{poster_path}" if poster_path else None,
            "provider":    "TMDB",
        })
    return candidates


def download_image_bytes(url: Optional[str], *, timeout: int = 10) -> Optional[bytes]:
    """
    Fetch raw image bytes from a URL (TMDB poster, AniList cover, …).
    Returns None on any failure — best-effort, never raises.
    """
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "NFO-Generator"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception:  # noqa: BLE001
        return None


def tmdb_lookup(api_key: str,
                *,
                tmdb_id: Optional[str] = None,
                media_kind: str = "movie",
                query: Optional[str] = None,
                year: Optional[str] = None,
                language: str = "en-US",
                season: Optional[int] = None,
                episode: Optional[int] = None,
                want_episode: bool = True) -> dict:
    """
    Look up a movie / show / episode on TMDB.

    Provide `tmdb_id` (+ `media_kind`) for a direct lookup, or `query` (+ year)
    to search by name. For TV, when `want_episode` is True and a `season` +
    `episode` are given, the *episode's* overview and name are used (falling
    back to the show otherwise). Returns:

        {
            "overview": str,          # episode / movie / series synopsis
            "title":    str,          # episode name, movie title, or series name
            "kind":     "movie"|"tv",
            "id":       <tmdb id>,
            "links":    {label: url}, # TMDB / TVDB / iMDB
        }
    Falls back to the English overview when the requested language has none.
    """
    if not api_key:
        raise TMDBError("No TMDB API key configured.")

    # In-session cache so reopening the picker / re-fetching the same title
    # doesn't hammer TMDB. Capped at TMDB_CACHE_MAX entries.
    cache_key = (
        str(tmdb_id) if tmdb_id else None,
        media_kind, (query or "").lower(), year, language,
        season, episode, bool(want_episode),
    )
    cached = _TMDB_LOOKUP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    kind, base_id, base = _tmdb_resolve(
        api_key, tmdb_id=tmdb_id, media_kind=media_kind,
        query=query, year=year, language=language,
    )

    overview = (base.get("overview") or "").strip()
    title = base.get("title") or base.get("name") or ""

    # Release year, from movie release_date or series first_air_date.
    release_date = base.get("release_date") or base.get("first_air_date") or ""
    release_year = release_date[:4] if release_date[:4].isdigit() else ""

    used_episode = False

    # --- Episode-level overview + title for TV ------------------------------
    if kind == "tv" and want_episode and season and episode:
        try:
            ep = _tmdb_request(
                f"tv/{base_id}/season/{season}/episode/{episode}",
                api_key, language=language,
            )
            ep_overview = (ep.get("overview") or "").strip()
            if not ep_overview and language != "en-US":
                ep_en = _tmdb_request(
                    f"tv/{base_id}/season/{season}/episode/{episode}",
                    api_key, language="en-US",
                )
                ep_overview = (ep_en.get("overview") or "").strip()
            if ep_overview:
                overview = ep_overview
            if ep.get("name"):
                title = ep["name"]
                used_episode = True
        except TMDBError:
            pass  # episode not found → keep the series-level overview/title

    # --- English fallback for the (show/movie) overview ---------------------
    if not overview and language != "en-US":
        en = _tmdb_request(f"{kind}/{base_id}", api_key, language="en-US")
        overview = (en.get("overview") or "").strip()

    # Append the year for a movie or a series PACK (series-level title), e.g.
    # "Scarlet et l'éternité (2025)". Not for an individual episode title.
    if release_year and not used_episode:
        title = f"{title} ({release_year})"

    result = {
        "overview": overview,
        "title": title,
        "kind": kind,
        "id": base_id,
        "links": _tmdb_external_links(api_key, kind, base_id),
        "provider": "TMDB",
        "managed_labels": list(TMDB_MANAGED_LABELS),
    }
    # Cache the assembled result (cap eviction = oldest insertion order).
    _TMDB_LOOKUP_CACHE[cache_key] = result
    if len(_TMDB_LOOKUP_CACHE) > TMDB_CACHE_MAX:
        _TMDB_LOOKUP_CACHE.pop(next(iter(_TMDB_LOOKUP_CACHE)))
    return result


# =============================================================================
# 9a-bis. ANILIST INTEGRATION (anime — GraphQL, no auth)
# =============================================================================
# Public GraphQL endpoint, no API key required. Best for anime where TMDB's
# coverage is patchy. Returns the same dict shape as tmdb_lookup so the GUI
# can apply the result through the same pipeline.

ANILIST_API_URL = "https://graphql.anilist.co"

# Reusable GraphQL queries. Compact form to keep the POST payload small.
_ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 8) {
    media(search: $search, type: ANIME) {
      id
      title { romaji english native }
      startDate { year }
      description(asHtml: false)
      coverImage { medium }
      siteUrl
      idMal
      format
    }
  }
}
""".strip()

_ANILIST_LOOKUP_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    startDate { year }
    description(asHtml: false)
    siteUrl
    idMal
    format
  }
}
""".strip()


def _anilist_request(query: str, variables: dict) -> dict:
    """
    POST a GraphQL query to AniList and return the parsed JSON `data` block.

    AniList sits behind Cloudflare and rejects requests that ship the default
    `Python-urllib/3.x` User-Agent (HTTP 403). We send a real UA + an
    explicit Accept-Encoding so the response always comes back uncompressed
    and easy to parse.
    """
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        ANILIST_API_URL,
        data=payload,
        headers={
            "Content-Type":    "application/json",
            "Accept":          "application/json",
            "Accept-Encoding": "identity",
            "User-Agent":      "NFO-Generator/1.0 (+https://github.com/)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        # Surface the most useful detail (status + a snippet of the body if any).
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise TMDBError(
            f"AniList HTTP {exc.code}: {exc.reason}"
            + (f" — {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TMDBError(humanize_network_error(exc, "AniList")) from exc
    except json.JSONDecodeError as exc:
        raise TMDBError("AniList returned an unreadable response.") from exc
    if "errors" in body and body["errors"]:
        msg = body["errors"][0].get("message", "AniList GraphQL error")
        raise TMDBError(f"AniList: {msg}")
    return body.get("data") or {}


def _anilist_clean_description(text: Optional[str]) -> str:
    """
    Convert AniList's HTML+spoiler-flavoured description into plain text.

    AniList descriptions can contain `<br>` tags, basic HTML, AniList's own
    `~!spoiler!~` markers, and a sprinkle of markdown.
    """
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)         # strip remaining HTML tags
    text = re.sub(r"~!|!~", "", text)            # AniList spoiler markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # markdown bold
    text = re.sub(r"__([^_]+)__", r"\1", text)      # markdown underline
    text = re.sub(r"\n{3,}", "\n\n", text)       # collapse blank lines
    return text.strip()


def _anilist_pick_title(title_obj: dict) -> str:
    """Pick the best AniList title: english → romaji → native."""
    if not title_obj:
        return ""
    for key in ("english", "romaji", "native"):
        value = (title_obj.get(key) or "").strip()
        if value:
            return value
    return ""


def _anilist_kind(format_value: Optional[str]) -> str:
    """Map an AniList `format` to our 'movie' / 'tv' bucket."""
    if (format_value or "").upper() == "MOVIE":
        return "movie"
    return "tv"  # TV / OVA / ONA / SPECIAL / TV_SHORT etc.


def _anilist_links(site_url: Optional[str], mal_id: Optional[int]) -> dict:
    links: dict[str, str] = {}
    if site_url:
        links["ANiLiST........:"] = site_url
    if mal_id:
        links["MAL............:"] = f"https://myanimelist.net/anime/{mal_id}"
    return links


def anilist_search_candidates(query: str,
                              *,
                              year: Optional[str] = None,  # noqa: ARG001 — AniList search ignores year
                              language: str = "en-US",     # noqa: ARG001 — AniList only ships English/native
                              limit: int = 8) -> list[dict]:
    """
    Search AniList for an anime title. Returns up to `limit` candidate dicts:
        {"id", "kind", "title", "year", "overview", "poster_url", "provider"}
    Suitable for the GUI candidate picker (same shape as TMDB candidates).
    """
    if not query:
        raise TMDBError("Nothing to search for (empty title).")
    data = _anilist_request(_ANILIST_SEARCH_QUERY, {"search": query})
    media = ((data.get("Page") or {}).get("media") or [])[:limit]
    candidates: list[dict] = []
    for raw in media:
        candidates.append({
            "id":         raw.get("id"),
            "kind":       _anilist_kind(raw.get("format")),
            "title":      _anilist_pick_title(raw.get("title") or {}),
            "year":       str((raw.get("startDate") or {}).get("year") or ""),
            "overview":   _anilist_clean_description(raw.get("description")),
            "poster_url": ((raw.get("coverImage") or {}).get("medium")),
            "poster_path": None,
            "provider":   "AniList",
        })
    return candidates


def anilist_lookup(*,
                   anilist_id: Optional[int] = None,
                   query: Optional[str] = None,
                   year: Optional[str] = None,
                   language: str = "en-US") -> dict:
    """
    Resolve an anime on AniList by ID or by search query. Returns the same
    dict shape as `tmdb_lookup` for the GUI's apply pipeline:
        {"overview", "title", "kind", "id", "links",
         "provider", "managed_labels"}
    No episode-level lookup: AniList's public API doesn't ship per-episode
    overviews, so we always return the series-level synopsis. The title gets
    `(YEAR)` appended like tmdb_lookup does for movies + series packs.
    """
    if anilist_id:
        data = _anilist_request(_ANILIST_LOOKUP_QUERY, {"id": int(anilist_id)})
        media = data.get("Media") or {}
    else:
        candidates = anilist_search_candidates(
            query=query or "", year=year, language=language, limit=1,
        )
        if not candidates:
            raise TMDBError(f"No AniList result for “{query}”.")
        media = _anilist_request(
            _ANILIST_LOOKUP_QUERY, {"id": int(candidates[0]["id"])},
        ).get("Media") or {}

    title = _anilist_pick_title(media.get("title") or {})
    overview = _anilist_clean_description(media.get("description"))
    kind = _anilist_kind(media.get("format"))
    release_year = str((media.get("startDate") or {}).get("year") or "")

    if release_year and title:
        title = f"{title} ({release_year})"

    return {
        "overview": overview,
        "title": title,
        "kind": kind,
        "id": media.get("id"),
        "links": _anilist_links(media.get("siteUrl"), media.get("idMal")),
        "provider": "AniList",
        "managed_labels": list(ANILIST_MANAGED_LABELS),
    }


# =============================================================================
# 9a-ter. TVDB INTEGRATION (TheTVDB v4 API — Bearer-token auth)
# =============================================================================
# Useful for older TV shows and niches where TMDB's coverage is thin. The v4
# API is token-based: POST /login with your API key returns a JWT that's
# valid for about a month. We cache it in memory so a session only hits
# /login once. All network access is best-effort; failures raise TMDBError
# so the existing GUI pipeline can surface the message.

TVDB_API_BASE = "https://api4.thetvdb.com/v4"

# Cached bearer token (per process). TVDB tokens are valid ~30 days; we
# refresh proactively after 25 days to avoid edge-of-expiry failures.
_TVDB_TOKEN: Optional[str] = None
_TVDB_TOKEN_OBTAINED_AT: float = 0.0
_TVDB_TOKEN_TTL_SECONDS: float = 25 * 24 * 3600


def _tvdb_auth(api_key: str, pin: str = "") -> str:
    """Return a usable TVDB bearer token (cached, auto-refreshed)."""
    global _TVDB_TOKEN, _TVDB_TOKEN_OBTAINED_AT
    if _TVDB_TOKEN and (time.time() - _TVDB_TOKEN_OBTAINED_AT) < _TVDB_TOKEN_TTL_SECONDS:
        return _TVDB_TOKEN

    if not api_key:
        raise TMDBError("No TVDB API key configured.")

    payload = json.dumps({"apikey": api_key, "pin": pin or ""}).encode("utf-8")
    request = urllib.request.Request(
        f"{TVDB_API_BASE}/login",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "NFO-Generator/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise TMDBError("Invalid TVDB API key.") from exc
        raise TMDBError(f"TVDB auth HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TMDBError(humanize_network_error(exc, "TVDB")) from exc
    except json.JSONDecodeError as exc:
        raise TMDBError("TVDB returned an unreadable response.") from exc

    if body.get("status") != "success":
        raise TMDBError(f"TVDB auth failed: {body.get('message', 'unknown')}")
    token = ((body.get("data") or {}).get("token"))
    if not token:
        raise TMDBError("TVDB returned no token.")
    _TVDB_TOKEN = token
    _TVDB_TOKEN_OBTAINED_AT = time.time()
    return token


def _tvdb_request(path: str, api_key: str, pin: str = "", **params: Any) -> dict:
    """GET against /{path}?{params} with Bearer auth; returns parsed JSON."""
    token = _tvdb_auth(api_key, pin)
    url = f"{TVDB_API_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/json",
            "User-Agent":    "NFO-Generator/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            # Token went stale earlier than expected — invalidate so the
            # next call re-authenticates.
            global _TVDB_TOKEN
            _TVDB_TOKEN = None
            raise TMDBError("TVDB token expired or unauthorized.") from exc
        raise TMDBError(f"TVDB request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TMDBError(humanize_network_error(exc, "TVDB")) from exc
    except json.JSONDecodeError as exc:
        raise TMDBError("TVDB returned an unreadable response.") from exc


def _tvdb_kind_to_path(kind: str) -> str:
    """Our 'tv'/'movie' bucket → TVDB's URL segment."""
    return "movies" if kind == "movie" else "series"


def tvdb_search_candidates(query: str,
                           *,
                           api_key: str,
                           pin: str = "",
                           year: Optional[str] = None,
                           media_kind: str = "tv",
                           limit: int = 8) -> list[dict]:
    """
    Search TVDB for a title. Returns up to `limit` candidate dicts shaped
    like the TMDB / AniList ones so the GUI picker treats them uniformly.
    """
    if not query:
        raise TMDBError("Nothing to search for (empty title).")
    params: dict[str, str] = {"query": query, "limit": str(limit)}
    if year:
        params["year"] = year
    # TVDB's `type` filter accepts "series" / "movie".
    if media_kind in ("tv", "movie"):
        params["type"] = "series" if media_kind == "tv" else "movie"
    data = _tvdb_request("search", api_key, pin=pin, **params)
    results = data.get("data") or []
    candidates: list[dict] = []
    for raw in results:
        type_label = (raw.get("type") or "").lower()
        if type_label not in ("series", "movie"):
            continue
        kind = "tv" if type_label == "series" else "movie"
        year_str = ""
        if raw.get("year"):
            year_str = str(raw["year"])
        elif raw.get("first_air_time"):
            year_str = str(raw["first_air_time"])[:4]
        candidates.append({
            "id":          raw.get("tvdb_id") or raw.get("id"),
            "kind":        kind,
            "title":       raw.get("name") or raw.get("translations", {}).get("eng") or "",
            "year":        year_str if year_str.isdigit() else "",
            "overview":    (raw.get("overview") or "").strip(),
            "poster_url":  raw.get("image_url") or raw.get("thumbnail"),
            "poster_path": None,
            "provider":    "TVDB",
        })
    return candidates


# ISO-639-1 (UI) → ISO-639-2/3 (TVDB) language map. TVDB endpoints take
# 3-letter codes ("fra", "eng", "jpn"…). We expose the same set of locales
# as the rest of the GUI.
_TVDB_LANG_MAP: dict[str, str] = {
    "en": "eng", "fr": "fra", "ja": "jpn", "es": "spa", "de": "deu",
    "it": "ita", "pt": "por", "zh": "zho", "ko": "kor", "ru": "rus",
    "pl": "pol", "nl": "nld", "tr": "tur", "ar": "ara",
}


def _tvdb_language_code(language: str) -> str:
    """Convert 'fr-FR' / 'en' to a TVDB 3-letter code, defaulting to 'eng'."""
    short = (language or "").split("-")[0].lower()
    return _TVDB_LANG_MAP.get(short, "eng")


def _tvdb_fetch_translation(kind_path: str,
                            tvdb_id: int,
                            lang3: str,
                            api_key: str,
                            pin: str = "") -> dict:
    """
    Fetch the localized name/overview for a series or movie.

    Returns `{"name": str, "overview": str}` (either field may be empty).
    Returns `{}` on any HTTP error (missing translation, 404, …) — callers
    should fall back to whatever they already had.
    """
    try:
        data = _tvdb_request(
            f"{kind_path}/{tvdb_id}/translations/{lang3}",
            api_key, pin=pin,
        ).get("data") or {}
    except Exception:  # noqa: BLE001
        return {}
    return {
        "name":     (data.get("name") or "").strip(),
        "overview": (data.get("overview") or "").strip(),
    }


def _tvdb_fetch_episode(series_id: int,
                        season: int,
                        episode: int,
                        lang3: str,
                        api_key: str,
                        pin: str = "") -> dict:
    """
    Find a single episode of a TVDB series by season + episode number, and
    return its localized name + overview.

    Returns `{"name": str, "overview": str, "id": int|None}`; any field may
    be empty. Returns `{}` if the episode cannot be located.

    Implementation notes:
      * We use the language-less `episodes/default` endpoint to *locate* the
        episode — the season+episodeNumber filters are reliable there but
        not on the localized `/{lang}` variant.
      * The exact season+number match is enforced in Python in case the API
        ignored our filters (otherwise `episodes[0]` would silently be the
        wrong episode — typically S01E01).
      * Localized name/overview are pulled from `episodes/{id}/translations/{lang}`.
    """
    # ---- Step 1: locate the episode -----------------------------------
    try:
        resp = _tvdb_request(
            f"series/{series_id}/episodes/default", api_key, pin=pin,
            season=season, episodeNumber=episode,
        )
    except Exception:  # noqa: BLE001
        return {}

    eps = ((resp.get("data") or {}).get("episodes") or [])
    if not eps:
        return {}

    # Defensive exact match — the API may return the full list if it ignored
    # our filter, in which case `episodes[0]` is meaningless.
    target_season = int(season)
    target_number = int(episode)
    ep = None
    for e in eps:
        if (e.get("seasonNumber") == target_season
                and e.get("number") == target_number):
            ep = e
            break
    if ep is None:
        # If the API returned exactly one match, trust it; otherwise bail
        # rather than pick a random episode.
        if len(eps) == 1:
            ep = eps[0]
        else:
            return {}

    ep_id = ep.get("id")
    name = (ep.get("name") or "").strip()
    overview = (ep.get("overview") or "").strip()

    # ---- Step 2: localize via per-episode translations endpoint --------
    if ep_id and lang3 != "eng":
        try:
            tr = _tvdb_request(
                f"episodes/{ep_id}/translations/{lang3}", api_key, pin=pin,
            ).get("data") or {}
            tr_name = (tr.get("name") or "").strip()
            tr_overview = (tr.get("overview") or "").strip()
            if tr_name:
                name = tr_name
            if tr_overview:
                overview = tr_overview
        except Exception:  # noqa: BLE001
            pass

    return {"name": name, "overview": overview, "id": ep_id}


def tvdb_lookup(*,
                tvdb_id: Optional[int] = None,
                api_key: str,
                pin: str = "",
                media_kind: str = "tv",
                query: Optional[str] = None,
                year: Optional[str] = None,
                language: str = "en-US",
                season: Optional[int] = None,
                episode: Optional[int] = None) -> dict:
    """
    Look up a TVDB title by ID or by name search. Returns the same dict
    shape as `tmdb_lookup` so the GUI's apply pipeline works unchanged.

    Localization: TVDB exposes translations through a dedicated endpoint —
    the base `/extended` payload only lists which languages are available,
    not their content. So we call `/series|movies/{id}/translations/{lang}`
    explicitly. Falls back to the base `name` / `overview` if the requested
    language has no translation.

    Episode-level synopsis: when `season` and `episode` are passed (and the
    target is a series), the overview is taken from that specific episode
    instead of the series-wide one — same behavior as `tmdb_lookup`.

    TVDB's only "owned" link row is TVDB itself; any IMDb / TMDB cross-
    references found in `remoteIds` are included in `links` but excluded
    from `managed_labels` so they don't clobber rows owned by other providers.
    """
    if not api_key:
        raise TMDBError("No TVDB API key configured.")

    if not tvdb_id:
        candidates = tvdb_search_candidates(
            query=query or "", api_key=api_key, pin=pin, year=year,
            media_kind=media_kind, limit=1,
        )
        if not candidates:
            raise TMDBError(f"No TVDB result for “{query}”.")
        tvdb_id = candidates[0]["id"]
        media_kind = candidates[0]["kind"]

    kind_path = _tvdb_kind_to_path(media_kind)
    data = _tvdb_request(
        f"{kind_path}/{tvdb_id}/extended", api_key, pin=pin,
    ).get("data") or {}

    # ---- Localized name / overview ----------------------------------------
    # Start with whatever the extended payload returns (usually English).
    title = (data.get("name") or "").strip()
    overview = (data.get("overview") or "").strip()

    lang3 = _tvdb_language_code(language)
    if lang3 != "eng":
        tr = _tvdb_fetch_translation(kind_path, tvdb_id, lang3, api_key, pin=pin)
        if tr.get("name"):
            title = tr["name"]
        if tr.get("overview"):
            overview = tr["overview"]

    # ---- Episode-level synopsis (series + SxxExx detected) ---------------
    is_series = (media_kind == "tv")
    episode_id: Optional[int] = None
    used_episode = False
    if is_series and season is not None and episode is not None:
        ep = _tvdb_fetch_episode(
            tvdb_id, int(season), int(episode), lang3, api_key, pin=pin,
        )
        if ep:
            episode_id = ep.get("id")
            if ep.get("overview"):
                overview = ep["overview"]
            if ep.get("name"):
                # Match TMDB behavior: when the episode resolves, the title
                # becomes the episode name rather than the show.
                title = ep["name"]
                used_episode = True

    # ---- Year suffix on the title -----------------------------------------
    # Skipped when we're showing an episode title — appending the series
    # premiere year to an episode name would be confusing.
    first_aired = data.get("firstAired") or str(data.get("year") or "")
    release_year = first_aired[:4] if first_aired[:4].isdigit() else ""
    if release_year and title and "(" not in title and not used_episode:
        title = f"{title} ({release_year})"

    # ---- Links: TVDB itself + best-effort cross-references ----------------
    kind = "tv" if media_kind == "tv" else "movie"
    links: dict[str, str] = {
        "TVDB...........:": f"https://thetvdb.com/{kind_path}/{tvdb_id}",
    }
    for remote in (data.get("remoteIds") or []):
        source = (remote.get("sourceName") or remote.get("type") or "").lower()
        rid = remote.get("id")
        if not rid:
            continue
        if source.startswith("imdb"):
            links["iMDB...........:"] = f"https://www.imdb.com/title/{rid}/"
        elif source.startswith("themoviedb") or source == "tmdb":
            tmdb_kind = "movie" if kind == "movie" else "tv"
            links["TMDB...........:"] = f"https://www.themoviedb.org/{tmdb_kind}/{rid}"

    return {
        "overview": overview,
        "title":    title,
        "kind":     kind,
        "id":       tvdb_id,
        "episode_id": episode_id,
        "links":    links,
        "provider": "TVDB",
        "managed_labels": list(TVDB_MANAGED_LABELS),
    }

