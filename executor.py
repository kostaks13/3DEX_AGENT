from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional, Set

try:
    import win32com.client  # type: ignore
except Exception:  # pragma: no cover - Windows/pywin32 yoksa
    win32com = None


log = logging.getLogger("3dex_agent")


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


# ────────────────────────────────────────────────────────────────────
# Validation
# ────────────────────────────────────────────────────────────────────


def validate_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValidationError("Plan JSON nesnesi olmalı.")

    intent = plan.get("intent")
    if intent != "cad_command":
        raise ValidationError(f"intent 'cad_command' olmalı (gelen: {intent!r}).")

    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValidationError("'operations' dolu bir liste olmalı.")

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


# ────────────────────────────────────────────────────────────────────
# CATIAContext — tek seferde önbelleğe alınan COM nesneleri
# ────────────────────────────────────────────────────────────────────


class CATIAContext(NamedTuple):
    app: Any
    editor: Any
    part: Any
    hsf: Any          # HybridShapeFactory
    sf: Any           # ShapeFactory
    geom_set: Any     # aktif Geometrical Set (HybridBody)


# ────────────────────────────────────────────────────────────────────
# ThreeDXExecutor
# ────────────────────────────────────────────────────────────────────


class ThreeDXExecutor:
    """
    Gerçek 3DEXPERIENCE / CATIA COM entegrasyonu.
    Windows'ta çalışır, pywin32 gerektirir.

    Temel varsayım: CATIA açık, ActiveEditor var, ActiveObject bir Part,
    kullanıcı doğru Part Design penceresinde.

    NOT: Bu sınıfta hiçbir yerde ActiveDocument, Documents.Open veya
    Documents.Add gibi V5 kalıntısı kullanılmaz. 3DEXPERIENCE tarafında
    giriş noktası ActiveEditor, kalıcılık tarafı PLM mantığıdır.
    """

    _PART_TYPE_NAMES = ("Part", "VPMRepReference", "DELFmiFunctionalModel")

    def __init__(self, progid: Optional[str] = None) -> None:
        self._progid = progid or os.getenv("THREEDX_PROGID", "CATIA.Application")
        self._app: Any = None
        self._deferred_update = False

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
        log.debug("COM bağlantısı kuruldu: %s", self._progid)

    # ── Preflight — tek noktadan kontrol ──────────────────────────────

    def preflight(self) -> CATIAContext:
        """
        Tüm ön koşulları tek seferde doğrular ve CATIAContext döndürür:
          1. CATIA bağlantısı var mı
          2. ActiveEditor alınabiliyor mu
          3. ActiveObject gerçekten Part mi
          4. HybridShapeFactory alınabiliyor mu
          5. ShapeFactory alınabiliyor mu
          6. En az bir Geometrical Set bulunabiliyor mu
        Herhangi biri başarısız olursa anlamlı hata fırlatır.
        """
        app = self._app
        if app is None:
            raise ThreeDXConnectionError(
                "3DEXPERIENCE uygulamasına bağlantı yok. Önce connect() çağırın."
            )

        try:
            editor = app.ActiveEditor
        except Exception as e:
            raise ThreeDXConnectionError(f"ActiveEditor alınamadı: {e}") from e
        if editor is None:
            raise ThreeDXConnectionError("Aktif editor bulunamadı.")

        try:
            obj = editor.ActiveObject
        except Exception as e:
            raise RuntimeError(f"ActiveEditor.ActiveObject alınamadı: {e}") from e
        if obj is None:
            raise RuntimeError("Aktif editor içinde nesne bulunamadı (ActiveObject=None).")

        type_name = ""
        try:
            type_name = str(type(obj).__name__)
        except Exception:
            pass

        is_part = type_name in self._PART_TYPE_NAMES
        if not is_part:
            try:
                _ = obj.HybridShapeFactory
                is_part = True
            except Exception:
                pass
        if not is_part:
            raise RuntimeError(
                f"ActiveObject bir Part değil (tip: {type_name!r}). "
                "Part Design editöründe açık bir Part olduğundan emin olun."
            )

        try:
            hsf = obj.HybridShapeFactory
        except Exception as e:
            raise RuntimeError(f"HybridShapeFactory alınamadı: {e}") from e
        if hsf is None:
            raise RuntimeError("HybridShapeFactory alınamadı (None döndü).")

        try:
            sf = obj.ShapeFactory
        except Exception as e:
            raise RuntimeError(f"ShapeFactory alınamadı: {e}") from e
        if sf is None:
            raise RuntimeError("ShapeFactory alınamadı (None döndü).")

        geom_set = self._get_geometrical_set(obj)

        ctx = CATIAContext(
            app=app, editor=editor, part=obj, hsf=hsf, sf=sf, geom_set=geom_set
        )
        log.debug(
            "Preflight OK — part_type=%s, geom_set=%s",
            type_name,
            self._safe_name(geom_set),
        )
        return ctx

    # ── Dahili yardımcılar ─────────────────────────────────────────────

    @staticmethod
    def _safe_name(com_obj: Any) -> str:
        try:
            return str(com_obj.Name)
        except Exception:
            return "?"

    def _get_geometrical_set(self, part: Any, set_name: str = "Geometrical Set.1") -> Any:
        """
        Geometrical set bulma stratejisi:
          1. Verilen isimle eşleşen set'i bul
          2. Yoksa ilk mevcut HybridBody'yi kullan
          3. O da yoksa yeni set oluştur
        """
        try:
            hbodies = part.HybridBodies
        except Exception as e:
            raise RuntimeError(
                f"Part.HybridBodies koleksiyonuna erişilemedi: {e}"
            ) from e
        if hbodies is None:
            raise RuntimeError("Part içinde HybridBodies koleksiyonu alınamadı.")

        count = 0
        try:
            count = hbodies.Count
        except Exception:
            count = 0

        for i in range(1, count + 1):
            try:
                body = hbodies.Item(i)
                if str(body.Name) == set_name:
                    log.debug("Geometrical set bulundu: %s", set_name)
                    return body
            except Exception:
                continue

        if count > 0:
            try:
                first = hbodies.Item(1)
                log.debug(
                    "İsimli set bulunamadı (%s); ilk set kullanılıyor: %s",
                    set_name, self._safe_name(first),
                )
                return first
            except Exception:
                pass

        try:
            body = hbodies.Add()
            body.Name = set_name
            log.debug("Yeni geometrical set oluşturuldu: %s", set_name)
            return body
        except Exception as e:
            raise RuntimeError(
                f"Yeni Geometrical Set oluşturulamadı: {e}"
            ) from e

    def _find_shape_in_set(self, geom_set: Any, shape_name: str) -> Any:
        try:
            return geom_set.HybridShapes.Item(shape_name)
        except Exception:
            return None

    def _find_hybrid_shape_by_name(self, part: Any, shape_name: str):
        """
        Tüm geometrical set'lerde isme göre arar.
        Aynı isimde birden fazla nesne varsa açık hata verir.
        Döndürür: (geom_set, shape) veya (None, None).
        """
        try:
            hbodies = part.HybridBodies
        except Exception:
            return None, None
        if hbodies is None:
            return None, None

        count = 0
        try:
            count = hbodies.Count
        except Exception:
            return None, None

        matches: list[tuple[Any, Any]] = []

        for i in range(1, count + 1):
            try:
                gset = hbodies.Item(i)
            except Exception:
                continue
            shape = self._find_shape_in_set(gset, shape_name)
            if shape is not None:
                matches.append((gset, shape))

        if len(matches) == 0:
            return None, None
        if len(matches) == 1:
            return matches[0]

        set_names = [self._safe_name(m[0]) for m in matches]
        raise RuntimeError(
            f"'{shape_name}' ismi birden fazla Geometrical Set'te bulundu: "
            f"{set_names}. Lütfen benzersiz isimler kullanın."
        )

    def _check_name_collision(self, part: Any, name: str) -> None:
        _gset, shape = self._find_hybrid_shape_by_name(part, name)
        if shape is not None:
            raise RuntimeError(f"İsim çakışması: '{name}' zaten mevcut.")

    def _create_reference(self, part: Any, obj: Any) -> Any:
        try:
            ref = part.CreateReferenceFromObject(obj)
        except Exception as e:
            raise RuntimeError(
                f"CreateReferenceFromObject başarısız: {e}"
            ) from e
        if ref is None:
            raise RuntimeError("CreateReferenceFromObject None döndü.")
        return ref

    def _append_and_update(self, part: Any, geom_set: Any, shape: Any) -> None:
        try:
            geom_set.AppendHybridShape(shape)
        except Exception as e:
            raise RuntimeError(f"AppendHybridShape başarısız: {e}") from e

        try:
            part.InWorkObject = shape
        except Exception:
            pass

        if not self._deferred_update:
            try:
                part.Update()
            except Exception as e:
                raise RuntimeError(f"Part.Update() başarısız: {e}") from e
            log.debug("Part.Update() çağrıldı")

    # ── Batch modu ─────────────────────────────────────────────────────

    def begin_batch(self) -> None:
        self._deferred_update = True
        log.debug("Batch modu başlatıldı — Update() erteleniyor")

    def finish_batch(self) -> None:
        self._deferred_update = False
        ctx = self.preflight()
        try:
            ctx.part.Update()
        except Exception as e:
            raise RuntimeError(f"Batch Update() başarısız: {e}") from e
        log.debug("Batch Update() tamamlandı")

    # ── Service erişimi ────────────────────────────────────────────────

    def get_service(self, service_name: str) -> Any:
        """editor.GetService — editor-level service (örn. PLMPropagateService)."""
        ctx = self.preflight()
        try:
            svc = ctx.editor.GetService(service_name)
        except Exception as e:
            raise RuntimeError(
                f"Editor servisi alınamadı ('{service_name}'): {e}"
            ) from e
        if svc is None:
            raise RuntimeError(f"Editor servisi None döndü: '{service_name}'")
        return svc

    def get_session_service(self, service_name: str) -> Any:
        """CATIA.GetSessionService — session-level service (örn. PLMSearch)."""
        if self._app is None:
            raise ThreeDXConnectionError("Bağlantı yok. Önce connect() çağırın.")
        try:
            svc = self._app.GetSessionService(service_name)
        except Exception as e:
            raise RuntimeError(
                f"Session servisi alınamadı ('{service_name}'): {e}"
            ) from e
        if svc is None:
            raise RuntimeError(f"Session servisi None döndü: '{service_name}'")
        return svc

    def save(self) -> None:
        """
        3DEXPERIENCE'ta değişiklikleri veritabanına kaydet.
        Yerel dosya save mantığı yoktur; PLMPropagateService kullanılır.
        """
        try:
            propagate_svc = self.get_service("PLMPropagateService")
            propagate_svc.PLMPropagate()
            log.info("PLMPropagate ile kayıt tamamlandı")
        except Exception as e:
            raise RuntimeError(
                f"PLMPropagate ile kayıt başarısız: {e}\n"
                "3DEXPERIENCE oturumunuzun veritabanına yazma izni olduğundan emin olun."
            ) from e

    # ── Aksiyon: create_point ──────────────────────────────────────────

    def create_point(self, name: str, x: float, y: float, z: float) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            pt = ctx.hsf.AddNewPointCoord(float(x), float(y), float(z))
            if pt is None:
                raise RuntimeError("AddNewPointCoord None döndü.")
            pt.Name = name
            self._append_and_update(ctx.part, ctx.geom_set, pt)
            log.debug("create_point OK — name=%s, geom_set=%s", name, self._safe_name(ctx.geom_set))
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Nokta oluşturulamadı: {e}") from e

    # ── Aksiyon: create_line_between_points ────────────────────────────

    def create_line_between_points(self, name: str, p1: str, p2: str) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            gset1, p1_obj = self._find_hybrid_shape_by_name(ctx.part, p1)
            _gset2, p2_obj = self._find_hybrid_shape_by_name(ctx.part, p2)

            if p1_obj is None:
                raise RuntimeError(f"'{p1}' isimli nokta part içinde bulunamadı.")
            if p2_obj is None:
                raise RuntimeError(f"'{p2}' isimli nokta part içinde bulunamadı.")

            ref1 = self._create_reference(ctx.part, p1_obj)
            ref2 = self._create_reference(ctx.part, p2_obj)

            line = ctx.hsf.AddNewLinePtPt(ref1, ref2)
            if line is None:
                raise RuntimeError("AddNewLinePtPt None döndü.")
            line.Name = name

            target = gset1 or ctx.geom_set
            self._append_and_update(ctx.part, target, line)
            log.debug("create_line OK — name=%s, p1=%s, p2=%s", name, p1, p2)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Çizgi oluşturulamadı: {e}") from e

    # ── Aksiyon: create_plane ──────────────────────────────────────────

    def create_plane(
        self, name: str, through_point: str, normal: List[float]
    ) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            _gset, pt_shape = self._find_hybrid_shape_by_name(ctx.part, through_point)
            if pt_shape is None:
                raise RuntimeError(
                    f"'{through_point}' isimli nokta part içinde bulunamadı."
                )

            pt_ref = self._create_reference(ctx.part, pt_shape)

            direction = ctx.hsf.AddNewDirectionByCoord(
                float(normal[0]), float(normal[1]), float(normal[2])
            )
            if direction is None:
                raise RuntimeError("AddNewDirectionByCoord None döndü.")

            plane = ctx.hsf.AddNewPlaneNormal(direction, pt_ref)
            if plane is None:
                raise RuntimeError("AddNewPlaneNormal None döndü.")
            plane.Name = name
            self._append_and_update(ctx.part, ctx.geom_set, plane)
            log.debug("create_plane OK — name=%s, through=%s", name, through_point)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Düzlem oluşturulamadı: {e}") from e

    # ── Aksiyon: create_sketch ─────────────────────────────────────────

    def create_sketch(self, name: str, on_plane: str) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            _gset, plane_shape = self._find_hybrid_shape_by_name(ctx.part, on_plane)
            if plane_shape is None:
                raise RuntimeError(
                    f"'{on_plane}' isimli düzlem part içinde bulunamadı."
                )

            plane_ref = self._create_reference(ctx.part, plane_shape)
            sketches = ctx.geom_set.HybridSketches
            sketch = sketches.Add(plane_ref)
            if sketch is None:
                raise RuntimeError("HybridSketches.Add None döndü.")
            sketch.Name = name

            if not self._deferred_update:
                ctx.part.Update()
            log.debug("create_sketch OK — name=%s, on_plane=%s", name, on_plane)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Sketch oluşturulamadı: {e}") from e

    # ── Aksiyon: create_circle ─────────────────────────────────────────

    def create_circle(
        self, name: str, center_point: str, radius: float
    ) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            _gset, pt_shape = self._find_hybrid_shape_by_name(ctx.part, center_point)
            if pt_shape is None:
                raise RuntimeError(
                    f"'{center_point}' isimli nokta part içinde bulunamadı."
                )

            pt_ref = self._create_reference(ctx.part, pt_shape)

            xy_plane = ctx.part.OriginElements.PlaneXY
            plane_ref = self._create_reference(ctx.part, xy_plane)

            circle = ctx.hsf.AddNewCircleCtrRad(
                pt_ref, plane_ref, False, float(radius)
            )
            if circle is None:
                raise RuntimeError("AddNewCircleCtrRad None döndü.")
            circle.Name = name
            self._append_and_update(ctx.part, ctx.geom_set, circle)
            log.debug("create_circle OK — name=%s, center=%s, r=%s", name, center_point, radius)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Daire oluşturulamadı: {e}") from e

    # ── Aksiyon: extrude_pad ───────────────────────────────────────────

    def extrude_pad(
        self, name: str, from_sketch: str, length: float
    ) -> None:
        try:
            ctx = self.preflight()
            self._check_name_collision(ctx.part, name)

            _gset, sketch_shape = self._find_hybrid_shape_by_name(ctx.part, from_sketch)
            if sketch_shape is None:
                raise RuntimeError(
                    f"'{from_sketch}' isimli sketch/profile part içinde bulunamadı."
                )

            sketch_ref = self._create_reference(ctx.part, sketch_shape)
            pad = ctx.sf.AddNewPad(sketch_ref, float(length))
            if pad is None:
                raise RuntimeError("AddNewPad None döndü.")
            pad.Name = name

            if not self._deferred_update:
                ctx.part.Update()
            log.debug("extrude_pad OK — name=%s, from=%s, len=%s", name, from_sketch, length)
        except Exception as e:
            if isinstance(e, (ThreeDXConnectionError, RuntimeError)):
                raise
            raise RuntimeError(f"Extrude (pad) oluşturulamadı: {e}") from e

    # ── Yardımcı: mevcut geometrileri listele ──────────────────────────

    def _iter_all_shapes(self, part: Any):
        try:
            hbodies = part.HybridBodies
        except Exception:
            return
        if hbodies is None:
            return

        count = 0
        try:
            count = hbodies.Count
        except Exception:
            return

        for i in range(1, count + 1):
            try:
                geom_set = hbodies.Item(i)
            except Exception:
                continue
            try:
                shapes = geom_set.HybridShapes
            except Exception:
                continue
            if shapes is None:
                continue

            shape_count = 0
            try:
                shape_count = shapes.Count
            except Exception:
                continue

            for j in range(1, shape_count + 1):
                try:
                    yield geom_set, shapes.Item(j)
                except Exception:
                    continue

    def get_point(self, name: str) -> Any:
        ctx = self.preflight()
        _gset, shape = self._find_hybrid_shape_by_name(ctx.part, name)
        if shape is None:
            raise RuntimeError(f"'{name}' isimli geometri part içinde bulunamadı.")
        return shape

    def geometry_exists(self, name: str) -> bool:
        ctx = self.preflight()
        _gset, shape = self._find_hybrid_shape_by_name(ctx.part, name)
        return shape is not None

    def list_geometry_names(self) -> List[str]:
        ctx = self.preflight()
        names: List[str] = []
        for _gset, shape in self._iter_all_shapes(ctx.part):
            try:
                names.append(str(shape.Name))
            except Exception:
                pass
        return names

    def list_objects(self) -> Dict[str, Dict[str, Any]]:
        ctx = self.preflight()
        result: Dict[str, Dict[str, Any]] = {}
        for _gset, shape in self._iter_all_shapes(ctx.part):
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
        objects = self.list_objects()
        if not objects:
            return "(boş sahne)"
        lines = []
        for obj_name, obj_data in objects.items():
            lines.append(f"  {obj_name} [{obj_data['type']}]")
        return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────
