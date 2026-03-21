from dataclasses import dataclass
from typing import Callable, Dict, List


@dataclass(frozen=True)
class MediaType:
    name: str
    label: str
    kodi_list_method: str
    kodi_set_method: str
    kodi_id_field: str
    kodi_result_key: str
    tmdb_endpoint: str
    tmdb_parse_certs: Callable[[List], Dict[str, str]]


def parse_movie_certs(results):
    """Parse TMDB /movie/{id}/release_dates response."""
    certs = {}
    for entry in results:
        country = entry["iso_3166_1"]
        for rd in entry.get("release_dates", []):
            if rd.get("certification"):
                certs[country] = rd["certification"]
                break
    return certs


def parse_tv_certs(results):
    """Parse TMDB /tv/{id}/content_ratings response."""
    return {r["iso_3166_1"]: r["rating"] for r in results if r.get("rating")}


MOVIE = MediaType(
    name="movie",
    label="Movie",
    kodi_list_method="VideoLibrary.GetMovies",
    kodi_set_method="VideoLibrary.SetMovieDetails",
    kodi_id_field="movieid",
    kodi_result_key="movies",
    tmdb_endpoint="/movie/{id}/release_dates",
    tmdb_parse_certs=parse_movie_certs,
)

TVSHOW = MediaType(
    name="tvshow",
    label="TV Show",
    kodi_list_method="VideoLibrary.GetTVShows",
    kodi_set_method="VideoLibrary.SetTVShowDetails",
    kodi_id_field="tvshowid",
    kodi_result_key="tvshows",
    tmdb_endpoint="/tv/{id}/content_ratings",
    tmdb_parse_certs=parse_tv_certs,
)
