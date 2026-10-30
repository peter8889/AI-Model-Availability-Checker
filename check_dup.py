"""重复定义体检：找出同一作用域内被重复定义的方法/函数。

反复手工改代码时最容易出现的问题——同一个方法被插入两次，
后一份静默覆盖前一份，运行时不报错但行为可能不是预期的那份。
"""
import ast
import collections
import sys


def check(path="model_checker_gui.py"):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    problems = []

    # 类内方法
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        names = [(m.name, m.lineno) for m in cls.body
                 if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        counter = collections.Counter(n for n, _ in names)
        for name, cnt in counter.items():
            if cnt > 1:
                lines = [ln for n, ln in names if n == name]
                problems.append(f"  {cls.name}.{name}() 定义了 {cnt} 次，行 {lines}")

    # 模块级函数
    mod = [(n.name, n.lineno) for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    counter = collections.Counter(n for n, _ in mod)
    for name, cnt in counter.items():
        if cnt > 1:
            lines = [ln for n, ln in mod if n == name]
            problems.append(f"  {name}() 定义了 {cnt} 次，行 {lines}")

    if problems:
        print(f"[重复定义] {path}")
        for p in problems:
            print(p)
        return 1
    print(f"[重复定义] {path}: 无重复 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(check(*sys.argv[1:]))