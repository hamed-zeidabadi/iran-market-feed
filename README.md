# Iran Market Feed | نرخ بازار ایران
![Last update](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fhamed-zeidabadi.github.io%2Firan-market-feed%2Fapi%2Fprices.json&query=%24%5B%27date%27%2C%27time%27%5D&label=updated%20%28Tehran%29&cacheSeconds=300)

**API:** [prices.json](https://hamed-zeidabadi.github.io/iran-market-feed/api/prices.json)

| کلید در `prices` | معنی | واحد |
|---|---|---|
| `gold_18k` | طلای ۱۸ عیار | تومان برای یک گرم |
| `silver_999` | نقرهٔ ۹۹۹ | تومان برای یک گرم |
| `coin_bahar` | سکه بهار آزادی | تومان برای یک عدد |
| `coin_emami` | سکه امامی | تومان برای یک عدد |
| `coin_half` | نیم سکه | تومان برای یک عدد |
| `coin_quarter` | ربع سکه | تومان برای یک عدد |
| `gold_ounce` | اونس جهانی طلا | دلار برای یک اونس تروا |
| `usdt` | تتر | تومان برای یک تتر |

`price` برابر `null` یعنی دادهٔ معتبر نیست. `status`: `ok`، `provisional`، `disputed` یا `unavailable`.

`parsian`: سکهٔ پارسیان با عیار ۷۵۰ به تفکیک وزن؛ `parsian_1000mg` یعنی یک گرم.

| زمان | معنی |
|---|---|
| `date` / `time` | تاریخ شمسی و ساعت `Asia/Tehran` |
| `datetime` / `timestamp` | UTC با `Z` / Unix بر حسب ثانیه |

`sources` برای هر منبع و هر قلم این‌ها را دارد: `price`، `difference` (منبع منهای قیمت نهایی)، `is_close`، `is_fresh`، `used_in_reference`، `quote_type`، `reason`، `published_at` (زمان اعلام منبع) و `observed_at` (زمان دریافت). منابع «فقط مقایسه‌ای» در قیمت نهایی رأی ندارند (`used_in_reference` برابر `false`).
