import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.db import connection
from django.db.models import Count, Sum
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import ExpenseEntry, NutritionEntry, WeightEntry


PIN_CODE = '8688'
AUTH_COOKIE_NAME = 'tracker_auth'
AUTH_COOKIE_VALUE = 'rupak-unlocked'
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
CALORIE_LIMIT = 1500
PROTEIN_MINIMUM = Decimal('70.0')
FAT_LIMIT = Decimal('50.0')
PREPARATION_STYLES = {
    'street': 'Hyderabad, India roadside street-food style with generous oil, chutneys, frying, ghee, butter, and full local portions',
    'restaurant': 'Hyderabad/Indian restaurant style with rich gravies, oil, cream, butter, cashews, frying, and realistic restaurant portions',
    'home': 'Hyderabad, India Telugu home-style cooking with common Andhra/Telangana ingredients, tadka, chutneys, rice, dal, curries, and household oil usage',
    'diet': 'Hyderabad, India diet-focused home preparation with controlled oil, leaner ingredients, and realistic but not optimistic portions',
}


@ensure_csrf_cookie
def home(request):
    return render(
        request,
        'app/tracker.html',
        {
            'is_unlocked': _is_unlocked(request),
        },
    )


@require_POST
def unlock(request):
    data = _json_body(request)
    if data.get('pin') == PIN_CODE:
        response = JsonResponse({'ok': True})
        response.set_cookie(
            AUTH_COOKIE_NAME,
            _auth_signer().sign(AUTH_COOKIE_VALUE),
            max_age=AUTH_COOKIE_MAX_AGE,
            httponly=True,
            secure=not settings.DEBUG,
            samesite='Lax',
        )
        return response

    return JsonResponse({'ok': False, 'error': 'That PIN did not match.'}, status=403)


@require_POST
def logout(request):
    response = JsonResponse({'ok': True})
    response.delete_cookie(AUTH_COOKIE_NAME, samesite='Lax')
    return response


