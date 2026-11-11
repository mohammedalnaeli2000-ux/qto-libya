"""توحيد النص العربي للمطابقة: همزات، ياء/ألف مقصورة، تاء مربوطة، تشكيل، تطويل."""
from __future__ import annotations
import re

_TASHKEEL = re.compile(r"[ً-ٰٟـ]")
_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
                      "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})


def norm_ar(s: str) -> str:
    return _TASHKEEL.sub("", s or "").translate(_MAP).lower()


def tokens(s: str) -> list[str]:
    """رموز الكلمات بعد التوحيد وإزالة 'ال' التعريف من بداية العربية."""
    out = []
    for t in re.findall(r"[a-z]+|[؀-ۿ]+|\d+", norm_ar(s)):
        if t.startswith("ال") and len(t) > 3:
            t = t[2:]
        out.append(t)
    return out
