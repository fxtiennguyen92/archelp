from django.contrib.gis.db import models


class Commune(models.Model):
    """
    Commune française. Clé naturelle = code INSEE (5 caractères).
    Source: IGN Admin Express / API Géo.
    """

    code_insee = models.CharField(
        max_length=5,
        unique=True,
        db_index=True,
        help_text="Code INSEE sur 5 caractères, ex: 67482 (Strasbourg)",
    )
    nom = models.CharField(max_length=200, db_index=True)
    code_postal = models.CharField(max_length=5, blank=True)

    code_departement = models.CharField(
        max_length=3,
        db_index=True,
        help_text="Ex: 67, 68, 2A",
    )
    code_region = models.CharField(
        max_length=2,
        db_index=True,
        help_text="Ex: 44 pour Grand Est",
    )

    population = models.IntegerField(null=True, blank=True)
    surface_ha = models.FloatField(null=True, blank=True)

    geom = models.MultiPolygonField(
        srid=4326,
        null=True,
        blank=True,
        spatial_index=True,
        help_text="Limites administratives, WGS84",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Dernière synchronisation depuis la source officielle",
    )

    class Meta:
        verbose_name = "Commune"
        verbose_name_plural = "Communes"
        ordering = ["code_insee"]
        indexes = [
            models.Index(fields=["code_region", "code_departement"]),
        ]

    def __str__(self):
        return f"{self.nom} ({self.code_insee})"


class DocumentUrbanisme(models.Model):
    """
    Le document d'urbanisme en vigueur sur une commune (PLU, PLUi, POS, CC, PSMV).
    Une commune peut en avoir plusieurs dans le temps ; un seul est en vigueur.
    Un PLUi couvre plusieurs communes.
    """

    class TypeDoc(models.TextChoices):
        PLU = "PLU", "Plan Local d'Urbanisme"
        PLUI = "PLUi", "Plan Local d'Urbanisme intercommunal"
        POS = "POS", "Plan d'Occupation des Sols"
        CC = "CC", "Carte Communale"
        PSMV = "PSMV", "Plan de Sauvegarde et de Mise en Valeur"
        RNU = "RNU", "Règlement National d'Urbanisme"

    partition = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Identifiant GPU, ex: DU_67482. Clé de rattachement du zonage.",
    )
    type_document = models.CharField(max_length=10, choices=TypeDoc.choices)

    communes = models.ManyToManyField(
        Commune,
        related_name="documents_urbanisme",
        help_text="Plusieurs communes si PLUi",
    )

    date_approbation = models.DateField(null=True, blank=True)
    date_fin_validite = models.DateField(null=True, blank=True)
    est_en_vigueur = models.BooleanField(default=True, db_index=True)

    url_gpu = models.URLField(
        max_length=500,
        blank=True,
        help_text="Fiche sur geoportail-urbanisme.gouv.fr",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Document d'urbanisme"
        verbose_name_plural = "Documents d'urbanisme"
        ordering = ["-date_approbation"]

    def __str__(self):
        return f"{self.type_document} {self.partition}"


class Zone(models.Model):
    """
    Zone de zonage issue d'un document d'urbanisme (U, AU, A, N + sous-zones).
    Standard CNIG. C'est l'objet auquel se rattache le règlement.
    """

    class TypeZone(models.TextChoices):
        U = "U", "Zone urbaine"
        AU = "AU", "Zone à urbaniser"
        A = "A", "Zone agricole"
        N = "N", "Zone naturelle et forestière"
        AUC = "AUc", "Zone à urbaniser constructible"
        AUS = "AUs", "Zone à urbaniser stricte"
        AUTRE = "AUTRE", "Autre / non normalisé"

    document = models.ForeignKey(
        DocumentUrbanisme,
        on_delete=models.CASCADE,
        related_name="zones",
    )

    libelle = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Libellé exact du PLU, ex: UB2, UAa, 1AU, Nzh",
    )
    libelle_long = models.CharField(max_length=500, blank=True)

    type_zone = models.CharField(
        max_length=10,
        choices=TypeZone.choices,
        db_index=True,
        help_text="Type normalisé CNIG, déduit du libellé",
    )

    geom = models.MultiPolygonField(
        srid=4326,
        spatial_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Zone"
        verbose_name_plural = "Zones"
        ordering = ["document", "libelle"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "libelle"],
                name="unique_zone_par_document",
            )
        ]

    def __str__(self):
        return f"{self.libelle} — {self.document.partition}"

class Parcelle(models.Model):
    """
    Parcelle cadastrale. Source: API Carto module Cadastre (IGN).
    Récupérée à la demande (cache-aside), pas en bulk.
    """

    idu = models.CharField(
        max_length=14,
        unique=True,
        db_index=True,
        help_text="Identifiant unique: code_insee + prefixe(3) + section(2) + numero(4). Ex: 674820000AB0123",
    )

    commune = models.ForeignKey(
        Commune,
        on_delete=models.PROTECT,
        related_name="parcelles",
    )

    prefixe = models.CharField(
        max_length=3,
        default="000",
        help_text="Préfixe, non nul si commune fusionnée",
    )
    section = models.CharField(max_length=2, help_text="Ex: AB")
    numero = models.CharField(max_length=4, help_text="Ex: 0123")

    contenance_m2 = models.IntegerField(
        null=True,
        blank=True,
        help_text="Surface cadastrale déclarée, en m²",
    )

    geom = models.MultiPolygonField(
        srid=4326,
        spatial_index=True,
    )

    zones = models.ManyToManyField(
        Zone,
        through="ParcelleZone",
        related_name="parcelles",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Parcelle"
        verbose_name_plural = "Parcelles"
        ordering = ["idu"]
        indexes = [
            models.Index(fields=["commune", "section", "numero"]),
        ]

    def __str__(self):
        return self.idu


class ParcelleZone(models.Model):
    """
    Intersection parcelle × zone, avec la part de surface concernée.
    Une parcelle peut chevaucher plusieurs zones : c'est fréquent et
    c'est précisément ce que l'architecte doit savoir.
    """

    parcelle = models.ForeignKey(Parcelle, on_delete=models.CASCADE)
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE)

    surface_intersection_m2 = models.FloatField(
        help_text="Surface de la parcelle dans cette zone, en m² (calculée en Lambert-93)",
    )
    part_pct = models.FloatField(
        help_text="Part de la parcelle dans cette zone, en %",
    )
    est_dominante = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Vrai pour la zone couvrant la plus grande part",
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Parcelle × Zone"
        verbose_name_plural = "Parcelles × Zones"
        ordering = ["-part_pct"]
        constraints = [
            models.UniqueConstraint(
                fields=["parcelle", "zone"],
                name="unique_parcelle_zone",
            )
        ]

    def __str__(self):
        return f"{self.parcelle.idu} ∩ {self.zone.libelle} ({self.part_pct:.1f}%)"