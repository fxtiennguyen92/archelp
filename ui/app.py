"""
Interface de démonstration ArcHelp.

    streamlit run ui/app.py

L'API Django doit tourner en parallèle (python manage.py runserver).
"""

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

API = "http://localhost:8000/api"

# ---------- Traductions ----------
# Les termes juridiques français (PLU, emprise au sol, recul...) sont
# conservés tels quels : ils n'ont pas d'équivalent exact et figurent
# ainsi dans les documents officiels que l'architecte devra manipuler.

T = {
    "fr": {
        "titre": "ArcHelp — Consultation d'urbanisme",
        "sous_titre": "Règles d'urbanisme applicables à une parcelle (région Grand Est)",
        "onglet_adresse": "Par adresse",
        "onglet_idu": "Par référence cadastrale",
        "champ_adresse": "Adresse",
        "aide_adresse": "ex. : 1 place Kléber, Strasbourg",
        "champ_idu": "Identifiant cadastral (14 caractères)",
        "aide_idu": "ex. : 67043000010151",
        "bouton": "Rechercher",
        "recherche": "Recherche en cours…",
        "parcelle": "Parcelle",
        "commune": "Commune",
        "contenance": "Contenance cadastrale",
        "zonage": "Zonage applicable",
        "zone": "Zone",
        "designation": "Désignation",
        "part": "Part",
        "surface": "Surface",
        "document": "Document",
        "reglement": "Règlement",
        "page": "page",
        "non_precisee": "non précisée",
        "dominante": "dominante",
        "avertissements": "Points de vigilance",
        "aucun_resultat": "Aucun résultat",
        "adresse_trouvee": "Adresse localisée",
        "erreur_api": "L'API ne répond pas. Vérifiez que le serveur Django est démarré.",
    },
    "en": {
        "titre": "ArcHelp — Urban planning lookup",
        "sous_titre": "Planning rules applying to a land parcel (Grand Est region)",
        "onglet_adresse": "By address",
        "onglet_idu": "By cadastral reference",
        "champ_adresse": "Address",
        "aide_adresse": "e.g. 1 place Kléber, Strasbourg",
        "champ_idu": "Cadastral identifier (14 characters)",
        "aide_idu": "e.g. 67043000010151",
        "bouton": "Search",
        "recherche": "Searching…",
        "parcelle": "Parcel",
        "commune": "Municipality",
        "contenance": "Registered area",
        "zonage": "Applicable zoning",
        "zone": "Zone",
        "designation": "Designation",
        "part": "Share",
        "surface": "Area",
        "document": "Document",
        "reglement": "Regulation",
        "page": "page",
        "non_precisee": "not specified",
        "dominante": "dominant",
        "avertissements": "Points requiring attention",
        "aucun_resultat": "No result",
        "adresse_trouvee": "Address located",
        "erreur_api": "The API is not responding. Check that the Django server is running.",
    },
    "de": {
        "titre": "ArcHelp — Bauleitplanung-Abfrage",
        "sous_titre": "Für ein Grundstück geltende Bauvorschriften (Region Grand Est)",
        "onglet_adresse": "Nach Adresse",
        "onglet_idu": "Nach Katasterkennung",
        "champ_adresse": "Adresse",
        "aide_adresse": "z. B. 1 place Kléber, Strasbourg",
        "champ_idu": "Katasterkennung (14 Zeichen)",
        "aide_idu": "z. B. 67043000010151",
        "bouton": "Suchen",
        "recherche": "Suche läuft…",
        "parcelle": "Flurstück",
        "commune": "Gemeinde",
        "contenance": "Katasterfläche",
        "zonage": "Geltende Zonierung",
        "zone": "Zone",
        "designation": "Bezeichnung",
        "part": "Anteil",
        "surface": "Fläche",
        "document": "Dokument",
        "reglement": "Vorschrift",
        "page": "Seite",
        "non_precisee": "nicht angegeben",
        "dominante": "überwiegend",
        "avertissements": "Zu beachten",
        "aucun_resultat": "Kein Ergebnis",
        "adresse_trouvee": "Adresse lokalisiert",
        "erreur_api": "Die API antwortet nicht. Prüfen Sie, ob der Django-Server läuft.",
    },
}

