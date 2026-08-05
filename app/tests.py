from unittest.mock import patch

from django.http import JsonResponse
from django.test import TestCase

from . import views
from .models import ExpenseEntry, NutritionEntry, WeightEntry


class TrackerTests(TestCase):
    def unlock(self):
        return self.client.post(
            '/api/unlock/',
            data={'pin': '8688'},
            content_type='application/json',
        )

    def test_pin_unlocks_tracker(self):
        response = self.unlock()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertIn('tracker_auth', response.cookies)
        self.assertEqual(response.cookies['tracker_auth']['max-age'], 120)

    def test_signed_auth_cookie_allows_protected_api(self):
        self.unlock()
        response = self.client.get('/api/day/2026-08-01/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['date'], '2026-08-01')

    @patch('app.views._ensure_tracker_table')
    def test_day_api_returns_clear_database_error(self, ensure_table_mock):
        ensure_table_mock.return_value = JsonResponse(
            {'error': 'Tracker database is not ready.'},
            status=503,
        )
        self.unlock()

        response = self.client.get('/api/day/2026-08-01/')

        self.assertEqual(response.status_code, 503)
        self.assertIn('database', response.json()['error'])

    def test_manual_entry_stores_only_nutrition_fields(self):
        self.unlock()
        response = self.client.post(
            '/api/day/2026-08-01/entries/',
            data={
                'source': 'manual',
                'calories': 420,
                'protein_g': 27,
                'fat_g': 14,
                'carbs_g': 48,
                'fibre_g': 6,
                'sugar_g': 5,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        entry = NutritionEntry.objects.get()
        self.assertEqual(entry.calories, 420)
        self.assertEqual(float(entry.carbs_g), 48.0)
        self.assertEqual(response.json()['totals']['carbs_g'], 48.0)
        self.assertEqual(
            {field.name for field in NutritionEntry._meta.fields},
            {
                'id',
                'date',
                'source',
                'calories',
                'protein_g',
                'fat_g',
                'carbs_g',
                'fibre_g',
                'sugar_g',
                'created_at',
            },
        )

    def test_weight_can_be_saved_and_edited_for_a_day(self):
        self.unlock()
        response = self.client.post(
            '/api/weight/day/2026-08-03/save/',
            data={'weight_kg': 82.4},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['weight']['weight_kg'], 82.4)
        self.assertEqual(response.json()['weight']['date'], '2026-08-03')

        response = self.client.post(
            '/api/weight/day/2026-08-03/save/',
            data={'weight_kg': 82.1},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['weight']['weight_kg'], 82.1)
        self.assertEqual(WeightEntry.objects.count(), 1)

    def test_multiple_expenses_can_be_saved_for_a_day(self):
        self.unlock()
        for amount, note in [(180, 'Breakfast'), (45.5, 'Tea')]:
            response = self.client.post(
                '/api/expenses/day/2026-08-03/entries/',
                data={'amount': amount, 'note': note},
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.get('/api/expenses/day/2026-08-03/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 225.5)
        self.assertEqual(len(response.json()['entries']), 2)
        self.assertEqual(ExpenseEntry.objects.count(), 2)

    @patch('app.views._openai_nutrition_estimate')
    def test_ai_estimate_does_not_create_entry_without_approval(self, estimate_mock):
        estimate_mock.return_value = {
            'calories': 510,
            'protein_g': 22.0,
            'fat_g': 28.0,
            'carbs_g': 42.0,
            'fibre_g': 5.0,
            'sugar_g': 7.0,
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
            'carbs_g': 82.0,
            'fibre_g': 9.0,
            'sugar_g': 6.0,
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

    @patch('app.views._openai_nutrition_estimate')
    def test_ai_estimate_allows_optional_grams(self, estimate_mock):
        estimate_mock.return_value = {
            'calories': 330,
            'protein_g': 8.0,
            'fat_g': 11.0,
            'carbs_g': 44.0,
            'fibre_g': 4.0,
            'sugar_g': 2.0,
            'confidence': 'medium',
            'explanation': 'Inferred from the described serving.',
        }
        self.unlock()

        response = self.client.post(
            '/api/ai-estimate/',
            data={
                'dish_name': 'half 10-inch dosa with chutney',
                'style': 'home',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        food_text, style_text, grams = estimate_mock.call_args.args
        self.assertEqual(food_text, 'half 10-inch dosa with chutney')
        self.assertIn('Hyderabad, India Telugu home-style', style_text)
        self.assertIsNone(grams)
        self.assertEqual(NutritionEntry.objects.count(), 0)

    @patch('app.views._openai_nutrition_estimate')
    def test_ai_estimate_supports_packaged_food_style(self, estimate_mock):
        estimate_mock.return_value = {
            'calories': 160,
            'protein_g': 4.0,
            'fat_g': 8.0,
            'carbs_g': 18.0,
            'fibre_g': 1.0,
            'sugar_g': 2.0,
            'confidence': 'medium',
            'explanation': 'Packaged snack estimate depends on the exact label.',
        }
        self.unlock()

        response = self.client.post(
            '/api/ai-estimate/',
            data={
                'dish_name': 'one packet masala lays',
                'style': 'packaged',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        _food_text, style_text, grams = estimate_mock.call_args.args
        self.assertIn('Packaged branded food', style_text)
        self.assertIsNone(grams)

    def test_estimate_breakdown_payload_is_normalized(self):
        breakdown = views._estimate_breakdown_payload(
            [
                {
                    'item': 'Hyderabad street veg fried rice',
                    'assumed_quantity': 'one street-style plate',
                    'assumed_grams': 420,
                    'calories': 760,
                    'protein_g': 13.4,
                    'fat_g': 28.2,
                    'carbs_g': 112.5,
                    'fibre_g': 5.6,
                    'sugar_g': 4.2,
                    'cooking_assumption': 'Wok-fried cooked rice with noticeable oil, sauces, and limited vegetables.',
                }
            ]
        )

        self.assertEqual(breakdown[0]['item'], 'Hyderabad street veg fried rice')
        self.assertEqual(breakdown[0]['assumed_grams'], 420.0)
        self.assertEqual(breakdown[0]['calories'], 760)
        self.assertEqual(breakdown[0]['protein_g'], 13.4)
        self.assertEqual(breakdown[0]['carbs_g'], 112.5)
        self.assertEqual(breakdown[0]['fibre_g'], 5.6)
        self.assertEqual(breakdown[0]['sugar_g'], 4.2)

    def test_ai_prompt_treats_sugar_as_added_sugar(self):
        with patch('app.views._env_value', return_value='test-key'), patch('urllib.request.urlopen') as urlopen_mock:
            urlopen_mock.side_effect = RuntimeError('stop before network')

            with self.assertRaises(RuntimeError):
                views._openai_nutrition_estimate('banana and curd', views.PREPARATION_STYLES['home'], None)

        request_payload = urlopen_mock.call_args.args[0].data.decode('utf-8')
        self.assertIn('added sugar only', request_payload)
        self.assertIn('Do not count natural sugars', request_payload)

# Create your tests here.
