"""Small native diagrams and portable SVG export from the same structured artifact."""

import html
import math


def diagram_layout(artifact, width):
    columns = max(1, min(3, int(width // 260)))
    cell_width = (width - 40) / columns
    node_width = min(220, cell_width - 50)
    node_height = max(74, max(math.ceil(len(n.label) / max(10, node_width / 8)) * 18 + 28 for n in artifact.nodes))
    row_height = node_height + 110
    height = 65 + math.ceil(len(artifact.nodes) / columns) * row_height
    boxes = {}
    for index, node in enumerate(artifact.nodes):
        row, col = divmod(index, columns)
        # Alternate row direction so the main flow folds naturally at a narrow width.
        if row % 2:
            col = columns - col - 1
        boxes[node.id] = (
            20 + col * cell_width + (cell_width - node_width) / 2,
            35 + row * row_height,
            node_width,
            node_height,
        )
    return boxes, height


def edge_geometry(edge, boxes):
    a, b = boxes[edge.source], boxes[edge.target]
    ax, ay, bx, by = a[0] + a[2] / 2, a[1] + a[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    if abs(ay - by) < 1 and abs(ax - bx) < a[2] * 2:
        direction = 1 if bx > ax else -1
        start, end = (ax + direction * a[2] / 2, ay), (bx - direction * b[2] / 2, by)
        c1, c2 = (start[0] + direction * 20, ay), (end[0] - direction * 20, by)
        label = ((start[0] + end[0]) / 2, ay - 22)
    elif abs(ay - by) < 1:
        start, end = (ax, a[1] + a[3]), (bx, b[1] + b[3])
        c1, c2 = (ax, start[1] + 82), (bx, end[1] + 82)
        label = ((ax + bx) / 2, start[1] + 65)
    elif abs(ax - bx) < 1:
        down = by > ay
        offset = -28 if down else 28
        start = (ax + offset, a[1] + a[3] if down else a[1])
        end = (bx + offset, b[1] if down else b[1] + b[3])
        c1, c2 = (start[0], (start[1] + end[1]) / 2), (end[0], (start[1] + end[1]) / 2)
        label = (ax + (-76 if down else 76), (start[1] + end[1]) / 2 - 8)
    else:
        down = by > ay
        start, end = (ax, a[1] + a[3] if down else a[1]), (bx, b[1] if down else b[1] + b[3])
        c1, c2 = (ax, (start[1] + end[1]) / 2), (bx, (start[1] + end[1]) / 2)
        label = ((ax + bx) / 2, (start[1] + end[1]) / 2 - 14)
    return start, c1, c2, end, label


def diagram_svg(artifact, width=1000):
    boxes, height = diagram_layout(artifact, width)
    escape = html.escape
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#87674a"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#f8f4ec"/>',
    ]
    for edge in artifact.edges:
        start, c1, c2, end, label = edge_geometry(edge, boxes)
        parts.append(
            f'<path d="M{start[0]},{start[1]} C{c1[0]},{c1[1]} {c2[0]},{c2[1]} {end[0]},{end[1]}" fill="none" stroke="#87674a" stroke-width="2" marker-end="url(#arrow)"/>'
        )
        parts.append(
            f'<text x="{label[0]}" y="{label[1]}" text-anchor="middle" fill="#5d4735" font-family="system-ui" font-size="11">{escape(edge.label)}</text>'
        )
    for node in artifact.nodes:
        x, y, w, h = boxes[node.id]
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#fffdfa" stroke="#c6b59f"/>')
        words, lines, line = node.label.split(), [], ""
        for word in words:
            if len(line + " " + word) > max(12, int(w / 8)):
                lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        lines.append(line)
        for index, line in enumerate(lines):
            parts.append(
                f'<text x="{x + w / 2}" y="{y + 30 + index * 18}" text-anchor="middle" fill="#30291f" font-family="system-ui" font-size="14">{escape(line)}</text>'
            )
    return "\n".join([*parts, "</svg>"])
