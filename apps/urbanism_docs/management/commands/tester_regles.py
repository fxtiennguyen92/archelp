"""
Extraction des règles d'une zone avec Gemini. Rien n'est écrit en base.

Un chapitre :
    python manage.py tester_regles --titre 32331_reglement_20241212.pdf --pages 47-57 --zone A

Pilote sur un échantillon de zones du Grand Est, une par document :
    python manage.py tester_regles --pilote 20 --region 44

Contrôles automatiques, indépendants du modèle :
  C — la citation existe dans le texte du PDF
  N — chaque nombre de la valeur figure dans la citation ; une valeur écrite
      en lettres (« deux mètres ») doit y figurer telle quelle
  F — pas de formule perdue : une règle de recul dont la citation parle de
      hauteur ou de moitié doit le dire aussi dans sa valeur
  Z — le chapitre traite bien de la zone attendue, d'après les titres lus
      par le modèle ; sinon le chapitre est rejeté en bloc
"""

import json
import random
import re
import time
import unicodedata
from collections import Counter

import pdfplumber
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.urbanism_docs.management.commands.detecter_chapitres import canon, famille_libelle

THEMES = [
    "destinations_interdites", "destinations_autorisees", "emprise_sol", "hauteur",
    "recul_voies", "recul_limites_separatives", "implantation_meme_propriete",
    "aspect_exterieur", "toitures", "clotures", "espaces_verts",
    "stationnement", "reseaux", "autre",
]
THEMES_RECUL = {"recul_voies", "recul_limites_separatives", "implantation_meme_propriete"}
PAGES_MAX = 25

INSTRUCTION = f"""Tu lis le chapitre d'un règlement de PLU français consacré à une zone.

Réponds uniquement par un objet JSON :
{{
  "zones_du_chapitre": ["<libellés des zones dont ce chapitre fixe les règles, lus uniquement dans les titres du texte fourni, ex : UA, UAa ; si le titre dit « zones agricoles », indique A ; liste vide si aucun titre de zone n'apparaît>"],
  "renvois": ["<passages du chapitre qui renvoient à des règles situées ailleurs : dispositions générales, autre titre, règlement graphique, annexe, code de l'urbanisme ; recopie la phrase de renvoi, 150 caractères maximum ; liste vide si le chapitre se suffit à lui-même>"],
  "regles": [
    {{
      "theme": "<un parmi : {', '.join(THEMES)}>",
      "valeur": "<valeur telle qu'écrite dans le texte — vide si la règle n'est pas chiffrée>",
      "condition": "<à quoi ou à qui la règle s'applique — vide si générale>",
      "secteurs": ["<secteurs visés — vide si toute la zone>"],
      "citation": "<extrait exact du texte contenant la valeur, recopié mot pour mot, 300 caractères maximum ; si la phrase est longue, commence juste avant la valeur>",
      "page": <numéro du bloc === PAGE PDF N === où se trouve la citation>
    }}
  ]
}}

Règles strictes :
- La citation est une copie exacte du texte, sans reformulation ni correction.
- La valeur reprend les mots du texte : n'écris pas « 2 » si le texte dit « deux ».
- Une règle exprimée par une formule garde sa formule entière dans la valeur,
  par exemple « la moitié de la hauteur, avec un minimum de 3 mètres ».
  Ne réduis jamais une formule à son seul minimum chiffré.
- Une même phrase contenant plusieurs règles donne plusieurs entrées.
- Les conditions et exceptions sont essentielles : ne les omets jamais.
- Liste toutes les règles du chapitre, chiffrées ou non.
- Un renvoi n'est pas une règle : il indique que le texte applicable est ailleurs.
  Relève-le dans "renvois", pas dans "regles".
- N'invente rien. Ce qui n'est pas écrit n'existe pas.
"""

FORMULE = re.compile(r"\b(hauteur|moitie|demi|h 2)\b")


