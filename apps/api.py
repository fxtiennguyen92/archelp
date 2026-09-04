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


class ParcelleOut(Schema):
    idu: str
    section: str
    numero: str
    contenance_m2: Optional[int]
    commune: CommuneOut
    zones: List[ZoneOut]
    avertissements: List[str]
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
    Les données publiques comportent des incohérences connues ; les taire
    reviendrait à donner une fausse impression de certitude sur un sujet
    à portée juridique.
    """
    messages = []

    if not liens:
        messages.append(
            "Aucun zonage trouvé pour cette parcelle. La commune relève "
            "peut-être du Règlement National d'Urbanisme, ou son document "
            "n'est pas encore publié sur le Géoportail de l'Urbanisme."
        )
        return messages

    if len(liens) > 1:
        libelles = ", ".join(f"{l.zone.libelle} ({l.part_pct:.0f} %)" for l in liens)
        messages.append(
            f"Parcelle à cheval sur {len(liens)} zones : {libelles}. "
            "Les règles diffèrent selon la partie concernée du terrain."
        )

    documents = {l.zone.document_id for l in liens}
    if len(documents) > 1:
        messages.append(
            "Les zones proviennent de documents d'urbanisme différents. "
            "Vérification en mairie indispensable."
        )

    if any(l.zone.document.a_doublon_source for l in liens):
        messages.append(
            "La source publique contient plusieurs documents opposables pour "
            "cette commune. La règle applicable ne peut être déterminée "
            "automatiquement."
        )

    types_cc = {"CC01", "CC02", "CC03", "CC99"}
    if any(l.zone.type_zone in types_cc for l in liens):
        messages.append(
            "Commune couverte par une carte communale : elle ne délimite que "
            "les secteurs constructibles et non constructibles. Les règles de "
            "hauteur, de recul et d'emprise relèvent du Règlement National "
            "d'Urbanisme."
        )

    sans_reglement = [l for l in liens if not l.zone.page_reglement]
    if sans_reglement and not any(l.zone.type_zone in types_cc for l in liens):
        messages.append(
            "La source ne précise pas la page du règlement pour certaines "
            "zones : le document complet doit être consulté."
        )
    
    if any(l.zone.document.type_document == "PSMV" for l in liens):
        messages.append(
            "Parcelle située dans le périmètre d'un Plan de Sauvegarde et de "
            "Mise en Valeur (secteur sauvegardé). Les règles patrimoniales "
            "priment sur le PLU et l'avis de l'Architecte des Bâtiments de "
            "France est requis."
        )

    messages.append(
        "Données issues du Géoportail de l'Urbanisme et du cadastre IGN. "
        "Un règlement d'urbanisme peut évoluer : confirmer auprès de la "
        "mairie avant toute décision de conception."
    )

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
        sortie["avertissements"].insert(0, (
            f"Géocodage approximatif (score {geo['score']:.2f}, "
            f"précision « {geo['precision']} »). La parcelle identifiée "
            "peut ne pas correspondre à l'adresse recherchée."
        ))

    return {"geocodage": geo, "parcelle": sortie, "message": None}