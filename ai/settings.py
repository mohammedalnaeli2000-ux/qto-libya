"""حفظ إعدادات الذكاء الاصطناعي لكل مستخدم في ~/.qto_libya/ai.json (المفاتيح لكل مزوّد).

مفتاح مدمج: نسخة الاختبار الموزّعة قد تحمل ملف config/ai_default.json (يُولَّد وقت بناء exe فقط، ولا يوجد في
الكود المصدري) فيه مزوّد ونموذج ومفتاح المطوّر، حتى يعمل المساعد عند المهندسين دون إنشاء مفتاح خاص بهم.
مفتاح المستخدم (إن أدخله) يتقدّم دائماً على المدمج، والمدمج لا يُعرض في الواجهة ولا يُكتب في ai.json.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

from core.config import CONFIG_DIR
from .client import AiClient, AiUnavailable, providers

PATH = Path.home() / ".qto_libya" / "ai.json"
BUNDLED_PATH = CONFIG_DIR / "ai_default.json"


def bundled() -> dict:
    """{provider, model, key} المدمجة في نسخة الاختبار، أو {}."""
    try:
        d = json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))
        return d if d.get("provider") and d.get("key") else {}
    except (OSError, ValueError):
        return {}


def load() -> dict:
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        first_run = False
    except (OSError, ValueError):
        d, first_run = {}, True
    b = bundled()
    if first_run and b:                              # أول تشغيل لنسخة فيها مفتاح مدمج: يعمل مباشرة
        d.update(enabled=True, provider=b["provider"], model=b.get("model", ""))
    d.setdefault("enabled", False); d.setdefault("provider", "gemini"); d.setdefault("model", "")
    d.setdefault("base_urls", {}); d.setdefault("keys", {})
    return d


def save(d: dict, path: Path = PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(path, 0o600)                       # المفتاح سرّي: قراءة المالك فقط (لينكس/ماك)
    except OSError:
        pass


def uses_bundled(d: Optional[dict] = None) -> bool:
    d = d or load()
    b = bundled()
    return bool(b) and d.get("provider") == b["provider"] and not d.get("keys", {}).get(b["provider"])


def build_client(d: Optional[dict] = None) -> Optional[AiClient]:
    """عميل جاهز من الإعدادات المحفوظة، أو None (وضع القواعد وحدها)."""
    d = d or load()
    if not d.get("enabled"):
        return None
    p = d["provider"]
    spec = providers().get(p)
    if not spec:
        return None
    b = bundled()
    key = d["keys"].get(p, "") or (b["key"] if b and b["provider"] == p else "")
    model = d.get("model") or (b.get("model") if b and b["provider"] == p else "") or (spec["models"][0] if spec["models"] else "")
    try:
        return AiClient(p, model, key, d["base_urls"].get(p, ""))
    except AiUnavailable:
        return None
