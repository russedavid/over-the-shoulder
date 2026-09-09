"""Small native diagrams and portable SVG export from the same structured artifact."""

import html
import math


def diagram_layout(artifact, width):
    columns = max(1, min(3, int(width // 260)))
    node_width = min(220, (width - 40 - (columns - 1) * 30) / columns)
    height = 80 + math.ceil(len(artifact.nodes) / columns) * 145
    boxes = {}
    for index, node in enumerate(artifact.nodes):
        row, col = divmod(index, columns)
        # Alternate row direction so the main flow folds naturally at a narrow width.
        if row % 2:
            col = columns - col - 1
        boxes[node.id] = (20 + col * (node_width + 30), 35 + row * 145, node_width, 74)
    return boxes, height


def diagram_svg(artifact, width=1000):
    boxes, height = diagram_layout(artifact, width)
    escape = html.escape
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#87674a"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#f8f4ec"/>',
    ]
    for edge in artifact.edges:
        a, b = boxes[edge.source], boxes[edge.target]
        x1, y1, x2, y2 = a[0] + a[2] / 2, a[1] + a[3], b[0] + b[2] / 2, b[1]
        parts.append(
            f'<path d="M{x1},{y1} C{x1},{y1 + 35} {x2},{y2 - 35} {x2},{y2}" fill="none" stroke="#87674a" stroke-width="2" marker-end="url(#arrow)"/>'
        )
        parts.append(
            f'<text x="{(x1 + x2) / 2 + 6}" y="{(y1 + y2) / 2}" fill="#5d4735" font-family="system-ui" font-size="12">{escape(edge.label)}</text>'
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
