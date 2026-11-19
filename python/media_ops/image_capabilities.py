"""Image-only adapter identifiers that do not require the inference runtime."""

IMAGE_CAPABILITIES: tuple[str, ...] = (
    "denoise",
    "object_detection",
    "depth",
    "matting",
    "frame_interpolation",
    "face_restoration",
)
