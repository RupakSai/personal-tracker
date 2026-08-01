from unittest.mock import patch

from django.test import TestCase

from .models import NutritionEntry


class TrackerTests(TestCase):
    def unlock(self):
        return self.client.post(
            '/api/unlock/',
            data={'pin': '5909'},
            content_type='application/json',
        )

    def test_pin_unlocks_tracker(self):
        response = self.unlock()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_manual_entry_stores_only_nutrition_fields(self):
        self.unlock()
        response = self.client.post(
            '/api/day/2026-08-01/entries/',
            data={
                'source': 'manual',
                'calories': 420,
                'protein_g': 27,
                'fat_g': 14,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        entry = NutritionEntry.objects.get()
        self.assertEqual(entry.calories, 420)
        self.assertEqual(
            {field.name for field in NutritionEntry._meta.fields},
            {'id', 'date', 'source', 'calories', 'protein_g', 'fat_g', 'created_at'},
        )

    @patch('app.views._openai_nutrition_estimate')
    def test_ai_estimate_does_not_create_entry_without_approval(self, estimate_mock):
        estimate_mock.return_value = {
            'calories': 510,
            'protein_g': 22.0,
            'fat_g': 28.0,
            'confidence': 'medium',
            'explanation': 'Estimated with restaurant oil and dairy included.',
        }
        self.unlock()
        response = self.client.post(
            '/api/ai-estimate/',
            data={
                'dish_name': 'paneer tikka',
                'style': 'restaurant',
                'grams': 250,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(NutritionEntry.objects.count(), 0)

    @patch('app.views._openai_nutrition_estimate')
    def test_ai_estimate_handles_multiple_food_items_with_hyderabad_context(self, estimate_mock):
        estimate_mock.return_value = {
            'calories': 690,
            'protein_g': 24.0,
            'fat_g': 31.0,
            'confidence': 'medium',
            'explanation': 'Combined estimate for the full serving.',
        }
        self.unlock()

        response = self.client.post(
            '/api/ai-estimate/',
            data={
                'dish_name': '2 dosa, sambar, peanut chutney',
                'style': 'home',
                'grams': 360,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        estimate_mock.assert_called_once()
        food_text, style_text, grams = estimate_mock.call_args.args
        self.assertEqual(food_text, '2 dosa, sambar, peanut chutney')
        self.assertIn('Hyderabad, India Telugu home-style', style_text)
        self.assertEqual(str(grams), '360.0')
        self.assertEqual(NutritionEntry.objects.count(), 0)

# Create your tests here.
