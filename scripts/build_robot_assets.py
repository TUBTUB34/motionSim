"""Build compact renderer assets from the pinned official UR description meshes.

Build-only dependencies: trimesh, pycollada, fast-simplification, PyYAML, Pillow, SciPy.
Runtime needs only NumPy. See assets/robots/README.md for provenance and licenses.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import trimesh
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kinematics import MODELS, forward_kinematics

REVISION = "6662e15f32c23c12ece57d0050aee9a716e4fc41"
LINKS = ("base", "shoulder", "upper_arm", "forearm", "wrist_1", "wrist_2", "wrist_3")


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!degrees", lambda loader, node: math.radians(float(loader.construct_scalar(node))))


def transform(values):
    return trimesh.transformations.compose_matrix(
        translate=[values[k] for k in ("x", "y", "z")],
        angles=[values[k] for k in ("roll", "pitch", "yaw")])


def face_colors(mesh):
    material = mesh.visual.material
    image = material.baseColorTexture
    if image is not None and mesh.visual.uv is not None:
        # Bake several samples per triangle, keeping the original material map
        # (including embedded markings) as the color source.
        pixels = np.asarray(image.convert("RGB"))
        uv = mesh.visual.uv[mesh.faces]
        weights = np.array([[1/3]*3, [.6,.2,.2], [.2,.6,.2], [.2,.2,.6]])
        uv = np.einsum("sv,fvc->fsc", weights, uv)
        x = np.clip(np.rint(uv[..., 0] * (image.width-1)).astype(int), 0, image.width-1)
        y = np.clip(np.rint((1-uv[..., 1]) * (image.height-1)).astype(int), 0, image.height-1)
        return np.rint(pixels[y, x].mean(axis=1)).astype(np.uint8)
    rgb = np.array(material.main_color[:3], dtype=float)
    # Legacy exports use a very dark grey to represent reflective metal. Our
    # lit canvas has no environment map: give neutral aluminum a satin albedo.
    if np.ptp(rgb) < 12 and 35 < rgb.mean() < 160:
        rgb = np.array([185, 193, 198])
    return np.tile(rgb.astype(np.uint8), (len(mesh.faces), 1))


def build(source, output, model):
    key = model.lower().replace(' ', '')
    config = source / "config" / key
    visual = yaml.load((config / "visual_parameters.yaml").read_text(), Loader=Loader)["mesh_files"]
    kinematics = yaml.safe_load((config / "default_kinematics.yaml").read_text())["kinematics"]
    origins = np.array([transform(kinematics[name]) for name in LINKS[1:]])
    dh_zero = forward_kinematics(model, np.zeros(6))
    link_zero = np.eye(4)
    arrays = {"source_joint_origins": origins}
    offsets = []
    sources = {}
    counts = []
    for i, name in enumerate(LINKS):
        if i:
            link_zero = link_zero @ origins[i-1]
        offset = np.linalg.inv(dh_zero[i]) @ link_zero
        offsets.append(offset)
        mesh_path = visual[name]["visual"]["mesh"]["path"]
        path = source / mesh_path
        sources[mesh_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        scene = trimesh.load(path, force="scene")
        total = sum(len(g.faces) for g in scene.geometry.values())
        batches = []
        for node in scene.graph.nodes_geometry:
            node_transform, geometry = scene.graph[node]
            mesh = scene.geometry[geometry].copy()
            # Modern textured assets are already low-poly; preserve their UVs.
            # Simplify each legacy material independently to keep blue caps and
            # black seams separate from metal, with a budget per complete link.
            if mesh.visual.material.baseColorTexture is None:
                colors = face_colors(mesh)[0]
                target = max(24, int(4000 * len(mesh.faces) / total))
                mesh.merge_vertices(merge_tex=True, merge_norm=True)
                if len(mesh.faces) > target:
                    mesh = mesh.simplify_quadric_decimation(face_count=target)
                colors = np.tile(colors, (len(mesh.faces), 1))
            else:
                colors = face_colors(mesh)
            matrix = offset @ transform(visual[name]["mesh_offset"]) @ node_transform
            triangles = trimesh.transform_points(mesh.vertices, matrix)[mesh.faces]
            smooth_mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)
            smooth_mesh.merge_vertices(merge_norm=True, merge_tex=True)
            shading = smooth_mesh.vertex_normals[smooth_mesh.faces] @ np.linalg.inv(matrix[:3, :3])
            shading /= np.maximum(np.linalg.norm(shading, axis=2)[:, :, None], 1e-12)
            normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            valid = lengths > 1e-12
            batches.append((triangles[valid], normals[valid] / lengths[valid, None], colors[valid], shading[valid]))
        arrays[f"vertices_{i}"] = np.concatenate([b[0] for b in batches]).astype(np.float32)
        arrays[f"normals_{i}"] = np.concatenate([b[1] for b in batches]).astype(np.float32)
        arrays[f"colors_{i}"] = np.concatenate([b[2] for b in batches])
        arrays[f"shading_{i}"] = np.concatenate([b[3] for b in batches]).astype(np.float32)
        counts.append(len(arrays[f"vertices_{i}"]))
    arrays["dh_from_source_link"] = np.array(offsets)
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / f"{key}.npz", **arrays)
    return {"triangles_per_link": counts, "sources_sha256": sources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Checkout of official UR ROS2 Description at the pinned revision")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]/"assets"/"robots")
    parser.add_argument("--models", nargs='+', choices=tuple(MODELS), help="Rebuild only selected models")
    args = parser.parse_args()
    manifest = {"repository": "https://github.com/UniversalRobots/Universal_Robots_ROS2_Description",
                "revision": REVISION, "links": LINKS, "models": {}}
    existing = args.output / 'manifest.json'
    if args.models and existing.exists():
        manifest = json.loads(existing.read_text())
        if manifest['revision'] != REVISION:
            raise ValueError('Selected-model rebuild requires the same upstream revision')
    for model in args.models or MODELS:
        result = build(args.source, args.output, model)
        manifest["models"][model] = result
        print(model, sum(result["triangles_per_link"]), "triangles", flush=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