AVERTISSEMENTS = {
    "aucun_zonage": {
        "fr": "Aucun zonage trouvé. La commune relève peut-être du Règlement National "
              "d'Urbanisme (RNU), ou son document n'est pas publié sur le Géoportail "
              "de l'Urbanisme.",
        "en": "No zoning found. The municipality may fall under the national planning "
              "regulation (RNU), or its document is not published on the Géoportail "
              "de l'Urbanisme.",
        "de": "Keine Zonierung gefunden. Die Gemeinde unterliegt möglicherweise der "
              "nationalen Bauordnung (RNU), oder ihr Dokument ist nicht im Géoportail "
              "de l'Urbanisme veröffentlicht.",
    },
    "multi_zones": {
        "fr": "Parcelle à cheval sur {nombre} zones : {detail}. Les règles diffèrent "
              "selon la partie du terrain concernée.",
        "en": "The parcel straddles {nombre} zones: {detail}. Rules differ depending "
              "on which part of the land is concerned.",
        "de": "Das Flurstück erstreckt sich über {nombre} Zonen: {detail}. Je nach "
              "betroffenem Teil gelten unterschiedliche Vorschriften.",
    },
    "documents_multiples": {
        "fr": "Les zones proviennent de documents d'urbanisme différents. "
              "Vérification en mairie indispensable.",
        "en": "The zones come from different planning documents. Verification at the "
              "town hall is essential.",
        "de": "Die Zonen stammen aus verschiedenen Bauleitplänen. Eine Prüfung im "
              "Rathaus ist unerlässlich.",
    },
    "doublon_source": {
        "fr": "La source publique contient plusieurs documents opposables pour cette "
              "commune. La règle applicable ne peut être déterminée automatiquement.",
        "en": "The public source contains several enforceable documents for this "
              "municipality. The applicable rule cannot be determined automatically.",
        "de": "Die öffentliche Quelle enthält mehrere rechtsverbindliche Dokumente für "
              "diese Gemeinde. Die geltende Regel lässt sich nicht automatisch bestimmen.",
    },
    "carte_communale": {
        "fr": "Commune couverte par une carte communale : elle ne délimite que les "
              "secteurs constructibles et non constructibles. Hauteur, recul et emprise "
              "au sol relèvent du Règlement National d'Urbanisme.",
        "en": "The municipality is covered by a carte communale, which only delimits "
              "buildable and non-buildable sectors. Height, setback and ground coverage "
              "fall under the national planning regulation (RNU).",
        "de": "Die Gemeinde ist durch eine carte communale abgedeckt, die nur bebaubare "
              "und nicht bebaubare Bereiche abgrenzt. Höhe, Abstand und Grundfläche "
              "richten sich nach der nationalen Bauordnung (RNU).",
    },
    "psmv": {
        "fr": "Parcelle située dans un Plan de Sauvegarde et de Mise en Valeur "
              "(secteur sauvegardé). Les règles patrimoniales priment sur le PLU et "
              "l'avis de l'Architecte des Bâtiments de France est requis.",
        "en": "The parcel lies within a Plan de Sauvegarde et de Mise en Valeur "
              "(heritage conservation area). Heritage rules take precedence over the PLU "
              "and the opinion of the Architecte des Bâtiments de France is required.",
        "de": "Das Flurstück liegt in einem Plan de Sauvegarde et de Mise en Valeur "
              "(Denkmalschutzbereich). Denkmalschutzvorschriften haben Vorrang vor dem "
              "PLU; die Stellungnahme des Architecte des Bâtiments de France ist "
              "erforderlich.",
    },
    "page_inconnue": {
        "fr": "La source ne précise pas la page du règlement pour certaines zones : "
              "consulter le document complet.",
        "en": "The source does not specify the regulation page for some zones: consult "
              "the full document.",
        "de": "Die Quelle gibt für einige Zonen die Seite der Vorschrift nicht an: das "
              "vollständige Dokument einsehen.",
    },
    "geocodage_approximatif": {
        "fr": "Géocodage approximatif (score {score}, précision « {precision} »). "
              "La parcelle identifiée peut ne pas correspondre à l'adresse recherchée.",
        "en": "Approximate geocoding (score {score}, precision \"{precision}\"). "
              "The identified parcel may not match the address searched.",
        "de": "Ungenaue Geokodierung (Score {score}, Genauigkeit „{precision}“). "
              "Das ermittelte Flurstück entspricht möglicherweise nicht der Suchadresse.",
    },
    "source_officielle": {
        "fr": "Données issues du Géoportail de l'Urbanisme et du cadastre IGN. "
              "Un règlement d'urbanisme peut évoluer : confirmer auprès de la mairie "
              "avant toute décision de conception.",
        "en": "Data from the Géoportail de l'Urbanisme and the IGN cadastre. Planning "
              "regulations may change: confirm with the town hall before any design "
              "decision.",
        "de": "Daten aus dem Géoportail de l'Urbanisme und dem IGN-Kataster. "
              "Bauvorschriften können sich ändern: vor jeder Planungsentscheidung beim "
              "Rathaus bestätigen lassen.",
    },
}

