"""
Extraction de la structure SRU niveau 1 par lots, avec un modèle local.

    python manage.py structurer_reglements --region 44 --duree-max 420
    python manage.py structurer_reglements --region 44 --limit 10
    python manage.py structurer_reglements --region 44 --reprendre

Le résultat brut est stocké dans ReglementDocument.extraction_brute ;
l'arborescence se construit ensuite, sans rappeler le modèle.
"""

import json
import re
import time
import unicodedata

import pdfplumber
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

TIMEOUT = 300
PAGES_PAR_LOT = 4
CHEVAUCHEMENT = 1
PAGES_SOMMAIRE_MAX = 16

INSTRUCTION = """Tu analyses le règlement écrit d'un PLU français.

Tâche : restituer la structure hiérarchique du document. Rien d'autre.
N'extrais AUCUNE valeur réglementaire (hauteur, emprise, recul).

Réponds uniquement par un objet JSON :
{
  "source": "sommaire" | "corps",
  "fragments": [
    {
      "type": "TITRE|CHAPITRE|SECTION|ARTICLE",
      "numero": "<ex: UB 10, 1, 2.3 — chaîne vide si absent>",
      "titre": "<intitulé exact, jamais vide>",
      "zones": ["<libellés de zone, ex: UB, UB1 — vide si général>"],
      "page": <numéro de la PAGE PDF, jamais null>,
      "profondeur": <0 à 3 uniquement>
    }
  ]
}

Règles strictes :
- Chaque bloc commence par « === PAGE PDF N === ». Le champ "page" vaut ce N.
  Ne recopie jamais un numéro de page imprimé dans le texte.
- "profondeur" ne dépasse jamais 3 : 0=TITRE, 1=CHAPITRE, 2=SECTION, 3=ARTICLE.
- Ignore la page de garde : commune, date, logo, bureau d'études.
- Une énumération de secteurs dans un paragraphe n'est PAS une hiérarchie.
- Si un intitulé de zone figure en en-tête de page, reporte-le dans "zones".
- N'invente rien. Dans le doute, ne produis pas le fragment.
"""


