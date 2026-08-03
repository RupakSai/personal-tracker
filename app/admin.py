from django.contrib import admin

from .models import ExpenseEntry, NutritionEntry, WeightEntry


@admin.register(NutritionEntry)
class NutritionEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'source', 'calories', 'protein_g', 'fat_g', 'carbs_g', 'fibre_g', 'sugar_g', 'created_at')
    list_filter = ('source', 'date')
    search_fields = ('date',)


@admin.register(WeightEntry)
class WeightEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'weight_kg', 'updated_at')
    list_filter = ('date',)


@admin.register(ExpenseEntry)
class ExpenseEntryAdmin(admin.ModelAdmin):
    list_display = ('date', 'amount', 'note', 'created_at')
    list_filter = ('date',)
    search_fields = ('note',)
