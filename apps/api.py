"""
API publique ArcHelp. Exposée via django-ninja.

Toute réponse concernant une parcelle inclut les avertissements
nécessaires : données publiques imparfaites, règlement susceptible
d'évoluer, vérification en mairie recommandée.
"""

from typing import List, Optional

from ninja import NinjaAPI, Schema

from apps.parcels import services

api = NinjaAPI(
    title="ArcHelp API",
    version="0.1.0",
    description="Consultation des règles d'urbanisme françaises par parcelle.",
)


# ---------- Schémas ----------

class ZoneOut(Schema):
    libelle: str
    libelle_long: str
    type_zone: str
    part_pct: float
    surface_m2: float
    est_dominante: bool
    fichier_reglement: str
    page_reglement: Optional[int]
    document_idurba: str
    document_type: str
    date_approbation: Optional[str]


class CommuneOut(Schema):
    code_insee: str
    nom: str
    code_departement: str


class AvertissementOut(Schema):
    code: str
    params: dict = {}


class ParcelleOut(Schema):
    idu: str
    section: str
    numero: str
    contenance_m2: Optional[int]
    commune: CommuneOut
    zones: List[ZoneOut]
    avertissements: List[AvertissementOut]
    geometry: dict


class GeocodageOut(Schema):
    label: str
    lon: float
    lat: float
    code_insee: str
    commune: str
    score: float
    precision: str
    fiable: bool


class RechercheOut(Schema):
    geocodage: Optional[GeocodageOut]
    parcelle: Optional[ParcelleOut]
    message: Optional[str]


# ---------- Sérialisation ----------

def serialiser_parcelle(parcelle):
    import json

    liens = list(
        parcelle.parcellezone_set.select_related("zone", "zone__document")
        .order_by("-part_pct")
    )

    zones = []
    for lien in liens:
        z, doc = lien.zone, lien.zone.document
        zones.append({
            "libelle": z.libelle,
            "libelle_long": z.libelle_long,
            "type_zone": z.type_zone,
            "part_pct": lien.part_pct,
            "surface_m2": lien.surface_intersection_m2,
            "est_dominante": lien.est_dominante,
            "fichier_reglement": z.nom_fichier_reglement,
            "page_reglement": z.page_reglement,
            "document_idurba": doc.idurba,
            "document_type": doc.type_document,
            "date_approbation": doc.date_approbation.isoformat()
                                if doc.date_approbation else None,
        })

    avertissements = construire_avertissements(parcelle, liens)

    return {
        "idu": parcelle.idu,
        "section": parcelle.section,
        "numero": parcelle.numero,
        "contenance_m2": parcelle.contenance_m2,
        "commune": {
            "code_insee": parcelle.commune.code_insee,
            "nom": parcelle.commune.nom,
            "code_departement": parcelle.commune.code_departement,
        },
        "zones": zones,
        "avertissements": avertissements,
        "geometry": json.loads(parcelle.geom.geojson),
    }

def construire_avertissements(parcelle, liens):
    """
    Retourne des codes, pas des phrases : l'interface est trilingue
    et doit pouvoir traduire. Les paramètres permettent d'insérer
    les valeurs variables dans la chaîne traduite.
    """
    messages = []

    if not liens:
        messages.append({"code": "aucun_zonage", "params": {}})
        return messages

    if len(liens) > 1:
        messages.append({
            "code": "multi_zones",
            "params": {
                "nombre": len(liens),
                "detail": ", ".join(
                    f"{l.zone.libelle} ({l.part_pct:.0f} %)" for l in liens
                ),
            },
        })

    if len({l.zone.document_id for l in liens}) > 1:
        messages.append({"code": "documents_multiples", "params": {}})

    if any(l.zone.document.a_doublon_source for l in liens):
        messages.append({"code": "doublon_source", "params": {}})

    types_cc = {"CC01", "CC02", "CC03", "CC99"}
    est_cc = any(l.zone.type_zone in types_cc for l in liens)
    if est_cc:
        messages.append({"code": "carte_communale", "params": {}})

    if any(l.zone.document.type_document == "PSMV" for l in liens):
        messages.append({"code": "psmv", "params": {}})

    if any(not l.zone.page_reglement for l in liens) and not est_cc:
        messages.append({"code": "page_inconnue", "params": {}})

    messages.append({"code": "source_officielle", "params": {}})
    return messages


# ---------- Points d'accès ----------

@api.get("/parcelle/point", response=RechercheOut, summary="Parcelle par coordonnées")
def parcelle_point(request, lon: float, lat: float, force: bool = False):
    """Retrouve la parcelle contenant un point WGS84 et son zonage."""
    try:
        parcelle, _ = services.parcelle_par_point(lon, lat, force=force)
    except services.ErreurSource as exc:
        return {"geocodage": None, "parcelle": None, "message": str(exc)}

    if parcelle is None:
        return {
            "geocodage": None,
            "parcelle": None,
            "message": "Aucune parcelle cadastrale à ces coordonnées.",
        }

    return {
        "geocodage": None,
        "parcelle": serialiser_parcelle(parcelle),
        "message": None,
    }


@api.get("/parcelle/{idu}", response=RechercheOut, summary="Parcelle par identifiant")
def parcelle_idu(request, idu: str, force: bool = False):
    """Retrouve une parcelle par son identifiant cadastral à 14 caractères."""
    try:
        parcelle, _ = services.parcelle_par_idu(idu, force=force)
    except ValueError as exc:
        return {"geocodage": None, "parcelle": None, "message": str(exc)}
    except services.ErreurSource as exc:
        return {"geocodage": None, "parcelle": None, "message": str(exc)}

    if parcelle is None:
        return {
            "geocodage": None,
            "parcelle": None,
            "message": f"Parcelle {idu} introuvable au cadastre.",
        }

    return {
        "geocodage": None,
        "parcelle": serialiser_parcelle(parcelle),
        "message": None,
    }


@api.get("/recherche", response=RechercheOut, summary="Recherche par adresse")
def recherche_adresse(request, adresse: str, code_insee: str = None):
    """Géocode une adresse puis retrouve la parcelle et son zonage."""
    try:
        geo = services.geocoder(adresse, code_insee=code_insee)
    except services.ErreurSource as exc:
        return {"geocodage": None, "parcelle": None, "message": str(exc)}

    if geo is None:
        return {
            "geocodage": None,
            "parcelle": None,
            "message": "Adresse introuvable dans la Base Adresse Nationale.",
        }

    try:
        parcelle, _ = services.parcelle_par_point(geo["lon"], geo["lat"])
    except services.ErreurSource as exc:
        return {"geocodage": geo, "parcelle": None, "message": str(exc)}

    if parcelle is None:
        return {
            "geocodage": geo,
            "parcelle": None,
            "message": "Adresse localisée mais aucune parcelle cadastrale à ce point.",
        }

    sortie = serialiser_parcelle(parcelle)

    if not geo["fiable"]:
        sortie["avertissements"].insert(0, {
            "code": "geocodage_approximatif",
            "params": {"score": round(geo["score"], 2), "precision": geo["precision"]},
        })

    return {"geocodage": geo, "parcelle": sortie, "message": None}