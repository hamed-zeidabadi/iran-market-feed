# قرارداد API، نسخهٔ ۳

`GET /api/prices.json` — بدون پارامتر، کلید API یا دیتابیس؛ پاسخ `application/json`.

نمونهٔ کامل فعلی: [../public/api/prices.json](../public/api/prices.json).
تعریف دقیق تمام فیلدها و نوع‌ها: [prices.schema.json](prices.schema.json).
این فایل آخرین snapshot است و در هر درخواست دوباره از منابع دریافت نمی‌شود.

```js
const response = await fetch('/api/prices.json');
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const feed = await response.json();
console.log(feed.date, feed.time, feed.datetime, feed.timestamp, feed.timezone);
for (const [asset, value] of Object.entries(feed.prices)) {
  console.log(asset, value.price, value.currency, value.unit, value.status);
}
```

در استفاده بین دو دامنه، نشانی کامل GitHub Pages را جایگزین کنید. همچنین سن `datetime` و زمان اعلام `published_at`، وضعیت و تعداد ناشران هر قلم را بررسی کنید.

## ساعت خروجی

- `date`: شمسی به وقت ایران، `YYYY/MM/DD`.
- `time`: ساعت ایران، `HH:mm`.
- `datetime`: همان لحظه در UTC، ISO 8601 با `Z`.
- `timestamp`: همان لحظه، عدد صحیح Unix بر حسب ثانیه.
- `timezone`: مقدار ثابت `Asia/Tehran`.

این پنج فیلد از یک لحظهٔ پایان اجرا ساخته می‌شوند. `published_at` و `observed_at` هر قلم مستقل‌اند؛ زمان‌های منبع در خروجی به UTC نرمال می‌شوند.
`published_at` در قیمت نهایی جدیدترین زمان اعلام بین مشارکت‌کنندگان است؛ برای مشاهدهٔ زمان تک‌تک آن‌ها به `sources` مراجعه کنید. زمان قدیمی‌ترین مشارکت‌کننده را نباید از این فیلد استنتاج کرد.

## قیمت‌ها و واحدها

تمام هشت کلید همیشه حاضرند. طلا و نقره: `IRT/gram` با عیار `750` و `999`؛ چهار نوع سکه: `IRT/coin`؛ اونس: `USD/troy_ounce`؛ تتر: `IRT/USDT`.

نسخهٔ ۳ هشت قلم `prices` را حفظ می‌کند و بخش ثابت `parsian` را به سطح اصلی و هر منبع اضافه می‌کند. ۲۸ وزن از ۵۰ میلی‌گرم تا ۳ گرم تعریف شده‌اند؛ نام دقیق وزن‌ها در schema است. نمونه: `feed.parsian.parsian_1000mg.price` و `feed.sources.tala_parsian.parsian.parsian_1000mg.published_at`.
هر قلم پارسیان `weight_grams` عددی، عیار ۷۵۰، واحد coin و قیمت تومانی دارد. قیمت‌ها از نرخ همان وزن استخراج می‌شوند؛ نرخ وزن دیگر محاسبه یا حدس زده نمی‌شود. مرجع `indicative_ex_vat` از نرخ اعلامی بدون مالیات است، نه قیمت قطعی فروشگاه. `metal_value` تابان‌گوهر صرفاً ارزش طلای خام است و در مرجع محصول رأی ندارد. وزن فاقد نرخ هم‌نوع زمان‌دار، price=null دارد.
`quote_type` برای تتر `midpoint` و برای نرخ‌های تابلو `indicative` است. `price` عدد JSON یا `null` است. `direction` به‌دلیل نبود تاریخچه همیشه `null` است.
`status` یکی از `ok`، `provisional`، `disputed` و `unavailable` است؛ دو وضعیت اول تعداد شواهد را بیان می‌کنند و اختلاف بیش از ۲٪ وضعیت را به `disputed` تبدیل می‌کند. برای disputed هم میانه منتشر می‌شود؛ برای unavailable قیمت null است.

## منابع

فقط منابع فعال در `sources` هستند؛ هرکدام ساختار ثابت برای تمام هشت قلم دارند، حتی وقتی دادهٔ یک قلم موجود نباشد.
`is_success` استخراج معتبر است؛ `is_fresh` تازگی، `is_close` نزدیکی به مرجع و `used_in_reference` حضور واقعی در میانه‌اند. این پرچم‌ها مستقل‌اند.
`reference_eligible: false` اجازهٔ رأی به آن مسیر نمی‌دهد، حتی اگر معتبر و تازه باشد. دادهٔ بدون زمان اعلام نیز رأی ندارد.
مرجع تتر فقط از `midpoint`های زمان‌دار و هم‌نوع ساخته می‌شود. نرخ `indicative` کانال جدید DollarGold صرفاً برای مقایسه است و `used_in_reference` آن false می‌ماند. صفحه و API نوبیتکس یک group هستند و یک رأی دارند.
برای snapshot مستقیم WebSocket تبدیل، `http_status` برابر null است؛ در fallback مستند REST برابر 200 می‌شود و `quote_type` آن `last_trade` است. این fallback در مرجع midpoint رأی ندارد. وضعیت `parsed` فقط دریافت قابل استخراج را بیان می‌کند؛ `is_fresh` و زمان اعلام را جدا بررسی کنید. timestamp رویداد/معامله در `published_at` است و زمان دریافت در `observed_at` باقی می‌ماند.
`difference = source price - final price` است. برای IRT، `difference_rial = difference × 10`؛ برای USD مقدار آن null است. `is_close` قدرمطلق اختلاف را با آستانهٔ تنظیم‌شده مقایسه می‌کند؛ در نبود قیمت یا مرجع، هر سه فیلد مقایسه null هستند.
`is_success` یک منبع یعنی دست‌کم یک قلم معتبر استخراج شده است؛ برای بررسی پوشش، وضعیت تک‌تک اقلام را بخوانید.
منبع قدیمی می‌تواند `is_success: true` ولی `is_fresh: false` و `used_in_reference: false` داشته باشد. نبود داده با مقدار صفر جایگزین نمی‌شود.

