import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import NutritionEntry


PIN_CODE = '5909'
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
            'is_unlocked': request.session.get('pin_ok', False),
        },
    )


@require_POST
def unlock(request):
    data = _json_body(request)
    if data.get('pin') == PIN_CODE:
        request.session['pin_ok'] = True
        return JsonResponse({'ok': True})

    return JsonResponse({'ok': False, 'error': 'That PIN did not match.'}, status=403)


@require_POST
def logout(request):
    request.session.flush()
    return JsonResponse({'ok': True})


@require_GET
def day_detail(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

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
        )
        .order_by('date')
    )

    days = {}
    for row in rows:
        totals = _status_payload(
            int(row['calories'] or 0),
            Decimal(row['protein_g'] or 0),
            Decimal(row['fat_g'] or 0),
        )
        days[row['date'].isoformat()] = totals

    return JsonResponse({'days': days})


@require_POST
def create_entry(request, selected_date):
    auth_error = _auth_error(request)
    if auth_error:
        return auth_error

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
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    entry = NutritionEntry.objects.create(
        date=entry_date,
        source=source,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
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
        grams = _positive_decimal(data.get('grams'), 'grams')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    try:
        estimate = _openai_nutrition_estimate(dish_name, PREPARATION_STYLES[style], grams)
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse({'estimate': estimate})


def _auth_error(request):
    if request.session.get('pin_ok'):
        return None
    return JsonResponse({'error': 'Please unlock the tracker first.'}, status=401)


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


def _entry_payload(entry):
    return {
        'id': entry.id,
        'source': entry.source,
        'source_label': entry.get_source_display(),
        'calories': entry.calories,
        'protein_g': _decimal_payload(entry.protein_g),
        'fat_g': _decimal_payload(entry.fat_g),
        'created_at': entry.created_at.isoformat(),
    }


def _totals_payload(entries):
    totals = entries.aggregate(
        calories=Sum('calories'),
        protein_g=Sum('protein_g'),
        fat_g=Sum('fat_g'),
    )
    calories = int(totals['calories'] or 0)
    protein_g = Decimal(totals['protein_g'] or 0)
    fat_g = Decimal(totals['fat_g'] or 0)
    return _status_payload(calories, protein_g, fat_g)


def _status_payload(calories, protein_g, fat_g):
    protein_g = Decimal(protein_g).quantize(Decimal('0.1'))
    fat_g = Decimal(fat_g).quantize(Decimal('0.1'))

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
        'targets': {
            'calories_max': CALORIE_LIMIT,
            'protein_min_g': _decimal_payload(PROTEIN_MINIMUM),
            'fat_max_g': _decimal_payload(FAT_LIMIT),
        },
        'messages': messages,
    }


def _decimal_payload(value):
    return float(Decimal(value).quantize(Decimal('0.1')))


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
        'reasoning': {'effort': 'low'},
        'input': [
            {
                'role': 'system',
                'content': [
                    {
                        'type': 'input_text',
                        'text': (
                            'You estimate nutrition for food eaten in Hyderabad, India, especially Telugu/Andhra/Telangana foods. '
                            'Prioritize regional realism over generic nutrition tables. Be brutally honest and accuracy-focused, not optimistic. '
                            'Account for oil, tadka, ghee, butter, cream, nuts, peanuts, coconut, chutneys, podi, frying, sugar, sauces, rice-heavy plates, '
                            'restaurant oil, street-food oil reuse, and normal Indian serving variance. The user may enter one food or a comma/newline-separated '
                            'list of multiple foods; treat the full input as one combined meal/plate and return the combined calories, protein_g, and fat_g. '
                            'If item-level quantities are provided, use them. If only one total gram value is provided, distribute that weight across the listed '
                            'items using realistic Hyderabad/Telugu serving proportions, then estimate the combined total for the exact grams. '
                            'If the input is ambiguous, choose the most likely Hyderabad/Telugu interpretation and mention uncertainty briefly in explanation. '
                            'Do not include dish names or the user-provided food text in your response.'
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
                                'total_serving_grams': str(grams),
                                'locale': 'Hyderabad, Telangana, India',
                                'cuisine_context': 'Telugu homestyle, Andhra/Telangana, Hyderabad-local assumptions',
                                'estimation_instruction': 'Return one combined total for all listed items, not separate line items.',
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
                        'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                        'explanation': {'type': 'string'},
                    },
                    'required': ['calories', 'protein_g', 'fat_g', 'confidence', 'explanation'],
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
            'confidence': estimate.get('confidence', 'medium'),
            'explanation': str(estimate.get('explanation', '')).strip()[:360],
        }
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError('OpenAI returned an estimate in an unexpected format.')


def _extract_response_text(response_payload):
    chunks = []
    for item in response_payload.get('output', []):
        for content in item.get('content', []):
            if content.get('type') in {'output_text', 'text'} and content.get('text'):
                chunks.append(content['text'])
    return ''.join(chunks)

# Create your views here.
