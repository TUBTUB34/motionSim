"""Cached model-specific UR visual meshes, expressed in the simulator's DH frames."""
from functools import lru_cache
from pathlib import Path

import numpy as np

from kinematics import MODELS

ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets" / "robots"


def model_family(model):
    if model not in MODELS:
        raise ValueError(f"Unknown robot model: {model}")
    if model.endswith("e"):
        return "e-Series"
    return "CB-Series" if model in ("UR3", "UR5", "UR10") else "UR Series"


@lru_cache(maxsize=len(MODELS))
def load_robot_mesh(model):
    """Load seven immutable link meshes once; no network or CAD library at runtime."""
    model_family(model)
    meshes = []
    with np.load(ASSET_DIRECTORY / f"{model.lower().replace(' ', '')}.npz", allow_pickle=False) as archive:
        for index in range(7):
            arrays = tuple(archive[f"{name}_{index}"] for name in ("vertices", "normals", "colors", "shading"))
            for array in arrays:
                array.setflags(write=False)
            meshes.append(arrays)
    return tuple(meshes)
