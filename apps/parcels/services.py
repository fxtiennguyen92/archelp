"""
Logique métier partagée entre les commandes de gestion et l'API HTTP.

Principe de cache-aside : on cherche d'abord en base, on n'appelle
l'API Carto que si la donnée est absente.
"""

import json
import time

import requests
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Point
from django.db import transaction
from django.utils import timezone

API_PARCELLE = "https://apicarto.ign.fr/api/cadastre/parcelle"
TIMEOUT = 60
SEUIL_BRUIT_M2 = 1.0


class ErreurSource(Exception):
    """Échec de récupération auprès d'une source externe."""


def appeler_cadastre(params, tentatives=3):
    derniere = None
    for essai in range(tentatives):
        try:
            reponse = requests.get(API_PARCELLE, params=params, timeout=TIMEOUT)
            reponse.raise_for_status()
            # L'API renvoie HTTP 200 avec une collection vide quand les
            # paramètres ne correspondent à rien : tester le contenu.
            return reponse.json().get("features") or []
        except (requests.RequestException, ValueError) as exc:
            derniere = exc
            if essai < tentatives - 1:
                time.sleep(2 ** essai)
    raise ErreurSource(f"API Cadastre injoignable après {tentatives} tentatives : {derniere}")


def en_multipolygone(geojson_geom):
    if not geojson_geom:
        return None
    try:
        geom = GEOSGeometry(json.dumps(geojson_geom), srid=4326)
    except Exception:
        return None

    if geom.geom_type == "Polygon":
        geom = MultiPolygon(geom, srid=4326)
    elif geom.geom_type != "MultiPolygon":
        return None

    if not geom.valid:
        repare = geom.buffer(0)
        if repare.geom_type == "Polygon":
            geom = MultiPolygon(repare, srid=4326)
        elif repare.geom_type == "MultiPolygon":
            geom = repare

    return geom


def enregistrer_parcelle(feature):
    from apps.parcels.models import Commune, Parcelle

    props = feature["properties"]
    idu = (props.get("idu") or "").strip()
    insee = (props.get("code_insee") or "").strip()
    if not idu or not insee:
        return None

    commune = Commune.objects.filter(code_insee=insee).first()
    if commune is None:
        return None

    geom = en_multipolygone(feature.get("geometry"))
    if geom is None:
        return None

    parcelle, _ = Parcelle.objects.update_or_create(
        idu=idu,
        defaults={
            "commune": commune,
            "prefixe": (props.get("com_abs") or "000")[:3],
            "section": (props.get("section") or "")[:2],
            "numero": (props.get("numero") or "")[:4],
            "feuille": props.get("feuille") or 1,
            "code_arrondissement": (props.get("code_arr") or "000")[:3],
            "contenance_m2": props.get("contenance"),
            "gid_ign": props.get("gid"),
            "geom": geom,
            "synced_at": timezone.now(),
        },
    )
    return parcelle


def calculer_zones(parcelle):
    """
    Intersection parcelle × zones, mesurée en Lambert-93 (SRID 2154).
    Le pourcentage est calculé sur la géométrie seule : mélanger surface
    géométrique et contenance cadastrale ferait dépasser 100 %.
    La surface affichée est ensuite ramenée à la contenance, valeur juridique.
    """
    from django.contrib.gis.db.models.functions import Area, Intersection, Transform
    from apps.parcels.models import ParcelleZone, Zone

    candidates = Zone.objects.filter(geom__intersects=parcelle.geom).annotate(
        aire_commune=Area(Transform(Intersection("geom", parcelle.geom), 2154))
    )

    resultats = []
    for zone in candidates:
        if zone.aire_commune is None:
            continue
        m2 = zone.aire_commune.sq_m
        if m2 < SEUIL_BRUIT_M2:
            continue
        resultats.append((zone, m2))

    with transaction.atomic():
        ParcelleZone.objects.filter(parcelle=parcelle).delete()

        if not resultats:
            return 0

        surface_geom = sum(m2 for _, m2 in resultats)
        if surface_geom <= 0:
            return 0

        base_affichage = parcelle.contenance_m2 or surface_geom
        dominante = max(resultats, key=lambda r: r[1])[0].id

        for zone, m2 in resultats:
            part = m2 / surface_geom
            ParcelleZone.objects.create(
                parcelle=parcelle,
                zone=zone,
                surface_intersection_m2=round(part * base_affichage, 2),
                part_pct=round(100 * part, 2),
                est_dominante=(zone.id == dominante),
            )

    return len(resultats)


def parcelle_par_point(lon, lat, force=False):
    from apps.parcels.models import Parcelle

    point = Point(lon, lat, srid=4326)

    if not force:
        existante = Parcelle.objects.filter(geom__contains=point).first()
        if existante:
            return existante, False

    features = appeler_cadastre(
        {"geom": json.dumps({"type": "Point", "coordinates": [lon, lat]})}
    )
    if not features:
        return None, False

    parcelle = enregistrer_parcelle(features[0])
    if parcelle:
        calculer_zones(parcelle)
    return parcelle, True


def parcelle_par_idu(idu, force=False):
    from apps.parcels.models import Parcelle

    idu = (idu or "").strip()
    if len(idu) != 14:
        raise ValueError("L'IDU doit comporter 14 caractères.")

    if not force:
        existante = Parcelle.objects.filter(idu=idu).first()
        if existante:
            return existante, False

    features = appeler_cadastre(
        {"code_insee": idu[:5], "section": idu[8:10], "numero": idu[10:14]}
    )
    if not features:
        return None, False

    parcelle = enregistrer_parcelle(features[0])
    if parcelle:
        calculer_zones(parcelle)
    return parcelle, True

API_BAN = "https://api-adresse.data.gouv.fr/search/"

SCORE_FIABLE = 0.5


def geocoder(adresse, code_insee=None):
    """
    Géocode une adresse via la Base Adresse Nationale.
    Retourne un dict ou None. Le champ 'precision' indique si le point
    correspond à un numéro de rue ou seulement à une voie/commune :
    la différence est déterminante pour retrouver la bonne parcelle.
    """
    params = {"q": adresse, "limit": 1}
    if code_insee:
        params["citycode"] = code_insee

    try:
        reponse = requests.get(API_BAN, params=params, timeout=30)
        reponse.raise_for_status()
        features = reponse.json().get("features") or []
    except (requests.RequestException, ValueError) as exc:
        raise ErreurSource(f"BAN injoignable : {exc}")

    if not features:
        return None

    f = features[0]
    props = f["properties"]
    lon, lat = f["geometry"]["coordinates"]

    return {
        "label": props.get("label", ""),
        "lon": lon,
        "lat": lat,
        "code_insee": props.get("citycode", ""),
        "commune": props.get("city", ""),
        "code_postal": props.get("postcode", ""),
        "score": props.get("score", 0),
        "precision": props.get("type", ""),
        "fiable": props.get("score", 0) >= SCORE_FIABLE
                  and props.get("type") == "housenumber",
    }