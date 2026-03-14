"""
Kullanıcının Türkçe komutundan koordinat, isim ve aksiyon ipuçlarını
regex ile çıkarır. LLM'ye daha az iş bırakır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class ParseHints:
    """LLM'ye verilecek ön-bilgi."""

    detected_actions: Set[str] = field(default_factory=set)
    coordinates: List[List[float]] = field(default_factory=list)
    named_objects: List[str] = field(default_factory=list)
    radius: Optional[float] = None
    length: Optional[float] = None

    def as_hint_text(self) -> str:
        """LLM prompt'una eklenecek kısa ipucu metni."""
        parts: List[str] = []

        if self.coordinates:
            coords_str = ", ".join(str(c) for c in self.coordinates)
            parts.append(f"Algılanan koordinatlar: {coords_str}")

        if self.named_objects:
            parts.append(f"Algılanan isimler: {', '.join(self.named_objects)}")

        if self.radius is not None:
            parts.append(f"Yarıçap: {self.radius} mm")

        if self.length is not None:
            parts.append(f"Uzunluk: {self.length} mm")

        return "\n".join(parts) if parts else ""


_COORD_PATTERN = re.compile(
    r"(?:^|[\s,(])"
    r"(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)"
    r"(?:[\s,)'e]|$)",
)

_RADIUS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:yarıçap|yaricap|radius|çap|cap)",
    re.IGNORECASE,
)
_RADIUS_PATTERN2 = re.compile(
    r"(?:yarıçap|yaricap|radius|çap|cap|r=)\s*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

_LENGTH_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:extrude|uzunluk|boy|length|derinlik|kalinlik|kalınlık)",
    re.IGNORECASE,
)
_LENGTH_PATTERN2 = re.compile(
    r"(?:extrude|uzunluk|boy|length|derinlik|kalinlik|kalınlık)\s*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

_NAME_PATTERN = re.compile(
    r"\b([A-Z][A-Z0-9_]{0,19})\b"
)

_ACTION_KEYWORDS: dict[str, set[str]] = {
    "create_point": {"nokta", "point", "noktası", "noktasını", "orijin", "orijine"},
    "create_line_between_points": {"line", "çizgi", "doğru", "dogru", "cizgi"},
    "create_plane": {"düzlem", "duzlem", "plane"},
    "create_sketch": {"sketch", "taslak", "eskiz"},
    "create_circle": {"daire", "circle", "çember", "cember"},
    "extrude_pad": {"extrude", "pad", "katıla", "kalınlık", "kalinlik"},
}


def preparse(text: str) -> ParseHints:
    """Türkçe komuttan ipuçlarını çıkarır."""
    hints = ParseHints()
    lower = text.lower()

    for action, keywords in _ACTION_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            hints.detected_actions.add(action)

    for m in _COORD_PATTERN.finditer(text):
        try:
            coords = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
            hints.coordinates.append(coords)
        except ValueError:
            pass

    if "orijin" in lower and not any(
        c == [0.0, 0.0, 0.0] for c in hints.coordinates
    ):
        hints.coordinates.insert(0, [0.0, 0.0, 0.0])

    for pattern in (_RADIUS_PATTERN, _RADIUS_PATTERN2):
        m = pattern.search(text)
        if m:
            hints.radius = float(m.group(1))
            break

    for pattern in (_LENGTH_PATTERN, _LENGTH_PATTERN2):
        m = pattern.search(text)
        if m:
            hints.length = float(m.group(1))
            break

    for m in _NAME_PATTERN.finditer(text):
        name = m.group(1)
        if len(name) >= 2 and name not in {"OK", "MM", "JSON", "CAD", "SK"}:
            if name not in hints.named_objects:
                hints.named_objects.append(name)

    return hints


def build_fallback_plan(
    hints: ParseHints,
    existing_names: Optional[set[str]] = None,
) -> Optional[dict]:
    """
    Preparser ipuçlarından doğrudan plan üretmeyi dener.
    LLM başarısız olduğunda güvenlik ağı olarak kullanılır.
    Sadece yeterli bilgi varsa plan döndürür, yoksa None.
    """
    actions = hints.detected_actions
    coords = hints.coordinates
    names = hints.named_objects
    taken = set(existing_names) if existing_names else set()

    if not actions or not coords:
        return None

    ops: List[dict] = []
    name_idx = 0
    point_names: List[str] = []

    def _next_name(prefix: str) -> str:
        nonlocal name_idx
        while name_idx < len(names):
            candidate = names[name_idx]
            name_idx += 1
            if candidate not in taken:
                taken.add(candidate)
                return candidate
        counter = 1
        while True:
            candidate = f"{prefix}{counter}"
            if candidate not in taken:
                taken.add(candidate)
                return candidate
            counter += 1

    if "create_point" in actions:
        for c in coords:
            pname = _next_name("P")
            ops.append({
                "action": "create_point",
                "name": pname,
                "coordinates": c,
            })
            point_names.append(pname)

    if "create_line_between_points" in actions and len(point_names) >= 2:
        lname = _next_name("L")
        ops.append({
            "action": "create_line_between_points",
            "name": lname,
            "point_names": [point_names[0], point_names[1]],
        })

    if "create_circle" in actions and hints.radius is not None and point_names:
        cname = _next_name("C")
        ops.append({
            "action": "create_circle",
            "name": cname,
            "center_point": point_names[0],
            "radius": hints.radius,
        })

        if "extrude_pad" in actions and hints.length is not None:
            ename = _next_name("PAD")
            ops.append({
                "action": "extrude_pad",
                "name": ename,
                "from_sketch": cname,
                "length": hints.length,
            })

    if not ops:
        return None

    return {"intent": "cad_command", "operations": ops}
