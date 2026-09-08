#!/usr/bin/env python3
"""
Detect coarse GUI boxes from actual long straight lines in a screenshot.

A border candidate is only accepted when the underlying line mask contains a
continuous horizontal or vertical run of at least --min-line-length pixels.
There is no OCR, background clustering, or inferred separator fallback.

Example:
  conda run -n tiny python gui_section_boxes.py \
    --image lib/screenshot_20260515_120144_155.png \
    --output-prefix lib/gui_sections_20260515_120144_155_dialed
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class Line:
    orientation: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def coord(self) -> int:
        return self.y1 if self.orientation == "h" else self.x1

    @property
    def span_start(self) -> int:
        return self.x1 if self.orientation == "h" else self.y1

    @property
    def span_end(self) -> int:
        return self.x2 if self.orientation == "h" else self.y2

    @property
    def length(self) -> int:
        return self.span_end - self.span_start + 1

    def as_dict(self, line_id: int) -> dict:
        return {
            "id": line_id,
            "orientation": "horizontal" if self.orientation == "h" else "vertical",
            "xyxy": [self.x1, self.y1, self.x2, self.y2],
            "length": self.length,
        }


@dataclass(frozen=True)
class Rect:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    def as_dict(self, box_id: int) -> dict:
        return {
            "id": box_id,
            "bbox_xyxy": [self.x1, self.y1, self.x2, self.y2],
            "bbox_xywh": [self.x1, self.y1, self.width, self.height],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect GUI boxes from long continuous border lines.")
    parser.add_argument("--image", required=True, help="Input screenshot.")
    parser.add_argument("--output-prefix", default="gui_sections", help="Output path prefix.")
    parser.add_argument("--min-line-length", type=int, default=250, help="Minimum continuous pixels for a border.")
    parser.add_argument("--max-gap", type=int, default=0, help="Allowed gap inside a line run; default is strict continuity.")
    parser.add_argument("--line-contrast", type=int, default=8, help="BGR channel delta for low-contrast separator pixels.")
    parser.add_argument("--canny-low", type=int, default=30)
    parser.add_argument("--canny-high", type=int, default=90)
    parser.add_argument("--coord-tolerance", type=int, default=3, help="Merge duplicate edges within this many pixels.")
    parser.add_argument("--corner-tolerance", type=int, default=6, help="Allowed side-line miss at a rectangle corner.")
    parser.add_argument("--side-support-ratio", type=float, default=0.5, help="Required side overlap when turning lines into boxes.")
    parser.add_argument("--min-final-box-size", type=int, default=250, help="Merge final boxes smaller than this in either dimension.")
    parser.add_argument("--min-box-width", type=int, default=50)
    parser.add_argument("--min-box-height", type=int, default=70)
    parser.add_argument("--draw-labels", action="store_true")
    return parser.parse_args()


def line_masks(image: np.ndarray, args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, args.canny_low, args.canny_high, apertureSize=3, L2gradient=True) > 0

    gx = np.abs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3))
    edge_vertical = edges & (gx >= gy)
    edge_horizontal = edges & (gy >= gx)

    bgr = image.astype(np.int16)
    dx = np.zeros(gray.shape, dtype=bool)
    dy = np.zeros(gray.shape, dtype=bool)
    dx[:, 1:] = np.max(np.abs(bgr[:, 1:] - bgr[:, :-1]), axis=2) >= args.line_contrast
    dy[1:, :] = np.max(np.abs(bgr[1:, :] - bgr[:-1, :]), axis=2) >= args.line_contrast

    return edge_horizontal | dy, edge_vertical | dx


def contiguous_ranges(indices: np.ndarray, max_gap: int) -> list[tuple[int, int, int]]:
    if len(indices) == 0:
        return []

    ranges: list[tuple[int, int, int]] = []
    start = int(indices[0])
    previous = int(indices[0])
    covered = 1
    for raw_value in indices[1:]:
        value = int(raw_value)
        if value - previous <= max_gap + 1:
            covered += 1
        else:
            ranges.append((start, previous, covered))
            start = value
            covered = 1
        previous = value
    ranges.append((start, previous, covered))
    return ranges


def scan_lines(mask: np.ndarray, orientation: str, min_length: int, max_gap: int) -> list[Line]:
    lines: list[Line] = []
    h, w = mask.shape

    if orientation == "h":
        for y in range(h):
            xs = np.flatnonzero(mask[y, :])
            for x1, x2, covered in contiguous_ranges(xs, max_gap):
                if x2 - x1 + 1 >= min_length and covered >= min_length:
                    lines.append(Line("h", x1, y, x2, y))
    else:
        for x in range(w):
            ys = np.flatnonzero(mask[:, x])
            for y1, y2, covered in contiguous_ranges(ys, max_gap):
                if y2 - y1 + 1 >= min_length and covered >= min_length:
                    lines.append(Line("v", x, y1, x, y2))

    return lines


def overlap_length(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, min(a2, b2) - max(a1, b1) + 1)


def remove_duplicate_lines(lines: list[Line], coord_tolerance: int) -> list[Line]:
    kept: list[Line] = []
    for line in sorted(lines, key=lambda item: item.length, reverse=True):
        duplicate = False
        for existing in kept:
            if line.orientation != existing.orientation:
                continue
            if abs(line.coord - existing.coord) > coord_tolerance:
                continue
            overlap = overlap_length(line.span_start, line.span_end, existing.span_start, existing.span_end)
            if overlap >= min(line.length, existing.length) * 0.8:
                duplicate = True
                break
        if not duplicate:
            kept.append(line)
    return sorted(kept, key=lambda item: (item.orientation, item.coord, item.span_start, item.span_end))


def unique_positions(lines: list[Line], orientation: str, limit: int, coord_tolerance: int) -> list[int]:
    positions: list[int] = []
    for line in sorted((line for line in lines if line.orientation == orientation), key=lambda item: item.length, reverse=True):
        position = line.coord
        if position < 0 or position >= limit:
            continue
        if all(abs(position - existing) > coord_tolerance for existing in positions):
            positions.append(position)
    return sorted(positions)


def supporting_lines_for_side(
    lines: list[Line],
    orientation: str,
    coord: int,
    span_start: int,
    span_end: int,
    args: argparse.Namespace,
) -> list[Line]:
    side_length = span_end - span_start + 1
    required = max(args.min_line_length, int(side_length * args.side_support_ratio))
    required = min(required, side_length)
    matches: list[Line] = []
    for line in lines:
        if line.orientation != orientation:
            continue
        if abs(line.coord - coord) > args.coord_tolerance:
            continue
        if line.span_start > span_start + args.corner_tolerance:
            continue
        if line.span_end < span_end - args.corner_tolerance:
            continue
        if overlap_length(span_start, span_end, line.span_start, line.span_end) >= required:
            matches.append(line)
    return matches


def boxes_from_lines(lines: list[Line], image_shape: tuple[int, int], args: argparse.Namespace) -> tuple[list[Rect], list[Line]]:
    h, w = image_shape
    xs = unique_positions(lines, "v", w, args.coord_tolerance)
    ys = unique_positions(lines, "h", h, args.coord_tolerance)

    boxes: list[Rect] = []
    used_lines: set[Line] = set()
    for y_index, y1 in enumerate(ys):
        for y2 in ys[y_index + 1 :]:
            if y2 - y1 < args.min_box_height:
                continue
            for x_index, x1 in enumerate(xs):
                for x2 in xs[x_index + 1 :]:
                    if x2 - x1 < args.min_box_width:
                        continue

                    left = supporting_lines_for_side(lines, "v", x1, y1, y2, args)
                    right = supporting_lines_for_side(lines, "v", x2, y1, y2, args)
                    top = supporting_lines_for_side(lines, "h", y1, x1, x2, args)
                    bottom = supporting_lines_for_side(lines, "h", y2, x1, x2, args)
                    if left and right and top and bottom:
                        boxes.append(Rect(x1, y1, x2, y2))
                        used_lines.update(left)
                        used_lines.update(right)
                        used_lines.update(top)
                        used_lines.update(bottom)
    return boxes, sorted(used_lines, key=lambda item: (item.orientation, item.coord, item.span_start, item.span_end))


def contains_rect(outer: Rect, inner: Rect, tolerance: int) -> bool:
    if outer == inner:
        return False
    return (
        outer.x1 <= inner.x1 + tolerance
        and outer.y1 <= inner.y1 + tolerance
        and outer.x2 >= inner.x2 - tolerance
        and outer.y2 >= inner.y2 - tolerance
    )


def remove_container_boxes(boxes: list[Rect], tolerance: int) -> list[Rect]:
    result: list[Rect] = []
    for box in sorted(boxes, key=lambda item: item.width * item.height):
        if any(contains_rect(box, kept, tolerance) for kept in result):
            continue
        result.append(box)
    return result


def union_rect(a: Rect, b: Rect) -> Rect:
    return Rect(min(a.x1, b.x1), min(a.y1, b.y1), max(a.x2, b.x2), max(a.y2, b.y2))


def is_small_box(box: Rect, min_size: int) -> bool:
    return box.width < min_size or box.height < min_size


def adjacency_score(a: Rect, b: Rect, tolerance: int) -> int:
    vertical_touch = abs(a.x2 - b.x1) <= tolerance or abs(b.x2 - a.x1) <= tolerance
    if vertical_touch:
        return overlap_length(a.y1, a.y2, b.y1, b.y2)

    horizontal_touch = abs(a.y2 - b.y1) <= tolerance or abs(b.y2 - a.y1) <= tolerance
    if horizontal_touch:
        return overlap_length(a.x1, a.x2, b.x1, b.x2)

    return 0


def intersection_area(a: Rect, b: Rect) -> int:
    width = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
    height = max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    return width * height


def gap_between(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, max(a1, b1) - min(a2, b2))


def preferred_small_merge_score(small: Rect, candidate: Rect, min_size: int) -> int:
    scores: list[int] = []
    if small.height < min_size:
        overlap = overlap_length(small.x1, small.x2, candidate.x1, candidate.x2)
        gap = gap_between(small.y1, small.y2, candidate.y1, candidate.y2)
        if overlap >= small.width * 0.5 and gap <= min_size:
            scores.append(overlap * 1000 - gap)
    if small.width < min_size:
        overlap = overlap_length(small.y1, small.y2, candidate.y1, candidate.y2)
        gap = gap_between(small.x1, small.x2, candidate.x1, candidate.x2)
        if overlap >= small.height * 0.5 and gap <= min_size:
            scores.append(overlap * 1000 - gap)
    return max(scores, default=0)


def merge_small_boxes(boxes: list[Rect], min_size: int, tolerance: int) -> list[Rect]:
    merged = boxes[:]
    while True:
        small_indices = [idx for idx, box in enumerate(merged) if is_small_box(box, min_size)]
        if not small_indices or len(merged) < 2:
            return merged

        small_index = min(small_indices, key=lambda idx: merged[idx].width * merged[idx].height)
        small = merged[small_index]
        container_index = -1
        for idx, candidate in enumerate(merged):
            if idx != small_index and contains_rect(candidate, small, tolerance):
                container_index = idx
                break
        if container_index >= 0:
            merged = [box for idx, box in enumerate(merged) if idx != small_index]
            continue

        neighbor_index = -1
        neighbor_score = 0
        for idx, candidate in enumerate(merged):
            if idx == small_index:
                continue
            score = preferred_small_merge_score(small, candidate, min_size)
            if score > neighbor_score:
                neighbor_score = score
                neighbor_index = idx

        if neighbor_index < 0:
            for idx, candidate in enumerate(merged):
                if idx == small_index:
                    continue
                score = adjacency_score(small, candidate, tolerance)
                if score > neighbor_score:
                    neighbor_score = score
                    neighbor_index = idx

        if neighbor_index < 0:
            for idx, candidate in enumerate(merged):
                if idx == small_index:
                    continue
                score = intersection_area(small, candidate)
                if score > neighbor_score:
                    neighbor_score = score
                    neighbor_index = idx
        if neighbor_index < 0:
            return merged

        neighbor = merged[neighbor_index]
        combined = union_rect(small, neighbor)
        next_boxes = [box for idx, box in enumerate(merged) if idx not in {small_index, neighbor_index}]
        next_boxes.append(combined)
        merged = next_boxes


def lines_for_boxes(lines: list[Line], boxes: list[Rect], args: argparse.Namespace) -> list[Line]:
    used_lines: set[Line] = set()
    for box in boxes:
        used_lines.update(supporting_lines_for_side(lines, "v", box.x1, box.y1, box.y2, args))
        used_lines.update(supporting_lines_for_side(lines, "v", box.x2, box.y1, box.y2, args))
        used_lines.update(supporting_lines_for_side(lines, "h", box.y1, box.x1, box.x2, args))
        used_lines.update(supporting_lines_for_side(lines, "h", box.y2, box.x1, box.x2, args))
    return sorted(used_lines, key=lambda item: (item.orientation, item.coord, item.span_start, item.span_end))


def sort_boxes(boxes: list[Rect]) -> list[Rect]:
    return sorted(boxes, key=lambda box: (box.y1 // 24, box.x1, box.y1, box.x2, box.y2))


def default_detection_args(**overrides) -> argparse.Namespace:
    values = {
        "min_line_length": 250,
        "max_gap": 0,
        "line_contrast": 8,
        "canny_low": 30,
        "canny_high": 90,
        "coord_tolerance": 3,
        "corner_tolerance": 6,
        "side_support_ratio": 0.5,
        "min_final_box_size": 250,
        "min_box_width": 50,
        "min_box_height": 70,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def detect_gui_boxes_from_image(image: np.ndarray, args: argparse.Namespace | None = None) -> list[Rect]:
    args = args or default_detection_args()
    h, w = image.shape[:2]
    horizontal_mask, vertical_mask = line_masks(image, args)
    lines = scan_lines(horizontal_mask, "h", args.min_line_length, args.max_gap)
    lines.extend(scan_lines(vertical_mask, "v", args.min_line_length, args.max_gap))
    lines = remove_duplicate_lines(lines, args.coord_tolerance)
    boxes, _ = boxes_from_lines(lines, (h, w), args)
    boxes = remove_container_boxes(boxes, args.coord_tolerance)
    return sort_boxes(merge_small_boxes(boxes, args.min_final_box_size, args.coord_tolerance))


def detect_gui_boxes(image_path: str | Path, args: argparse.Namespace | None = None) -> list[Rect]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    return detect_gui_boxes_from_image(image, args=args)


def draw_output(image: np.ndarray, lines: list[Line], boxes: list[Rect], draw_labels: bool) -> np.ndarray:
    output = image.copy()
    box_color = (0, 180, 255)
    line_color = (255, 0, 180)
    thickness = max(2, min(image.shape[:2]) // 700)
    font_scale = max(0.55, min(image.shape[:2]) / 1800)

    for line in lines:
        cv2.line(output, (line.x1, line.y1), (line.x2, line.y2), line_color, thickness)

    for index, box in enumerate(boxes, start=1):
        cv2.rectangle(output, (box.x1, box.y1), (box.x2, box.y2), box_color, thickness)
        if not draw_labels:
            continue
        label = str(index)
        text_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        text_w, text_h = text_size
        label_y2 = min(box.y2, box.y1 + text_h + baseline + 8)
        cv2.rectangle(output, (box.x1, box.y1), (box.x1 + text_w + 10, label_y2), box_color, -1)
        cv2.putText(output, label, (box.x1 + 5, label_y2 - baseline - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

    return output


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    output_prefix = Path(args.output_prefix)
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not read image: {image_path}")

    h, w = image.shape[:2]
    horizontal_mask, vertical_mask = line_masks(image, args)
    lines = scan_lines(horizontal_mask, "h", args.min_line_length, args.max_gap)
    lines.extend(scan_lines(vertical_mask, "v", args.min_line_length, args.max_gap))
    lines = remove_duplicate_lines(lines, args.coord_tolerance)
    boxes, _ = boxes_from_lines(lines, (h, w), args)
    boxes = remove_container_boxes(boxes, args.coord_tolerance)
    boxes = sort_boxes(merge_small_boxes(boxes, args.min_final_box_size, args.coord_tolerance))
    box_lines = lines_for_boxes(lines, boxes, args)

    png_path = output_prefix.with_suffix(".png")
    json_path = output_prefix.with_suffix(".json")
    lines_path = output_prefix.with_name(f"{output_prefix.name}_lines").with_suffix(".json")
    png_path.parent.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(png_path), draw_output(image, box_lines, boxes, args.draw_labels))
    json_path.write_text(json.dumps([box.as_dict(i) for i, box in enumerate(boxes, 1)], indent=2) + "\n", encoding="utf-8")
    lines_path.write_text(json.dumps([line.as_dict(i) for i, line in enumerate(box_lines, 1)], indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(boxes)} boxes")
    print(f"Wrote {len(box_lines)} boxed lines with continuous length >= {args.min_line_length}")
    print(f"Annotated image: {png_path}")
    print(f"JSON boxes: {json_path}")
    print(f"JSON lines: {lines_path}")


if __name__ == "__main__":
    main()