@require_GET
def day_detail(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_table()
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    entries = NutritionEntry.objects.filter(date=entry_date)
    return JsonResponse(
        {
            'date': entry_date.isoformat(),
            'entries': [_entry_payload(entry) for entry in entries],
            'totals': _totals_payload(entries),
        }
    )


@require_GET
def month_summary(request):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_table()
    if table_error:
        return table_error

    today = date.today()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        first_day = date(year, month, 1)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Please provide a valid month.'}, status=400)

    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    rows = (
        NutritionEntry.objects.filter(date__gte=first_day, date__lt=next_month)
        .values('date')
        .annotate(
            calories=Sum('calories'),
            protein_g=Sum('protein_g'),
            fat_g=Sum('fat_g'),
            carbs_g=Sum('carbs_g'),
            fibre_g=Sum('fibre_g'),
            sugar_g=Sum('sugar_g'),
        )
        .order_by('date')
    )

    days = {}
    for row in rows:
        totals = _status_payload(
            int(row['calories'] or 0),
            Decimal(row['protein_g'] or 0),
            Decimal(row['fat_g'] or 0),
            Decimal(row['carbs_g'] or 0),
            Decimal(row['fibre_g'] or 0),
            Decimal(row['sugar_g'] or 0),
        )
        days[row['date'].isoformat()] = totals

    return JsonResponse({'days': days})


@require_POST
def create_entry(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_table()
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    data = _json_body(request)
    source = data.get('source')
    if source not in {NutritionEntry.SOURCE_MANUAL, NutritionEntry.SOURCE_AI}:
        return JsonResponse({'error': 'Please choose a valid entry source.'}, status=400)

    try:
        calories = _positive_int(data.get('calories'), 'calories')
        protein_g = _positive_decimal(data.get('protein_g'), 'protein')
        fat_g = _positive_decimal(data.get('fat_g'), 'fat')
        carbs_g = _positive_decimal(data.get('carbs_g', 0), 'carbohydrates')
        fibre_g = _positive_decimal(data.get('fibre_g', 0), 'fibre')
        sugar_g = _positive_decimal(data.get('sugar_g', 0), 'added sugar')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    entry = NutritionEntry.objects.create(
        date=entry_date,
        source=source,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        fibre_g=fibre_g,
        sugar_g=sugar_g,
    )
    entries = NutritionEntry.objects.filter(date=entry_date)
    return JsonResponse(
        {
            'entry': _entry_payload(entry),
            'totals': _totals_payload(entries),
        },
        status=201,
    )


@require_POST
def delete_entry(request, entry_id):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_table()
    if table_error:
        return table_error

    deleted, _ = NutritionEntry.objects.filter(id=entry_id).delete()
    if not deleted:
        return JsonResponse({'error': 'Entry not found.'}, status=404)

    return JsonResponse({'ok': True})


@require_POST
def ai_estimate(request):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    data = _json_body(request)
    dish_name = str(data.get('dish_name', '')).strip()
    style = data.get('style')

    if not dish_name:
        return JsonResponse({'error': 'Please enter the dish name.'}, status=400)
    if style not in PREPARATION_STYLES:
        return JsonResponse({'error': 'Please choose a preparation style.'}, status=400)

    try:
        grams = _optional_positive_decimal(data.get('grams'), 'grams')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    try:
        estimate = _openai_nutrition_estimate(dish_name, PREPARATION_STYLES[style], grams)
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse({'estimate': estimate})


@require_GET
def weight_month_summary(request):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(WeightEntry)
    if table_error:
        return table_error

    month_range, error = _month_range(request)
    if error:
        return error
    first_day, next_month = month_range

    rows = WeightEntry.objects.filter(date__gte=first_day, date__lt=next_month)
    return JsonResponse(
        {
            'days': {
                row.date.isoformat(): {
                    'weight_kg': _decimal_payload(row.weight_kg),
                }
                for row in rows
            }
        }
    )


@require_GET
def weight_day_detail(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(WeightEntry)
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    entry = WeightEntry.objects.filter(date=entry_date).first()
    return JsonResponse(
        {
            'date': entry_date.isoformat(),
            'weight': _weight_payload(entry) if entry else None,
        }
    )


@require_POST
def save_weight(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(WeightEntry)
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    data = _json_body(request)
    try:
        weight_kg = _positive_decimal(data.get('weight_kg'), 'weight')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    if weight_kg > Decimal('500.0'):
        return JsonResponse({'error': 'Please enter a realistic weight.'}, status=400)

    entry, _created = WeightEntry.objects.update_or_create(
        date=entry_date,
        defaults={'weight_kg': weight_kg},
    )
    return JsonResponse({'weight': _weight_payload(entry)})


@require_GET
def expenses_month_summary(request):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(ExpenseEntry)
    if table_error:
        return table_error

    month_range, error = _month_range(request)
    if error:
        return error
    first_day, next_month = month_range

    rows = (
        ExpenseEntry.objects.filter(date__gte=first_day, date__lt=next_month)
        .values('date')
        .annotate(total=Sum('amount'), count=Count('id'))
        .order_by('date')
    )
    return JsonResponse(
        {
            'days': {
                row['date'].isoformat(): {
                    'total': _money_payload(row['total'] or 0),
                    'count': row['count'] or 0,
                }
                for row in rows
            }
        }
    )


@require_GET
def expenses_day_detail(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(ExpenseEntry)
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    entries = ExpenseEntry.objects.filter(date=entry_date)
    return JsonResponse(
        {
            'date': entry_date.isoformat(),
            'entries': [_expense_payload(entry) for entry in entries],
            'total': _money_payload(entries.aggregate(total=Sum('amount'))['total'] or 0),
        }
    )


@require_POST
def create_expense(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(ExpenseEntry)
    if table_error:
        return table_error

    entry_date, error = _parse_date(selected_date)
    if error:
        return error

    data = _json_body(request)
    try:
        amount = _positive_money(data.get('amount'), 'amount')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    entry = ExpenseEntry.objects.create(
        date=entry_date,
        amount=amount,
        note=str(data.get('note', '')).strip()[:120],
    )
    entries = ExpenseEntry.objects.filter(date=entry_date)
    return JsonResponse(
        {
            'entry': _expense_payload(entry),
            'total': _money_payload(entries.aggregate(total=Sum('amount'))['total'] or 0),
        },
        status=201,
    )


@require_POST
def delete_expense(request, entry_id):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

    table_error = _ensure_tracker_tables(ExpenseEntry)
    if table_error:
        return table_error

    deleted, _ = ExpenseEntry.objects.filter(id=entry_id).delete()
    if not deleted:
        return JsonResponse({'error': 'Expense not found.'}, status=404)

    return JsonResponse({'ok': True})


def _auth_error(request):
    if _is_unlocked(request):
        return None
    return JsonResponse({'error': 'Please unlock the tracker first.'}, status=401)


def _is_unlocked(request):
    signed_value = request.COOKIES.get(AUTH_COOKIE_NAME)
    if not signed_value:
        return False

    try:
        value = _auth_signer().unsign(signed_value, max_age=AUTH_COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False

    return value == AUTH_COOKIE_VALUE


def _auth_signer():
    return signing.TimestampSigner(salt='personal-tracker-auth')


def _ensure_tracker_table():
    return _ensure_tracker_tables(NutritionEntry)


def _ensure_tracker_tables(*models):
    try:
        existing_tables = connection.introspection.table_names()

        with connection.schema_editor() as schema_editor:
            for model in models:
                if model._meta.db_table not in existing_tables:
                    schema_editor.create_model(model)
                    continue

                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        connection.cursor(),
                        model._meta.db_table,
                    )
                }
                for field in model._meta.local_fields:
                    if field.column not in columns:
                        schema_editor.add_field(model, field)
    except (DatabaseError, OperationalError, ProgrammingError):
        connection.close()
        try:
            existing_tables = connection.introspection.table_names()
            tables_and_columns_exist = True
            for model in models:
                if model._meta.db_table not in existing_tables:
                    tables_and_columns_exist = False
                    break
                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        connection.cursor(),
                        model._meta.db_table,
                    )
                }
                if any(field.column not in columns for field in model._meta.local_fields):
                    tables_and_columns_exist = False
                    break

            if tables_and_columns_exist:
                return None
        except (DatabaseError, OperationalError, ProgrammingError):
            pass

        return JsonResponse(
            {
                'error': (
                    'Tracker database is not ready. Add DATABASE_URL or POSTGRES_URL in Vercel, '
                    'then redeploy so entries can be saved.'
                )
            },
            status=503,
        )

    return None


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return {}


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date(), None
    except ValueError:
        return None, JsonResponse({'error': 'Please select a valid date.'}, status=400)


def _month_range(request):
    today = date.today()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        first_day = date(year, month, 1)
    except (TypeError, ValueError):
        return None, JsonResponse({'error': 'Please provide a valid month.'}, status=400)

    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    return (first_day, next_month), None


def _positive_int(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Please enter a valid {label} number.')

    if number < 0 or number > 10000:
        raise ValueError(f'Please enter a realistic {label} number.')
    return number


def _positive_decimal(value, label):
    try:
        number = Decimal(str(value)).quantize(Decimal('0.1'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Please enter a valid {label} number.')

    if number < 0 or number > Decimal('10000'):
        raise ValueError(f'Please enter a realistic {label} number.')
    return number


def _positive_money(value, label):
    try:
        number = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Please enter a valid {label}.')

    if number < 0 or number > Decimal('10000000'):
        raise ValueError(f'Please enter a realistic {label}.')
    return number


def _optional_positive_decimal(value, label):
    if value in (None, ''):
        return None
    return _positive_decimal(value, label)


def _entry_payload(entry):
    return {
        'id': entry.id,
        'source': entry.source,
        'source_label': entry.get_source_display(),
        'calories': entry.calories,
        'protein_g': _decimal_payload(entry.protein_g),
        'fat_g': _decimal_payload(entry.fat_g),
        'carbs_g': _decimal_payload(entry.carbs_g),
        'fibre_g': _decimal_payload(entry.fibre_g),
        'sugar_g': _decimal_payload(entry.sugar_g),
        'created_at': entry.created_at.isoformat(),
    }


def _weight_payload(entry):
    return {
        'id': entry.id,
        'date': entry.date.isoformat(),
        'weight_kg': _decimal_payload(entry.weight_kg),
        'updated_at': entry.updated_at.isoformat(),
    }


def _expense_payload(entry):
    return {
        'id': entry.id,
        'amount': _money_payload(entry.amount),
        'note': entry.note,
        'created_at': entry.created_at.isoformat(),
    }


def _totals_payload(entries):
    totals = entries.aggregate(
        calories=Sum('calories'),
        protein_g=Sum('protein_g'),
        fat_g=Sum('fat_g'),
        carbs_g=Sum('carbs_g'),
        fibre_g=Sum('fibre_g'),
        sugar_g=Sum('sugar_g'),
    )
    calories = int(totals['calories'] or 0)
    protein_g = Decimal(totals['protein_g'] or 0)
    fat_g = Decimal(totals['fat_g'] or 0)
    carbs_g = Decimal(totals['carbs_g'] or 0)
    fibre_g = Decimal(totals['fibre_g'] or 0)
    sugar_g = Decimal(totals['sugar_g'] or 0)
    return _status_payload(calories, protein_g, fat_g, carbs_g, fibre_g, sugar_g)


def _status_payload(calories, protein_g, fat_g, carbs_g=0, fibre_g=0, sugar_g=0):
    protein_g = Decimal(protein_g).quantize(Decimal('0.1'))
    fat_g = Decimal(fat_g).quantize(Decimal('0.1'))
    carbs_g = Decimal(carbs_g).quantize(Decimal('0.1'))
    fibre_g = Decimal(fibre_g).quantize(Decimal('0.1'))
    sugar_g = Decimal(sugar_g).quantize(Decimal('0.1'))

    messages = []
    messages.append(
        {
            'kind': 'good' if calories <= CALORIE_LIMIT else 'bad',
            'text': 'Calories are in range.' if calories <= CALORIE_LIMIT else 'Calories crossed the 1500 kcal cap.',
        }
    )
    messages.append(
        {
            'kind': 'good' if protein_g >= PROTEIN_MINIMUM else 'warn',
            'text': 'Protein goal is met.' if protein_g >= PROTEIN_MINIMUM else 'You are low on protein today.',
        }
    )
    messages.append(
        {
            'kind': 'good' if fat_g <= FAT_LIMIT else 'bad',
            'text': 'Fat is within the 50 g limit.' if fat_g <= FAT_LIMIT else 'Fat crossed the 50 g limit.',
        }
    )

    return {
        'calories': calories,
        'protein_g': _decimal_payload(protein_g),
        'fat_g': _decimal_payload(fat_g),
        'carbs_g': _decimal_payload(carbs_g),
        'fibre_g': _decimal_payload(fibre_g),
        'sugar_g': _decimal_payload(sugar_g),
        'targets': {
            'calories_max': CALORIE_LIMIT,
            'protein_min_g': _decimal_payload(PROTEIN_MINIMUM),
            'fat_max_g': _decimal_payload(FAT_LIMIT),
        },
        'messages': messages,
    }


def _decimal_payload(value):
    return float(Decimal(value).quantize(Decimal('0.1')))


def _money_payload(value):
    return float(Decimal(value).quantize(Decimal('0.01')))


def _env_value(name):
    value = os.environ.get(name)
    if value:
        return value

    env_path = settings.BASE_DIR / '.env'
    if not env_path.exists():
        return ''

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, raw_value = line.split('=', 1)
        if key.strip() == name:
            return raw_value.strip().strip('"').strip("'")
    return ''


def _openai_nutrition_estimate(dish_name, preparation_style, grams):
    api_key = _env_value('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError('OPENAI_API_KEY was not found in .env.')

    model = _env_value('OPENAI_MODEL') or 'gpt-5.6-sol'
    payload = {
        'model': model,
        'reasoning': {'effort': 'high'},
        'input': [
            {
                'role': 'system',
                'content': [
                    {
                        'type': 'input_text',
                        'text': (
                            'You estimate nutrition for food eaten in Hyderabad, India, especially Telugu/Andhra/Telangana foods. '
                            'Prioritize regional realism over generic nutrition tables. Think carefully and be accuracy-focused, not optimistic. '
                            'Account for oil, tadka, ghee, butter, cream, nuts, peanuts, coconut, chutneys, podi, frying, added sugar, jaggery, sauces, rice-heavy plates, '
                            'restaurant oil, street-food oil reuse, and normal Indian serving variance. The user may enter one food or a comma/newline-separated '
                            'list of multiple foods; treat the full input as one combined meal/plate and return the combined calories, protein_g, fat_g, carbs_g, fibre_g, and sugar_g. '
                            'Interpret sugar_g strictly as added sugar only: refined sugar, jaggery, honey, syrups, sweet sauces, sweetened drinks, sweetened packaged ingredients, dessert sugar, '
                            'or sugar added during cooking. Do not count natural sugars from unsweetened fruit, plain milk/curd, vegetables, grains, dal, or coconut as sugar_g, though they still count in carbs_g. '
                            'Model the preparation method explicitly. For Hyderabad street-style fried rice, for example, assume typical wok cooking with cooked rice, '
                            'noticeable oil, soy/chilli sauces, limited vegetables unless stated, and common street-vendor portion sizes. For restaurant style, assume richer '
                            'oil/butter/cream/cashew use where relevant. For Telugu home style, assume household tadka, regional chutneys, dal, rice, curry, and controlled but real oil. '
                            'For diet-focused style, reduce oil only when the food text supports it, but do not make unrealistically lean assumptions. '
                            'For carbohydrates, fibre, and added sugar, account for rice, wheat, maida, dosa/idli batter, potatoes, sweets, jaggery, chutneys, fruits, sauces, packaged ingredients, '
                            'and the realistic fibre loss or gain from refined grains, dal, legumes, vegetables, peanuts, and coconut. '
                            'If item-level quantities are provided in natural language, use them: examples include 3 idlis, seven-inch pizza, half 10-inch dosa, '
                            '1 bowl rice, 2 ladles sambar, one small plate biryani, or one cup curd. If a total gram value is also provided, treat it as the '
                            'highest-priority serving weight and distribute that weight across the listed items using realistic Hyderabad/Telugu proportions. '
                            'If no grams are provided, infer a realistic total weight and serving size from the food text, preparation style, and local portion norms. '
                            'Return an item-by-item breakdown so the user can judge the estimate before approval. Include inferred grams and cooking assumptions for each item. '
                            'The sum of the breakdown should approximately match the top-level total, allowing small rounding differences. '
                            'If the input is ambiguous, choose the most likely Hyderabad/Telugu interpretation and mention uncertainty briefly in explanation. '
                            'You may include short normalized food names in the temporary breakdown response, but do not echo long user-provided text verbatim.'
                        ),
                    }
                ],
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'input_text',
                        'text': json.dumps(
                            {
                                'food_items_text': dish_name,
                                'preparation_style': preparation_style,
                                'total_serving_grams': str(grams) if grams is not None else None,
                                'grams_were_provided': grams is not None,
                                'locale': 'Hyderabad, Telangana, India',
                                'cuisine_context': 'Telugu homestyle, Andhra/Telangana, Hyderabad-local assumptions',
                                'estimation_instruction': (
                                    'Return one combined total for all listed items, not separate line items. '
                                    'When grams are missing, infer the serving from the portion words in food_items_text. '
                                    'Also return a visible breakdown that explains quantity, grams, oil/cooking assumptions, and macro contribution per item, including carbs, fibre, and added sugar only.'
                                ),
                            }
                        ),
                    }
                ],
            },
        ],
        'text': {
            'format': {
                'type': 'json_schema',
                'name': 'nutrition_estimate',
                'strict': True,
                'schema': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'calories': {'type': 'integer', 'minimum': 0},
                        'protein_g': {'type': 'number', 'minimum': 0},
                        'fat_g': {'type': 'number', 'minimum': 0},
                        'carbs_g': {'type': 'number', 'minimum': 0},
                        'fibre_g': {'type': 'number', 'minimum': 0},
                        'sugar_g': {'type': 'number', 'minimum': 0},
                        'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                        'explanation': {'type': 'string'},
                        'methodology': {'type': 'string'},
                        'breakdown': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'additionalProperties': False,
                                'properties': {
                                    'item': {'type': 'string'},
                                    'assumed_quantity': {'type': 'string'},
                                    'assumed_grams': {'type': 'number', 'minimum': 0},
                                    'calories': {'type': 'integer', 'minimum': 0},
                                    'protein_g': {'type': 'number', 'minimum': 0},
                                    'fat_g': {'type': 'number', 'minimum': 0},
                                    'carbs_g': {'type': 'number', 'minimum': 0},
                                    'fibre_g': {'type': 'number', 'minimum': 0},
                                    'sugar_g': {'type': 'number', 'minimum': 0},
                                    'cooking_assumption': {'type': 'string'},
                                },
                                'required': [
                                    'item',
                                    'assumed_quantity',
                                    'assumed_grams',
                                    'calories',
                                    'protein_g',
                                    'fat_g',
                                    'carbs_g',
                                    'fibre_g',
                                    'sugar_g',
                                    'cooking_assumption',
                                ],
                            },
                        },
                    },
                    'required': [
                        'calories',
                        'protein_g',
                        'fat_g',
                        'carbs_g',
                        'fibre_g',
                        'sugar_g',
                        'confidence',
                        'explanation',
                        'methodology',
                        'breakdown',
                    ],
                },
            }
        },
    }

    request = urllib.request.Request(
        'https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response_payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8')
        raise RuntimeError(f'OpenAI estimate failed: {detail[:300]}')
    except urllib.error.URLError as exc:
        raise RuntimeError(f'OpenAI estimate failed: {exc.reason}')

    text = response_payload.get('output_text') or _extract_response_text(response_payload)
    try:
        estimate = json.loads(text)
        return {
            'calories': _positive_int(estimate.get('calories'), 'calories'),
            'protein_g': _decimal_payload(_positive_decimal(estimate.get('protein_g'), 'protein')),
            'fat_g': _decimal_payload(_positive_decimal(estimate.get('fat_g'), 'fat')),
            'carbs_g': _decimal_payload(_positive_decimal(estimate.get('carbs_g'), 'carbohydrates')),
            'fibre_g': _decimal_payload(_positive_decimal(estimate.get('fibre_g'), 'fibre')),
            'sugar_g': _decimal_payload(_positive_decimal(estimate.get('sugar_g'), 'added sugar')),
            'confidence': estimate.get('confidence', 'medium'),
            'explanation': str(estimate.get('explanation', '')).strip()[:520],
            'methodology': str(estimate.get('methodology', '')).strip()[:520],
            'breakdown': _estimate_breakdown_payload(estimate.get('breakdown', [])),
        }
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError('OpenAI returned an estimate in an unexpected format.')


def _estimate_breakdown_payload(items):
    if not isinstance(items, list):
        return []

    breakdown = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        breakdown.append(
            {
                'item': str(item.get('item', 'Item')).strip()[:80],
                'assumed_quantity': str(item.get('assumed_quantity', '')).strip()[:80],
                'assumed_grams': _decimal_payload(_positive_decimal(item.get('assumed_grams', 0), 'grams')),
                'calories': _positive_int(item.get('calories', 0), 'calories'),
                'protein_g': _decimal_payload(_positive_decimal(item.get('protein_g', 0), 'protein')),
                'fat_g': _decimal_payload(_positive_decimal(item.get('fat_g', 0), 'fat')),
                'carbs_g': _decimal_payload(_positive_decimal(item.get('carbs_g', 0), 'carbohydrates')),
                'fibre_g': _decimal_payload(_positive_decimal(item.get('fibre_g', 0), 'fibre')),
                'sugar_g': _decimal_payload(_positive_decimal(item.get('sugar_g', 0), 'added sugar')),
                'cooking_assumption': str(item.get('cooking_assumption', '')).strip()[:180],
            }
        )
    return breakdown


def _extract_response_text(response_payload):
    chunks = []
    for item in response_payload.get('output', []):
        for content in item.get('content', []):
            if content.get('type') in {'output_text', 'text'} and content.get('text'):
                chunks.append(content['text'])
    return ''.join(chunks)

# Create your views here.