# Dry-run — COM adımlarını çalıştırmadan göster
# ────────────────────────────────────────────────────────────────────


def _dry_run_describe(op: Dict[str, Any]) -> List[str]:
    """Tek bir operasyon için hangi COM adımlarının çalışacağını döndürür."""
    action = op["action"]
    name = op.get("name", "?")
    steps: List[str] = []

    steps.append(f"  preflight() → app, editor, part, hsf, sf, geom_set")

    if action == "create_point":
        x, y, z = op.get("coordinates", [0, 0, 0])
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  hsf.AddNewPointCoord({x}, {y}, {z})")
        steps.append(f"  pt.Name = '{name}'")
        steps.append(f"  geom_set.AppendHybridShape(pt)")
        steps.append(f"  part.InWorkObject = pt")

    elif action == "create_line_between_points":
        pnames = op.get("point_names", ["?", "?"])
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{pnames[0]}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{pnames[1]}')")
        steps.append(f"  ref1 = part.CreateReferenceFromObject(p1_obj)")
        steps.append(f"  ref2 = part.CreateReferenceFromObject(p2_obj)")
        steps.append(f"  hsf.AddNewLinePtPt(ref1, ref2)")
        steps.append(f"  line.Name = '{name}'")
        steps.append(f"  geom_set.AppendHybridShape(line)")
        steps.append(f"  part.InWorkObject = line")

    elif action == "create_plane":
        tp = op.get("through_point", "?")
        n = op.get("normal", [0, 0, 0])
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{tp}')")
        steps.append(f"  ref = part.CreateReferenceFromObject(pt_shape)")
        steps.append(f"  hsf.AddNewDirectionByCoord({n[0]}, {n[1]}, {n[2]})")
        steps.append(f"  hsf.AddNewPlaneNormal(direction, ref)")
        steps.append(f"  plane.Name = '{name}'")
        steps.append(f"  geom_set.AppendHybridShape(plane)")

    elif action == "create_sketch":
        op_name = op.get("on_plane", "?")
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{op_name}')")
        steps.append(f"  ref = part.CreateReferenceFromObject(plane_shape)")
        steps.append(f"  geom_set.HybridSketches.Add(ref)")
        steps.append(f"  sketch.Name = '{name}'")

    elif action == "create_circle":
        cp = op.get("center_point", "?")
        r = op.get("radius", 0)
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{cp}')")
        steps.append(f"  pt_ref = part.CreateReferenceFromObject(pt_shape)")
        steps.append(f"  plane_ref = part.CreateReferenceFromObject(OriginElements.PlaneXY)")
        steps.append(f"  hsf.AddNewCircleCtrRad(pt_ref, plane_ref, False, {r})")
        steps.append(f"  circle.Name = '{name}'")
        steps.append(f"  geom_set.AppendHybridShape(circle)")

    elif action == "extrude_pad":
        fs = op.get("from_sketch", "?")
        ln = op.get("length", 0)
        steps.append(f"  _check_name_collision(part, '{name}')")
        steps.append(f"  _find_hybrid_shape_by_name(part, '{fs}')")
        steps.append(f"  ref = part.CreateReferenceFromObject(sketch_shape)")
        steps.append(f"  sf.AddNewPad(ref, {ln})")
        steps.append(f"  pad.Name = '{name}'")

    steps.append(f"  part.Update()")
    return steps


