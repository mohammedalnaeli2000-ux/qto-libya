"""عميل ذكاء اصطناعي موحّد لعدة مزوّدين (Gemini / OpenAI وكل المتوافقين معه / Anthropic) بمكتبة بايثون القياسية فقط.

حدود الدور (لا تتغير بتغيّر المزوّد):
- المهمة الوحيدة: تصنيف الفراغات الملتبسة، بمخطط JSON ثابت (id, type, confidence).
- ممنوع أن يعيد أو يعدّل أي كمية أو سعر: أي رد بحقول إضافية يُرفض ويُعاد الطلب (حتى 3 محاولات بانتظار متزايد).
- وضع عدم الاتصال: إن فشل الاتصال يُرمى AiUnavailable وتتحول الحالات الملتبسة إلى أسئلة للمستخدم.
"""
from __future__ import annotations
import json
import re
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from core import config


FALLBACK = {"gemini": ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.8-flash", "gemini-flash-lite-latest", "gemini-3.5-flash-lite"],
            "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]}     # بدائل عند انشغال النموذج


class AiUnavailable(Exception):
    pass


class SchemaError(Exception):
    pass


class AiHttpError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text[:300]}")
        self.status, self.text = status, text


def http_json(method: str, url: str, headers: dict, body: Optional[dict] = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "User-Agent": "QTO-Libya/2.0 (+https://qto-libya.streamlit.app)", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AiHttpError(e.code, e.read().decode("utf-8", "replace")) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AiUnavailable(f"تعذّر الاتصال: {getattr(e, 'reason', e)}") from e


def validate_classification(raw: str, allowed_ids: set[str], allowed_types: list[str]) -> dict[str, dict]:
    raw = raw.strip()
    if raw.startswith("```"):                       # بعض النماذج تغلّف JSON بعلامات كود
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        raw = raw[4:] if raw.lstrip().startswith("json") else raw
    data = json.loads(raw)
    items = data["results"] if isinstance(data, dict) else data
    out: dict[str, dict] = {}
    for it in items:
        if set(it.keys()) != {"id", "type", "confidence"}:
            raise SchemaError("حقول غير مسموحة في رد النموذج")
        if it["id"] not in allowed_ids or it["type"] not in allowed_types:
            raise SchemaError("معرّف أو نوع غير معروف")
        c = float(it["confidence"])
        if not 0.0 <= c <= 1.0:
            raise SchemaError("ثقة خارج المدى")
        out[it["id"]] = {"type": it["type"], "confidence": c}
    return out


def validate_option(raw: str, allowed_ids: set[str]) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        raw = raw[4:] if raw.lstrip().startswith("json") else raw
    d = json.loads(raw)
    if not isinstance(d, dict) or set(d.keys()) != {"id", "reason", "confidence"}:
        raise SchemaError("حقول غير مسموحة في رد النموذج")
    if str(d["id"]) not in allowed_ids:
        raise SchemaError("بند غير موجود ضمن الخيارات")
    c = float(d["confidence"])
    if not 0.0 <= c <= 1.0:
        raise SchemaError("ثقة خارج المدى")
    return {"id": str(d["id"]), "reason": str(d["reason"])[:300], "confidence": c}


def providers() -> dict:
    return config.load("ai_providers.json")


class AiClient:
    def __init__(self, provider: str, model: str, api_key: str = "", base_url: str = "",
                 transport: Callable = http_json, sleep: Callable[[float], None] = time.sleep):
        spec = providers().get(provider)
        if spec is None:
            raise AiUnavailable(f"مزوّد غير معروف: {provider}")
        self.provider, self.spec, self.kind = provider, spec, spec["kind"]
        self.model = model
        self.key = api_key or (os.environ.get(spec["key_env"], "") if spec.get("key_env") else "")
        self.base = (base_url or spec["base_url"]).rstrip("/")
        if spec["needs_key"] and not self.key:
            raise AiUnavailable("لا يوجد مفتاح API لهذا المزوّد")
        if not self.base:
            raise AiUnavailable("أدخل عنوان الخادم (Base URL)")
        self._t, self._sleep = transport, sleep

    # ---------- استدعاء النموذج ----------
    def generate(self, prompt: str) -> str:
        if self.kind == "gemini":
            r = self._t("POST", f"{self.base}/models/{self.model}:generateContent", {"x-goog-api-key": self.key},
                        {"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}})
            return r["candidates"][0]["content"]["parts"][0]["text"]
        if self.kind == "anthropic":
            r = self._t("POST", f"{self.base}/messages", {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                        {"model": self.model, "max_tokens": 2048, "temperature": 0.1,
                         "messages": [{"role": "user", "content": prompt}]})
            return "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        # OpenAI وكل المتوافقين: نجرّب كامل الخيارات ثم نتنازل عن ما يرفضه المزوّد (temperature / response_format)
        h = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        base = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        variants = [{**base, "temperature": 0.1, "response_format": {"type": "json_object"}},
                    {**base, "response_format": {"type": "json_object"}}, base]
        last: Exception | None = None
        for body in variants:
            try:
                r = self._t("POST", f"{self.base}/chat/completions", h, body)
                return r["choices"][0]["message"]["content"]
            except AiHttpError as e:
                if e.status not in (400, 422):
                    raise
                last = e
        raise last or AiUnavailable("رفض المزوّد الطلب")

    def chat(self, system: str, history: list[dict], max_tokens: int = 4096) -> str:
        """محادثة نصية حرة (المساعد) مع إعادة المحاولة عند ضغط الخادم (429/500/503)،
        ثم نماذج بديلة لنفس المزوّد (Gemini) إن بقي النموذج المختار مشغولاً."""
        if self.provider == "groq":
            max_tokens = min(max_tokens, 1500)          # حد Groq المجاني يحسب الرد المتوقع ضمن الطلب
        models = [self.model] + [m for m in FALLBACK.get(self.provider, []) if m != self.model]
        last: Exception | None = None
        for model in models:
            for attempt in range(3):
                try:
                    out = self._chat_once(model, system, history, max_tokens)
                    if out and out.strip():
                        return out
                    last = AiUnavailable("رد فارغ من النموذج")
                    break                                        # رد فارغ (نموذج استهلك حده في التفكير): جرّب البديل
                except AiHttpError as e:
                    last = e
                    if e.status in (401, 403):
                        try:
                            why = json.loads(e.text)["error"]["message"][:200]
                        except (ValueError, KeyError, TypeError):
                            why = e.text[:200]
                        raise AiUnavailable(f"مفتاح الذكاء الاصطناعي غير صالح أو بلا صلاحية ({e.status}: {why})") from e
                    if e.status == 400 and "API key" in e.text:
                        raise AiUnavailable("مفتاح الذكاء الاصطناعي غير صالح (400)") from e
                    if e.status in (404, 413):
                        break                                    # النموذج غير متاح أو حدّه أصغر من الطلب: جرّب البديل
                    if e.status not in (429, 500, 502, 503, 504):
                        raise
                    self._sleep(2 * (attempt + 1))
        if isinstance(last, AiHttpError) and last.status == 429:
            raise AiUnavailable("تجاوزنا حد الاستخدام المسموح للمساعد حالياً. حاول بعد دقائق.") from last
        raise AiUnavailable("خوادم الذكاء الاصطناعي مشغولة الآن (ضغط مؤقت). حاول بعد دقيقة.") from last

    def _chat_once(self, model: str, system: str, history: list[dict], max_tokens: int) -> str:
        if self.kind == "gemini":
            contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
            r = self._t("POST", f"{self.base}/models/{model}:generateContent", {"x-goog-api-key": self.key},
                        {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                         "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}}, timeout=120)
            cands = r.get("candidates") or []
            if not cands:
                raise AiUnavailable("لم يُرجع النموذج رداً (قد يكون الطلب محجوباً أو تجاوز الحد)")
            return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
        if self.kind == "anthropic":
            r = self._t("POST", f"{self.base}/messages", {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                        {"model": model, "max_tokens": max_tokens, "temperature": 0.3, "system": system,
                         "messages": [{"role": m["role"], "content": m["content"]} for m in history]}, timeout=120)
            return "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        h = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        msgs = [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in history]
        try:
            body = {"model": model, "messages": msgs, "temperature": 0.3, "max_tokens": max_tokens}
            if "gpt-oss" in model:
                body["reasoning_effort"] = "low"
            r = self._t("POST", f"{self.base}/chat/completions", h, body, timeout=120)
        except AiHttpError as e:
            if e.status not in (400, 422):
                raise
            r = self._t("POST", f"{self.base}/chat/completions", h, {"model": model, "messages": msgs}, timeout=120)
        txt = r["choices"][0]["message"]["content"] or ""
        return re.sub(r"<think>.*?</think>\s*", "", txt, flags=re.S)   # بعض النماذج تُرجع تفكيرها

    def list_models(self) -> list[str]:
        """القائمة الحقيقية الحالية من المزوّد (تشمل كل النماذج المتاحة لحسابك)."""
        if self.kind == "gemini":
            r = self._t("GET", f"{self.base}/models?pageSize=1000", {"x-goog-api-key": self.key})
            return sorted(m["name"].split("/", 1)[-1] for m in r.get("models", [])
                          if "generateContent" in m.get("supportedGenerationMethods", []))
        if self.kind == "anthropic":
            out, url = [], f"{self.base}/models?limit=1000"
            r = self._t("GET", url, {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
            return sorted(m["id"] for m in r.get("data", []))
        h = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        r = self._t("GET", f"{self.base}/models", h)
        return sorted(m["id"] for m in r.get("data", []))

    def test(self) -> tuple[bool, str]:
        try:
            out = self.generate('أرجع JSON فقط: {"ok": true}')
            return True, f"اتصال ناجح مع {self.spec['label']} / {self.model}"
        except AiHttpError as e:
            hint = {401: "المفتاح غير صالح", 403: "المفتاح لا يملك صلاحية", 404: "النموذج غير موجود لهذا المزوّد",
                    429: "تجاوزت حد الطلبات أو الرصيد"}.get(e.status, "")
            return False, f"{hint or 'فشل الطلب'} ({e})"
        except AiUnavailable as e:
            return False, str(e)
        except Exception as e:                           # noqa: BLE001
            return False, f"رد غير متوقع: {e}"

    # ---------- مهمة التصنيف ----------
    def classify_spaces(self, spaces: list[dict], types: list[str]) -> dict[str, dict]:
        prompt = ("أنت مساعد تصنيف فراغات معمارية. لكل فراغ اختر نوعاً واحداً فقط من القائمة المسموحة "
                  "مع نسبة ثقة بين 0 و1. لا تُرجع أي أرقام أخرى (لا مساحات ولا أسعار ولا كميات).\n"
                  f"الأنواع المسموحة: {json.dumps(types, ensure_ascii=False)}\n"
                  f"الفراغات: {json.dumps(spaces, ensure_ascii=False)}\n"
                  'أرجع JSON فقط بالشكل: {"results":[{"id":"...","type":"...","confidence":0.0}]}')
        ids = {s["id"] for s in spaces}
        last: Exception | None = None
        for attempt in range(3):
            try:
                return validate_classification(self.generate(prompt), ids, types)
            except (SchemaError, ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as e:
                last = e
            except AiHttpError as e:
                if e.status in (401, 403, 404):               # لا فائدة من إعادة المحاولة
                    raise AiUnavailable(f"رفض المزوّد الطلب: {e}") from e
                last = e
            except AiUnavailable as e:
                last = e
            self._sleep(2 ** attempt)
        raise AiUnavailable(f"تعذّر الحصول على رد صالح: {last}")

    # ---------- مهمة اقتراح بند من خيارات اللائحة ----------
    def suggest_option(self, title: str, options: list[dict], context: dict | None = None) -> dict:
        """يختار بنداً واحداً من خيارات اللائحة المعروضة فقط + سبباً مختصراً. لا أرقام ولا كميات من النموذج."""
        prompt = ("أنت مساعد مهندس حصر كميات في ليبيا. اختر بنداً واحداً فقط من الخيارات المعروضة يناسب السياق، "
                  "واذكر سبباً مختصراً بالعربية (حتى 25 كلمة). لا تخترع بنوداً ولا تُرجع أي كميات أو أسعار.\n"
                  f"السؤال: {title}\nالسياق: {json.dumps(context or {}, ensure_ascii=False)}\n"
                  f"الخيارات: {json.dumps(options, ensure_ascii=False)}\n"
                  'أرجع JSON فقط: {"id":"...","reason":"...","confidence":0.0}')
        allowed = {str(o["id"]) for o in options}
        last: Exception | None = None
        for attempt in range(3):
            try:
                return validate_option(self.generate(prompt), allowed)
            except (SchemaError, ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError) as e:
                last = e
            except AiHttpError as e:
                if e.status in (401, 403, 404):
                    raise AiUnavailable(f"رفض المزوّد الطلب: {e}") from e
                last = e
            except AiUnavailable as e:
                last = e
            self._sleep(2 ** attempt)
        raise AiUnavailable(f"تعذّر الحصول على رد صالح: {last}")

