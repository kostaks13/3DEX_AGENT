from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

try:
    import win32com.client  # type: ignore
except Exception:  # pragma: no cover - Windows/pywin32 yoksa
    win32com = None


SUPPORTED_ACTIONS = {
    "create_point",
    "create_line_between_points",
    "create_plane",
    "create_sketch",
    "create_circle",
    "extrude_pad",
}


@dataclass
class ValidationError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover - basit temsil
        return self.message


@dataclass
class ThreeDXConnectionError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def validate_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    LLM'den gelen JSON planını doğrular.
    - Yapı
    - intent değeri
    - operations listesi
    - her bir operation için zorunlu alanlar
    - desteklenmeyen action isimleri
    """

    if not isinstance(plan, dict):
        raise ValidationError("Plan JSON nesnesi olmalı.")

    intent = plan.get("intent")
    if intent != "cad_command":
        raise ValidationError(f"intent 'cad_command' olmalı (gelen: {intent!r}).")

    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValidationError("'operations' dolu bir liste olmalı.")

    # Nesne isimlerini takip ederek bağımlılıkları kontrol edeceğiz
    defined_points: Set[str] = set()
    defined_planes: Set[str] = set()
    defined_sketches: Set[str] = set()
    defined_circles: Set[str] = set()

    for idx, op in enumerate(operations):
        if not isinstance(op, dict):
            raise ValidationError(f"{idx}. operation JSON nesnesi olmalı.")

        action = op.get("action")
        if action not in SUPPORTED_ACTIONS:
            raise ValidationError(
                f"{idx}. operation için desteklenmeyen action: {action!r}."
            )

        name = op.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"{idx}. operation için geçerli bir 'name' zorunlu.")

        if action == "create_point":
            coords = op.get("coordinates")
            if (
                not isinstance(coords, list)
                or len(coords) != 3
                or not all(isinstance(v, (int, float)) for v in coords)
            ):
                raise ValidationError(
                    f"{idx}. operation 'create_point' için 'coordinates' [x, y, z] sayısal liste olmalı."
                )
            defined_points.add(name)

        elif action == "create_line_between_points":
            point_names = op.get("point_names")
            if (
                not isinstance(point_names, list)
                or len(point_names) != 2
                or not all(isinstance(v, str) and v.strip() for v in point_names)
            ):
                raise ValidationError(
                    f"{idx}. operation 'create_line_between_points' için 'point_names' 2 elemanlı string liste olmalı."
                )

            missing = [p for p in point_names if p not in defined_points]
            if missing:
                raise ValidationError(
                    f"{idx}. operation 'create_line_between_points' için tanımlı olmayan nokta isimleri: {missing}."
                )

        elif action == "create_plane":
            through_point = op.get("through_point")
            normal = op.get("normal")
            if not isinstance(through_point, str) or not through_point.strip():
                raise ValidationError(
                    f"{idx}. operation 'create_plane' için 'through_point' string olmalı."
                )
            if (
                not isinstance(normal, list)
                or len(normal) != 3
                or not all(isinstance(v, (int, float)) for v in normal)
            ):
                raise ValidationError(
                    f"{idx}. operation 'create_plane' için 'normal' [nx, ny, nz] sayısal liste olmalı."
                )
            if through_point not in defined_points:
                raise ValidationError(
                    f"{idx}. operation 'create_plane' için referans noktası tanımsız: {through_point!r}."
                )
            defined_planes.add(name)

        elif action == "create_sketch":
            on_plane = op.get("on_plane")
            if not isinstance(on_plane, str) or not on_plane.strip():
                raise ValidationError(
                    f"{idx}. operation 'create_sketch' için 'on_plane' string olmalı."
                )
            if on_plane not in defined_planes:
                raise ValidationError(
                    f"{idx}. operation 'create_sketch' için referans düzlem tanımsız: {on_plane!r}."
                )
            defined_sketches.add(name)

        elif action == "create_circle":
            center_point = op.get("center_point")
            radius = op.get("radius")
            if not isinstance(center_point, str) or not center_point.strip():
                raise ValidationError(
                    f"{idx}. operation 'create_circle' için 'center_point' string olmalı."
                )
            if not isinstance(radius, (int, float)) or radius <= 0:
                raise ValidationError(
                    f"{idx}. operation 'create_circle' için 'radius' pozitif bir sayı olmalı."
                )
            if center_point not in defined_points:
                raise ValidationError(
                    f"{idx}. operation 'create_circle' için merkez noktası tanımsız: {center_point!r}."
                )
            defined_circles.add(name)

        elif action == "extrude_pad":
            from_sketch = op.get("from_sketch")
            length = op.get("length")
            if not isinstance(from_sketch, str) or not from_sketch.strip():
                raise ValidationError(
                    f"{idx}. operation 'extrude_pad' için 'from_sketch' string olmalı."
                )
            if not isinstance(length, (int, float)) or length <= 0:
                raise ValidationError(
                    f"{idx}. operation 'extrude_pad' için 'length' pozitif bir sayı olmalı."
                )
            extrudable = defined_sketches | defined_circles
            if from_sketch not in extrudable:
                raise ValidationError(
                    f"{idx}. operation 'extrude_pad' için kaynak sketch/circle tanımsız: {from_sketch!r}."
                )

    return plan


class ThreeDXExecutor:
    """
    Gerçek 3DEXPERIENCE / CATIA COM entegrasyonu.
    Windows'ta çalışır, pywin32 gerektirir.
    """

    def __init__(self, progid: Optional[str] = None) -> None:
        self._progid = progid or os.getenv("THREEDX_PROGID", "CATIA.Application")
        self._app = None

    # ── Bağlantı ───────────────────────────────────────────────────────

    def connect(self) -> None:
        if win32com is None:
            raise ThreeDXConnectionError(
                "pywin32 kurulu değil. python/Lib/site-packages/ altında win32com paketi bulunamadı."
            )
        try:
            self._app = win32com.client.Dispatch(self._progid)
        except Exception as e:
            raise ThreeDXConnectionError(
                f"3DEXPERIENCE'a bağlanılamadı ({self._progid}). "
                f"Uygulama açık ve Part editörü aktif mi?\nDetay: {e}"
            ) from e
        if self._app is None:
            raise ThreeDXConnectionError(
                "Dispatch başarılı ama Application nesnesi None döndü."
            )

    # ── Dahili yardımcılar ─────────────────────────────────────────────

    def _get_active_editor(self):
        if self._app is None:
            raise ThreeDXConnectionError(
                "3DEXPERIENCE uygulamasına bağlantı yok. Önce connect() çağırın."
            )

        editor = self._app.ActiveEditor
        if editor is None:
            raise ThreeDXConnectionError("Aktif editor bulunamadı.")
        return editor

    def _get_active_part(self):
        editor = self._get_active_editor()
        part = editor.ActiveObject
        if part is None:
            raise RuntimeError("Aktif editor içinde Part nesnesi bulunamadı.")
        return part

    def _get_hybrid_shape_factory(self, part):
        factory = part.HybridShapeFactory
        if factory is None:
            raise RuntimeError("HybridShapeFactory alınamadı.")
        return factory

    def _get_shape_factory(self, part):
        factory = part.ShapeFactory
        if factory is None:
            raise RuntimeError("ShapeFactory alınamadı.")
        return factory

    def _get_hybrid_bodies(self, part):
        hybrid_bodies = part.HybridBodies
        if hybrid_bodies is None:
            raise RuntimeError("Part içinde HybridBodies koleksiyonu alınamadı.")
        return hybrid_bodies

    def _get_or_create_hybrid_body(self, part, body_name: str = "LLM_Geometry"):
        hybrid_bodies = self._get_hybrid_bodies(part)

        count = 0
        try:
            count = hybrid_bodies.Count
        except Exception:
            count = 0

        for i in range(1, count + 1):
            body = hybrid_bodies.Item(i)
            try:
                if str(body.Name) == body_name:
                    return body
            except Exception:
                pass

        if count > 0:
            try:
                first_body = hybrid_bodies.Item(1)
                if first_body is not None:
                    return first_body
            except Exception:
                pass

        body = hybrid_bodies.Add()
        body.Name = body_name
        return body

    def _iter_hybrid_shapes(self, part):
        hybrid_bodies = self._get_hybrid_bodies(part)

        body_count = 0
        try:
            body_count = hybrid_bodies.Count
        except Exception:
            body_count = 0

        for i in range(1, body_count + 1):
            body = hybrid_bodies.Item(i)
            shapes = None
            try:
                shapes = body.HybridShapes
            except Exception:
                shapes = None
            if shapes is None:
                continue

            shape_count = 0
            try:
                shape_count = shapes.Count
            except Exception:
                shape_count = 0

            for j in range(1, shape_count + 1):
                shape = shapes.Item(j)
                yield body, shape

    def _find_hybrid_shape_by_name(self, part, shape_name: str):
        for body, shape in self._iter_hybrid_shapes(part):
            try:
                if str(shape.Name) == shape_name:
                    return body, shape
            except Exception:
                pass
        return None, None

    def _check_name_collision(self, part, name: str) -> None:
        _body, shape = self._find_hybrid_shape_by_name(part, name)
        if shape is not None:
            raise RuntimeError(f"İsim çakışması: '{name}' zaten mevcut.")

    def _append_and_update(self, part, target_body, shape) -> None:
        target_body.AppendHybridShape(shape)
        part.InWorkObject = shape
        part.Update()

    # ── Aksiyon: create_point ──────────────────────────────────────────

    def create_point(self, name: str, x: float, y: float, z: float) -> None:
        try:
            part = self._get_active_part()
            hybrid_shape_factory = self._get_hybrid_shape_factory(part)
            target_body = self._get_or_create_hybrid_body(part)
            self._check_name_collision(part, name)

            point = hybrid_shape_factory.AddNewPointCoord(float(x), float(y), float(z))
            point.Name = name
            self._append_and_update(part, target_body, point)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Nokta oluşturulamadı: {e}") from e

    # ── Aksiyon: create_line_between_points ────────────────────────────

    def create_line_between_points(self, name: str, p1: str, p2: str) -> None:
        try:
            part = self._get_active_part()
            hybrid_shape_factory = self._get_hybrid_shape_factory(part)
            self._check_name_collision(part, name)

            body1, point1 = self._find_hybrid_shape_by_name(part, p1)
            body2, point2 = self._find_hybrid_shape_by_name(part, p2)

            if point1 is None:
                raise RuntimeError(f"'{p1}' isimli nokta part içinde bulunamadı.")
            if point2 is None:
                raise RuntimeError(f"'{p2}' isimli nokta part içinde bulunamadı.")

            target_body = body1 or body2 or self._get_or_create_hybrid_body(part)

            ref1 = part.CreateReferenceFromObject(point1)
            ref2 = part.CreateReferenceFromObject(point2)

            line = hybrid_shape_factory.AddNewLinePtPt(ref1, ref2)
            line.Name = name
            self._append_and_update(part, target_body, line)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Çizgi oluşturulamadı: {e}") from e

    # ── Aksiyon: create_plane ──────────────────────────────────────────

    def create_plane(
        self, name: str, through_point: str, normal: List[float]
    ) -> None:
        try:
            part = self._get_active_part()
            hybrid_shape_factory = self._get_hybrid_shape_factory(part)
            target_body = self._get_or_create_hybrid_body(part)
            self._check_name_collision(part, name)

            _body, pt_shape = self._find_hybrid_shape_by_name(part, through_point)
            if pt_shape is None:
                raise RuntimeError(
                    f"'{through_point}' isimli nokta part içinde bulunamadı."
                )

            pt_ref = part.CreateReferenceFromObject(pt_shape)

            plane = hybrid_shape_factory.AddNewPlane1Curve1Point(pt_ref, pt_ref)

            direction = hybrid_shape_factory.AddNewDirectionByCoord(
                float(normal[0]), float(normal[1]), float(normal[2])
            )
            plane_by_normal = hybrid_shape_factory.AddNewPlaneNormal(
                direction, pt_ref
            )
            plane_by_normal.Name = name
            self._append_and_update(part, target_body, plane_by_normal)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Düzlem oluşturulamadı: {e}") from e

    # ── Aksiyon: create_sketch ─────────────────────────────────────────

    def create_sketch(self, name: str, on_plane: str) -> None:
        try:
            part = self._get_active_part()
            target_body = self._get_or_create_hybrid_body(part)
            self._check_name_collision(part, name)

            _body, plane_shape = self._find_hybrid_shape_by_name(part, on_plane)
            if plane_shape is None:
                raise RuntimeError(
                    f"'{on_plane}' isimli düzlem part içinde bulunamadı."
                )

            plane_ref = part.CreateReferenceFromObject(plane_shape)
            sketches = target_body.HybridSketches
            sketch = sketches.Add(plane_ref)
            sketch.Name = name

            part.Update()
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Sketch oluşturulamadı: {e}") from e

    # ── Aksiyon: create_circle ─────────────────────────────────────────

    def create_circle(
        self, name: str, center_point: str, radius: float
    ) -> None:
        try:
            part = self._get_active_part()
            hybrid_shape_factory = self._get_hybrid_shape_factory(part)
            target_body = self._get_or_create_hybrid_body(part)
            self._check_name_collision(part, name)

            _body, pt_shape = self._find_hybrid_shape_by_name(part, center_point)
            if pt_shape is None:
                raise RuntimeError(
                    f"'{center_point}' isimli nokta part içinde bulunamadı."
                )

            pt_ref = part.CreateReferenceFromObject(pt_shape)

            xy_plane_ref = part.OriginElements.PlaneXY
            plane_ref = part.CreateReferenceFromObject(xy_plane_ref)

            circle = hybrid_shape_factory.AddNewCircleCtrRad(
                pt_ref, plane_ref, False, float(radius)
            )
            circle.Name = name
            self._append_and_update(part, target_body, circle)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Daire oluşturulamadı: {e}") from e

    # ── Aksiyon: extrude_pad ───────────────────────────────────────────

    def extrude_pad(
        self, name: str, from_sketch: str, length: float
    ) -> None:
        try:
            part = self._get_active_part()
            shape_factory = self._get_shape_factory(part)
            self._check_name_collision(part, name)

            _body, sketch_shape = self._find_hybrid_shape_by_name(part, from_sketch)
            if sketch_shape is None:
                raise RuntimeError(
                    f"'{from_sketch}' isimli sketch/profile part içinde bulunamadı."
                )

            sketch_ref = part.CreateReferenceFromObject(sketch_shape)

            pad = shape_factory.AddNewPad(sketch_ref, float(length))
            pad.Name = name
            part.Update()
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Extrude (pad) oluşturulamadı: {e}") from e

    # ── Yardımcı: mevcut geometrileri listele ──────────────────────────

    def get_point(self, name: str):
        part = self._get_active_part()
        _body, shape = self._find_hybrid_shape_by_name(part, name)
        if shape is None:
            raise RuntimeError(f"'{name}' isimli geometri part içinde bulunamadı.")
        return shape

    def geometry_exists(self, name: str) -> bool:
        part = self._get_active_part()
        _body, shape = self._find_hybrid_shape_by_name(part, name)
        return shape is not None

    def list_geometry_names(self) -> List[str]:
        part = self._get_active_part()
        names: List[str] = []
        for _body, shape in self._iter_hybrid_shapes(part):
            try:
                names.append(str(shape.Name))
            except Exception:
                pass
        return names

    def list_objects(self) -> Dict[str, Dict[str, Any]]:
        """Sahnedeki nesneleri dict olarak döndürür."""
        part = self._get_active_part()
        result: Dict[str, Dict[str, Any]] = {}
        for _body, shape in self._iter_hybrid_shapes(part):
            try:
                sname = str(shape.Name)
                stype = "unknown"
                type_name = str(type(shape).__name__).lower()
                if "point" in type_name:
                    stype = "point"
                elif "line" in type_name:
                    stype = "line"
                elif "plane" in type_name:
                    stype = "plane"
                elif "circle" in type_name:
                    stype = "circle"
                elif "sketch" in type_name:
                    stype = "sketch"
                elif "pad" in type_name:
                    stype = "pad"
                result[sname] = {"type": stype}
            except Exception:
                pass
        return result

    def dump_summary(self) -> str:
        """Sahne özetini string olarak döndürür."""
        objects = self.list_objects()
        if not objects:
            return "(boş sahne)"
        lines = []
        for obj_name, obj_data in objects.items():
            lines.append(f"  {obj_name} [{obj_data['type']}]")
        return "\n".join(lines)


def execute_plan(
    plan: Dict[str, Any],
    simulate: bool = True,
    connector: Optional[ThreeDXExecutor] = None,
) -> List[str]:
    """
    Planı çalıştırır.
    - simulate=True  → sadece terminal simülasyonu
    - simulate=False → connector verilmişse gerçek 3DEXPERIENCE çağrıları
    """

    plan = validate_plan(plan)
    operations: List[Dict[str, Any]] = plan["operations"]

    messages: List[str] = []

    if simulate or connector is None:
        messages.append(
            "[SIMULATION] 3DEXPERIENCE bağlantısı yerine terminal çıktısı kullanılıyor."
        )

    for op in operations:
        action = op["action"]

        if simulate or connector is None:
            if action == "create_point":
                msg = _simulate_create_point(op)
            elif action == "create_line_between_points":
                msg = _simulate_create_line_between_points(op)
            elif action == "create_plane":
                msg = _simulate_create_plane(op)
            elif action == "create_sketch":
                msg = _simulate_create_sketch(op)
            elif action == "create_circle":
                msg = _simulate_create_circle(op)
            elif action == "extrude_pad":
                msg = _simulate_extrude_pad(op)
            else:
                raise ValidationError(f"Desteklenmeyen action: {action!r}")
            messages.append(msg)
            continue

        name = op["name"]
        try:
            if action == "create_point":
                x, y, z = op["coordinates"]
                connector.create_point(name, float(x), float(y), float(z))
                messages.append(f"[OK] create_point {name} ({x}, {y}, {z})")
            elif action == "create_line_between_points":
                p1, p2 = op["point_names"]
                connector.create_line_between_points(name, p1, p2)
                messages.append(f"[OK] create_line_between_points {name} ({p1}, {p2})")
            elif action == "create_plane":
                connector.create_plane(name, op["through_point"], op["normal"])
                messages.append(
                    f"[OK] create_plane {name} through {op['through_point']} "
                    f"normal {op['normal']}"
                )
            elif action == "create_sketch":
                connector.create_sketch(name, op["on_plane"])
                messages.append(f"[OK] create_sketch {name} on {op['on_plane']}")
            elif action == "create_circle":
                connector.create_circle(name, op["center_point"], float(op["radius"]))
                messages.append(
                    f"[OK] create_circle {name} at {op['center_point']} r={op['radius']}"
                )
            elif action == "extrude_pad":
                connector.extrude_pad(name, op["from_sketch"], float(op["length"]))
                messages.append(
                    f"[OK] extrude_pad {name} from {op['from_sketch']} len={op['length']}"
                )
            else:
                raise ValidationError(f"Desteklenmeyen action: {action!r}")
        except (ValidationError, ThreeDXConnectionError):
            raise
        except Exception as exc:
            messages.append(f"[HATA] {action} {name}: {exc}")
            raise

    return messages


def _simulate_create_point(op: Dict[str, Any]) -> str:
    name = op["name"]
    x, y, z = op["coordinates"]
    return f"[SIMULATION] create_point {name} at ({x}, {y}, {z})"


def _simulate_create_line_between_points(op: Dict[str, Any]) -> str:
    name = op["name"]
    p1, p2 = op["point_names"]
    return f"[SIMULATION] create_line_between_points {name} using {p1}, {p2}"


def _simulate_create_plane(op: Dict[str, Any]) -> str:
    name = op["name"]
    through_point = op["through_point"]
    nx, ny, nz = op["normal"]
    return (
        f"[SIMULATION] create_plane {name} through {through_point} "
        f"with normal ({nx}, {ny}, {nz})"
    )


def _simulate_create_sketch(op: Dict[str, Any]) -> str:
    name = op["name"]
    on_plane = op["on_plane"]
    return f"[SIMULATION] create_sketch {name} on plane {on_plane}"


def _simulate_create_circle(op: Dict[str, Any]) -> str:
    name = op["name"]
    center_point = op["center_point"]
    radius = op["radius"]
    return f"[SIMULATION] create_circle {name} at {center_point} with radius {radius}"


def _simulate_extrude_pad(op: Dict[str, Any]) -> str:
    name = op["name"]
    from_sketch = op["from_sketch"]
    length = op["length"]
    return f"[SIMULATION] extrude_pad {name} from sketch {from_sketch} length {length}"


def pretty_print_plan(plan: Dict[str, Any]) -> str:
    """Debug / --debug modu için JSON'u okunabilir string'e çevirir."""

    return json.dumps(plan, ensure_ascii=False, indent=2)
