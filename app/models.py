from django.db import models


class NutritionEntry(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_AI = 'ai'

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_AI, 'AI estimate'),
    ]

    date = models.DateField(db_index=True)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    calories = models.PositiveIntegerField()
    protein_g = models.DecimalField(max_digits=7, decimal_places=1)
    fat_g = models.DecimalField(max_digits=7, decimal_places=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.date} - {self.get_source_display()} ({self.calories} kcal)'


class WeightEntry(models.Model):
    date = models.DateField(unique=True, db_index=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=1)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f'{self.date} - {self.weight_kg} kg'


class ExpenseEntry(models.Model):
    date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.date} - {self.amount}'
