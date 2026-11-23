# -*- coding: utf-8 -*-
"""
Pipeline de génération et d'auto-actualisation des 10 catalogues français Stremio
« 🎬 CinéFR Portail (FR) » v4.0.0.
- Mode TMDB_API_KEY : interroge les Watch Providers France officiels (8=Netflix, 350=Apple TV+, 337=Disney+, 1899|119=Max/Prime).
- Mode 0-clé (fallback) : lit config/curated.json et enrichit chaque fiche en HD FR via tmdb.elfhosted.com.
"""

import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "curated.json"
MANIFEST_PATH = ROOT_DIR / "manifest.json"
CATALOG_DIR = ROOT_DIR / "catalog"

ELFHOSTED_FR_BASE = "https://tmdb.elfhosted.com/%7B%22language%22%3A%22fr-FR%22%7D/meta"
TMDB_API_BASE = "https://api.themoviedb.org/3"
CURRENT_YEAR = datetime.date.today().year


def http_get_json(url: str, timeout: int = 8) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "CineFR-CatalogBuilder/4.0",
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def is_rich_meta_valid(meta: Dict[str, Any]) -> bool:
    imdb_id = str(meta.get("id", ""))
    poster = str(meta.get("poster", ""))
    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    return (
        bool(re.match(r"^tt\d{5,10}$", imdb_id))
        and poster.startswith("https://")
        and len(name) > 0
        and len(desc) >= 10
    )


def is_released_year(meta: Dict[str, Any]) -> bool:
    rel = str(meta.get("releaseInfo", "")).strip()
    if not rel:
        return True
    match = re.match(r"^(\d{4})", rel)
    if not match:
        return True
    year = int(match.group(1))
    return year <= CURRENT_YEAR


