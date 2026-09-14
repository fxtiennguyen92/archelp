"""
Interface de démonstration ArcHelp.

    streamlit run ui/app.py

L'API Django doit tourner en parallèle (python manage.py runserver).
"""

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# L'interface appelle l'API en local : elle tourne sur la même machine,
# et passer par le domaine public la ferait bloquer par Cloudflare Access.
BASE = os.environ.get("ARCHELP_API_BASE") or "http://localhost:8000"
API = f"{BASE}/api"

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
        "ouvrir_pdf": "Ouvrir le règlement",
        "page_precise": "page {page} sur {total}",
        "page_inconnue_court": "page non précisée",
        "pdf_absent": "PDF non encore récupéré",
        "prescriptions": "Prescriptions graphiques",
        "impact_fort": "Contraintes chiffrées",
        "impact_procedure": "Autorisations et programme",
        "impact_contexte": "Contexte réglementaire",
        "aucune_prescription": "Aucune prescription graphique relevée",
        "mesure_HT": "hors tout",
        "mesure_ET": "à l'égout de toiture",
        "servitudes": "Servitudes d'utilité publique",
        "servitude_nombre": "{n} périmètre(s)",
        "abf_requis": "avis ABF requis",
        "voir_detail": "Détail",
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
        "ouvrir_pdf": "Open the regulation",
        "page_precise": "page {page} of {total}",
        "page_inconnue_court": "page not specified",
        "pdf_absent": "PDF not yet retrieved",
        "prescriptions": "Graphic prescriptions",
        "impact_fort": "Quantified constraints",
        "impact_procedure": "Permits and programme",
        "impact_contexte": "Regulatory context",
        "aucune_prescription": "No graphic prescription found",
        "mesure_HT": "overall height",
        "mesure_ET": "to the eaves",
        "servitudes": "Servitudes d'utilité publique (public easements)",
        "servitude_nombre": "{n} perimeter(s)",
        "abf_requis": "ABF opinion required",
        "voir_detail": "Details",
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
        "ouvrir_pdf": "Vorschrift öffnen",
        "page_precise": "Seite {page} von {total}",
        "page_inconnue_court": "Seite nicht angegeben",
        "pdf_absent": "PDF noch nicht abgerufen",
        "prescriptions": "Zeichnerische Festsetzungen",
        "impact_fort": "Bezifferte Auflagen",
        "impact_procedure": "Genehmigungen und Programm",
        "impact_contexte": "Rechtlicher Kontext",
        "aucune_prescription": "Keine zeichnerische Festsetzung gefunden",
        "mesure_HT": "Gesamthöhe",
        "mesure_ET": "bis zur Traufe",
        "servitudes": "Servitudes d'utilité publique (öffentliche Baulasten)",
        "servitude_nombre": "{n} Perimeter",
        "abf_requis": "ABF-Stellungnahme erforderlich",
        "voir_detail": "Einzelheiten",
    },
    "vi": {
        "titre": "ArcHelp — Tra cứu quy hoạch",
        "sous_titre": "Quy định quy hoạch áp dụng cho một thửa đất (vùng Grand Est)",
        "onglet_adresse": "Theo địa chỉ",
        "onglet_idu": "Theo số thửa",
        "champ_adresse": "Địa chỉ",
        "aide_adresse": "ví dụ: 1 place Kléber, Strasbourg",
        "champ_idu": "Số thửa cadastre (14 ký tự)",
        "aide_idu": "ví dụ: 67043000010151",
        "bouton": "Tra cứu",
        "recherche": "Đang tra cứu…",
        "parcelle": "Thửa đất",
        "commune": "Commune",
        "contenance": "Diện tích cadastre",
        "zonage": "Zonage áp dụng",
        "zone": "Zone",
        "designation": "Mô tả",
        "part": "Tỷ lệ",
        "surface": "Diện tích",
        "document": "Tài liệu",
        "reglement": "Règlement",
        "page": "trang",
        "non_precisee": "không rõ trang",
        "dominante": "chiếm ưu thế",
        "avertissements": "Điểm cần lưu ý",
        "aucun_resultat": "Không có kết quả",
        "adresse_trouvee": "Địa chỉ xác định được",
        "erreur_api": "API không phản hồi. Kiểm tra xem server Django đã chạy chưa.",
        "ouvrir_pdf": "Mở règlement",
        "page_precise": "trang {page} / {total}",
        "page_inconnue_court": "không rõ trang",
        "pdf_absent": "Chưa tải được PDF",
        "prescriptions": "Prescriptions trên bản đồ quy hoạch",
        "impact_fort": "Ràng buộc có số liệu",
        "impact_procedure": "Thủ tục và chương trình",
        "impact_contexte": "Bối cảnh pháp lý",
        "aucune_prescription": "Không có prescription nào",
        "mesure_HT": "hors tout — tới điểm cao nhất",
        "mesure_ET": "à l'égout — tới mép mái",
        "servitudes": "Servitudes d'utilité publique (hạn chế sử dụng đất)",
        "servitude_nombre": "{n} périmètre",
        "abf_requis": "cần ý kiến ABF",
        "voir_detail": "Chi tiết",
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
        "vi": "Không tìm thấy zonage. Commune có thể thuộc diện áp dụng Règlement "
              "National d'Urbanisme (RNU), hoặc tài liệu chưa được đăng lên "
              "Géoportail de l'Urbanisme.",
    },
    "multi_zones": {
        "fr": "Parcelle à cheval sur {nombre} zones : {detail}. Les règles diffèrent "
              "selon la partie du terrain concernée.",
        "en": "The parcel straddles {nombre} zones: {detail}. Rules differ depending "
              "on which part of the land is concerned.",
        "de": "Das Flurstück erstreckt sich über {nombre} Zonen: {detail}. Je nach "
              "betroffenem Teil gelten unterschiedliche Vorschriften.",
        "vi": "Thửa đất nằm trên {nombre} zone: {detail}. Quy định khác nhau tùy "
              "phần đất.",
    },
    "documents_multiples": {
        "fr": "Les zones proviennent de documents d'urbanisme différents. "
              "Vérification en mairie indispensable.",
        "en": "The zones come from different planning documents. Verification at the "
              "town hall is essential.",
        "de": "Die Zonen stammen aus verschiedenen Bauleitplänen. Eine Prüfung im "
              "Rathaus ist unerlässlich.",
        "vi": "Các zone thuộc những tài liệu quy hoạch khác nhau. Bắt buộc phải "
              "xác minh tại mairie.",
    },
    "doublon_source": {
        "fr": "La source publique contient plusieurs documents opposables pour cette "
              "commune. La règle applicable ne peut être déterminée automatiquement.",
        "en": "The public source contains several enforceable documents for this "
              "municipality. The applicable rule cannot be determined automatically.",
        "de": "Die öffentliche Quelle enthält mehrere rechtsverbindliche Dokumente für "
              "diese Gemeinde. Die geltende Regel lässt sich nicht automatisch bestimmen.",
        "vi": "Nguồn dữ liệu công có nhiều tài liệu cùng hiệu lực cho commune này. "
              "Không thể tự động xác định quy định nào áp dụng.",
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
        "vi": "Commune áp dụng carte communale — chỉ phân định khu vực được xây và "
              "không được xây. Chiều cao, khoảng lùi và emprise au sol theo "
              "Règlement National d'Urbanisme (RNU).",
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
        "vi": "Thửa đất nằm trong Plan de Sauvegarde et de Mise en Valeur (khu bảo "
              "tồn). Quy định về di sản ưu tiên hơn PLU, và bắt buộc có ý kiến của "
              "Architecte des Bâtiments de France (ABF).",
    },
    "page_inconnue": {
        "fr": "La source ne précise pas la page du règlement pour certaines zones : "
              "consulter le document complet.",
        "en": "The source does not specify the regulation page for some zones: consult "
              "the full document.",
        "de": "Die Quelle gibt für einige Zonen die Seite der Vorschrift nicht an: das "
              "vollständige Dokument einsehen.",
        "vi": "Nguồn dữ liệu không ghi rõ trang règlement cho một số zone: cần đọc "
              "toàn bộ tài liệu.",
    },
    "geocodage_approximatif": {
        "fr": "Géocodage approximatif (score {score}, précision « {precision} »). "
              "La parcelle identifiée peut ne pas correspondre à l'adresse recherchée.",
        "en": "Approximate geocoding (score {score}, precision \"{precision}\"). "
              "The identified parcel may not match the address searched.",
        "de": "Ungenaue Geokodierung (Score {score}, Genauigkeit „{precision}“). "
              "Das ermittelte Flurstück entspricht möglicherweise nicht der Suchadresse.",
        "vi": "Định vị địa chỉ không chính xác (điểm {score}, độ chính xác "
              "« {precision} »). Thửa đất tìm được có thể không đúng địa chỉ cần tra.",
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
        "vi": "Dữ liệu từ Géoportail de l'Urbanisme và cadastre IGN. Règlement có "
              "thể thay đổi: cần xác nhận với mairie trước khi quyết định thiết kế.",
    },
    "prescriptions_fortes": {
        "fr": "{nombre} prescription(s) graphique(s) affectent directement la "
              "constructibilité du terrain — voir le tableau ci-dessous.",
        "en": "{nombre} graphic prescription(s) directly affect what can be built "
              "on this land — see the table below.",
        "de": "{nombre} zeichnerische Festsetzung(en) wirken sich unmittelbar auf "
              "die Bebaubarkeit aus — siehe Tabelle unten.",
        "vi": "{nombre} prescription trên bản đồ quy hoạch ảnh hưởng trực tiếp tới "
              "khả năng xây dựng — xem bảng bên dưới.",
    },
    "servitude_abf": {
        "fr": "Parcelle grevée d'une servitude patrimoniale : l'avis de "
              "l'Architecte des Bâtiments de France est requis pour tout projet.",
        "en": "The parcel is subject to a heritage easement: the opinion of the "
              "Architecte des Bâtiments de France is required for any project.",
        "de": "Das Flurstück unterliegt einer Denkmalschutzdienstbarkeit: für jedes "
              "Vorhaben ist die Stellungnahme des Architecte des Bâtiments de France "
              "erforderlich.",
        "vi": "Thửa đất chịu servitude về di sản: mọi dự án đều cần ý kiến của "
              "Architecte des Bâtiments de France (ABF).",
    },
    "servitude_risque": {
        "fr": "Parcelle concernée par un plan de prévention des risques : des "
              "prescriptions de construction spécifiques peuvent s'appliquer, voire "
              "une inconstructibilité.",
        "en": "The parcel falls within a risk prevention plan: specific building "
              "requirements may apply, or the land may be unbuildable.",
        "de": "Das Flurstück liegt in einem Risikovorsorgeplan: besondere "
              "Bauauflagen oder ein Bauverbot können gelten.",
        "vi": "Thửa đất nằm trong plan de prévention des risques: có thể có yêu cầu "
              "xây dựng riêng, hoặc không được phép xây.",
    },
    "doublon_patrimoine": {
        "fr": "Une même protection patrimoniale peut apparaître à la fois dans les "
              "prescriptions graphiques du PLU et dans les servitudes d'utilité "
              "publique. Les deux sont conservées : leurs fondements juridiques "
              "diffèrent et les actes de référence ne sont pas les mêmes.",
        "en": "The same heritage protection may appear both in the PLU's graphic "
              "prescriptions and in the public easements. Both are kept: their legal "
              "bases differ and the reference documents are not the same.",
        "de": "Derselbe Denkmalschutz kann sowohl in den zeichnerischen Festsetzungen "
              "des PLU als auch in den öffentlichen Baulasten erscheinen. Beide "
              "bleiben erhalten: ihre Rechtsgrundlagen und Bezugsakte unterscheiden "
              "sich.",
        "vi": "Cùng một quy định bảo vệ di sản có thể xuất hiện ở cả prescription "
              "của PLU lẫn servitude d'utilité publique. Cả hai đều được giữ: căn cứ "
              "pháp lý khác nhau và văn bản tham chiếu cũng khác.",
    },
}

