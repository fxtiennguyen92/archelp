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

import re

API_PARCELLE = "https://apicarto.ign.fr/api/cadastre/parcelle"
TIMEOUT = 60
SEUIL_BRUIT_M2 = 1.0
SEUIL_PRESCRIPTION_M2 = 10.0
SEUIL_PRESCRIPTION_PCT = 0.5

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

    # buffer(0) ne répare pas toutes les auto-intersections : mieux vaut
    # écarter une géométrie douteuse que fausser un calcul de surface.
    if not geom.valid or geom.empty:
        return None

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

    # Le seuil de bruit doit rester proportionné : sur une parcelle technique
    # de 1 m² — poteau, borne, reliquat de division — un seuil fixe écarterait
    # la totalité des intersections.
    reference = parcelle.contenance_m2 or 0
    seuil = min(SEUIL_BRUIT_M2, max(0.01, reference * 0.01))

    resultats = []
    for zone in candidates:
        if zone.aire_commune is None:
            continue
        m2 = zone.aire_commune.sq_m
        if m2 < seuil:
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
        for fonction in (calculer_prescriptions, calculer_servitudes):
            try:
                fonction(parcelle)
            except ErreurSource:
                # Compléments : leur indisponibilité ne doit pas faire
                # échouer la consultation du zonage.
                pass
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
        for fonction in (calculer_prescriptions, calculer_servitudes):
            try:
                fonction(parcelle)
            except ErreurSource:
                # Compléments : leur indisponibilité ne doit pas faire
                # échouer la consultation du zonage.
                pass
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

API_PRESCRIPTION = "https://apicarto.ign.fr/api/gpu/prescription-surf"

# « 12mHT » → 12,0 m hors tout ; « 10mET » → 10,0 m égout de toiture.
# La distinction n'est pas cosmétique : sur une toiture en pente,
# l'écart entre les deux modes de mesure atteint plusieurs mètres.
MOTIF_VALEUR = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(m|%)\s*(HT|ET)?", re.IGNORECASE
)


def analyser_txt(txt):
    """Extrait valeur, unité et mode de mesure du champ txt du GPU."""
    if not txt:
        return None, "", ""
    trouve = MOTIF_VALEUR.search(txt)
    if not trouve:
        return None, "", ""
    try:
        valeur = float(trouve.group(1).replace(",", "."))
    except ValueError:
        return None, "", ""
    unite = trouve.group(2).lower()
    mesure = (trouve.group(3) or "").upper()
    return valeur, unite, mesure


def appeler_prescriptions(geom_geojson, tentatives=3):
    derniere = None
    for essai in range(tentatives):
        try:
            reponse = requests.get(
                API_PRESCRIPTION,
                params={"geom": json.dumps(geom_geojson)},
                timeout=TIMEOUT,
            )
            reponse.raise_for_status()
            return reponse.json().get("features") or []
        except (requests.RequestException, ValueError) as exc:
            derniere = exc
            if essai < tentatives - 1:
                time.sleep(2 ** essai)
    raise ErreurSource(f"API Prescriptions injoignable : {derniere}")


def calculer_prescriptions(parcelle):
    """
    Récupère les prescriptions couvrant la parcelle et calcule la part
    de surface concernée. Interrogation par polygone et non par point :
    une prescription locale — espace planté, emplacement réservé — ne
    couvre souvent qu'une fraction du terrain.
    """
    from django.contrib.gis.db.models.functions import Area, Intersection, Transform
    from apps.parcels.models import (
        DocumentUrbanisme, ParcellePrescription, Prescription,
    )

    features = appeler_prescriptions(json.loads(parcelle.geom.geojson))
    if not features:
        return 0

    # Le rattachement au document se fait par partition, comme pour le zonage.
    documents = {}
    maintenant = timezone.now()
    objets = []

    for feat in features:
        props = feat["properties"]
        partition = (props.get("partition") or "").strip()
        gid = props.get("gid")
        if not partition or gid is None:
            continue

        if partition not in documents:
            documents[partition] = (
                DocumentUrbanisme.objects.filter(partition=partition)
                .order_by("-date_approbation")
                .first()
            )
        doc = documents[partition]
        if doc is None:
            continue

        geom = en_multipolygone(feat.get("geometry"))
        if geom is None:
            continue

        valeur, unite, mesure = analyser_txt(props.get("txt"))

        presc, _ = Prescription.objects.update_or_create(
            document=doc,
            gid_ign=gid,
            defaults={
                "type_psc": (props.get("typepsc") or "")[:2],
                "stype_psc": (props.get("stypepsc") or "")[:2],
                "libelle": (props.get("libelle") or "")[:300],
                "txt": (props.get("txt") or "")[:200],
                "valeur_num": valeur,
                "unite": unite,
                "reference_mesure": mesure,
                "geom": geom,
                "synced_at": maintenant,
            },
        )
        objets.append(presc)

    if not objets:
        return 0

    surface_parcelle = parcelle.contenance_m2

    with transaction.atomic():
        ParcellePrescription.objects.filter(parcelle=parcelle).delete()

        ids = [o.id for o in objets]
        candidates = Prescription.objects.filter(id__in=ids).annotate(
            aire_commune=Area(Transform(Intersection("geom", parcelle.geom), 2154))
        )

        resultats = []
        for presc in candidates:
            if presc.aire_commune is None:
                continue
            m2 = presc.aire_commune.sq_m
            # Deux sources géométriques différentes : les micro-intersections
            # sont du bruit de bord, pas des contraintes réelles.
            if m2 < SEUIL_PRESCRIPTION_M2:
                continue
            resultats.append((presc, m2))

        if not resultats:
            return 0

        # Contrairement au zonage, les prescriptions se superposent :
        # le pourcentage se calcule sur la surface de la parcelle,
        # pas sur la somme des intersections.
        base = surface_parcelle
        if not base:
            geom_m2 = parcelle.geom.transform(2154, clone=True).area
            base = geom_m2 or 1

        for presc, m2 in resultats:
            pct = min(100.0, 100 * m2 / base)
            if pct < SEUIL_PRESCRIPTION_PCT and presc.valeur_num is None:
                continue
            ParcellePrescription.objects.create(
                parcelle=parcelle,
                prescription=presc,
                surface_intersection_m2=round(m2, 2),
                part_pct=round(min(100.0, 100 * m2 / base), 2),
            )

    return len(resultats)


