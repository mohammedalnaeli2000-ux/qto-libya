"""تحميل ملفات الإعداد (لا ثوابت مدفونة في الكود)."""
from __future__ import annotations
import copy, json
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# نسخة exe (PyInstaller): مجلد البرنامج مؤقت ويُحذف عند الإغلاق، فننسخ قاعدة اللائحة إلى مجلد المستخدم
# حتى تبقى الإصدارات المستوردة محفوظة بين مرات التشغيل.
import sys as _sys
if getattr(_sys, "frozen", False):
    import shutil as _shutil
    _user_data = Path.home() / ".qto_libya" / "data"
    _user_data.mkdir(parents=True, exist_ok=True)
    if not (_user_data / "boq.db").exists():
        _shutil.copy2(DATA_DIR / "boq.db", _user_data / "boq.db")
    DATA_DIR = _user_data


@lru_cache(maxsize=None)
def _load(name: str):
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def load(name: str):
    """نسخة مستقلة قابلة للتعديل من ملف إعداد."""
    return copy.deepcopy(_load(name))


def load_settings(overrides: dict | None = None) -> dict:
    s = load("settings.json")
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    return s
