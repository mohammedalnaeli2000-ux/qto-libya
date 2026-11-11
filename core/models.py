"""نماذج البيانات المشتركة بين الوحدات."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

Pt = tuple[float, float]


@dataclass
class Opening:
    """فتحة (باب/شباك) مأخوذة من بلوك في المسقط. الأبعاد بالمتر."""
    id: str
    kind: str                   # door | window
    subkind: str                # main | interior | bath | window ...
    block: str
    layer: str
    pos: Pt
    width: float
    height: float
    sill: float = 0.0
    estimated: bool = False     # true إن كان الارتفاع/العرض مفترضاً
    size_source: str = ""       # attribute | name | extents | default
    spaces: list[str] = field(default_factory=list)   # الفراغات المجاورة
    owner: Optional[str] = None                        # الفراغ الذي يُحسب عليه البند


@dataclass
class Space:
    id: str
    name: str
    type: str = "unknown"
    confidence: float = 0.0
    classified_by: str = ""     # rule | ai | user
    area: float = 0.0           # م2
    perimeter: float = 0.0      # م
    polygon: list[Pt] = field(default_factory=list)
    layer: str = ""
    floor: str = ""
    source_file: str = ""
    openings: list[str] = field(default_factory=list)  # معرّفات الفتحات المجاورة
    fixtures: dict[str, int] = field(default_factory=dict)  # أجهزة صحية مرصودة بالبلوكات


@dataclass
class Plan:
    """ناتج قراءة ملف كاد واحد."""
    file: str
    floor: str
    unit_scale: float
    unit_note: str
    spaces: list[Space] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    wall_lengths: dict[str, float] = field(default_factory=dict)  # دور الطبقة -> طول (م)
    warnings: list[str] = field(default_factory=list)
    wall_mode: str = "centerline"


@dataclass
class Demand:
    """كمية مطلوبة من قاعدة ما لفراغ ما، قبل اختيار البند من اللائحة."""
    id: str
    rule_id: str
    space_id: Optional[str]
    space_type: Optional[str]
    quantity: float
    unit: str
    formula: str
    inputs: dict
    estimated: bool = False
    note: str = ""
    filter: dict = field(default_factory=dict)   # مثل {"thickness_cm": 20}


@dataclass
class BoqItem:
    id: int
    code: str
    parent_code: Optional[str]
    chapter_no: int
    description: str
    context: str
    unit: Optional[str]
    unit_price: Optional[float]
    sheet: str
    ancestors: str = ""         # نص البنود الآباء (للتصنيف والعرض)
    unit_raw: str = ""          # الوحدة بنصها الأصلي في اللائحة (م2، م.ط ...)

    @property
    def label(self) -> str:
        head = (self.ancestors + " ← " if self.ancestors else "")
        return f"{self.code}  {head}{self.description}"


@dataclass
class Line:
    """سطر في المقايسة النهائية."""
    demand_id: str
    space_id: Optional[str]
    space_name: str
    space_type: Optional[str]
    rule_id: str
    item_code: str
    description: str
    unit: str
    quantity: float
    unit_price: float
    chapter_no: int
    formula: str
    inputs: dict
    estimated: bool = False
    manual: bool = False
    modified: bool = False
    item_id: int = 0
    note: str = ""
    base_price: float = 0.0          # سعر اللائحة قبل أي تعديل
    price_modified: bool = False     # سعر معدّل يدوياً أو بنسبة

    @property
    def total(self) -> float:
        return round(round(self.quantity, 2) * self.unit_price, 2)