COULEURS = {
    "U": "#e05252", "AUc": "#e8a33d", "AUs": "#f0d264",
    "A": "#d9d264", "N": "#6aa84f",
    "CC01": "#e05252", "CC02": "#c27ba0", "CC03": "#6aa84f", "CC99": "#999999",
    "AUTRE": "#8e7cc3",
}


def traduire_avertissement(av, langue):
    modele = AVERTISSEMENTS.get(av["code"], {}).get(langue)
    if not modele:
        return av["code"]
    try:
        return modele.format(**av.get("params", {}))
    except (KeyError, IndexError):
        return modele


def appeler_api(chemin, params=None):
    try:
        reponse = requests.get(f"{API}{chemin}", params=params, timeout=90)
        reponse.raise_for_status()
        return reponse.json()
    except requests.RequestException:
        return None


def dessiner_carte(parcelle):
    coords = parcelle["geometry"]["coordinates"][0][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    centre = [sum(lats) / len(lats), sum(lons) / len(lons)]

    carte = folium.Map(location=centre, zoom_start=18, tiles=None)
    carte.get_root().html.add_child(folium.Element("""
    <style>
      .leaflet-control-attribution {
        font-size: 9px;
        opacity: 0.55;
        background: rgba(255,255,255,0.6) !important;
      }
    </style>
    """))
    folium.TileLayer(
        tiles="https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
              "&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
              "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}"
              "&TILECOL={x}&FORMAT=image/jpeg",
        attr="IGN — Géoplateforme",
        name="Orthophoto IGN",
    ).add_to(carte)
    folium.TileLayer("OpenStreetMap", name="Plan").add_to(carte)

    folium.GeoJson(
        parcelle["geometry"],
        name="Parcelle",
        style_function=lambda _: {
            "fillColor": "#ffffff", "color": "#1f77b4",
            "weight": 3, "fillOpacity": 0.15,
        },
        tooltip=parcelle["idu"],
    ).add_to(carte)

    folium.LayerControl(collapsed=False).add_to(carte)
    return carte


def afficher_resultat(donnees, langue):
    t = T[langue]
    parcelle = donnees.get("parcelle")

    if parcelle is None:
        st.warning(donnees.get("message") or t["aucun_resultat"])
        return

    geo = donnees.get("geocodage")
    if geo:
        st.caption(f"{t['adresse_trouvee']} : {geo['label']}")

    col1, col2, col3 = st.columns(3)
    col1.metric(t["parcelle"], parcelle["idu"])
    col2.metric(t["commune"], parcelle["commune"]["nom"])
    col3.metric(t["contenance"], f"{parcelle['contenance_m2'] or '—'} m²")

    gauche, droite = st.columns([3, 2])

    with gauche:
        st_folium(dessiner_carte(parcelle), height=420, use_container_width=True,
                  key=f"carte_{parcelle['idu']}", returned_objects=[])

    with droite:
        st.subheader(t["zonage"])
        if not parcelle["zones"]:
            st.info(t["aucun_resultat"])
        for z in parcelle["zones"]:
            couleur = COULEURS.get(z["type_zone"], "#888888")
            marque = f" · {t['dominante']}" if z["est_dominante"] else ""
            st.markdown(
                f"<div style='border-left:5px solid {couleur};padding-left:10px;"
                f"margin-bottom:12px'>"
                f"<b>{z['libelle']}</b> — {z['part_pct']:.1f} %{marque}<br>"
                f"<span style='color:#666;font-size:0.9em'>"
                f"{z['libelle_long'] or z['type_zone']}<br>"
                f"{z['surface_m2']:.0f} m² · {z['document_type']} "
                f"{z['date_approbation'] or ''}</span></div>",
                unsafe_allow_html=True,
            )
            if z["fichier_reglement"]:
                page = z["page_reglement"]
                suffixe = f" ({t['page']} {page})" if page else f" ({t['non_precisee']})"
                st.caption(f"📄 {z['fichier_reglement'].split('#')[0]}{suffixe}")

    if parcelle["avertissements"]:
        st.subheader(t["avertissements"])
        for av in parcelle["avertissements"]:
            texte = traduire_avertissement(av, langue)
            if av["code"] in ("source_officielle", "page_inconnue"):
                st.caption(texte)
            else:
                st.warning(texte)


def main():
    st.set_page_config(page_title="ArcHelp", page_icon="🏛", layout="wide")

    if "langue" not in st.session_state:
        st.session_state.langue = "fr"
    if "resultat" not in st.session_state:
        st.session_state.resultat = None
    if "erreur" not in st.session_state:
        st.session_state.erreur = None

    with st.sidebar:
        st.session_state.langue = st.radio(
            "Langue / Language / Sprache",
            options=["fr", "en", "de"],
            format_func=lambda x: {"fr": "Français", "en": "English", "de": "Deutsch"}[x],
            index=["fr", "en", "de"].index(st.session_state.langue),
        )

    langue = st.session_state.langue
    t = T[langue]

    st.title(t["titre"])
    st.caption(t["sous_titre"])

    onglet1, onglet2 = st.tabs([t["onglet_adresse"], t["onglet_idu"]])

    with onglet1:
        adresse = st.text_input(t["champ_adresse"], placeholder=t["aide_adresse"],
                                key="saisie_adresse")
        if st.button(t["bouton"], key="btn_adresse", type="primary") and adresse:
            with st.spinner(t["recherche"]):
                donnees = appeler_api("/recherche", {"adresse": adresse})
            # Le résultat est mémorisé : Streamlit réexécute tout le script
            # à chaque interaction, y compris au rendu de la carte.
            st.session_state.resultat = donnees
            st.session_state.erreur = None if donnees else t["erreur_api"]

    with onglet2:
        idu = st.text_input(t["champ_idu"], placeholder=t["aide_idu"],
                            max_chars=14, key="saisie_idu")
        if st.button(t["bouton"], key="btn_idu", type="primary") and idu:
            with st.spinner(t["recherche"]):
                donnees = appeler_api(f"/parcelle/{idu.strip()}")
            st.session_state.resultat = donnees
            st.session_state.erreur = None if donnees else t["erreur_api"]

    if st.session_state.erreur:
        st.error(st.session_state.erreur)
    elif st.session_state.resultat:
        afficher_resultat(st.session_state.resultat, langue)


if __name__ == "__main__":
    main()