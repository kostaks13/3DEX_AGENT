import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from llama_cpp import Llama

from executor import (
    ThreeDXExecutor,
    ThreeDXConnectionError,
    ValidationError,
    execute_plan,
    pretty_print_plan,
    validate_plan,
)
from json_grammar import get_json_grammar
from preparser import build_fallback_plan, preparse
from prompts import build_chat_prompt, build_planner_prompt


class SceneTracker:
    """Simülasyon modunda sahnedeki nesneleri takip eder (isim+tip)."""

    def __init__(self) -> None:
        self._objects: Dict[str, str] = {}

    def update_from_plan(self, plan: Dict[str, Any]) -> None:
        action_type_map = {
            "create_point": "point",
            "create_line_between_points": "line",
            "create_plane": "plane",
            "create_sketch": "sketch",
            "create_circle": "circle",
            "extrude_pad": "pad",
        }
        for op in plan.get("operations", []):
            action = op.get("action", "")
            name = op.get("name", "")
            if name and action in action_type_map:
                self._objects[name] = action_type_map[action]

    def get_scene_names(self) -> Dict[str, str]:
        return dict(self._objects)

    def dump_summary(self) -> str:
        if not self._objects:
            return "(boş sahne)"
        return "\n".join(
            f"  {name} [{typ}]" for name, typ in self._objects.items()
        )

MAX_RETRIES = 3

MODEL_DIR = Path(__file__).parent / "models"
DEFAULT_MODEL = "qwen2.5-1.5b-instruct-q4_k_m.gguf"


def _find_model() -> Path:
    default = MODEL_DIR / DEFAULT_MODEL
    if default.exists():
        return default

    gguf_files = sorted(MODEL_DIR.glob("*.gguf"))
    if gguf_files:
        return gguf_files[0]

    raise FileNotFoundError("models/ klasöründe .gguf dosyası bulunamadı.")


def _load_llm() -> Llama:
    model_path = _find_model()
    print(f"[INFO] Model yükleniyor: {model_path.name}")

    return Llama(
        model_path=str(model_path),
        n_ctx=2048,
        n_batch=256,
        n_threads=os.cpu_count() or 4,
        n_gpu_layers=-1,
        verbose=False,
    )


def _extract_json(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Modelden boş yanıt geldi.")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Geçerli JSON gövdesi bulunamadı.")

    return text[start : end + 1]


def _json_loads_merge_dupes(text: str) -> Dict[str, Any]:
    """
    JSON parse ederken duplicate key'leri (özellikle 'operations')
    birleştirir. Model bazen:
      {"intent":...,"operations":[A,B],"operations":[C]}
    üretir — standart json.loads sadece son key'i alır.
    """

    def _merge_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result and key == "operations":
                existing = result[key]
                if isinstance(existing, list) and isinstance(value, list):
                    existing.extend(value)
                    continue
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=_merge_pairs)


