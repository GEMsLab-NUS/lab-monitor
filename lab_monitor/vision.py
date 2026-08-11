from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

import numpy as np

from .tracking import BBox


def load_cv2() -> Any:
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is required. Install dependencies with: uv sync"
        ) from exc
    return cv2


def clamp_box(box: BBox, width: int, height: int) -> BBox:
    x, y, w, h = box
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, w, h


def crop(frame: np.ndarray, box: BBox) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, w, h = clamp_box(box, width, height)
    return frame[y : y + h, x : x + w]


class PersonDetector:
    def __init__(self) -> None:
        cv2 = load_cv2()
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray) -> list[BBox]:
        boxes, weights = self._hog.detectMultiScale(
            frame,
            winStride=(8, 8),
            padding=(16, 16),
            scale=1.05,
        )
        weighted_boxes = [
            (tuple(map(int, box)), float(weight))
            for box, weight in zip(boxes, weights)
            if float(weight) > 0.35
        ]
        return self._non_max_suppression(weighted_boxes)

    @staticmethod
    def _non_max_suppression(boxes: list[tuple[BBox, float]]) -> list[BBox]:
        if not boxes:
            return []
        boxes = sorted(boxes, key=lambda item: item[1], reverse=True)
        kept: list[BBox] = []
        for box, _ in boxes:
            if all(_iou(box, existing) < 0.35 for existing in kept):
                kept.append(box)
        return kept


