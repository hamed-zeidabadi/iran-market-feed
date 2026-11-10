"""Source times and output clock: IANA Tehran timezone, validated Jalali calendar."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import jdatetime

TEHRAN = ZoneInfo('Asia/Tehran')


def jalali_to_date(year, month, day):
    date = jdatetime.date(year, month, day).togregorian()
    return date.year, date.month, date.day


def output_clock(now):
    if now.tzinfo is None:
        raise ValueError('output clock requires timezone')
    instant = now.astimezone(timezone.utc).replace(microsecond=0)
    local = instant.astimezone(TEHRAN)
    date = jdatetime.date.fromgregorian(date=local.date())
    return {
        'date': f'{date.year:04d}/{date.month:02d}/{date.day:02d}',
        'time': local.strftime('%H:%M'),
        'datetime': instant.isoformat().replace('+00:00', 'Z'),
        'timestamp': int(instant.timestamp()),
        'timezone': 'Asia/Tehran',
    }
