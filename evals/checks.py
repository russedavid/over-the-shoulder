"""Task checks and a deliberately restricted interpreter; generated code is never exec'd."""

import ast
import json
import math
import operator
import os
import subprocess
import tempfile
from pathlib import Path

from otsc.models import safe_relative_path


class UnsupportedCode(ValueError):
    pass


class Returned(Exception):
    def __init__(self, value):
        self.value = value


class Raised(Exception):
    def __init__(self, name):
        self.name = name


BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
FUNCTIONS = {
    "sum": sum,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "bool": bool,
    "isinstance": isinstance,
}
TYPES = {"int": int, "float": float, "bool": bool, "str": str}


def expression(node, scope, depth=0):
    if depth > 30:
        raise UnsupportedCode("Expression too deep")

    def recurse(n):
        return expression(n, scope, depth + 1)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and (not math.isfinite(node.value) or abs(node.value) > 10**12):
            raise UnsupportedCode("Numeric constant exceeds evaluation bounds")
        return node.value
    if isinstance(node, ast.Name) and node.id in scope:
        return scope[node.id]
    if isinstance(node, ast.Name) and node.id in TYPES:
        return TYPES[node.id]
    if isinstance(node, ast.List):
        return [recurse(n) for n in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(recurse(n) for n in node.elts)
    if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
        left, right = recurse(node.left), recurse(node.right)
        if isinstance(node.op, ast.Pow) and (not isinstance(right, (int, float)) or abs(right) > 32):
            raise UnsupportedCode("Exponent exceeds the evaluation limit")
        if (
            not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
            or max(abs(left), abs(right)) > 10**12
        ):
            raise UnsupportedCode("Only bounded numeric arithmetic is supported")
        result = BINOPS[type(node.op)](left, right)
        if not math.isfinite(result) or abs(result) > 10**12:
            raise UnsupportedCode("Arithmetic exceeds evaluation bounds")
        return result
    if isinstance(node, ast.UnaryOp):
        value = recurse(node.operand)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
    if isinstance(node, ast.BoolOp):
        value = recurse(node.values[0])
        for part in node.values[1:]:
            if isinstance(node.op, ast.And) and not value or isinstance(node.op, ast.Or) and value:
                return value
            value = recurse(part)
        return value
    if isinstance(node, ast.Compare):
        left = recurse(node.left)
        for op, part in zip(node.ops, node.comparators):
            right = recurse(part)
            if type(op) not in COMPARISONS:
                raise UnsupportedCode("Unsupported comparison")
            if not COMPARISONS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return recurse(node.body if recurse(node.test) else node.orelse)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCTIONS
        and not node.keywords
    ):
        if node.func.id in scope:
            raise UnsupportedCode("Shadowed builtin requires review")
        args = [recurse(arg) for arg in node.args]
        return FUNCTIONS[node.func.id](*args)
    raise UnsupportedCode("Unsupported expression: " + type(node).__name__)


def statements(body, scope):
    if len(body) > 100:
        raise UnsupportedCode("Function too large for the restricted checker")
    for node in body:
        if isinstance(node, ast.Return):
            raise Returned(expression(node.value, scope) if node.value else None)
        if isinstance(node, ast.If):
            statements(node.body if expression(node.test, scope) else node.orelse, scope)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            scope[node.targets[0].id] = expression(node.value, scope)
        elif isinstance(node, ast.Raise):
            error = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(error, ast.Name) and error.id in {"ValueError", "TypeError"}:
                if isinstance(node.exc, ast.Call):
                    if node.exc.keywords:
                        raise UnsupportedCode("Exception keywords require review")
                    for argument in node.exc.args:
                        expression(argument, scope)
                raise Raised(error.id)
            raise UnsupportedCode("Unsupported exception")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            pass
        elif isinstance(node, ast.Pass):
            pass
        elif not isinstance(node, (ast.Return, ast.If)):
            raise UnsupportedCode("Unsupported statement: " + type(node).__name__)