class FaceService:
    def __init__(
        self,
        faces_dir: Path,
        enrolled_dir: Path,
        threshold: float,
        *,
        min_face_size_px: int = 72,
        min_face_area_ratio: float = 0.012,
        min_sharpness: float = 18.0,
        min_brightness: float = 35.0,
        max_brightness: float = 220.0,
    ) -> None:
        cv2 = load_cv2()
        self.cv2 = cv2
        self.faces_dir = faces_dir
        self.enrolled_dir = enrolled_dir
        self.threshold = threshold
        self.min_face_size_px = min_face_size_px
        self.min_face_area_ratio = min_face_area_ratio
        self.min_sharpness = min_sharpness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.faces_dir.mkdir(parents=True, exist_ok=True)
        self.enrolled_dir.mkdir(parents=True, exist_ok=True)
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self._detector = cv2.CascadeClassifier(str(cascade_path))
        if self._detector.empty():
            raise RuntimeError(f"Failed to load Haar cascade: {cascade_path}")
        self.labels_path = self.faces_dir / "labels.json"
        self.model_path = self.faces_dir / "lbph_model.yml"
        self._recognizer = self._create_recognizer()
        self._labels = self._load_labels()
        if self.model_path.exists() and self._recognizer is not None:
            self._recognizer.read(str(self.model_path))

    @property
    def can_recognize(self) -> bool:
        return self._recognizer is not None and self.model_path.exists() and bool(self._labels)

    def detect_faces(self, frame: np.ndarray) -> list[BBox]:
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        faces = self._detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=7,
            minSize=(self.min_face_size_px, self.min_face_size_px),
        )
        return [
            tuple(map(int, face))
            for face in faces
            if is_face_box_usable(
                tuple(map(int, face)),
                frame.shape,
                min_size_px=self.min_face_size_px,
                min_area_ratio=self.min_face_area_ratio,
            )
        ]

    def recognize_face(
        self,
        frame: np.ndarray,
        face_box: BBox,
        threshold: float | None = None,
    ) -> tuple[str | None, float | None]:
        if not self.can_recognize or self._recognizer is None:
            return None, None
        if not self.is_enrollable_face(frame, face_box):
            return None, None
        gray_face = self._prepare_face(frame, face_box)
        label_id, distance = self._recognizer.predict(gray_face)
        name = self._labels.get(str(label_id))
        max_distance = self.threshold if threshold is None else threshold
        if name and float(distance) <= max_distance:
            return name, float(distance)
        return None, float(distance)

    def enroll_from_images(self, name: str, image_paths: list[Path]) -> int:
        label_id = self._label_id_for_name(name)
        saved = 0
        target_dir = self.enrolled_dir / str(label_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        for image_path in image_paths:
            frame = self.cv2.imread(str(image_path))
            if frame is None:
                continue
            face = self._largest_face(frame)
            if face is None:
                continue
            prepared = self._prepare_face(frame, face)
            output = target_dir / f"{image_path.stem}_{saved + 1}.png"
            self.cv2.imwrite(str(output), prepared)
            saved += 1
        if saved:
            self._labels[str(label_id)] = name
            self._save_labels()
            self.retrain()
        return saved

    def enroll_face_crop(self, name: str, frame: np.ndarray, face_box: BBox, sample_name: str) -> bool:
        if not self.is_enrollable_face(frame, face_box):
            return False
        label_id = self._label_id_for_name(name)
        target_dir = self.enrolled_dir / str(label_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        prepared = self._prepare_face(frame, face_box)
        output = target_dir / f"{sample_name}.png"
        ok = self.cv2.imwrite(str(output), prepared)
        if not ok:
            return False
        self._labels[str(label_id)] = name
        self._save_labels()
        self.retrain()
        return True

    def rename_label(self, old_name: str, new_name: str) -> bool:
        renamed = False
        for raw_id, existing in list(self._labels.items()):
            if existing == old_name:
                self._labels[raw_id] = new_name
                renamed = True
        if renamed:
            self._save_labels()
        return renamed

    def delete_label(self, name: str) -> int:
        deleted = 0
        for raw_id, existing in list(self._labels.items()):
            if existing != name:
                continue
            del self._labels[raw_id]
            target_dir = self.enrolled_dir / raw_id
            if target_dir.exists():
                shutil.rmtree(target_dir)
            deleted += 1
        if deleted:
            self._save_labels()
            self.retrain()
        return deleted

    def label_names(self) -> set[str]:
        return set(self._labels.values())

    def enroll_from_camera(self, name: str, camera_index: int, samples: int = 20) -> int:
        cap = self.cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {camera_index}")
        label_id = self._label_id_for_name(name)
        target_dir = self.enrolled_dir / str(label_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        try:
            while saved < samples:
                ok, frame = cap.read()
                if not ok:
                    continue
                face = self._largest_face(frame)
                if face is None:
                    continue
                prepared = self._prepare_face(frame, face)
                self.cv2.imwrite(str(target_dir / f"camera_{saved + 1:03d}.png"), prepared)
                saved += 1
        finally:
            cap.release()
        if saved:
            self._labels[str(label_id)] = name
            self._save_labels()
            self.retrain()
        return saved

    def retrain(self) -> int:
        if self._recognizer is None:
            raise RuntimeError("OpenCV face recognizer is unavailable. Use opencv-contrib-python.")
        faces: list[np.ndarray] = []
        labels: list[int] = []
        for label_dir in self.enrolled_dir.iterdir():
            if not label_dir.is_dir() or not label_dir.name.isdigit():
                continue
            label_id = int(label_dir.name)
            for image_path in label_dir.glob("*.png"):
                face = self.cv2.imread(str(image_path), self.cv2.IMREAD_GRAYSCALE)
                if face is None:
                    continue
                faces.append(face)
                labels.append(label_id)
        if not faces:
            if self.model_path.exists():
                self.model_path.unlink()
            return 0
        self._recognizer.train(faces, np.array(labels, dtype=np.int32))
        self._recognizer.write(str(self.model_path))
        return len(faces)

    def _prepare_face(self, frame: np.ndarray, face_box: BBox) -> np.ndarray:
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        face = crop(gray, face_box)
        return self.cv2.resize(face, (160, 160))

    def is_enrollable_face(self, frame: np.ndarray, face_box: BBox) -> bool:
        if not is_face_box_usable(
            face_box,
            frame.shape,
            min_size_px=max(self.min_face_size_px, 64),
            min_area_ratio=max(self.min_face_area_ratio, 0.010),
        ):
            return False
        gray = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        face = crop(gray, face_box)
        if face.size == 0:
            return False
        mean_brightness = float(np.mean(face))
        if mean_brightness < self.min_brightness or mean_brightness > self.max_brightness:
            return False
        sharpness = float(self.cv2.Laplacian(face, self.cv2.CV_64F).var())
        if sharpness < self.min_sharpness:
            return False
        return True

    def _largest_face(self, frame: np.ndarray) -> BBox | None:
        faces = self.detect_faces(frame)
        if not faces:
            return None
        return max(faces, key=lambda box: box[2] * box[3])

    def _create_recognizer(self) -> Any | None:
        face_module = getattr(self.cv2, "face", None)
        if face_module is None:
            return None
        return face_module.LBPHFaceRecognizer_create()

    def _load_labels(self) -> dict[str, str]:
        if not self.labels_path.exists():
            return {}
        return json.loads(self.labels_path.read_text(encoding="utf-8"))

    def _save_labels(self) -> None:
        self.labels_path.write_text(
            json.dumps(self._labels, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    def _label_id_for_name(self, name: str) -> int:
        for raw_id, existing in self._labels.items():
            if existing == name:
                return int(raw_id)
        existing_ids = [int(raw_id) for raw_id in self._labels]
        return (max(existing_ids) + 1) if existing_ids else 1


def face_inside_person(face: BBox, person: BBox) -> bool:
    fx, fy, fw, fh = face
    px, py, pw, ph = person
    cx = fx + fw / 2
    cy = fy + fh / 2
    return px <= cx <= px + pw and py <= cy <= py + ph


def box_area_ratio(box: BBox, frame_shape: tuple[int, ...]) -> float:
    height, width = frame_shape[:2]
    _x, _y, w, h = clamp_box(box, width, height)
    frame_area = width * height
    return (w * h / frame_area) if frame_area else 0.0


def is_face_box_usable(
    box: BBox,
    frame_shape: tuple[int, ...],
    *,
    min_size_px: int = 64,
    min_area_ratio: float = 0.010,
) -> bool:
    height, width = frame_shape[:2]
    x, y, w, h = clamp_box(box, width, height)
    if w < min_size_px or h < min_size_px:
        return False
    aspect = w / h if h else 0.0
    if aspect < 0.78 or aspect > 1.28:
        return False
    margin_x = max(3, int(width * 0.01))
    margin_y = max(3, int(height * 0.01))
    if x <= margin_x or y <= margin_y or x + w >= width - margin_x or y + h >= height - margin_y:
        return False
    if box_area_ratio((x, y, w, h), frame_shape) < min_area_ratio:
        return False
    return True


def is_person_box_usable(
    box: BBox,
    frame_shape: tuple[int, ...],
    *,
    min_area_ratio: float = 0.015,
    max_area_ratio: float = 0.70,
) -> bool:
    ratio = box_area_ratio(box, frame_shape)
    return min_area_ratio <= ratio <= max_area_ratio


def person_box_from_face(face: BBox, frame_width: int, frame_height: int) -> BBox:
    x, y, w, h = face
    person_w = int(w * 2.2)
    person_h = int(h * 4.0)
    person_x = int(x + w / 2 - person_w / 2)
    person_y = int(y - h * 0.35)
    return clamp_box((person_x, person_y, person_w, person_h), frame_width, frame_height)


def _iou(a: BBox, b: BBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0
