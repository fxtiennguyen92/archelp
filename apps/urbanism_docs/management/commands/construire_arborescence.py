"""
Construit l'arborescence SRU niveau 1 (ReglementSection) à partir de
ReglementDocument.extraction_brute. N'appelle aucun modèle : rejouable
à volonté, chaque passage remplace l'arborescence précédente.

    python manage.py construire_arborescence --titre 246700488_reglement_20260206.pdf
    python manage.py construire_arborescence --tout
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# Le modèle a produit une soixantaine de libellés de type ; 98,5 % relèvent
# de quatre catégories. Le reste est ramené à la plus proche ou écarté.
TYPES = {
    "TITRE": "TITRE", "VOLET": "TITRE", "ANNEXE": "TITRE", "ANNEXES": "TITRE",
    "LEXIQUE": "TITRE", "DISPOSITIONS": "TITRE",
    "CHAPITRE": "CHAPITRE", "CHAP": "CHAPITRE", "ZONE": "CHAPITRE",
    "SECTION": "SECTION", "SOUS-SECTION": "SECTION", "SOUS_SECTION": "SECTION",
    "SUBSECTION": "SECTION", "SUBSUBSECTION": "SECTION",
    "ARTICLE": "ARTICLE", "SUBARTICLE": "ARTICLE",
    "PARAGRAPHE": "PARAGRAPHE",
    "ALI": "ALINEA", "ALINEA": "ALINEA",
}

# Rang hiérarchique : la profondeur se déduit de l'enchaînement des types,
# plus fiable que le champ "profondeur" renvoyé par le modèle.
RANGS = {"TITRE": 0, "CHAPITRE": 1, "SECTION": 2, "ARTICLE": 3,
         "PARAGRAPHE": 4, "ALINEA": 5}


def canon(libelle):
    """
    Forme comparable d'un libellé : sans séparateurs, en majuscules, avec
    la numérotation romaine ramenée à la numérotation CNIG (IAU → 1AU).
    « IAUX.zaa » du GPU et « IAUXZAA » du règlement désignent la même zone.
    """
    s = re.sub(r"[\s.\-_]", "", str(libelle)).upper()
    s = re.sub(r"^IIAU", "2AU", s)
    s = re.sub(r"^IAU", "1AU", s)
    return s


def zone_mere(libelle):
    """
    Zone de rattachement d'un secteur : partie en majuscules avant le premier
    séparateur ou la première minuscule, sans l'indice numérique final.
    UDz3 → UD, UB1 → UB, Ni → N, 1AUm → 1AU, IAUX.zaa → 1AUX.
    AU reste AU : « A » ne l'absorbe donc pas.
    """
    tete = re.match(r"[A-Z0-9]+", str(libelle).strip())
    if not tete:
        return ""
    return re.sub(r"\d+$", "", canon(tete.group(0))) or canon(tete.group(0))


def rattacher_zones(libelles_fragment, libelles_document):
    retenus = set()
    for lib in libelles_fragment:
        c = canon(lib)
        if not c:
            continue
        for doc in libelles_document:
            cd = canon(doc)
            mere = zone_mere(doc)
            if (c == cd or c == mere or c == re.sub(r"\d+$", "", cd)
                    or c == re.sub(r"^\d", "", mere)):
                retenus.add(doc)
    return retenus


class Command(BaseCommand):
    help = "Construit l'arborescence des règlements depuis l'extraction brute"

    def add_arguments(self, parser):
        parser.add_argument("--titre", help="Un seul fichier")
        parser.add_argument("--tout", action="store_true")

    def handle(self, *args, **options):
        from apps.urbanism_docs.models import ReglementDocument

        qs = ReglementDocument.objects.exclude(extraction_brute__isnull=True)
        if options.get("titre"):
            qs = qs.filter(titre=options["titre"])
        elif not options["tout"]:
            raise CommandError("Précisez --titre ou --tout.")

        stats = {"docs": 0, "sections": 0, "ecartes": 0, "sans_page": 0, "liens_zone": 0}
        for regl in qs.select_related("document").iterator():
            d = regl.extraction_brute or {}
            if not d.get("fragments"):
                continue
            n, e, sp, lz = self._construire(regl, d)
            stats["docs"] += 1
            stats["sections"] += n
            stats["ecartes"] += e
            stats["sans_page"] += sp
            stats["liens_zone"] += lz
            if stats["docs"] % 100 == 0:
                self.stdout.write(f"  {stats['docs']} documents…")

        self.stdout.write(self.style.SUCCESS(
            f"{stats['docs']} documents · {stats['sections']} sections · "
            f"{stats['ecartes']} fragments écartés · "
            f"{stats['sans_page']} sans page fiable · {stats['liens_zone']} liens zone"
        ))

    @transaction.atomic
    def _construire(self, regl, d):
        from apps.urbanism_docs.models import ReglementSection

        libelles_doc = set(regl.document.zones.values_list("libelle", flat=True))
        zones_par_libelle = {
            z.libelle: z.id for z in regl.document.zones.only("id", "libelle")
        }

        # Sommaire : la page n'est fiable que si le décalage a été appliqué
        # (le fragment porte alors sa page imprimée d'origine). Sinon on
        # préfère ne pas afficher de page plutôt qu'une page fausse.
        source = d.get("source")

        retenus, ecartes = [], 0
        for f in d["fragments"]:
            if not isinstance(f, dict) or not f.get("titre"):
                ecartes += 1
                continue
            t = TYPES.get(str(f.get("type", "")).strip().upper())
            if t is None:
                ecartes += 1
                continue
            page = f.get("page") if isinstance(f.get("page"), int) else None
            if source == "sommaire" and f.get("page_imprimee") is None:
                page = None
            retenus.append((t, f, page))

        regl.sections.all().delete()

        objets, sans_page = [], 0
        pile = []  # (rang, index dans objets, compteur d'enfants)
        compteurs_racine = 0
        parents = []

        for t, f, page in retenus:
            rang = RANGS[t]
            while pile and pile[-1][0] >= rang:
                pile.pop()

            if pile:
                parent_idx = pile[-1][1]
                pile[-1][2] += 1
                ordre = pile[-1][2]
                chemin = f"{objets[parent_idx].chemin}.{ordre:04d}"
            else:
                parent_idx = None
                compteurs_racine += 1
                ordre = compteurs_racine
                chemin = f"{ordre:04d}"

            if page is None:
                sans_page += 1

            objets.append(ReglementSection(
                reglement=regl,
                type_fragment=t,
                profondeur=len(pile),
                ordre=ordre,
                chemin=chemin[:200],
                numero=str(f.get("numero") or "")[:50],
                titre=str(f["titre"])[:500],
                page_debut=page,
            ))
            parents.append(parent_idx)
            pile.append([rang, len(objets) - 1, 0])

        # page_fin : juste avant le fragment suivant de même niveau ou supérieur.
        for i, obj in enumerate(objets):
            if obj.page_debut is None:
                continue
            for j in range(i + 1, len(objets)):
                if objets[j].profondeur <= obj.profondeur and objets[j].page_debut:
                    obj.page_fin = max(obj.page_debut, objets[j].page_debut)
                    break

        ReglementSection.objects.bulk_create(objets, batch_size=1000)

        a_relier = []
        for obj, p in zip(objets, parents):
            if p is not None:
                obj.parent_id = objets[p].id
                a_relier.append(obj)
        ReglementSection.objects.bulk_update(a_relier, ["parent"], batch_size=1000)

        # Zones : celles du fragment, sinon héritées du parent.
        Lien = ReglementSection.zones.through
        liens, zones_de = [], {}
        for idx, ((t, f, page), obj) in enumerate(zip(retenus, objets)):
            libs = rattacher_zones(f.get("zones") or [], libelles_doc)
            if not libs and parents[idx] is not None:
                libs = zones_de.get(parents[idx], set())
            zones_de[idx] = libs
            for lib in libs:
                liens.append(Lien(reglementsection_id=obj.id,
                                  zone_id=zones_par_libelle[lib]))
        Lien.objects.bulk_create(liens, batch_size=2000, ignore_conflicts=True)

        return len(objets), ecartes, sans_page, len(liens)