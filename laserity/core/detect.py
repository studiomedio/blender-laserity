"""Finding plate-like objects in a scene and measuring their thickness.

A "flat part" here means a slab: geometry whose vertices sit almost entirely on
two parallel planes a short distance apart. That test catches the panels, ribs
and gussets people actually laser-cut, while rejecting solids, and it works
regardless of how the object is rotated in the scene because everything is done
in world space.
"""

from __future__ import annotations

import contextlib
import math

import bmesh
from mathutils import Matrix, Vector

#: Object types that can be converted to a mesh for analysis.
CONVERTIBLE_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}

_MERGE_DISTANCE_MM = 1e-4

#: Bit flags stored in the ``laserity_mark`` bmesh edge layer.
MARK_FREESTYLE = 1
MARK_SEAM = 2
MARK_SHARP = 4


class SlabAnalysis:
  """The result of measuring one object."""

  __slots__ = (
    "normal",
    "u_axis",
    "v_axis",
    "thickness",
    "planarity",
    "width",
    "height",
    "face_area",
    "top_offset",
    "bottom_offset",
    "is_flat",
    "reason",
  )

  def __init__(self, **kwargs):
    for slot in self.__slots__:
      setattr(self, slot, kwargs.get(slot))

  def project(self, co):
    """World-space (mm) vector -> 2D part-space (mm) tuple."""
    return (co.dot(self.u_axis), co.dot(self.v_axis))

  def __repr__(self):  # pragma: no cover - debugging aid
    return (
      f"SlabAnalysis(thickness={self.thickness:.3f}mm, "
      f"planarity={self.planarity:.2f}, flat={self.is_flat}, reason={self.reason!r})"
    )


class DetectSettings:
  """Plain container so the core can be driven from tests without Blender props."""

  def __init__(
    self,
    max_thickness=50.0,
    min_planarity=0.80,
    min_flatness_ratio=1.5,
    min_area=1.0,
    normal_angle_tolerance=math.radians(2.0),
    unit_to_mm=1000.0,
  ):
    self.max_thickness = max_thickness
    self.min_planarity = min_planarity
    self.min_flatness_ratio = min_flatness_ratio
    self.min_area = min_area
    self.normal_angle_tolerance = normal_angle_tolerance
    self.unit_to_mm = unit_to_mm


def scene_unit_to_mm(scene):
  """Millimetres per Blender unit for the given scene."""
  scale = getattr(scene.unit_settings, "scale_length", 1.0) or 1.0
  return scale * 1000.0


@contextlib.contextmanager
def object_bmesh(obj, depsgraph, unit_to_mm, merge_doubles=True):
  """Yield a world-space bmesh in millimetres, or ``None`` if unusable.

  Modifiers are applied (the evaluated object is used), the mesh is transformed
  into world space and scaled to millimetres, and winding is corrected for
  negatively scaled objects so face normals stay meaningful.
  """
  if obj.type not in CONVERTIBLE_TYPES:
    yield None
    return

  eval_obj = obj.evaluated_get(depsgraph)
  mesh = None
  bm = None
  try:
    try:
      mesh = eval_obj.to_mesh()
    except RuntimeError:
      mesh = None
    if mesh is None or len(mesh.polygons) == 0:
      yield None
      return

    bm = bmesh.new()
    bm.from_mesh(mesh)
    _collect_edge_marks(bm)

    matrix = Matrix.Diagonal((unit_to_mm,) * 3).to_4x4() @ obj.matrix_world
    bm.transform(matrix)
    if matrix.determinant() < 0.0:
      bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

    if merge_doubles:
      bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=_MERGE_DISTANCE_MM)

    bm.normal_update()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    yield bm
  finally:
    if bm is not None:
      bm.free()
    if mesh is not None:
      with contextlib.suppress(Exception):
        eval_obj.to_mesh_clear()


def _collect_edge_marks(bm):
  """Fold Blender's three edge markings into one ``laserity_mark`` bit layer.

  Everything is read through bmesh rather than through the mesh: seams and
  sharpness are native bmesh flags, while Freestyle marks live in a generic
  boolean attribute since Blender 4.1 and are no longer exposed on ``MeshEdge``
  at all. Collapsing them here also means the marks ride along through the
  topology edits that follow, instead of depending on edge indices staying put.
  """
  freestyle = bm.edges.layers.bool.get("freestyle_edge")
  layer = bm.edges.layers.int.get("laserity_mark")
  if layer is None:
    layer = bm.edges.layers.int.new("laserity_mark")

  for edge in bm.edges:
    flags = 0
    if freestyle is not None and edge[freestyle]:
      flags |= MARK_FREESTYLE
    if edge.seam:
      flags |= MARK_SEAM
    if not edge.smooth:
      flags |= MARK_SHARP
    edge[layer] = flags


