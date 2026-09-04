import hashlib
from django.db import models

from apps.parcels.models import DocumentUrbanisme, Zone


class ReglementDocument(models.Model):
    """
    Un fichier PDF de règlement, tel que publié sur le GPU ou l'open data
    communal. Un document d'urbanisme produit plusieurs PDF (règlement écrit,
    annexes, OAP...). Le fichier est stocké tel quel, sans retraitement.
    """

    class TypePiece(models.TextChoices):
        REGLEMENT = "REGLEMENT", "Règlement écrit"
        OAP = "OAP", "Orientations d'Aménagement et de Programmation"
        RAPPORT = "RAPPORT", "Rapport de présentation"
        ANNEXE = "ANNEXE", "Annexe"
        PADD = "PADD", "Projet d'Aménagement et de Développement Durables"
        AUTRE = "AUTRE", "Autre"

    class Statut(models.TextChoices):
        A_TELECHARGER = "A_TELECHARGER", "À télécharger"
        TELECHARGE = "TELECHARGE", "Téléchargé"
        TEXTE_EXTRAIT = "TEXTE_EXTRAIT", "Texte extrait"
        ERREUR = "ERREUR", "Erreur"

    document = models.ForeignKey(
        DocumentUrbanisme,
        on_delete=models.CASCADE,
        related_name="reglements",
    )

    titre = models.CharField(max_length=500)
    type_piece = models.CharField(
        max_length=20,
        choices=TypePiece.choices,
        default=TypePiece.REGLEMENT,
        db_index=True,
    )

    url_source = models.URLField(
        max_length=1000,
        help_text="URL d'origine du PDF",
    )
    fichier = models.FileField(
        upload_to="reglements/%Y/%m/",
        blank=True,
        help_text="Copie locale du PDF",
    )

    sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Empreinte du fichier, pour détecter les mises à jour",
    )
    taille_octets = models.BigIntegerField(null=True, blank=True)
    nb_pages = models.IntegerField(null=True, blank=True)

    date_document = models.DateField(
        null=True,
        blank=True,
        help_text="Date d'approbation ou de mise à jour du règlement. "
        "À citer obligatoirement dans toute réponse à l'utilisateur.",
    )

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.A_TELECHARGER,
        db_index=True,
    )
    message_erreur = models.TextField(blank=True)

    est_numerise = models.BooleanField(
        null=True,
        blank=True,
        help_text="Vrai si PDF scanné sans couche texte (OCR nécessaire)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    telecharge_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Règlement (PDF)"
        verbose_name_plural = "Règlements (PDF)"
        ordering = ["document", "type_piece", "titre"]
        indexes = [
            models.Index(fields=["document", "statut"]),
        ]

    def __str__(self):
        return f"{self.titre} ({self.document.partition})"

    def calculer_sha256(self):
        """Recalcule l'empreinte à partir du fichier local."""
        if not self.fichier:
            return ""
        h = hashlib.sha256()
        with self.fichier.open("rb") as f:
            for bloc in iter(lambda: f.read(65536), b""):
                h.update(bloc)
        return h.hexdigest()


class ReglementSection(models.Model):
    """
    Un bloc de texte extrait du PDF, découpé par article.
    C'est l'unité de citation : toute réponse doit pouvoir pointer
    vers une section précise, avec sa page et son document.
    """

    reglement = models.ForeignKey(
        ReglementDocument,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    zone = models.ForeignKey(
        Zone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sections",
        help_text="Zone concernée, si la section est rattachable",
    )

    ordre = models.IntegerField(
        help_text="Position dans le document, pour restituer l'ordre de lecture",
    )
    page_debut = models.IntegerField(null=True, blank=True)
    page_fin = models.IntegerField(null=True, blank=True)

    titre = models.CharField(
        max_length=500,
        blank=True,
        help_text="Ex: Article UB 10 - Hauteur maximale des constructions",
    )
    numero_article = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Ex: UB 10, ou 'UB-4.2' pour un PLU post-2015",
    )

    texte = models.TextField()
    nb_caracteres = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Section de règlement"
        verbose_name_plural = "Sections de règlement"
        ordering = ["reglement", "ordre"]
        constraints = [
            models.UniqueConstraint(
                fields=["reglement", "ordre"],
                name="unique_section_par_reglement",
            )
        ]
        indexes = [
            models.Index(fields=["zone", "numero_article"]),
        ]

    def __str__(self):
        etiquette = self.titre or self.numero_article or f"section {self.ordre}"
        return f"{etiquette} — p.{self.page_debut}"

    def save(self, *args, **kwargs):
        self.nb_caracteres = len(self.texte)
        super().save(*args, **kwargs)