# ────────────────────────────────────────────────────────────────────
# Plan çalıştırıcı
# ────────────────────────────────────────────────────────────────────


def execute_plan(
    plan: Dict[str, Any],
    simulate: bool = True,
    connector: Optional[ThreeDXExecutor] = None,
    dry_run: bool = False,
) -> List[str]:
    """
    Planı çalıştırır.
    - simulate=True  → sadece terminal simülasyonu
    - simulate=False → connector ile gerçek 3DEXPERIENCE çağrıları
    - dry_run=True   → COM çağrısı yapmadan hangi adımların çalışacağını göster

    Birden fazla operasyonda batch modu kullanılır: her adımda ayrı
    Update() yerine tüm geometri eklendikten sonra tek bir Update().
    """

    plan = validate_plan(plan)
    operations: List[Dict[str, Any]] = plan["operations"]

    messages: List[str] = []

    # ── Dry-run modu ──────────────────────────────────────────────
    if dry_run:
        messages.append("[DRY-RUN] Aşağıdaki COM adımları çalıştırılacaktı:")
        for idx, op in enumerate(operations):
            action = op["action"]
            name = op.get("name", "?")
            messages.append(f"")
            messages.append(f"── Adım {idx + 1}: {action} '{name}' ──")
            for step in _dry_run_describe(op):
                messages.append(step)
        if len(operations) > 1:
            messages.append(f"")
            messages.append("── Batch modu: tüm adımlar tamamlandıktan sonra tek part.Update() ──")
        return messages

    # ── Normal çalıştırma ─────────────────────────────────────────
    use_real = not simulate and connector is not None

    if not use_real:
        messages.append(
            "[SIMULATION] 3DEXPERIENCE bağlantısı yerine terminal çıktısı kullanılıyor."
        )

    batch_mode = use_real and len(operations) > 1
    if batch_mode:
        connector.begin_batch()  # type: ignore[union-attr]

    try:
        for op in operations:
            action = op["action"]

            if not use_real:
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
                    connector.create_point(name, float(x), float(y), float(z))  # type: ignore[union-attr]
                    messages.append(f"[OK] create_point {name} ({x}, {y}, {z})")
                elif action == "create_line_between_points":
                    p1, p2 = op["point_names"]
                    connector.create_line_between_points(name, p1, p2)  # type: ignore[union-attr]
                    messages.append(f"[OK] create_line_between_points {name} ({p1}, {p2})")
                elif action == "create_plane":
                    connector.create_plane(name, op["through_point"], op["normal"])  # type: ignore[union-attr]
                    messages.append(
                        f"[OK] create_plane {name} through {op['through_point']} "
                        f"normal {op['normal']}"
                    )
                elif action == "create_sketch":
                    connector.create_sketch(name, op["on_plane"])  # type: ignore[union-attr]
                    messages.append(f"[OK] create_sketch {name} on {op['on_plane']}")
                elif action == "create_circle":
                    connector.create_circle(name, op["center_point"], float(op["radius"]))  # type: ignore[union-attr]
                    messages.append(
                        f"[OK] create_circle {name} at {op['center_point']} r={op['radius']}"
                    )
                elif action == "extrude_pad":
                    connector.extrude_pad(name, op["from_sketch"], float(op["length"]))  # type: ignore[union-attr]
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
    finally:
        if batch_mode:
            try:
                connector.finish_batch()  # type: ignore[union-attr]
            except Exception as e:
                messages.append(f"[HATA] Toplu Update başarısız: {e}")

    return messages


# ────────────────────────────────────────────────────────────────────
# Simülasyon fonksiyonları
# ────────────────────────────────────────────────────────────────────


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
    return json.dumps(plan, ensure_ascii=False, indent=2)