API_SERVITUDE = "https://apicarto.ign.fr/api/gpu/assiette-sup-s"

SEUIL_SERVITUDE_M2 = 10.0


def reparer_encodage(texte):
    """
    Le GPU renvoie certains libellés encodés deux fois en UTF-8 :
    « é » devient « Ã© ». On rétablit la chaîne quand la double
    conversion réussit, et on laisse le texte intact sinon — le défaut
    peut être corrigé côté source à tout moment.
    """
    if not texte:
        return ""
    try:
        return texte.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte


def appeler_servitudes(geom_geojson, tentatives=3):
    derniere = None
    for essai in range(tentatives):
        try:
            reponse = requests.get(
                API_SERVITUDE,
                params={"geom": json.dumps(geom_geojson)},
                timeout=TIMEOUT,
            )
            reponse.raise_for_status()
            return reponse.json().get("features") or []
        except (requests.RequestException, ValueError) as exc:
            derniere = exc
            if essai < tentatives - 1:
                time.sleep(2 ** essai)
    raise ErreurSource(f"API Servitudes injoignable : {derniere}")


def calculer_servitudes(parcelle):
    """
    Récupère les servitudes d'utilité publique grevant la parcelle.

    À la différence du zonage et des prescriptions, les SUP ne dépendent
    d'aucun document d'urbanisme : une commune sans PLU peut en supporter.
    """
    from django.contrib.gis.db.models.functions import Area, Intersection, Transform
    from apps.parcels.models import ParcelleServitude, Servitude

    features = appeler_servitudes(json.loads(parcelle.geom.geojson))
    if not features:
        return 0

    maintenant = timezone.now()
    objets = []

    for feat in features:
        props = feat["properties"]
        partition = (props.get("partition") or "").strip()
        gid = props.get("gid")
        if not partition or gid is None:
            continue

        geom = en_multipolygone(feat.get("geometry"))
        if geom is None:
            continue

        param = props.get("paramcalc")
        try:
            param = float(param) if param is not None else None
        except (TypeError, ValueError):
            param = None

        serv, _ = Servitude.objects.update_or_create(
            partition=partition,
            gid_ign=gid,
            defaults={
                "sup_type": (props.get("suptype") or "")[:10].upper(),
                "id_assiette": (props.get("idass") or "")[:100],
                "id_generateur": (props.get("idgen") or "")[:100],
                "nom_litteral": reparer_encodage(props.get("nomsuplitt"))[:300],
                "type_assiette": reparer_encodage(props.get("typeass"))[:150],
                "mode_geometrie": reparer_encodage(props.get("modegeoass"))[:100],
                "parametre_calcul": param,
                "fichier_acte": (props.get("fichier") or "")[:300],
                "geom": geom,
                "synced_at": maintenant,
            },
        )
        objets.append(serv)

    if not objets:
        return 0

    with transaction.atomic():
        ParcelleServitude.objects.filter(parcelle=parcelle).delete()

        ids = [o.id for o in objets]
        candidates = Servitude.objects.filter(id__in=ids).annotate(
            aire_commune=Area(Transform(Intersection("geom", parcelle.geom), 2154))
        )

        base = parcelle.contenance_m2
        if not base:
            base = parcelle.geom.transform(2154, clone=True).area or 1

        n = 0
        for serv in candidates:
            if serv.aire_commune is None:
                continue
            m2 = serv.aire_commune.sq_m
            if m2 < SEUIL_SERVITUDE_M2:
                continue
            ParcelleServitude.objects.create(
                parcelle=parcelle,
                servitude=serv,
                surface_intersection_m2=round(m2, 2),
                part_pct=round(min(100.0, 100 * m2 / base), 2),
            )
            n += 1

    return n