نسخهٔ ۲ به‌جای `updated_at` و `updated_at_tehran` از قرارداد پنج‌فیلدی بالا استفاده می‌کند؛ مصرف‌کنندگان نسخهٔ قبلی باید مهاجرت کنند.

## بروزرسانی خودکار

زمان‌بندی هدف هر ۵ دقیقه است و `refresh_interval_seconds` برابر ۳۰۰ است. Worker موجود هر ۵ دقیقه workflow را فراخوانی می‌کند؛ cron پشتیبان GitHub هر ۱۰ دقیقه در دقیقه‌های ۷، ۱۷، ۲۷، ۳۷، ۴۷ و ۵۷ UTC حفظ شده است. push و اجرای دستی نیز فعال‌اند. GitHub ممکن است اجرای schedule را با تأخیر انجام دهد یا در بار زیاد حذف کند؛ زمان واقعی تولید را از datetime بخوانید. دریافت جدید، زمان اعلام نرخ منبع را تازه نمی‌کند.

### شفافیت و پایش کیفیت

- `oldest_contributor_at`: زمان قدیمی‌ترین رأی استفاده‌شده، در کنار `published_at` که تازه‌ترین رأی است.
- `max_contributor_age_seconds`: سن قدیمی‌ترین رأی در زمان تولید گزارش؛ مصرف‌کننده باید زمان سپری‌شده از تولید گزارش را نیز به آن اضافه کند.
- `reference_range`: کمینه و بیشینهٔ همان رأی‌های انتخاب‌شده، یا `null` در نبود رأی.
- `quality_warnings`: شامل `source_disagreement` برای اختلاف زیاد و `insufficient_groups` برای کمبود گروه؛ این هشدارها میانه را تغییر نمی‌دهند.
- `market`: تتر `open` و ۲۴/۷ است. سایر اقلام تا تنظیم تقویم معتبر `unknown` هستند؛ تعطیلات و ساعت رسمی حدس زده نمی‌شوند.
- `last_valid`: آخرین مرجع با وضعیت `ok` همراه `accepted_at`، زمان‌های منابع، مشارکت‌کنندگان و سن قدیمی‌ترین رأی. هیچ‌گاه جایگزین `price` زنده نمی‌شود؛ مرجع `disputed` یا `provisional` سابقهٔ معتبر را بازنویسی نمی‌کند.
- `sources.<id>.prices.<asset>.monitoring`: شمار نمونه‌ها، نسبت استخراج موفق و تازه، تعداد و میانهٔ فاصلهٔ زمان‌های اعلام مشاهده‌شده، سن اعلام، فاصلهٔ دریافت تا تولید گزارش و هشدار ثابت ماندن مشکوک. فیلد برای اقلامی که منبع پوشش نمی‌دهد وجود ندارد. این آمار فقط هشدار است؛ وزن‌دهی یا حذف خودکار انجام نمی‌شود.

حالت پایش در `.validation/monitoring.json` خارج از public ذخیره و در Actions با cache منتقل می‌شود. حذف/انقضای cache آمار و `last_valid` را از نو شروع می‌کند؛ تاریخچهٔ قیمت منتشر نمی‌شود. فاصله‌های اعلام صرفاً cadence مشاهده‌شده‌اند، نه تضمین ریتم واقعی تمام رویدادها. سقف تازگی فعلی ۵۴۰۰ ثانیه تغییر نکرده است. تنظیمات اختیاری `asset_policies.<asset>.max_age_seconds` و `sources[].asset_policies.<asset>.max_age_seconds` برای اعمال آستانهٔ اندازه‌گیری‌شده وجود دارند؛ تنظیم منبع اولویت دارد.

تقویم اختیاری در `market_sessions.<asset>` با `weekdays` (دوشنبه=۰ تا یکشنبه=۶)، `open`/`close` به ساعت تهران و `closed_dates` میلادی تعریف می‌شود. وضعیت بازار فقط اطلاعاتی است و دادهٔ قدیمی را تازه نمی‌کند.

پس از deploy، `scripts/check_publication.py` با تطبیق زمان گزارش منتشرشده، فاصلهٔ دریافت تا مشاهدهٔ انتشار را در لاگ ثبت می‌کند. این عدد کران بالای تأخیر انتشار است، نه timestamp دقیق شروع سرویس‌دهی Pages. شکست اندازه‌گیری، deploy موفق را ناموفق اعلام نمی‌کند.
