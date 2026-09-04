from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Commune, DocumentUrbanisme, Zone, Parcelle, ParcelleZone


@admin.register(Commune)
class CommuneAdmin(GISModelAdmin):
    list_display = (
        "code_insee",
        "nom",
        "code_departement",
        "population",
        "a_geometrie",
        "synced_at",
    )
    list_filter = ("code_region", "code_departement")
    search_fields = ("code_insee", "nom", "code_postal")
    ordering = ("code_insee",)
    readonly_fields = ("created_at", "updated_at", "synced_at")
    list_per_page = 50

    @admin.display(boolean=True, description="Contour")
    def a_geometrie(self, obj):
        return obj.geom is not None


class ZoneInline(admin.TabularInline):
    model = Zone
    extra = 0
    fields = ("libelle", "type_zone", "libelle_long")
    show_change_link = True


@admin.register(DocumentUrbanisme)
class DocumentUrbanismeAdmin(admin.ModelAdmin):
    list_display = (
        "partition",
        "type_document",
        "date_approbation",
        "est_en_vigueur",
        "nb_communes",
        "nb_zones",
    )
    list_filter = ("type_document", "est_en_vigueur")
    search_fields = ("partition",)
    filter_horizontal = ("communes",)
    readonly_fields = ("created_at", "updated_at", "synced_at")
    inlines = [ZoneInline]

    @admin.display(description="Communes")
    def nb_communes(self, obj):
        return obj.communes.count()

    @admin.display(description="Zones")
    def nb_zones(self, obj):
        return obj.zones.count()


@admin.register(Zone)
class ZoneAdmin(GISModelAdmin):
    list_display = ("libelle", "type_zone", "document", "libelle_long")
    list_filter = ("type_zone", "document__type_document")
    search_fields = ("libelle", "libelle_long", "document__partition")
    autocomplete_fields = ("document",)
    readonly_fields = ("created_at", "updated_at", "synced_at")
    list_per_page = 50


class ParcelleZoneInline(admin.TabularInline):
    model = ParcelleZone
    extra = 0
    fields = ("zone", "part_pct", "surface_intersection_m2", "est_dominante")
    readonly_fields = ("computed_at",)
    autocomplete_fields = ("zone",)


@admin.register(Parcelle)
class ParcelleAdmin(GISModelAdmin):
    list_display = ("idu", "commune", "section", "numero", "contenance_m2")
    list_filter = ("commune__code_departement",)
    search_fields = ("idu", "section", "numero", "commune__nom")
    autocomplete_fields = ("commune",)
    readonly_fields = ("created_at", "updated_at", "synced_at")
    inlines = [ParcelleZoneInline]
    list_per_page = 50