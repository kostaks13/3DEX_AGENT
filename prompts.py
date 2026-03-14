from __future__ import annotations

from typing import Optional, Set


def build_chat_prompt(system_text: str, user_text: str) -> str:
    system_text = (system_text or "").strip()
    user_text = (user_text or "").strip()

    return (
        "<|im_start|>system\n"
        + system_text
        + "\n<|im_end|>\n"
        + "<|im_start|>user\n"
        + user_text
        + "\n<|im_end|>\n"
        + "<|im_start|>assistant\n"
    )


# ── Aksiyon şemaları (her biri tek satır, küçük model için yoğun) ──────────

_ACTION_SCHEMAS: dict[str, str] = {
    "create_point":
        'create_point: name, coordinates=[x,y,z]',
    "create_line_between_points":
        'create_line_between_points: name, point_names=[p1,p2]',
    "create_plane":
        'create_plane: name, through_point, normal=[nx,ny,nz]',
    "create_sketch":
        'create_sketch: name, on_plane',
    "create_circle":
        'create_circle: name, center_point, radius',
    "extrude_pad":
        'extrude_pad: name, from_sketch, length',
}

# ── Aksiyon bazlı tek-satır örnekler ──────────────────────────────────────

_ACTION_EXAMPLES: dict[str, str] = {
    "create_point":
        'Kullanıcı: orijine nokta at, 30 30 30\'a nokta at\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"P1","coordinates":[0,0,0]},{"action":"create_point","name":"P2","coordinates":[30,30,30]}]}',
    "create_line_between_points":
        'Kullanıcı: orijine nokta at, 30 30 30\'a nokta at, iki noktadan line çiz\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"P1","coordinates":[0,0,0]},{"action":"create_point","name":"P2","coordinates":[30,30,30]},{"action":"create_line_between_points","name":"L1","point_names":["P1","P2"]}]}',
    "create_plane":
        'Kullanıcı: orijine P0 koy, Z\'ye dik düzlem oluştur\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"P0","coordinates":[0,0,0]},{"action":"create_plane","name":"Plane1","through_point":"P0","normal":[0,0,1]}]}',
    "create_sketch":
        'Kullanıcı: orijine P0 koy, Z\'ye dik düzlem, SK1 sketch aç\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"P0","coordinates":[0,0,0]},{"action":"create_plane","name":"Plane1","through_point":"P0","normal":[0,0,1]},{"action":"create_sketch","name":"SK1","on_plane":"Plane1"}]}',
    "create_circle":
        'Kullanıcı: orijine CC koy, 25mm daire çiz\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"CC","coordinates":[0,0,0]},{"action":"create_circle","name":"C1","center_point":"CC","radius":25}]}',
    "extrude_pad":
        'Kullanıcı: orijine CC koy, 25mm daire çiz, 50mm extrude et\n'
        '{"intent":"cad_command","operations":[{"action":"create_point","name":"CC","coordinates":[0,0,0]},{"action":"create_circle","name":"C1","center_point":"CC","radius":25},{"action":"extrude_pad","name":"PAD1","from_sketch":"C1","length":50}]}',
}

_CLARIFICATION_EXAMPLE = (
    'Kullanıcı: nokta at\n'
    '{"intent":"clarification_needed","message":"Noktanın koordinatlarını belirtir misiniz? Örnek: 10 20 30"}'
)


def _build_dynamic_system(
    detected_actions: Optional[Set[str]] = None,
    hint_text: str = "",
    scene_names: Optional[dict[str, str]] = None,
) -> str:
    """
    Algılanan aksiyonlara göre sadece ilgili şema ve örnekleri içeren
    kısa sistem prompt üretir.
    """
    parts: list[str] = []

    parts.append(
        "CAD JSON planlayıcı. Sadece JSON döndür, başka metin yazma.\n"
        "ÇIKTI: {\"intent\":\"cad_command\",\"operations\":[...]} veya "
        "{\"intent\":\"clarification_needed\",\"message\":\"...\"}\n"
        "Eksik bilgi varsa tahmin ETME, soru sor. Sayılar mm."
    )

    if detected_actions:
        relevant = detected_actions | {"create_point"}
    else:
        relevant = set(_ACTION_SCHEMAS.keys())

    parts.append("\nAKSİYONLAR:")
    for action in _ACTION_SCHEMAS:
        if action in relevant:
            parts.append(_ACTION_SCHEMAS[action])

    parts.append("\nÖRNEKLER:")
    shown: set[str] = set()
    for action in _ACTION_EXAMPLES:
        if action in relevant and action not in shown:
            parts.append(_ACTION_EXAMPLES[action])
            shown.add(action)
    parts.append(_CLARIFICATION_EXAMPLE)

    if hint_text:
        parts.append("\nÖN-BİLGİ (regex ile algılandı):")
        parts.append(hint_text)

    if scene_names:
        parts.append("\nSAHNE (bu isimleri tekrar KULLANMA, referans verebilirsin):")
        for obj_name, obj_type in scene_names.items():
            parts.append(f"  {obj_name} [{obj_type}]")

    return "\n".join(parts)


def build_planner_prompt(
    user_text: str,
    scene_names: Optional[dict[str, str]] = None,
    detected_actions: Optional[Set[str]] = None,
    hint_text: str = "",
) -> str:
    system = _build_dynamic_system(
        detected_actions=detected_actions,
        hint_text=hint_text,
        scene_names=scene_names,
    )
    return build_chat_prompt(system, user_text)