def _postprocess_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Küçük modellerin sık yaptığı hataları sessizce düzeltir.
    """

    if "operation" in plan and "operations" not in plan:
        val = plan.pop("operation")
        plan["operations"] = val if isinstance(val, list) else [val]
    elif "operation" in plan and "operations" in plan:
        extra = plan.pop("operation")
        extra_list = extra if isinstance(extra, list) else [extra]
        plan["operations"].extend(extra_list)

    if isinstance(plan.get("operations"), dict):
        plan["operations"] = [plan["operations"]]

    if "action" in plan and "operations" not in plan:
        plan = {"intent": "cad_command", "operations": [plan]}

    if "intent" not in plan:
        if "operations" in plan:
            plan["intent"] = "cad_command"
        elif "message" in plan:
            plan["intent"] = "clarification_needed"

    ops = plan.get("operations")
    if not isinstance(ops, list):
        return plan

    for op in ops:
        if not isinstance(op, dict):
            continue

        if "coordinate" in op and "coordinates" not in op:
            op["coordinates"] = op.pop("coordinate")
        if "coords" in op and "coordinates" not in op:
            op["coordinates"] = op.pop("coords")

        if "point_name" in op and "point_names" not in op:
            val = op.pop("point_name")
            op["point_names"] = val if isinstance(val, list) else [val]
        if "points" in op and "point_names" not in op:
            val = op.pop("points")
            op["point_names"] = val if isinstance(val, list) else [val]

        coords = op.get("coordinates")
        if isinstance(coords, list):
            op["coordinates"] = [
                float(v) if isinstance(v, (int, float, str)) and _is_number(v) else v
                for v in coords
            ]

        if "type" in op and "action" not in op:
            op["action"] = op.pop("type")

    return plan


def _fix_name_collisions(
    plan: Dict[str, Any], scene_names: Optional[Dict[str, str]]
) -> Dict[str, Any]:
    """
    Plan içindeki isim çakışmalarını otomatik düzeltir.
    Mevcut sahnedeki isimlerle çakışan isimlere artan numara ekler.
    Dahili referanslar da güncellenir (point_names, through_point vb.).
    """
    if not scene_names:
        return plan

    ops = plan.get("operations")
    if not isinstance(ops, list):
        return plan

    taken = set(scene_names.keys())
    rename_map: Dict[str, str] = {}

    for op in ops:
        if not isinstance(op, dict):
            continue
        name = op.get("name", "")
        if name in taken:
            base = name.rstrip("0123456789") or name
            counter = 1
            while True:
                candidate = f"{base}{counter}"
                if candidate not in taken and candidate not in rename_map.values():
                    rename_map[name] = candidate
                    op["name"] = candidate
                    taken.add(candidate)
                    break
                counter += 1
        else:
            taken.add(name)

    if not rename_map:
        return plan

    ref_fields = ["point_names", "through_point", "on_plane", "center_point", "from_sketch"]
    for op in ops:
        if not isinstance(op, dict):
            continue
        for field in ref_fields:
            val = op.get(field)
            if isinstance(val, str) and val in rename_map:
                op[field] = rename_map[val]
            elif isinstance(val, list):
                op[field] = [rename_map.get(v, v) if isinstance(v, str) else v for v in val]

    return plan


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _llm_call(llm: Llama, prompt: str) -> str:
    grammar = get_json_grammar()
    output = llm(
        prompt,
        max_tokens=384,
        temperature=0.0,
        top_k=30,
        top_p=0.9,
        repeat_penalty=1.15,
        stop=["<|im_end|>", "<|im_start|>"],
        grammar=grammar,
    )
    return output["choices"][0]["text"].strip()


def _ask_llm_for_plan(
    llm: Llama,
    user_text: str,
    scene_names: Optional[Dict[str, str]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    hints = preparse(user_text)

    if debug and hints.as_hint_text():
        print(f"  [PRE-PARSE] {hints.detected_actions or 'genel'}")
        print(f"  {hints.as_hint_text()}")

    prompt = build_planner_prompt(
        user_text,
        scene_names=scene_names,
        detected_actions=hints.detected_actions or None,
        hint_text=hints.as_hint_text(),
    )

    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1 and last_error:
            error_addendum = (
                f"\n\nÖNCEKİ ÇIKTINDA HATA VARDI: {last_error}\n"
                "Düzelt. intent ve operations alanlarını UNUTMA."
            )
            prompt = build_planner_prompt(
                user_text + error_addendum,
                scene_names=scene_names,
                detected_actions=hints.detected_actions or None,
                hint_text=hints.as_hint_text(),
            )
            if debug:
                print(f"  [RETRY {attempt}/{MAX_RETRIES}] Hata ile tekrar deneniyor...")

        raw = _llm_call(llm, prompt)

        if debug:
            print(f"  [RAW #{attempt}] {raw[:400]}")

        json_str = _extract_json(raw)

        try:
            plan = _json_loads_merge_dupes(json_str)
        except json.JSONDecodeError as e:
            last_error = f"JSON parse hatası: {e}"
            continue

        plan = _postprocess_plan(plan)
        plan = _fix_name_collisions(plan, scene_names)

        intent = plan.get("intent", "")
        if intent == "clarification_needed":
            return plan

        try:
            validate_plan(plan)
            return plan
        except ValidationError as e:
            last_error = str(e)
            continue

    existing = set(scene_names.keys()) if scene_names else set()
    fallback = build_fallback_plan(hints, existing_names=existing)
    if fallback:
        try:
            validate_plan(fallback)
            if debug:
                print("  [FALLBACK] Preparser'dan plan üretildi.")
            return fallback
        except ValidationError:
            pass

    if last_error:
        raise ValueError(f"Model {MAX_RETRIES} denemede geçerli plan üretemedi. Son hata: {last_error}")
    raise ValueError("Model geçerli plan üretemedi.")


def run_repl(debug: bool = False, real_3dx: bool = False) -> None:
    llm = _load_llm()

    connector: Optional[ThreeDXExecutor] = None
    tracker = SceneTracker()
    use_connector = False

    if real_3dx:
        print("[INFO] 3DEXPERIENCE'a bağlanılıyor...")
        try:
            connector = ThreeDXExecutor()
            connector.connect()
            use_connector = True
            print("[OK] 3DEXPERIENCE bağlantısı kuruldu.")
        except ThreeDXConnectionError as e:
            print(f"[HATA] 3DEXPERIENCE bağlantısı kurulamadı: {e}")
            print("[INFO] Simülasyon moduna geçiliyor.")
            real_3dx = False

    mode_label = "3DEXPERIENCE" if real_3dx else "SIMULATION"

    print("========================================")
    print("  Offline LLM CAD Komut Yorumlayıcısı")
    print(f"  Mod: {mode_label}")
    print("========================================")
    print("Türkçe komut yazın, örnek:")
    print("  orijine nokta at, 30 30 30'a bir nokta daha at, iki noktadan geçen bir line çiz")
    print()
    print("  'sahne' yazarak oluşturulmuş nesneleri görebilirsiniz.")
    if not real_3dx:
        print("  'sıfırla' yazarak sahneyi temizleyebilirsiniz.")
    print("Çıkmak için: q, quit veya exit")
    print()

    while True:
        try:
            user_text = input("Komut> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Çıkılıyor.")
            break

        if not user_text:
            continue

        if user_text.lower() in {"q", "quit", "exit"}:
            print("[INFO] Çıkılıyor.")
            break

        if user_text.lower() == "sahne":
            print()
            print("----- SAHNE ÖZETİ -----")
            if use_connector and connector is not None:
                print(connector.dump_summary())
            else:
                print(tracker.dump_summary())
            print("------------------------")
            print()
            continue

        if user_text.lower() in {"sıfırla", "sifirla", "reset"}:
            if real_3dx:
                print("[UYARI] 3DEXPERIENCE modunda sahne Python'dan sıfırlanamaz.")
                print()
                continue
            tracker = SceneTracker()
            print("[INFO] Sahne sıfırlandı.")
            print()
            continue

        scene_names: Optional[Dict[str, str]] = None
        if use_connector and connector is not None:
            try:
                scene_names = {
                    n: d["type"]
                    for n, d in connector.list_objects().items()
                }
            except Exception:
                scene_names = tracker.get_scene_names()
        else:
            scene_names = tracker.get_scene_names()

        try:
            plan = _ask_llm_for_plan(
                llm, user_text, scene_names=scene_names, debug=debug
            )
        except Exception as e:
            print(f"[HATA] Plan üretilemedi: {e}")
            continue

        intent = plan.get("intent", "")

        if intent == "clarification_needed":
            question = plan.get("message", "Komutunuz belirsiz, lütfen detaylandırın.")
            print(f"[SORU] {question}")
            print()
            continue

        if debug:
            print()
            print("----- JSON PLAN (DEBUG) -----")
            print(pretty_print_plan(plan))
            print("-----------------------------")

        try:
            if use_connector and connector is not None:
                messages = execute_plan(plan, simulate=False, connector=connector)
            else:
                messages = execute_plan(plan, simulate=True)
        except ValidationError as e:
            print(f"[HATA][VALIDATION] {e}")
            continue
        except Exception as e:
            print(f"[HATA][EXECUTOR] {e}")
            continue

        tracker.update_from_plan(plan)

        for msg in messages:
            print(msg)

        obj_count: int
        if use_connector and connector is not None:
            try:
                obj_count = len(connector.list_objects())
            except Exception:
                obj_count = len(tracker.get_scene_names())
        else:
            obj_count = len(tracker.get_scene_names())
        print(f"  [{mode_label}] Sahnedeki toplam nesne: {obj_count}")

        print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline çalışan CLI tabanlı doğal dil CAD komut yorumlayıcısı.\n"
            "Bu araç, kullanıcı komutlarını yerel LLM ile JSON aksiyon planına çevirir "
            "ve planı (şimdilik simülasyon modunda) yürütür."
        )
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="LLM'nin ürettiği JSON planını ekrana yazdır.",
    )
    parser.add_argument(
        "--3dx",
        dest="real_3dx",
        action="store_true",
        help="Gerçek 3DEXPERIENCE'a COM ile bağlan (Windows, pywin32 gerekli).",
    )

    args = parser.parse_args(argv)
    run_repl(debug=args.debug, real_3dx=args.real_3dx)


if __name__ == "__main__":
    main(sys.argv[1:])

