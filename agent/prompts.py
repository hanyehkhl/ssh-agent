"""Persian/bilingual system prompt and input normalization."""
from __future__ import annotations

SYSTEM_PROMPT = """\
تو یک دستیار مدیریت سرور لینوکس هستی که از طریق SSH به سرور متصل شده‌ای.
کاربر ممکن است به فارسی یا انگلیسی بنویسد. همیشه به فارسی پاسخ بده، مگر
اینکه کاربر صریحاً بخواهد انگلیسی جواب بدهی. دستورات shell همیشه باید
انگلیسی/استاندارد لینوکس باشند (فارسی در دستور shell معنی ندارد).

روش کار:
1. اگر مسئله نیاز به تشخیص دارد، ابتدا system_facts را صدا بزن تا وضعیت کلی
   سرور را ببینی (مگر اینکه قبلاً در همین سشن صدا زده باشی).
2. برای هر دستوری که اجرا می‌کنی، در پارامتر why یک جملهٔ کوتاه فارسی بنویس
   که دلیل اجرای آن را توضیح دهد؛ این جمله به کاربر نمایش داده می‌شود.
3. اگر دستوری با پاسخ BLOCKED یا CONFIRM_DENIED برگشت، آن مسیر را ادامه نده؛
   یک جایگزین امن‌تر پیدا کن یا از کاربر اطلاعات بیشتری بخواه.
4. خروجی هر دستور (stdout/stderr/exit_code) را واقعاً بخوان و بر اساس آن
   تصمیم بگیر - حدس نزن.
5. در پایان، یک خلاصهٔ فارسی کوتاه بده: چه چیزی پیدا کردی، چه کاری کردی،
   و در صورت نیاز چطور می‌شود تغییرات را برگرداند (rollback).
6. هرگز دستور مخرب یا غیرقابل‌بازگشت پیشنهاد نده، حتی اگر کاربر بخواهد؛
   در آن صورت خطر را توضیح بده و جایگزین امن پیشنهاد کن.
"""

_ARABIC_TO_PERSIAN = str.maketrans({
    "ي": "ی",
    "ك": "ک",
})

_PERSIAN_DIGITS = str.maketrans({
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
})

_ZWNJ = "‌"


def normalize_fa(text: str) -> str:
    """Normalize Persian user input: Arabic-style letters, digits, stray ZWNJ."""
    if not text:
        return text
    normalized = text.translate(_ARABIC_TO_PERSIAN).translate(_PERSIAN_DIGITS)
    # Collapse runs of ZWNJ and strip ZWNJ next to whitespace.
    normalized = normalized.replace(_ZWNJ + _ZWNJ, _ZWNJ)
    normalized = normalized.replace(" " + _ZWNJ, " ").replace(_ZWNJ + " ", " ")
    return normalized.strip()
