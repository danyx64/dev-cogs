"""Read-only AST audit of ALL repository Python files; no cog code is executed."""
import ast
import json
from pathlib import Path


def audit(root):
    result = {"python_files": 0, "role_writers": [], "join_listeners": [], "syntax_errors": []}
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", "__pycache__", ".venv", "tests"} for part in path.parts):
            continue
        result["python_files"] += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError) as exc:
            result["syntax_errors"].append({"file": str(path.relative_to(root)), "error": str(exc)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "on_member_join":
                result["join_listeners"].append({"file": str(path.relative_to(root)), "line": node.lineno})
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method in {"add_roles", "remove_roles"} or (method == "edit" and any(k.arg == "roles" for k in node.keywords)):
                result["role_writers"].append({"file": str(path.relative_to(root)), "line": node.lineno, "method": method})
    return result


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1]), indent=2))
