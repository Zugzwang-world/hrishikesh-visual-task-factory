"""Choose the Cycles render device. Runs inside Blender.

GPU is the default for speed. CPU is the option for byte-identical images: on the Metal GPU a
same-seed re-render can differ by one colour level in a few pixels, while CPU renders are exact
(see docs/thinking-partner/DECISIONS.md, 23 Nov 2026, determinism).
"""

import platform

import bpy

# First backend that offers a device wins: Metal on the Mac, OptiX or CUDA on NVIDIA.
GPU_BACKENDS = ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI")


def configure_device(device: str = "GPU") -> dict:
    """Set the scene's Cycles device to "GPU" or "CPU" and report what will be used.

    Asking for GPU falls back to CPU, with a note in the report, when no GPU backend is found.
    """
    device = device.upper()
    if device not in ("GPU", "CPU"):
        raise ValueError(f"device must be GPU or CPU, not {device!r}")
    prefs = bpy.context.preferences.addons["cycles"].preferences
    scene = bpy.context.scene
    info: dict = {"requested": device}
    if device == "GPU":
        for backend in GPU_BACKENDS:
            try:
                prefs.compute_device_type = backend
            except TypeError:  # backend not compiled into this Blender build
                continue
            prefs.refresh_devices()
            gpus = [d for d in prefs.devices if d.type == backend]
            if not gpus:
                continue
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            info.update(used="GPU", backend=backend, devices=[d.name for d in gpus])
            return info
        info["fallback"] = "No GPU backend found; rendering on CPU"
    prefs.compute_device_type = "NONE"
    scene.cycles.device = "CPU"
    info.update(used="CPU", backend="CPU", devices=[platform.processor() or platform.machine()])
    return info
