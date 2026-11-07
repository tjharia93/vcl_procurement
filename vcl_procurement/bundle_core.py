"""Bundle identity preparation. Counts here are declared contents, never stock balances."""
import json
import math
import re


def whole(value, label, maximum=100000):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a positive whole number.')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a positive whole number.')
    if not math.isfinite(number) or number < 1 or number > maximum or int(number) != number:
        raise ValueError(f'{label} must be a positive whole number (maximum {maximum}).')
    return int(number)


def text(value, label, limit=140):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{label} is required (maximum {limit} characters).')
    return value.strip()


def clean_plan(value):
    if not isinstance(value, dict):
        raise ValueError('Enter the bundle preparation details.')
    plan = {key: text(value.get(key), label) for key, label in (
        ('company','Company'),('material','Material'),('intended_warehouse','Intended warehouse'))}
    plan.update({key: whole(value.get(key), label) for key, label in (
        ('width_mm','Sheet width'),('length_mm','Sheet length'),('gsm','GSM'),
        ('sheets_per_ream','Sheets per ream'),('reams_per_bundle','Reams per bundle'))})
    rows = value.get('lines')
    if not isinstance(rows, list) or not rows or len(rows) > 50:
        raise ValueError('Enter one or more colour/count lines (maximum 50).')
    seen = set()
    plan['lines'] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each colour needs a bundle count.')
        colour = text(row.get('colour'), 'Colour', 60)
        if colour.casefold() in seen:
            raise ValueError('Enter each colour only once.')
        seen.add(colour.casefold())
        plan['lines'].append(dict(colour=colour, count=whole(row.get('count'),'Bundle count',1000)))
    plan['lines'].sort(key=lambda r:r['colour'].casefold())
    if sum(r['count'] for r in plan['lines']) > 1000:
        raise ValueError('Prepare at most 1000 bundles per registration.')
    if totals(plan)['total_sheets'] > 2147483647:
        raise ValueError('The declared sheet count exceeds the supported quantity. Split this preparation into smaller runs.')
    return plan


def canonical(plan):
    return json.dumps(plan, sort_keys=True, separators=(',',':'))


def registration_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{2,79}', value):
        raise ValueError('Use a preparation reference of 3–80 uppercase letters, numbers or hyphens.')
    return value


def totals(plan):
    bundles = sum(r['count'] for r in plan['lines'])
    reams = bundles * plan['reams_per_bundle']
    return dict(total_bundles=bundles,total_reams=reams,total_sheets=reams*plan['sheets_per_ream'])
