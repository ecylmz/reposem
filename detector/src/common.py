"""Shared helpers: configuration, git calls, and the diff features of one commit.

The features use only the commit and its first parent. They never read the ignore file
or the later history of the repository.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DETECTOR = ROOT / "detector"
CONFIG = json.loads((DETECTOR / "configs" / "detector.json").read_text())
GIT = CONFIG["git"]

# Keyword groups of the RQ1 category rule (scripts/analyze_full.py of v1.0.0).
KEYWORDS = {
    "lint": r"\blint|flake8|pylint|clippy|pyupgrade|codespell|typo|spelling|shellcheck|cppcheck|clang-tidy|"
            r"\b[EFW]\d{3}\b|\bpep ?8\b",
    "formatter": r"\bblack\b|prettier|clang-?format|scalafmt|\bruff\b|gofmt|rustfmt|\byapf\b|autopep8|\bisort\b|"
                 r"spotless|ktlint|eslint|stylua|swiftformat|google-java-format|dprint|\bbiome\b|fourmolu|ormolu|"
                 r"ocamlformat|phpcbf|php-cs|cargo fmt|astyle|uncrustify|rubocop|standardrb|shfmt|prettif|pgindent|"
                 r"perltidy|beautif|coding standard|\b(re)?format(s|ted|ting|ter|ters)?\b|\bfmt\b",
    "whitespace": r"whitespace|indent|\btabs?\b|\beol\b|line ending|crlf|newline",
    "migration": r"migrat|convert|port to|python 3|py3|es6|upgrade|moderniz|f-string|u-strings|namespaces|pep 604",
    "style": r"\bstyle\b|code style|\bcs\b|reflow|tidy|clean-?up|braces|quote|usings|consisten|harmoniz|"
             r"sort(ed)? imports|update imports|trailing comma",
    "move": r"\bmove|\bmoved|rename|relocat|restructur|reorganiz|src layout|\bsplit",
    "license": r"license|licence|copyright|header|spdx",
    "refactoring": r"refactor|replace|extract|separate|simplif|typing|type annotation|annotations|\btypes?\b|rip out",
}
# The keyword baseline flags the groups that describe layout changes.
MECHANICAL_GROUPS = ("lint", "formatter", "whitespace", "style")

CONFIG_FILES = re.compile(
    r"(^|/)(\.prettierrc[^/]*|prettier\.config\.[^/]+|\.clang-format|\.editorconfig|pyproject\.toml|setup\.cfg|"
    r"\.flake8|ruff\.toml|\.ruff\.toml|\.eslintrc[^/]*|eslint\.config\.[^/]+|\.rubocop\.yml|rustfmt\.toml|"
    r"\.rustfmt\.toml|\.scalafmt\.conf|\.php-cs-fixer[^/]*|biome\.jsonc?|\.stylelintrc[^/]*|\.golangci\.ya?ml|"
    r"tox\.ini|\.pre-commit-config\.yaml|\.isort\.cfg|\.style\.yapf|\.swiftformat|dprint\.json|\.jsbeautifyrc)$"
)
BOT = re.compile(r"\[bot\]|github-actions|dependabot|renovate|\bbot\b|-bot@|bot@", re.I)
TOKEN = re.compile(r"\w+|[^\w\s]")
MAX_LINES = 300_000

FEATURES = [
    "log_files", "log_lines", "balance", "net_ratio", "ws_pres", "tok_pres", "alnum_eq", "ws_only",
    "cfg_change", "bot",
] + [f"kw_{g}" for g in KEYWORDS]
CONTENT_FEATURES = ["ws_pres", "tok_pres", "alnum_eq", "ws_only"]
KEYWORD_FEATURES = [f"kw_{g}" for g in KEYWORDS]


def git(repo: Path, *args: str, timeout: int = 600) -> bytes:
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, check=True, timeout=timeout).stdout


def seed_for(*parts: str) -> int:
    h = hashlib.sha256(("|".join([str(CONFIG["master_seed"]), *parts])).encode()).hexdigest()
    return int(h[:8], 16)


def clone_dir(full_name: str) -> Path:
    return Path(CONFIG["clones_dir"]) / full_name.replace("/", "__")


def _preservation(deleted: Counter, added: Counter) -> float:
    total = sum(deleted.values()) + sum(added.values())
    if total == 0:
        return 0.0
    matched = sum(min(c, added[k]) for k, c in deleted.items())
    return 2 * matched / total


def commit_features(repo: Path, sha: str) -> dict:
    """Return the feature dict of one commit, measured against its first parent."""
    meta = git(repo, "show", "-s", "--format=%an%x00%ae%x00%B", sha).decode("utf-8", "replace")
    name, email, message = (meta.split("\x00", 2) + ["", ""])[:3]
    numstat = git(repo, "show", "--no-renames", "--format=", "--numstat", sha).decode("utf-8", "replace")
    files, adds, dels, cfg = 0, 0, 0, 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-":
            continue
        files += 1
        adds += int(parts[0])
        dels += int(parts[1])
        if CONFIG_FILES.search(parts[2]):
            cfg = 1
    ws_numstat = git(repo, "show", "-w", "--no-renames", "--format=", "--numstat", sha).decode("utf-8", "replace")
    ws_changes = 0
    for line in ws_numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] != "-":
            ws_changes += int(parts[0]) + int(parts[1])
    patch = git(repo, "show", "--no-renames", "--format=", "-U0", "--no-color", sha)
    ws_del, ws_add, tok_del, tok_add = Counter(), Counter(), Counter(), Counter()
    alnum_equal, alnum_files = 0, 0
    cur_del, cur_add = [], []
    seen = 0

    def close_file():
        nonlocal alnum_equal, alnum_files
        if cur_del or cur_add:
            alnum_files += 1
            a = "".join(ch for s in cur_del for ch in s if ch.isalnum())
            b = "".join(ch for s in cur_add for ch in s if ch.isalnum())
            if a and a == b:
                alnum_equal += 1

    for raw in patch.split(b"\n"):
        if raw.startswith(b"diff --git "):
            close_file()
            cur_del, cur_add = [], []
            continue
        if raw.startswith((b"--- ", b"+++ ")) or seen > MAX_LINES:
            continue
        if raw.startswith(b"-") or raw.startswith(b"+"):
            seen += 1
            text = raw[1:].decode("utf-8", "replace")
            squeezed = "".join(text.split())
            toks = TOKEN.findall(text)
            if raw.startswith(b"-"):
                cur_del.append(text)
                if squeezed:
                    ws_del[squeezed] += 1
                tok_del.update(toks)
            else:
                cur_add.append(text)
                if squeezed:
                    ws_add[squeezed] += 1
                tok_add.update(toks)
    close_file()
    lines = adds + dels
    stripped = message.strip()
    subject = stripped.splitlines()[0].lower() if stripped else ""
    feats = {
        "log_files": math.log1p(files),
        "log_lines": math.log1p(lines),
        "balance": (min(adds, dels) / max(adds, dels)) if max(adds, dels) else 0.0,
        "net_ratio": (abs(adds - dels) / lines) if lines else 0.0,
        "ws_pres": _preservation(ws_del, ws_add),
        "tok_pres": _preservation(tok_del, tok_add),
        "alnum_eq": (alnum_equal / alnum_files) if alnum_files else 0.0,
        "ws_only": 1 if lines > 0 and ws_changes == 0 else 0,
        "cfg_change": cfg,
        "bot": 1 if BOT.search(name) or BOT.search(email) else 0,
    }
    for g, pattern in KEYWORDS.items():
        feats[f"kw_{g}"] = 1 if re.search(pattern, subject) else 0
    feats["n_files"] = files
    feats["n_lines"] = lines
    return feats