def normaliser(texte):
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class Command(BaseCommand):
    help = "Extrait la structure des règlements avec un modèle local"

    def add_arguments(self, parser):
        parser.add_argument("--region", help="Code région, ex: 44")
        parser.add_argument("--titre", help="Un seul fichier, pour essai")
        parser.add_argument("--limit", type=int, help="Nombre de documents")
        parser.add_argument(
            "--duree-max", type=int,
            help="Minutes au-delà desquelles s'arrêter proprement",
        )
        parser.add_argument(
            "--modele", default=None, help="Modèle Ollama à utiliser",
        )
        parser.add_argument(
            "--refaire", action="store_true",
            help="Retraiter même les documents déjà structurés",
        )
        parser.add_argument(
            "--avec-corps", action="store_true",
            help="Traiter aussi les documents sans sommaire (résultats peu fiables)",
        )

    def handle(self, *args, **options):
        from apps.urbanism_docs.models import ReglementDocument

        modele = options["modele"] or settings.OLLAMA_MODEL
        limite_minutes = options.get("duree_max")

        self.avec_corps = options["avec_corps"]

        qs = ReglementDocument.objects.exclude(fichier="").filter(est_numerise=False)

        if options.get("titre"):
            qs = qs.filter(titre=options["titre"])
        elif options.get("region"):
            qs = qs.filter(document__communes__code_region=options["region"])
        else:
            raise CommandError("Précisez --region ou --titre.")

        if not options["refaire"]:
            qs = qs.filter(extraction_brute__isnull=True)

        # Les communes peuplées d'abord : c'est là que se trouveront
        # les premiers utilisateurs.
        qs = qs.annotate(
            pop=Max("document__communes__population")
        ).order_by("-pop").distinct()

        documents = list(qs)
        if options.get("limit"):
            documents = documents[: options["limit"]]

        if not documents:
            raise CommandError("Aucun document à traiter.")

        total = len(documents)
        self.stdout.write(f"{total} documents · modèle {modele}")
        if limite_minutes:
            self.stdout.write(f"Arrêt programmé après {limite_minutes} min")

        debut = time.time()
        stats = {"ok": 0, "vides": 0, "erreurs": 0, "lots": 0}

        for i, regl in enumerate(documents, 1):
            if limite_minutes and (time.time() - debut) / 60 >= limite_minutes:
                self.stdout.write(
                    self.style.WARNING(f"\nDurée maximale atteinte à {i - 1}/{total}.")
                )
                break

            try:
                nb_frag, nb_lots, tronques = self._traiter(regl, modele)
            except Exception as exc:
                stats["erreurs"] += 1
                self.stdout.write(self.style.ERROR(f"  {regl.titre} : {exc}"))
                continue

            stats["lots"] += nb_lots
            if nb_frag == 0:
                stats["vides"] += 1
            else:
                stats["ok"] += 1

            ecoule = (time.time() - debut) / 60
            avert = f" · {tronques} lots tronqués" if tronques else ""
            self.stdout.write(
                f"  {i}/{total}  {nb_frag:4} fragments · {nb_lots:2} lots{avert} · "
                f"{ecoule:.0f} min · {regl.titre[:45]}"
            )

        ecoule = (time.time() - debut) / 60
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{stats['ok']} traités, {stats['vides']} vides, "
                f"{stats['erreurs']} erreurs · {stats['lots']} lots · {ecoule:.0f} min"
            )
        )

    def _traiter(self, regl, modele):
        self._lots_tronques = 0

        with pdfplumber.open(regl.fichier.path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]

        a_sommaire = any(
            re.search(r"(?i)(sommaire|table des mati)", t) for t in pages[:6]
        )

        if not a_sommaire and not self.avec_corps:
            # On marque le document pour ne pas relire son PDF à chaque passe,
            # tout en gardant trace du motif : un modèle plus capable pourra
            # reprendre ce lot plus tard.
            regl.extraction_brute = {
                "source": "corps",
                "ignore": "sans_sommaire",
                "fragments": [],
            }
            regl.extraction_at = timezone.now()
            regl.save(update_fields=["extraction_brute", "extraction_at"])
            return 0, 0, 0

        # Avec un sommaire, la structure entière tient dans les premières
        # pages : inutile de parcourir les 200 suivantes.
        derniere = min(PAGES_SOMMAIRE_MAX, len(pages)) if a_sommaire else len(pages)

        fragments, nb_lots = [], 0
        depart = 0
        while depart < derniere:
            fin = min(depart + PAGES_PAR_LOT, derniere)
            lot = self._traiter_lot(pages, depart, fin, modele)
            fragments.extend(lot)
            nb_lots += 1
            depart = fin - CHEVAUCHEMENT if fin < derniere else fin

        fragments = self._dedoublonner(fragments)
        fragments = self._verifier(fragments, pages)

        decalage, confiance = None, 0.0
        if a_sommaire:
            decalage, confiance = self._calculer_decalage(fragments, pages, derniere)
            if decalage is not None and confiance >= 0.25:
                for f in fragments:
                    if isinstance(f.get("page"), int):
                        f["page_imprimee"] = f["page"]
                        f["page"] = f["page"] + decalage

        regl.extraction_brute = {
            "source": "sommaire" if a_sommaire else "corps",
            "pages_analysees": derniere,
            "fragments": fragments,
            "lots_tronques": self._lots_tronques,
            "decalage": decalage,
            "confiance_decalage": round(confiance, 2),
        }
        regl.extraction_modele = modele
        regl.extraction_at = timezone.now()
        from apps.urbanism_docs.models import ReglementDocument
        regl.statut = ReglementDocument.Statut.STRUCTURE_N1
        regl.save(update_fields=[
            "extraction_brute", "extraction_modele", "extraction_at", "statut",
        ])

        return len(fragments), nb_lots, self._lots_tronques

    def _traiter_lot(self, pages, depart, fin, modele, profondeur=0):
        """
        Un lot tronqué signifie que le modèle a produit trop de sortie.
        Le redécouper en deux donne à chaque moitié la place de tenir.
        """
        morceau = "\n\n".join(
            f"=== PAGE PDF {j + 1} ===\n{pages[j]}" for j in range(depart, fin)
        )
        if not morceau.strip():
            return []

        avant = self._lots_tronques
        resultat = self._appeler(morceau, modele)

        tronque = self._lots_tronques > avant
        if tronque and (fin - depart) > 1 and profondeur < 2:
            milieu = depart + (fin - depart) // 2
            return (
                self._traiter_lot(pages, depart, milieu, modele, profondeur + 1)
                + self._traiter_lot(pages, milieu, fin, modele, profondeur + 1)
            )
        return resultat

    def _appeler(self, contenu, modele):
        for essai in range(2):
            try:
                r = requests.post(
                    f"{settings.OLLAMA_HOST}/api/generate",
                    json={
                        "model": modele,
                        "system": INSTRUCTION,
                        "prompt": contenu,
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0,
                            "num_ctx": 16384,
                            "num_predict": 6144,
                        },
                    },
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                brut = r.json().get("response", "")
            except requests.RequestException:
                if essai == 0:
                    time.sleep(3)
                    continue
                return []

            try:
                data = json.loads(brut)
            except json.JSONDecodeError:
                # Sortie tronquée : le modèle s'arrête parfois de lui-même sur
                # les lots denses. On le compte, sinon un document presque vide
                # passe pour un document sans structure.
                self._lots_tronques = getattr(self, "_lots_tronques", 0) + 1
                return []

            # Le modèle renvoie parfois une liste de chaînes au lieu d'objets :
            # le format JSON est garanti, le schéma ne l'est pas.
            bruts = data.get("fragments")
            if not isinstance(bruts, list):
                return []
            return [
                f for f in bruts
                if isinstance(f, dict) and f.get("titre")
            ]
        return []

    @staticmethod
    def _dedoublonner(fragments):
        """Le chevauchement d'une page produit des fragments en double."""
        vus, sortie = set(), []
        for f in fragments:
            cle = (f.get("page"), normaliser(f.get("titre"))[:60])
            if cle in vus:
                continue
            vus.add(cle)
            sortie.append(f)
        return sortie

    @staticmethod
    def _verifier(fragments, pages):
        for f in fragments:
            z = f.get("zones")
            if not isinstance(z, list):
                f["zones"] = []
            else:
                f["zones"] = [str(x) for x in z if isinstance(x, (str, int))]

        """
        Contrôle indépendant : l'intitulé existe-t-il dans le PDF ?
        Le modèle peut se tromper de page ; la chaîne, elle, s'y trouve ou non.
        """
        normalisees = [normaliser(p) for p in pages]
        for f in fragments:
            cible = normaliser(f.get("titre"))[:45]
            if len(cible) < 20:
                f["verifie"] = None
                continue
            f["verifie"] = any(cible in p for p in normalisees)
        return fragments

    @staticmethod
    def _calculer_decalage(fragments, pages, fin_sommaire):
        """
        Dans un sommaire, le modèle recopie la page imprimée, pas la page PDF.
        L'écart se déduit en cherchant chaque intitulé dans le corps du
        document : la valeur qui revient le plus souvent est la bonne.
        """
        from collections import Counter
        normalisees = [normaliser(p) for p in pages]
        ecarts = Counter()
        for f in fragments:
            page_imp = f.get("page")
            if not isinstance(page_imp, int):
                continue
            cible = normaliser(f.get("titre"))[:45]
            if len(cible) < 25:
                continue
            for i in range(fin_sommaire, len(normalisees)):
                if cible in normalisees[i]:
                    ecarts[i + 1 - page_imp] += 1
                    break
        if not ecarts:
            return None, 0.0
        # La page PDF est toujours après la page imprimée : couverture et
        # sommaire décalent la numérotation. Un écart négatif est du bruit.
        positifs = {e: n for e, n in ecarts.items() if 0 <= e <= 60}
        if not positifs:
            return None, 0.0
        dominant = max(positifs, key=positifs.get)
        return dominant, positifs[dominant] / sum(positifs.values())