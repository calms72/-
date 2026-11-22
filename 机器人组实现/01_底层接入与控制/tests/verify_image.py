"""Check an image with the PC vision model without opening a camera, SSH, or CAN."""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

from elf_dexhand.camera_app import _make_landmarker
from elf_dexhand.vision_mapping import MappingProfile, estimate_pose


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--image", type=Path, help="Local photo; omitted uses a blank RGB frame")
    parser.add_argument("--expect", type=int, choices=(0, 1, 2), help="Expected detected hands")
    args = parser.parse_args()
    if args.image:
        frame = cv2.imdecode(np.frombuffer(args.image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            parser.error("could not decode the input photo")
        height, width = frame.shape[:2]
        scale = min(1, 800 / max(height, width))
        if scale < 1:
            frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    else:
        rgb = np.zeros((256, 256, 3), dtype=np.uint8)
    with _make_landmarker(args.model) as detector:
        result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), 100)
    hands = result.hand_world_landmarks
    print(f"detected hands: {len(hands)}")
    for landmarks in hands:
        pose = estimate_pose(landmarks, MappingProfile.default())
        print(f"landmarks: {len(landmarks)}; joint targets: {[pose[j] for j in pose]}")
    if args.expect is not None and len(hands) != args.expect:
        print(f"expected {args.expect} hands")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