def enrich_via_elfhosted(imdb_id: str, item_type: str) -> Optional[Dict[str, Any]]:
    url = f"{ELFHOSTED_FR_BASE}/{item_type}/{imdb_id}.json"
    data = http_get_json(url, timeout=8)
    if not data or not isinstance(data.get("meta"), dict):
        return None
    raw = data["meta"]
    poster = str(raw.get("poster") or "").strip()
    description = str(raw.get("description") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not poster.startswith("https://") or not name or not description:
        return None

    out: Dict[str, Any] = {
        "id": imdb_id,
        "type": item_type,
        "name": name,
        "posterShape": "poster",
        "poster": poster,
        "background": str(raw.get("background") or poster),
        "releaseInfo": str(raw.get("releaseInfo") or raw.get("year") or CURRENT_YEAR),
        "imdbRating": str(raw.get("imdbRating") or "7.5"),
        "genres": [str(g) for g in (raw.get("genres") or ["Drame"])][:4],
        "description": description
    }
    logo = str(raw.get("logo") or "").strip()
    if logo.startswith("https://"):
        out["logo"] = logo
    runtime = str(raw.get("runtime") or "").strip()
    if runtime:
        out["runtime"] = runtime
    return out


def fetch_tmdb_provider_ids(
    api_key: str,
    item_type: str,
    provider_id: str,
    limit: int = 12
) -> List[str]:
    endpoint = "movie" if item_type == "movie" else "tv"
    today = datetime.date.today().isoformat()
    params = {
        "api_key": api_key,
        "language": "fr-FR",
        "watch_region": "FR",
        "with_watch_providers": provider_id,
        "sort_by": "popularity.desc",
        "page": "1"
    }
    if endpoint == "movie":
        params["release_date.lte"] = today
    else:
        params["first_air_date.lte"] = today

    query = urllib.parse.urlencode(params)
    data = http_get_json(f"{TMDB_API_BASE}/discover/{endpoint}?{query}")
    if not data or not isinstance(data.get("results"), list):
        return []

    imdb_ids: List[str] = []
    for entry in data["results"]:
        tmdb_id = entry.get("id")
        if not tmdb_id:
            continue
        ext = http_get_json(f"{TMDB_API_BASE}/{endpoint}/{tmdb_id}/external_ids?api_key={api_key}")
        imdb_id = str((ext or {}).get("imdb_id") or "")
        if re.match(r"^tt\d{5,10}$", imdb_id) and imdb_id not in imdb_ids:
            imdb_ids.append(imdb_id)
        if len(imdb_ids) >= limit:
            break
    return imdb_ids


def build_row_metas(row_spec: Dict[str, Any], tmdb_api_key: str) -> List[Dict[str, Any]]:
    item_type = str(row_spec["type"])
    is_watch_provider = row_spec.get("mode") == "watch_provider"
    max_items = int(row_spec.get("limit", 18)) if is_watch_provider else 0
    curated_items: List[Dict[str, Any]] = list(row_spec.get("items") or [])
    by_id: Dict[str, Dict[str, Any]] = {
        str(m["id"]): m for m in curated_items if isinstance(m, dict) and "id" in m
    }

    target_order: List[str] = []
    if tmdb_api_key and is_watch_provider and row_spec.get("provider_id"):
        dynamic_ids = fetch_tmdb_provider_ids(
            tmdb_api_key,
            item_type,
            str(row_spec["provider_id"]),
            limit=max_items
        )
        target_order.extend(dynamic_ids)

    for m in curated_items:
        mid = str(m.get("id", ""))
        if mid and mid not in target_order:
            target_order.append(mid)

    final_metas: List[Dict[str, Any]] = []
    seen_ids = set()

    for imdb_id in target_order:
        if imdb_id in seen_ids or not re.match(r"^tt\d{5,10}$", imdb_id):
            continue
        existing = by_id.get(imdb_id, {"id": imdb_id, "type": item_type})
        if is_rich_meta_valid(existing):
            candidate = dict(existing)
            candidate["type"] = item_type
            candidate["posterShape"] = "poster"
        else:
            enriched = enrich_via_elfhosted(imdb_id, item_type)
            if not enriched:
                continue
            candidate = enriched

        if not is_released_year(candidate):
            continue

        seen_ids.add(imdb_id)
        final_metas.append(candidate)
        if max_items > 0 and len(final_metas) >= max_items:
            break

    return final_metas


def build_all() -> None:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    tmdb_api_key = os.environ.get("TMDB_API_KEY", "").strip()
    catalog_specs: List[Dict[str, Any]] = config.get("catalogs", [])
    manifest_catalogs: List[Dict[str, str]] = []

    for spec in catalog_specs:
        row_id = str(spec["id"])
        row_type = str(spec["type"])
        row_name = str(spec["name"])

        metas = build_row_metas(spec, tmdb_api_key)
        out_file = CATALOG_DIR / row_type / f"{row_id}.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as wf:
            json.dump({"metas": metas}, wf, ensure_ascii=False, indent=2)

        manifest_catalogs.append({
            "type": row_type,
            "id": row_id,
            "name": row_name
        })
        print(f"[OK] {row_type}/{row_id}.json -> {len(metas)} fiches FR HD")

    # Remove legacy catalog files not present in v4.0.0 manifest
    active_files = {
        (CATALOG_DIR / str(s["type"]) / f"{s['id']}.json").resolve()
        for s in catalog_specs
    }
    for existing_json in CATALOG_DIR.rglob("*.json"):
        if existing_json.resolve() not in active_files:
            existing_json.unlink()

    manifest = {
        "id": "com.hamza.stremio.noel.fr",
        "version": "4.0.0",
        "name": "🎬 CinéFR Portail (FR)",
        "description": "Portail auto-actualisé 100 % français HD : Noël, Stephen King, Sorties Ciné 4K VF, Top Netflix FR, Apple TV+ FR, Disney+ FR, Max & Prime FR, Films Cultes.",
        "logo": "https://em-content.zobj.net/source/apple/391/clapper-board_1f3ac.png",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": manifest_catalogs
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)

    print(f"[DONE] manifest.json v4.0.0 generated with {len(manifest_catalogs)} catalogs.")


if __name__ == "__main__":
    build_all()