def dominant_axis(bm, angle_tolerance):
  """The normal of the largest set of mutually parallel faces.

  Faces are greedily clustered by absolute normal alignment, seeded from the
  largest faces, so a slab's two big opposing faces dominate the result.
  """
  cos_tol = math.cos(angle_tolerance)
  faces = []
  for face in bm.faces:
    a = face.calc_area()
    if a > 1e-12:
      faces.append((a, face.normal.copy()))
  if not faces:
    return None
  faces.sort(key=lambda item: -item[0])

  clusters = []  # [normal, accumulated_area]
  for face_area, normal in faces:
    for cluster in clusters:
      if abs(normal.dot(cluster[0])) >= cos_tol:
        cluster[1] += face_area
        break
    else:
      clusters.append([normal, face_area])

  clusters.sort(key=lambda c: -c[1])
  return _canonical_direction(clusters[0][0])


def _canonical_direction(normal):
  """Flip ``normal`` to a deterministic hemisphere so results are stable."""
  n = normal.normalized()
  for component in (2, 1, 0):  # prefer +Z, then +Y, then +X
    if abs(n[component]) > 1e-6:
      if n[component] < 0.0:
        n = -n
      break
  return n


def build_basis(normal, reference_matrix=None):
  """Orthonormal right-handed ``(u, v, n)`` with ``u`` near the object's X axis.

  Anchoring ``u`` to the object's own X axis keeps the flattened part in a
  predictable orientation relative to how it was modelled.
  """
  n = normal.normalized()
  ref = Vector((1.0, 0.0, 0.0))
  if reference_matrix is not None:
    ref = (reference_matrix.to_3x3() @ Vector((1.0, 0.0, 0.0)))
    if ref.length < 1e-9:
      ref = Vector((1.0, 0.0, 0.0))
    ref.normalize()

  u = ref - n * ref.dot(n)
  if u.length < 1e-4:
    u = n.orthogonal()
  u.normalize()
  v = n.cross(u).normalized()
  return (u, v, n)


def analyze(bm, settings, reference_matrix=None):
  """Measure a world-space bmesh. Always returns a :class:`SlabAnalysis`.

  ``is_flat`` reports whether it passed, and ``reason`` explains a rejection so
  the UI can tell the user why an object was skipped.
  """
  normal = dominant_axis(bm, settings.normal_angle_tolerance)
  if normal is None:
    return SlabAnalysis(
      normal=Vector((0.0, 0.0, 1.0)),
      u_axis=Vector((1.0, 0.0, 0.0)),
      v_axis=Vector((0.0, 1.0, 0.0)),
      thickness=0.0,
      planarity=0.0,
      width=0.0,
      height=0.0,
      face_area=0.0,
      top_offset=0.0,
      bottom_offset=0.0,
      is_flat=False,
      reason="no faces",
    )

  u_axis, v_axis, n_axis = build_basis(normal, reference_matrix)

  offsets = [vert.co.dot(n_axis) for vert in bm.verts]
  bottom = min(offsets)
  top = max(offsets)
  thickness = top - bottom

  tolerance = max(thickness * 0.02, 1e-3)
  on_plane = sum(
    1 for d in offsets if (d - bottom) <= tolerance or (top - d) <= tolerance
  )
  planarity = on_plane / len(offsets) if offsets else 0.0

  us = [vert.co.dot(u_axis) for vert in bm.verts]
  vs = [vert.co.dot(v_axis) for vert in bm.verts]
  width = max(us) - min(us)
  height = max(vs) - min(vs)

  cos_tol = math.cos(settings.normal_angle_tolerance)
  face_area = sum(
    face.calc_area() for face in bm.faces if abs(face.normal.dot(n_axis)) >= cos_tol
  ) * 0.5

  analysis = SlabAnalysis(
    normal=n_axis,
    u_axis=u_axis,
    v_axis=v_axis,
    thickness=thickness,
    planarity=planarity,
    width=width,
    height=height,
    face_area=face_area,
    top_offset=top,
    bottom_offset=bottom,
    is_flat=False,
    reason="",
  )

  smallest_in_plane = min(width, height)
  if smallest_in_plane <= 1e-6:
    analysis.reason = "degenerate outline"
  elif thickness > settings.max_thickness:
    analysis.reason = f"thicker than {settings.max_thickness:g} mm"
  elif planarity < settings.min_planarity:
    analysis.reason = f"not slab-like ({planarity * 100:.0f}% of verts on the faces)"
  elif thickness > 1e-6 and smallest_in_plane < thickness * settings.min_flatness_ratio:
    analysis.reason = "too chunky (smallest side vs thickness)"
  elif face_area < settings.min_area:
    analysis.reason = f"face area under {settings.min_area:g} mm²"
  else:
    analysis.is_flat = True

  return analysis


def thickness_key(thickness, tolerance):
  """Quantise a thickness so parts cut from the same stock group together."""
  if tolerance <= 1e-9:
    return round(thickness, 4)
  return round(round(thickness / tolerance) * tolerance, 4)


def format_thickness(thickness):
  """Human label for a thickness, e.g. ``6 mm``, ``3.2 mm``."""
  text = f"{thickness:.2f}".rstrip("0").rstrip(".")
  return f"{text or '0'} mm"
