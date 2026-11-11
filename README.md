# Iran Market Feed | نرخ بازار ایران

![Last update](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fhamed-zeidabadi.github.io%2Firan-market-feed%2Fapi%2Fprices.json&query=%24%5B%27date%27%2C%27time%27%5D&label=updated%20%28Tehran%29&cacheSeconds=300)

| خروجی قیمت | معنی | واحد |
|---|---|---|
| `gold_18k` | طلای ۱۸ عیار | تومان برای یک گرم |
| `silver_999` | نقرهٔ ۹۹۹ | تومان برای یک گرم |
| `coin_bahar` | سکه بهار آزادی | تومان برای یک عدد |
| `coin_emami` | سکه امامی | تومان برای یک عدد |
| `coin_half` | نیم سکه | تومان برای یک عدد |
| `coin_quarter` | ربع سکه | تومان برای یک عدد |
| `gold_ounce` | اونس جهانی طلا | دلار برای یک اونس تروا |
| `usdt` | تتر | تومان برای یک تتر |

`prices` قیمت‌های نهایی را دارد. `price` مقدار قیمت است؛ `null` یعنی دادهٔ معتبر موجود نیست.

`parsian` قیمت پارسیان با عیار ۷۵۰ را به تفکیک وزن دارد؛ مثلاً `parsian_1000mg` یعنی یک گرم. واحد قیمت تومان برای یک عدد است؛ `weight_grams` وزن به گرم است. نرخ مرجع `indicative_ex_vat` بدون مالیات است؛ `metal_value` فقط ارزش طلای خام و مقایسه‌ای است. جزئیات هر منبع در `sources[شناسه].parsian` است.

| زمان خروجی | معنی |
|---|---|
| `date` | تاریخ شمسی ایران؛ `YYYY/MM/DD` |
| `time` | ساعت ایران؛ `HH:mm` |
| `datetime` | زمان UTC با پسوند `Z` |
| `timestamp` | زمان Unix بر حسب ثانیه |
| `timezone` | منطقهٔ زمانی؛ `Asia/Tehran` |

`status` وضعیت هر قیمت است: `ok` شواهد کافی، `provisional` منابع محدود، `disputed` اختلاف زیاد و `unavailable` نبود دادهٔ معتبر.

`sources` جزئیات منابع را دارد:

- `is_success`: دادهٔ معتبر استخراج شده است.
- `is_fresh`: زمان اعلام قیمت تازه است.
- `used_in_reference`: این منبع در قیمت نهایی نقش داشته است.
- `difference`: قیمت منبع منهای قیمت نهایی.
- `difference_rial`: همان اختلاف به ریال؛ برای اونس `null` است.
- `is_close`: اختلاف در محدودهٔ مجاز است؛ اگر مقایسه ممکن نباشد `null` است.
- `published_at`: زمان اعلام قیمت.
- `observed_at`: زمان دریافت قیمت.