def run_function(code, name, args):
    if len(code) > 50000:
        raise UnsupportedCode("Code exceeds the checker's limit")
    tree = ast.parse(code)
    if any(
        not isinstance(n, ast.FunctionDef)
        and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))
        for n in tree.body
    ):
        raise UnsupportedCode("Module-level behavior needs review")
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise UnsupportedCode("Expected function was not found exactly once")
    function = matches[0]
    if function.decorator_list or function.args.vararg or function.args.kwarg:
        raise UnsupportedCode("Decorators and variadic functions are outside this checker")
    parameters = [a.arg for a in function.args.posonlyargs + function.args.args]
    if len(parameters) != len(args):
        raise UnsupportedCode("Unexpected function signature")
    try:
        statements(function.body, dict(zip(parameters, args)))
    except Returned as result:
        return result.value
    return None


def patch_result(artifact, files):
    import re

    path = artifact.path
    if path not in files or not safe_relative_path(path):
        raise UnsupportedCode("Patch has no complete fixture base")
    if re.findall(r"(?m)^(?:---|\+\+\+) (.+)$", artifact.content) != ["a/" + path, "b/" + path]:
        raise UnsupportedCode("Patch does not target exactly its declared fixture")
    with tempfile.TemporaryDirectory(prefix="otsc-eval-patch-") as directory:
        target = Path(directory) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[path])
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "apply", "-"],
            input=artifact.content,
            text=True,
            cwd=directory,
            env=env,
            capture_output=True,
            timeout=5,
        )
        if result.returncode:
            raise ValueError("Patch does not apply to the recorded fixture")
        return target.read_text()


def task_checks(item, response, snapshot):
    checks = []

    def add(name, passed, critique):
        checks.append({"name": name, "passed": passed, "critique": critique, "method": "deterministic"})

    try:
        response.validate_sources(snapshot.observations, json.loads(snapshot.verified_files))
        add("contract_and_provenance", True, "Structured output and source references are valid.")
    except ValueError as error:
        add("contract_and_provenance", False, str(error))
    kinds = item["artifact_kinds"]
    if kinds:
        requested = any(a.kind in kinds for a in response.artifacts)
        if not requested and "diagram" in kinds and any(a.kind == "image" for a in response.artifacts):
            requested = None
        add(
            "requested_artifact",
            requested,
            "The generated drawing requires a separate image-generation and visual-review check."
            if requested is None else "The task requires one of: " + ", ".join(kinds),
        )
    if item["no_observed_files"]:
        add(
            "no_invented_file_observation",
            not response.observed_files,
            "No actual file content/path was established by the observations.",
        )
    if item["no_code_changes"]:
        changed = any(
            a.kind == "patch" or a.kind == "code" and a.content != item["files"].get(a.path) for a in response.artifacts
        )
        add("honors_no_rewrite", not changed, "The user requested an explanation without a code rewrite.")
    specification = item.get("code_checks")
    if specification:
        candidates = []
        for artifact in response.artifacts:
            if artifact.kind == "code":
                candidates.append(artifact.content)
            elif artifact.kind == "patch" and artifact.basis == "verified_file":
                try:
                    candidates.append(patch_result(artifact, item["files"]))
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    add("patch_applies", False, str(error))
        code = None
        for candidate in candidates:
            try:
                if any(
                    isinstance(n, ast.FunctionDef) and n.name == specification["function"]
                    for n in ast.parse(candidate).body
                ):
                    code = candidate
                    break
            except SyntaxError:
                add("python_syntax", False, "The proposed Python function cannot be parsed.")
        if code is None:
            add(
                "function_behavior",
                None,
                "Function was not available to the restricted behavioral checker; review is required.",
            )
        else:
            for index, example in enumerate(specification["cases"]):
                try:
                    value = run_function(code, specification["function"], example["args"])
                    expected = example.get("expected")
                    passed = "raises" not in example and (
                        math.isclose(value, expected, rel_tol=1e-8, abs_tol=1e-8)
                        if isinstance(value, (int, float)) and isinstance(expected, (int, float))
                        else value == expected
                    )
                    add(
                        "behavior_" + str(index),
                        passed,
                        f"Inputs {example['args']!r}; expected {example.get('raises', expected)!r}; received {value!r}.",
                    )
                except Raised as raised:
                    add("behavior_" + str(index), raised.name == example.get("raises"), "Raised " + raised.name)
                except UnsupportedCode as error:
                    add("behavior_" + str(index), None, str(error) + "; no generated code was executed.")
                except (ValueError, TypeError, ZeroDivisionError, OverflowError, SyntaxError) as error:
                    add("behavior_" + str(index), False, type(error).__name__ + ": " + str(error))
    return checks
