"""Executed inside background Blender; bounded useful facts instead of a scene dump."""

import bpy, json
from mathutils import Vector

s = bpy.context.scene
names = globals().get("SUBJECT_NAMES", [])
mode = globals().get("PROBE_MODE", "inspect")
if mode == "api":
    result = {"version": bpy.app.version_string, "types": {}}
    for name in (names or ["ShaderNodeTexSky", "RenderSettings"])[:4]:
        cls = getattr(bpy.types, name, None)
        if not cls or not hasattr(cls, "bl_rna"):
            result["types"][name] = {"error": "Unknown RNA type"}
            continue
        props = {}
        for prop in cls.bl_rna.properties:
            if prop.identifier == "rna_type":
                continue
            if prop.type == "ENUM":
                props[prop.identifier] = [x.identifier for x in prop.enum_items][:30]
        result["types"][name] = props
    print("BLENDER_API:" + json.dumps(result))
else:
    meshes = [
        o for o in s.objects if o.type == "MESH" and (not names or o.name in names)
    ]
    points = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    bbox = None
    if points:
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        bbox = {"min": lo, "max": hi, "dimensions": [hi[i] - lo[i] for i in range(3)]}
    sorted_meshes = sorted(meshes, key=lambda o: len(o.data.vertices), reverse=True)
    result = {
        "version": bpy.app.version_string,
        "file": bpy.data.filepath,
        "object_count": len(s.objects),
        "meshes": len(meshes),
        "vertices": sum(len(o.data.vertices) for o in meshes),
        "world_bounds": bbox,
        "camera": s.camera.name if s.camera else None,
        "engine": s.render.engine,
        "frames": [s.frame_start, s.frame_end],
        "fps": s.render.fps,
        "resolution": [s.render.resolution_x, s.render.resolution_y],
        "animated_objects": sum(bool(o.animation_data) for o in s.objects),
        "roots": [o.name for o in s.objects if o.parent is None][:12],
        "largest_meshes": [
            {
                "name": o.name,
                "vertices": len(o.data.vertices),
                "dimensions": [round(v, 3) for v in o.dimensions],
                "materials": [m.name for m in o.data.materials if m][:5],
            }
            for o in sorted_meshes[:8]
        ],
    }
    print("PROJECT_INFO:" + json.dumps(result))
