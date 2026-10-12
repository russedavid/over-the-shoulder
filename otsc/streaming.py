"""Extract only a named JSON string, including its safely decoded partial prefix."""

import json


def _field_start(text, key, start=0):
    decoder = json.JSONDecoder()
    index = start
    try:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != "{":
            return None
        index += 1
        while index < len(text):
            while index < len(text) and (text[index].isspace() or text[index] == ","):
                index += 1
            name, index = decoder.raw_decode(text, index)
            if not isinstance(name, str):
                return None
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text) or text[index] != ":":
                return None
            index += 1
            while index < len(text) and text[index].isspace():
                index += 1
            if name == key:
                return index
            _, index = decoder.raw_decode(text, index)
    except (ValueError, IndexError):
        return None
    return None


def _path_start(text, path):
    start = 0
    for key in path:
        if isinstance(key, int):
            while start < len(text) and text[start].isspace():
                start += 1
            if start >= len(text) or text[start] != "[" or key < 0:
                return None
            start += 1
            try:
                for _ in range(key):
                    while start < len(text) and text[start].isspace():
                        start += 1
                    _, start = json.JSONDecoder().raw_decode(text, start)
                    while start < len(text) and text[start].isspace():
                        start += 1
                    if text[start] != ",":
                        return None
                    start += 1
            except (ValueError, IndexError):
                return None
            while start < len(text) and text[start].isspace():
                start += 1
        else:
            start = _field_start(text, key, start)
        if start is None:
            return None
    return start


def completed_field(text, key):
    start = _path_start(text, (key,) if isinstance(key, str) else key)
    if start is not None:
        try:
            return json.JSONDecoder().raw_decode(text, start)[0]
        except ValueError:
            pass
    return None


def string_preview(text, path=("summary",)):
    start = _path_start(text, path)
    if start is None:
        return ""
    if start >= len(text) or text[start] != '"':
        return ""
    result, index = [], start + 1
    while index < len(text) and len(result) < 1000:
        char = text[index]
        if char == '"':
            break
        if char == "\\":
            length = 6 if index + 1 < len(text) and text[index + 1] == "u" else 2
            escaped = text[index:index + length]
            if len(escaped) < length:
                break
            try:
                result.append(json.loads('"' + escaped + '"'))
            except ValueError:
                break
            index += length
        elif ord(char) < 32:
            break
        else:
            result.append(char)
            index += 1
    if result and len(result[-1]) == 1 and 0xD800 <= ord(result[-1]) <= 0xDBFF:
        result.pop()  # An incomplete surrogate pair must wait for its next delta.
    return "".join(result).encode("utf-16", errors="surrogatepass").decode("utf-16", errors="replace")