def normaliser(texte):
    t = unicodedata.normalize("NFKD", (texte or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def nombres(texte):
    return [n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", texte or "")]


def zone_confirmee(attendue, zones_chapitre):
    """La zone attendue relève-t-elle d'un des libellés lus dans les titres ?"""
    ca = canon(attendue)
    for z in zones_chapitre or []:
        cz = canon(z)
        if cz and famille_libelle(ca) == famille_libelle(cz) and ca.startswith(cz):
            return True
    return False


def controler(regle, texte_total):
    """Renvoie un dictionnaire de contrôles, True quand le contrôle passe."""
    citation = normaliser(regle.get("citation"))
    valeur = regle.get("valeur") or ""
    c_ok = len(citation) >= 15 and citation[:80] in texte_total

    brute = (regle.get("citation") or "").replace(",", ".")
    chiffres = nombres(valeur)
    if chiffres:
        n_ok = all(n in brute for n in chiffres)
    elif valeur.strip():
        n_ok = normaliser(valeur) in citation
    else:
        n_ok = True

        f_ok = True
    if regle.get("theme") in THEMES_RECUL and FORMULE.search(citation):
        # La hauteur peut être une condition (« annexes de moins de 3,5 m ») plutôt
        # qu'une formule : elle doit figurer dans la valeur ou dans la condition.
        condition = regle.get("condition") or ""
        f_ok = bool(FORMULE.search(normaliser(valeur + " " + condition)))

    return {"C": c_ok, "N": n_ok, "F": f_ok}


def lire(chemin, debut, fin):
    with pdfplumber.open(chemin) as pdf:
        fin = min(fin, len(pdf.pages))
        return {i: (pdf.pages[i - 1].extract_text() or "") for i in range(debut, fin + 1)}


class Command(BaseCommand):
    help = "Extraction des règles d'une zone avec Gemini, avec contrôles"

    def add_arguments(self, parser):
        parser.add_argument("--titre")
        parser.add_argument("--pages", help="Plage, ex : 47-57")
        parser.add_argument("--zone", default="")
        parser.add_argument("--modele", default="gemini-3.1-pro-preview")
        parser.add_argument("--pilote", type=int, help="Nombre de zones à tester")
        parser.add_argument("--region", default="44")
        parser.add_argument("--graine", type=int, default=1)

    def handle(self, *args, **options):
        cle = getattr(settings, "GEMINI_API_KEY", "")
        if not cle:
            raise CommandError("GEMINI_API_KEY absent de la configuration.")
        from google import genai
        self.client = genai.Client(api_key=cle)
        self.modele = options["modele"]

        if options.get("pilote"):
            self.pilote(options["pilote"], options["region"], options["graine"])
        elif options.get("titre") and options.get("pages"):
            self.un_chapitre(options["titre"], options["pages"], options["zone"])
        else:
            raise CommandError("Précisez --titre et --pages, ou --pilote.")

    def extraire(self, textes, zone):
        from google.genai import types

        contenu = f"Zone : {zone}\n\n" + "\n\n".join(
            f"=== PAGE PDF {i} ===\n{t}" for i, t in textes.items()
        )
        derniere = None
        for essai in range(3):
            try:
                reponse = self.client.models.generate_content(
                    model=self.modele,
                    contents=contenu,
                    config=types.GenerateContentConfig(
                        system_instruction=INSTRUCTION,
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                data = json.loads(reponse.text)
                usage = getattr(reponse, "usage_metadata", None)
                jetons = (
                    (usage.prompt_token_count or 0, usage.candidates_token_count or 0)
                    if usage else (0, 0)
                )
                return data.get("zones_du_chapitre") or [], data.get("regles") or [], jetons
            except json.JSONDecodeError:
                return [], [], (0, 0)
            except Exception as exc:
                derniere = exc
                if essai < 2:
                    time.sleep(5 * (2 ** essai))
        raise CommandError(f"Appel échoué : {derniere}")

    def un_chapitre(self, titre, plage, zone):
        from apps.urbanism_docs.models import ReglementDocument

        regl = ReglementDocument.objects.filter(titre=titre).exclude(fichier="").first()
        if regl is None:
            raise CommandError("Règlement introuvable.")
        debut, fin = (int(x) for x in plage.split("-"))
        textes = lire(regl.fichier.path, debut, fin)

        depart = time.time()
        zones_chap, regles, (j_in, j_out) = self.extraire(textes, zone)
        self.stdout.write(
            f"{titre} · pages {debut}–{fin} · {self.modele} · "
            f"{j_in} + {j_out} jetons · {time.time() - depart:.0f} s"
        )
        confirme = zone_confirmee(zone, zones_chap) if zone else None
        self.stdout.write(f"Zones lues dans les titres : {', '.join(zones_chap) or '—'}"
                          + (f" · zone {zone} {'confirmée' if confirme else 'NON confirmée'}" if zone else ""))

        texte_total = normaliser(" ".join(textes.values()))
        cumul = Counter()
        self.stdout.write(f"\n{len(regles)} règles  (contrôles C N F)\n")
        for r in regles:
            ctl = controler(r, texte_total)
            for k, v in ctl.items():
                cumul[k] += v
            marque = "".join("✓" if ctl[k] else "✗" for k in "CNF")
            secteurs = ",".join(r.get("secteurs") or []) or "—"
            self.stdout.write(
                f"  {marque} p.{str(r.get('page')):>3} {r.get('theme', ''):26} "
                f"{(r.get('valeur') or '—')[:28]:28} [{secteurs:8}] {(r.get('condition') or '')[:40]}"
            )
        if regles:
            n = len(regles)
            self.stdout.write(
                f"\nC citation retrouvée : {cumul['C']}/{n} · "
                f"N nombres : {cumul['N']}/{n} · F formule conservée : {cumul['F']}/{n}"
            )

    def pilote(self, taille, region, graine):
        """
        Une zone par document, parmi celles qui ont à la fois un chapitre
        détecté et une page nomfic fiable. Mesure si le contrôle Z écarte
        bien les chapitres que le nomfic désigne comme faux.
        """
        from apps.parcels.models import Zone

        candidates = list(
            Zone.objects.filter(
                document__communes__code_region=region,
                page_chapitre_debut__isnull=False,
                page_reglement__isnull=False,
            ).exclude(type_chapitre__startswith="famille").select_related("document").distinct()
        )

        # Un document dont toutes les zones pointent la même page n'a pas de nomfic fiable.
        pages_doc = {}
        for z in candidates:
            pages_doc.setdefault(z.document_id, set()).add(z.page_reglement)
        candidates = [z for z in candidates if len(pages_doc[z.document_id]) > 1]

        random.seed(graine)
        random.shuffle(candidates)
        vus, echantillon = set(), []
        for z in candidates:
            if z.document_id in vus:
                continue
            vus.add(z.document_id)
            echantillon.append(z)
            if len(echantillon) >= taille:
                break

        self.stdout.write(f"{len(echantillon)} zones · {self.modele}\n")
        croise = Counter()
        cumul, total_regles = Counter(), 0
        j_in_tot = j_out_tot = 0
        depart = time.time()

        for k, z in enumerate(echantillon, 1):
            doc = z.document
            nom = (z.nom_fichier_reglement or "").split("#")[0]
            regl = (doc.reglements.filter(titre=nom).exclude(fichier="").first()
                    or doc.reglements.filter(type_piece="REGLEMENT").exclude(fichier="").first())
            if regl is None:
                continue

            debut = z.page_chapitre_debut
            fin = min(z.page_chapitre_fin or debut, debut + PAGES_MAX - 1)
            textes = lire(regl.fichier.path, debut, fin)
            zones_chap, regles, (j_in, j_out) = self.extraire(textes, z.libelle)
            j_in_tot += j_in
            j_out_tot += j_out

            confirme = zone_confirmee(z.libelle, zones_chap)
            nomfic_ok = abs(debut - z.page_reglement) <= 1
            croise[(confirme, nomfic_ok)] += 1

            texte_total = normaliser(" ".join(textes.values()))
            n_regles = len(regles)
            if confirme:
                total_regles += n_regles
                for r in regles:
                    for cle, v in controler(r, texte_total).items():
                        cumul[cle] += v

            self.stdout.write(
                f"  {k:2}. {z.libelle:8} p.{debut}–{fin:<4} nomfic p.{z.page_reglement:<4} "
                f"{'Z✓' if confirme else 'Z✗'} {'nomfic✓' if nomfic_ok else 'nomfic✗'} "
                f"· lu : {','.join(zones_chap)[:20]:20} · {n_regles} règles · {regl.titre}"
            )

        self.stdout.write(f"\nDurée : {(time.time() - depart) / 60:.0f} min · "
                          f"jetons : {j_in_tot} en entrée, {j_out_tot} en sortie")
        self.stdout.write("\nContrôle Z croisé avec le nomfic :")
        self.stdout.write(f"  confirmé et nomfic concordant  : {croise[(True, True)]}")
        self.stdout.write(f"  confirmé mais nomfic discordant : {croise[(True, False)]}")
        self.stdout.write(f"  rejeté alors que nomfic concorde : {croise[(False, True)]}")
        self.stdout.write(f"  rejeté et nomfic discordant     : {croise[(False, False)]}")
        if total_regles:
            self.stdout.write(
                f"\nSur les chapitres confirmés, {total_regles} règles : "
                f"C {100 * cumul['C'] / total_regles:.0f} % · "
                f"N {100 * cumul['N'] / total_regles:.0f} % · "
                f"F {100 * cumul['F'] / total_regles:.0f} %"
            )