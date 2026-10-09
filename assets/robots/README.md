# Universal Robots visual assets

These are converted model-specific visual meshes from
[UniversalRobots/Universal_Robots_ROS2_Description](https://github.com/UniversalRobots/Universal_Robots_ROS2_Description),
pinned to commit `6662e15f32c23c12ece57d0050aee9a716e4fc41`.
`manifest.json` records the source mesh hashes and per-link triangle counts.

| Family | Models |
| --- | --- |
| CB-Series | UR3, UR5, UR10 |
| e-Series | UR3e, UR5e, UR7e, UR10e, UR12e, UR16e |
| UR Series | UR8 Long, UR15, UR18, UR20, UR30 |

Each NumPy archive contains seven rigid visual links in the application's DH
frames, face normals for visibility, vertex normals for smooth lighting, and
RGB materials. The source URDF joint origins and DH alignment matrices are
included for independent pose-alignment tests. The selected model loads once
and works offline. No mesh-conversion libraries are needed at runtime.

Changes from upstream: legacy CAD meshes are simplified by material; modern
low-poly UV meshes have their original texture colors sampled per triangle;
mesh offsets are baked into DH link coordinates; normals are smoothed and
neutral legacy metal albedo is adjusted for the viewport's lighting. Tiny
lettering, texture details, and machined features are approximate. Original
UR-Series texture images, including their embedded markings, are retained
unchanged in `source_textures/`. These are nominal visualization assets, not
calibrated metrology or collision meshes.

## Licensing and attribution

© 2023 Universal Robots A/S. Use hereof is subject to Universal Robots A/S’
Terms and Conditions for Use of Graphical Documentation.

- UR8 Long, UR15, UR18, UR20, UR30 meshes and textures: [UR graphical documentation terms](LICENSE-UR-GRAPHICS.txt).
- Other upstream content: [BSD 3-Clause license](LICENSE-BSD.txt).
- [Upstream licensing statement](https://github.com/UniversalRobots/Universal_Robots_ROS2_Description/blob/6662e15f32c23c12ece57d0050aee9a716e4fc41/README.md#license).

Retain these notices and the included terms when distributing the application.
The UR-Series assets have separate terms from the application's Python code.

## Rebuild

In a separate build environment, install `numpy`, `trimesh`, `pycollada`,
`fast-simplification`, `PyYAML`, `Pillow`, and `scipy` (Trimesh normal calculation).
Check out the official repository at the pinned commit, then run:

```bash
python scripts/build_robot_assets.py /path/to/Universal_Robots_ROS2_Description
```

Use `--models UR7e UR12e "UR8 Long" UR18` for a partial rebuild at the same
pinned revision. UR7e and UR12e reuse the official UR5e and UR10e visual sources
respectively, as specified in their own upstream configuration files. UR8 Long
and UR18 use their own arm links with the shared UR15 base and wrist meshes.

Keep the upstream licenses and original textures alongside rebuilt archives.
The builder is an offline conversion tool and does not contact the robot.