COULEURS = {
    "U": "#e05252", "AUc": "#e8a33d", "AUs": "#f0d264",
    "A": "#d9d264", "N": "#6aa84f",
    "CC01": "#e05252", "CC02": "#c27ba0", "CC03": "#6aa84f", "CC99": "#999999",
    "AUTRE": "#8e7cc3",
}

COULEURS_IMPACT = {
    "fort": "#c0392b",
    "procedure": "#d68910",
    "contexte": "#7f8c8d",
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


def dessiner_carte(parcelle, t):
    coords = parcelle["geometry"]["coordinates"][0][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    centre = [sum(lats) / len(lats), sum(lons) / len(lons)]

    carte = folium.Map(location=centre, zoom_start=18, tiles=None)
    folium.TileLayer(
        tiles="https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
              "&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
              "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}"
              "&TILECOL={x}&FORMAT=image/jpeg",
        attr="IGN — Géoplateforme",
        name="Orthophoto IGN",
    ).add_to(carte)
    folium.TileLayer(
        tiles="https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
              "&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
              "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}"
              "&TILECOL={x}&FORMAT=image/png",
        attr="IGN — Géoplateforme",
        name="Plan IGN",
    ).add_to(carte)

    # Zonage : une couche par zone, colorée selon le type CNIG.
    groupe_zones = folium.FeatureGroup(name=t["zonage"], show=True)
    for z in parcelle.get("zones", []):
        if not z.get("geometry"):
            continue
        couleur = COULEURS.get(z["type_zone"], "#888888")
        folium.GeoJson(
            z["geometry"],
            style_function=lambda _, c=couleur: {
                "fillColor": c, "color": c,
                "weight": 1, "fillOpacity": 0.45,
            },
            tooltip=f"{z['libelle']} — {z['part_pct']:.1f} %",
        ).add_to(groupe_zones)
    groupe_zones.add_to(carte)

    # Prescriptions à impact fort : espaces à conserver, emplacements réservés.
    # Les autres couvrent souvent toute la parcelle et masqueraient le zonage.
    fortes = [
        p for p in parcelle.get("prescriptions", [])
        if p["niveau_impact"] == "fort" and p.get("geometry")
        and p["part_pct"] < 100
    ]
    if fortes:
        groupe_psc = folium.FeatureGroup(name=t["impact_fort"], show=True)
        for p in fortes:
            folium.GeoJson(
                p["geometry"],
                style_function=lambda _: {
                    "fillColor": "#c0392b", "color": "#c0392b",
                    "weight": 2, "fillOpacity": 0.35,
                    "dashArray": "5, 5",
                },
                tooltip=f"{p['libelle']} — {p['part_pct']:.1f} %",
            ).add_to(groupe_psc)
        groupe_psc.add_to(carte)

    # La parcelle par-dessus, sans remplissage : c'est le repère principal.
    folium.GeoJson(
        parcelle["geometry"],
        name=t["parcelle"],
        style_function=lambda _: {
            "fillColor": "#ffffff", "color": "#1f77b4",
            "weight": 3, "fillOpacity": 0,
        },
        tooltip=parcelle["idu"],
    ).add_to(carte)

    folium.LayerControl(collapsed=False).add_to(carte)
    return carte


def afficher_resultat(donnees, langue):
    t = T[langue]
    parcelle = donnees.get("parcelle")

    prescriptions = parcelle.get("prescriptions") or []
    if prescriptions:
        st.subheader(t["prescriptions"])
        for niveau in ("fort", "procedure", "contexte"):
            groupe = [p for p in prescriptions if p["niveau_impact"] == niveau]
            if not groupe:
                continue

            titre = t[f"impact_{niveau}"]
            # Le contexte est replié : il compte souvent le plus de lignes
            # alors qu'il change rarement la conception.
            conteneur = st.expander(f"{titre} ({len(groupe)})",
                                    expanded=(niveau != "contexte"))
            with conteneur:
                for p in groupe:
                    couleur = COULEURS_IMPACT[niveau]
                    valeur = ""
                    if p["valeur"] is not None:
                        mesure = p["reference_mesure"]
                        suffixe = f" — {t['mesure_' + mesure]}" if mesure in ("HT", "ET") else ""
                        valeur = (
                            f"<br><b style='font-size:1.15em'>"
                            f"{p['valeur']:g} {p['unite']}</b>"
                            f"<span style='color:#666'>{suffixe}</span>"
                        )
                    couverture = (
                        "" if p["part_pct"] >= 100
                        else f" · {p['part_pct']:.0f} % ({p['surface_m2']:.0f} m²)"
                    )

                    nom = p["libelle"]
                    nom_court = nom if len(nom) <= 70 else nom[:67] + "…"

                    st.markdown(
                        f"<div style='border-left:4px solid {couleur};"
                        f"padding-left:10px;margin-bottom:10px' "
                        f"title='{nom}'>"
                        f"{nom_court}"
                        f"<span style='color:#888;font-size:0.85em'>{couverture}</span>"
                        f"{valeur}</div>",
                        unsafe_allow_html=True,
                    )
    
    if parcelle is None:
        st.warning(donnees.get("message") or t["aucun_resultat"])
        return

    servitudes = parcelle.get("servitudes") or []
    if servitudes:
        st.subheader(t["servitudes"])
        for s in servitudes:
            couleur = "#8e44ad" if s["requiert_abf"] else "#7f8c8d"
            badge = f" · <b>{t['abf_requis']}</b>" if s["requiert_abf"] else ""
            couverture = "" if s["part_pct_max"] >= 100 else f" · {s['part_pct_max']:.0f} %"
            st.markdown(
                f"<div style='border-left:4px solid {couleur};padding-left:10px;"
                f"margin-bottom:8px'>"
                f"<b>{s['sup_type']}</b> — {s['categorie']}"
                f"<span style='color:#888;font-size:0.85em'>{couverture}</span>"
                f"{badge}</div>",
                unsafe_allow_html=True,
            )
            if s["nombre"] > 1:
                with st.expander(
                    f"{t['voir_detail']} — {t['servitude_nombre'].format(n=s['nombre'])}"
                ):
                    for d in s["details"]:
                        st.caption(f"{d['nom']} — {d['part_pct']:.1f} %")

    geo = donnees.get("geocodage")
    if geo:
        st.caption(f"{t['adresse_trouvee']} : {geo['label']}")

    col1, col2, col3 = st.columns(3)
    col1.metric(t["parcelle"], parcelle["idu"])
    col2.metric(t["commune"], parcelle["commune"]["nom"])
    col3.metric(t["contenance"], f"{parcelle['contenance_m2'] or '—'} m²")

    gauche, droite = st.columns([3, 2])

    with gauche:
        st_folium(dessiner_carte(parcelle, t), height=420, use_container_width=True,
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
            url = z.get("url_reglement")
            if url:
                total = z.get("reglement_nb_pages")
                page = z.get("page_reglement")
                if page and total:
                    detail = t["page_precise"].format(page=page, total=total)
                elif total:
                    detail = f"{total} p. · {t['page_inconnue_court']}"
                else:
                    detail = t["page_inconnue_court"]
                st.link_button(
                    f"📄 {t['ouvrir_pdf']} — {detail}",
                    url if url.startswith("http") else f"{BASE}{url}",
                    use_container_width=True,
                )
            elif z["fichier_reglement"]:
                st.caption(f"📄 {z['fichier_reglement'].split('#')[0]} — {t['pdf_absent']}")

    if parcelle["avertissements"]:
        st.subheader(t["avertissements"])
        for av in parcelle["avertissements"]:
            texte = traduire_avertissement(av, langue)
            if av["code"] in ("source_officielle", "page_inconnue"):
                st.caption(texte)
            else:
                st.warning(texte)

def rechercher_adresse():
    adresse = st.session_state.get("saisie_adresse", "").strip()
    if not adresse:
        return
    donnees = appeler_api("/recherche", {"adresse": adresse})
    st.session_state.resultat = donnees
    st.session_state.erreur = None if donnees else "api"


def rechercher_idu():
    idu = st.session_state.get("saisie_idu", "").strip()
    if not idu:
        return
    donnees = appeler_api(f"/parcelle/{idu}")
    st.session_state.resultat = donnees
    st.session_state.erreur = None if donnees else "api"


def main():
    st.set_page_config(page_title="ArcHelp", page_icon="🏛", layout="wide")

    if "langue" not in st.session_state:
        st.session_state.langue = "fr"
    if "resultat" not in st.session_state:
        st.session_state.resultat = None
    if "erreur" not in st.session_state:
        st.session_state.erreur = None

    LANGUES = ["fr", "en", "de", "vi"]
    NOMS = {"fr": "Français", "en": "English", "de": "Deutsch", "vi": "Tiếng Việt"}

    with st.sidebar:
        # Le widget gère lui-même son état via `key` : réaffecter
        # st.session_state ici ferait perdre le premier clic.
        st.radio(
            "Langue / Language / Sprache / Ngôn ngữ",
            options=LANGUES,
            format_func=lambda x: NOMS[x],
            key="langue",
        )

    langue = st.session_state.langue
    t = T[langue]

    st.title(t["titre"])
    st.caption(t["sous_titre"])

    onglet1, onglet2 = st.tabs([t["onglet_adresse"], t["onglet_idu"]])

    with onglet1:
        st.text_input(
            t["champ_adresse"],
            placeholder=t["aide_adresse"],
            key="saisie_adresse",
            on_change=rechercher_adresse,
        )
        if st.button(t["bouton"], key="btn_adresse", type="primary"):
            rechercher_adresse()

    with onglet2:
        st.text_input(
            t["champ_idu"],
            placeholder=t["aide_idu"],
            max_chars=14,
            key="saisie_idu",
            on_change=rechercher_idu,
        )
        if st.button(t["bouton"], key="btn_idu", type="primary"):
            rechercher_idu()

    if st.session_state.erreur == "api":
        st.error(t["erreur_api"])
    elif st.session_state.resultat:
        afficher_resultat(st.session_state.resultat, langue)


if __name__ == "__main__":
    main()