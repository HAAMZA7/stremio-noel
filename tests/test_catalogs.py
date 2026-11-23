# -*- coding: utf-8 -*-
import datetime
import json
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT_DIR / "manifest.json"
CATALOG_DIR = ROOT_DIR / "catalog"
CURRENT_YEAR = datetime.date.today().year

EXPECTED_CATALOG_IDS = [
    ("movie", "noel_romance"),
    ("movie", "noel_classiques"),
    ("movie", "noel_magie"),
    ("movie", "stephen_king_fr"),
    ("movie", "nouveautes_fr"),
    ("series", "top_netflix_fr"),
    ("series", "top_appletv_fr"),
    ("movie", "top_disney_fr"),
    ("series", "top_max_prime_fr"),
    ("movie", "cultes_fr"),
]


def test_manifest_v4_structure():
    assert MANIFEST_PATH.exists(), "manifest.json doit exister"
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert data["version"] == "4.0.0"
    assert data["name"] == "🎬 CinéFR Portail (FR)"
    assert data["resources"] == ["catalog"]
    assert data["types"] == ["movie", "series"]

    catalogs = data.get("catalogs", [])
    assert len(catalogs) == 10
    actual_pairs = [(c["type"], c["id"]) for c in catalogs]
    assert actual_pairs == EXPECTED_CATALOG_IDS


def test_all_catalog_files_valid_and_deduplicated():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for cat in manifest["catalogs"]:
        cat_type = cat["type"]
        cat_id = cat["id"]
        file_path = CATALOG_DIR / cat_type / f"{cat_id}.json"
        assert file_path.exists(), f"Catalogue manquant : {file_path}"

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        metas = payload.get("metas", [])
        assert len(metas) >= 8, f"Le catalogue {cat_id} doit contenir au moins 8 titres (trouvé: {len(metas)})"

        seen_ids = set()
        for item in metas:
            imdb_id = item.get("id", "")
            assert re.match(r"^tt\d{5,10}$", imdb_id), f"ID IMDb invalide dans {cat_id}: {imdb_id}"
            assert imdb_id not in seen_ids, f"Doublon détecté dans {cat_id}: {imdb_id}"
            seen_ids.add(imdb_id)

            assert item.get("type") == cat_type
            assert str(item.get("poster", "")).startswith("https://"), f"Poster non-HTTPS pour {imdb_id}"
            assert len(str(item.get("name", "")).strip()) > 0, f"Nom vide pour {imdb_id}"
            assert len(str(item.get("description", "")).strip()) >= 10, f"Résumé FR manquant pour {imdb_id}"

            rel = str(item.get("releaseInfo", "")).strip()
            year_match = re.match(r"^(\d{4})", rel)
            if year_match:
                assert int(year_match.group(1)) <= CURRENT_YEAR, f"Titre futur non sorti dans {cat_id}: {imdb_id} ({rel})"