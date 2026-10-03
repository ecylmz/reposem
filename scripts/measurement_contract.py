"""Git helpers used by the measurement scripts: the pinned Git runner, identity
normalization with .mailmap, parsing of .git-blame-ignore-revs, and blame parsing.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

GIT_EXECUTABLE = "/opt/homebrew/bin/git"
REQUIRED_GIT_VERSION = "2.55.0"
LANES: Tuple[str, ...] = ("RAW", "DECLARED_PRE", "DECLARED_POST", "W", "WMC")

# States named by Section 10.5 plus explicit contract states used by the
# synthetic producer.  Unknown states are rejected by the schema validators.
VALID_STATES: Set[str] = {
    "ABSENT",
    "UNAVAILABLE",
    "COLLECTION_FAILED",
    "NOT_APPLICABLE",
    "UNRESOLVED",
    "INVALID_IGNORE_ENTRY",
    "NOT_DEFAULT_BRANCH_ANCESTOR",
    "DECLARATION_TIME_UNRESOLVED",
    "UNRESOLVED_DECLARATION_HISTORY",
    "MIXED_DECLARATION_TRANSITION",
    "FILE_ABSENT_AT_DECLARATION",
    "PREDECLARATION_LINEAGE_AMBIGUOUS",
    "PATH_OBSERVABLE",
    "PATH_RENAMED",
    "PATH_DELETED",
    "LINEAGE_AMBIGUOUS",
    "SNAPSHOT_UNAVAILABLE",
    "UNBLAMABLE_LINE",
    "NO_FUTURE_ACTIVITY",
    "NO_BASELINE_KNOWN_FUTURE_ACTIVITY",
    "INSUFFICIENT_FUTURE_WINDOW",
    "BLAME_FAILED",
    "IDENTITY_UNRESOLVED",
    "PRIOR_OUTCOME_EXPOSURE_QUARANTINE",
    "DECLARATION_ADDED",
    "DECLARATION_REMOVED",
    "NO_TRANSITION",
    "OBSERVED",
}


class ContractError(ValueError):
    """Raised when a frozen contract invariant or strict schema is violated."""


class GitCommandError(RuntimeError):
    """Raised for a failed Git command in a synthetic fixture."""

    def __init__(self, command: Sequence[str], returncode: int, stderr: str):
        self.command = tuple(command)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            "Git command failed ({}): {}\n{}".format(
                returncode, " ".join(command), stderr.strip()
            )
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def git_version(executable: str = GIT_EXECUTABLE) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
    )
    if completed.returncode != 0:
        raise ContractError(
            "unable to obtain Git version from {}: {}".format(
                executable, completed.stderr.strip()
            )
        )
    return completed.stdout.strip()


def assert_pinned_git(executable: str = GIT_EXECUTABLE) -> str:
    actual = git_version(executable)
    expected = "git version {}".format(REQUIRED_GIT_VERSION)
    if actual != expected:
        raise ContractError(
            "Git version mismatch: required {!r}, observed {!r}".format(
                expected, actual
            )
        )
    return actual


class GitRunner:
    """Small deterministic wrapper around the pinned Git executable."""

    def __init__(self, repository: Path, executable: str = GIT_EXECUTABLE):
        self.repository = Path(repository)
        self.executable = executable
        self._base_env = {
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        }

    def run(
        self,
        args: Sequence[str],
        *,
        input_text: Optional[str] = None,
        check: bool = True,
        env_extra: Optional[Mapping[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        command = [self.executable] + list(args)
        env = dict(self._base_env)
        if env_extra:
            env.update({str(key): str(value) for key, value in env_extra.items()})
        completed = subprocess.run(
            command,
            cwd=str(self.repository),
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise GitCommandError(command, completed.returncode, completed.stderr)
        return completed

    def rev_parse(self, revision: str) -> str:
        result = self.run(["rev-parse", "--verify", revision])
        return result.stdout.strip()

    def head(self) -> str:
        return self.rev_parse("HEAD")

    def parents(self, commit: str) -> List[str]:
        result = self.run(["show", "-s", "--format=%P", commit])
        value = result.stdout.strip()
        return value.split() if value else []

    def commit_timestamp(self, commit: str) -> int:
        result = self.run(["show", "-s", "--format=%ct", commit])
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise ContractError("invalid committer timestamp for {}".format(commit)) from exc

    def commit_author(self, commit: str) -> Tuple[str, str]:
        result = self.run(["show", "-s", "--format=%an%x00%ae", commit])
        value = result.stdout.rstrip("\n")
        if "\x00" not in value:
            raise ContractError("missing commit author for {}".format(commit))
        name, email = value.split("\x00", 1)
        return name, email

    def file_exists(self, commit: str, path: str) -> bool:
        result = self.run(["cat-file", "-e", "{}:{}".format(commit, path)], check=False)
        return result.returncode == 0

    def show_file(self, commit: str, path: str) -> str:
        result = self.run(["show", "{}:{}".format(commit, path)])
        return result.stdout

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self.run(["merge-base", "--is-ancestor", ancestor, descendant], check=False)
        return result.returncode == 0

    def rev_list(self, *args: str) -> List[str]:
        result = self.run(["rev-list"] + list(args))
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _strip_email(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value.strip().casefold()


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


class IdentityNormalizer:
    """Apply one frozen HEAD .mailmap to all synthetic observations."""

    def __init__(self, repository: GitRunner, frozen_head: Optional[str] = None, scope_id: str = ""):
        self.repository = repository
        self.frozen_head = frozen_head or repository.head()
        self.scope_id = scope_id or self.frozen_head
        self._by_email: Dict[str, Tuple[str, str]] = {}
        self._by_name_email: Dict[Tuple[str, str], Tuple[str, str]] = {}
        if repository.file_exists(self.frozen_head, ".mailmap"):
            self._read_mailmap(repository.show_file(self.frozen_head, ".mailmap"))

    def _read_mailmap(self, content: str) -> None:
        angle_pattern = re.compile(r"([^<>]*)<([^<>]+)>")
        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            matches = angle_pattern.findall(line)
            if len(matches) < 2:
                # A one-entry line has no source identity to map and is
                # intentionally not used as an alias rule.
                continue
            canonical_name = matches[0][0].strip()
            canonical_email = _strip_email(matches[0][1])
            source_name = matches[1][0].strip()
            source_email = _strip_email(matches[1][1])
            mapped = (_normalize_name(canonical_name), canonical_email)
            self._by_email[source_email] = mapped
            if source_name:
                self._by_name_email[(_normalize_name(source_name), source_email)] = mapped

    def canonical_email(self, name: str, email: str) -> str:
        normalized_email = _strip_email(email)
        normalized_name = _normalize_name(name)
        mapped = self._by_name_email.get((normalized_name, normalized_email))
        if mapped is None:
            mapped = self._by_email.get(normalized_email)
        if mapped is None:
            return normalized_email
        return mapped[1]

    def developer_id(self, name: str, email: str) -> str:
        canonical = self.canonical_email(name, email)
        return sha256_text(self.scope_id + "\x00" + canonical)


@dataclass(frozen=True)
class DeclarationSnapshot:
    commit: str
    state: str
    resolved_shas: Tuple[str, ...]
    invalid_entries: Tuple[str, ...] = ()
    present: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "commit": self.commit,
            "state": self.state,
            "resolved_shas": list(self.resolved_shas),
            "invalid_entries": list(self.invalid_entries),
            "present": self.present,
        }


def _resolve_declared_token(repository: GitRunner, token: str) -> Optional[str]:
    # The token is data, not an option.  Requiring a commit object and a full
    # 40-hex result prevents lexical aliases from becoming measurement state.
    if not re.fullmatch(r"[0-9A-Fa-f]{4,64}", token):
        return None
    result = repository.run(
        ["rev-parse", "--verify", "--quiet", "{}^{{commit}}".format(token)],
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        return None
    return value


def declaration_snapshot(repository: GitRunner, commit: str) -> DeclarationSnapshot:
    path = ".git-blame-ignore-revs"
    if not repository.file_exists(commit, path):
        return DeclarationSnapshot(commit, "ABSENT", (), (), False)
    content = repository.show_file(commit, path)
    resolved: Set[str] = set()
    invalid: List[str] = []
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 1:
            invalid.append(line)
            continue
        token = fields[0]
        resolved_sha = _resolve_declared_token(repository, token)
        if resolved_sha is None:
            invalid.append(token)
        else:
            resolved.add(resolved_sha)
    state = "INVALID_IGNORE_ENTRY" if invalid else "OBSERVED"
    return DeclarationSnapshot(
        commit,
        state,
        tuple(sorted(resolved)),
        tuple(sorted(invalid)),
        True,
    )


@dataclass(frozen=True)
class DeclarationTransition:
    parent: Optional[str]
    commit: str
    pre: DeclarationSnapshot
    post: DeclarationSnapshot
    added: Tuple[str, ...]
    removed: Tuple[str, ...]
    state: str
    t_decl: int

    @property
    def is_primary_event(self) -> bool:
        return bool(self.added) and not self.removed and self.pre.state != "INVALID_IGNORE_ENTRY" and self.post.state != "INVALID_IGNORE_ENTRY"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "parent": self.parent,
            "commit": self.commit,
            "pre": self.pre.as_dict(),
            "post": self.post.as_dict(),
            "added": list(self.added),
            "removed": list(self.removed),
            "state": self.state,
            "t_decl": self.t_decl,
            "is_primary_event": self.is_primary_event,
        }


def declaration_transition(repository: GitRunner, commit: str) -> DeclarationTransition:
    parents = repository.parents(commit)
    parent = parents[0] if parents else None
    pre = (
        declaration_snapshot(repository, parent)
        if parent is not None
        else DeclarationSnapshot("<root>", "ABSENT", (), (), False)
    )
    post = declaration_snapshot(repository, commit)
    pre_set = set(pre.resolved_shas)
    post_set = set(post.resolved_shas)
    added = tuple(sorted(post_set - pre_set))
    removed = tuple(sorted(pre_set - post_set))
    if pre.state == "INVALID_IGNORE_ENTRY" or post.state == "INVALID_IGNORE_ENTRY":
        state = "INVALID_IGNORE_ENTRY"
    elif added and removed:
        state = "MIXED_DECLARATION_TRANSITION"
    elif added:
        state = "DECLARATION_ADDED"
    elif removed:
        state = "DECLARATION_REMOVED"
    else:
        state = "NO_TRANSITION"
    return DeclarationTransition(
        parent,
        commit,
        pre,
        post,
        added,
        removed,
        state,
        repository.commit_timestamp(commit),
    )


_HEADER_RE = re.compile(r"^(\^?[0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$")


@dataclass(frozen=True)
class BlameLine:
    line_no: int
    commit_sha: str
    author_name: str
    author_email: str
    content: str
    ignored: bool
    unblamable: bool
    developer_id: Optional[str] = None

    @property
    def line_state(self) -> str:
        if self.ignored and self.unblamable:
            return "IGNORED_AND_UNBLAMABLE"
        if self.ignored:
            return "IGNORED"
        if self.unblamable:
            return "UNBLAMABLE"
        return "NORMAL"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "line_no": self.line_no,
            "commit_sha": self.commit_sha,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "content": self.content,
            "ignored": self.ignored,
            "unblamable": self.unblamable,
            "line_state": self.line_state,
            "developer_id": self.developer_id,
        }


def parse_line_porcelain(output: str) -> List[BlameLine]:
    lines = output.splitlines()
    parsed: List[BlameLine] = []
    i = 0
    while i < len(lines):
        header_match = _HEADER_RE.match(lines[i])
        if header_match is None:
            raise ContractError("malformed blame porcelain header at line {}".format(i + 1))
        raw_sha, _orig_line, final_line, span_text = header_match.groups()
        sha = raw_sha.lstrip("^")
        span = int(span_text or "1")
        i += 1
        metadata: Dict[str, str] = {}
        ignored = False
        unblamable = False
        contents: List[str] = []
        while i < len(lines) and not _HEADER_RE.match(lines[i]):
            value = lines[i]
            if value == "ignored":
                ignored = True
                i += 1
                continue
            if value == "unblamable":
                unblamable = True
                i += 1
                continue
            if value == "boundary":
                metadata["boundary"] = ""
                i += 1
                continue
            if value.startswith("\t"):
                contents.append(value[1:])
                i += 1
                if len(contents) >= span:
                    break
                continue
            if " " in value:
                key, item = value.split(" ", 1)
                metadata[key] = item
                i += 1
                continue
            raise ContractError("malformed blame porcelain metadata: {!r}".format(value))
        if not contents:
            raise ContractError("blame porcelain block has no content")
        author_name = metadata.get("author", "")
        author_email = metadata.get("author-mail", "").strip()
        for offset, content in enumerate(contents):
            parsed.append(
                BlameLine(
                    line_no=int(final_line) + offset,
                    commit_sha=sha,
                    author_name=author_name,
                    author_email=author_email,
                    content=content,
                    ignored=ignored,
                    unblamable=unblamable,
                )
            )
        if len(contents) != span and span != 1:
            raise ContractError("blame porcelain span/content mismatch")
    if not parsed:
        return []
    expected = list(range(1, len(parsed) + 1))
    observed = [line.line_no for line in parsed]
    if observed != expected:
        raise ContractError("blame line numbers are not contiguous")
    return parsed


@dataclass(frozen=True)
class LaneMeasurement:
    lane: str
    commit: str
    path: str
    ignore_set_shas: Tuple[str, ...]
    lines: Tuple[BlameLine, ...]

    def with_identity(self, identity: IdentityNormalizer) -> "LaneMeasurement":
        return LaneMeasurement(
            self.lane,
            self.commit,
            self.path,
            self.ignore_set_shas,
            tuple(
                BlameLine(
                    line.line_no,
                    line.commit_sha,
                    line.author_name,
                    line.author_email,
                    line.content,
                    line.ignored,
                    line.unblamable,
                    None
                    if line.unblamable
                    else identity.developer_id(line.author_name, line.author_email),
                )
                for line in self.lines
            ),
        )

    def summary(self) -> Dict[str, Any]:
        developers = Counter(
            line.developer_id
            for line in self.lines
            if not line.unblamable and line.developer_id is not None
        )
        return {
            "lane": self.lane,
            "commit": self.commit,
            "path": self.path,
            "ignore_set_shas": list(self.ignore_set_shas),
            "line_count": len(self.lines),
            "ignored_line_count": sum(1 for line in self.lines if line.ignored),
            "unblamable_line_count": sum(1 for line in self.lines if line.unblamable),
            "developer_line_counts": dict(sorted(developers.items())),
            "line_states": [line.line_state for line in self.lines],
            # Public synthetic evidence retains measurement status but not raw
            # author strings or file contents.
            "lines": [
                {
                    "line_no": line.line_no,
                    "commit_sha": line.commit_sha,
                    "ignored": line.ignored,
                    "unblamable": line.unblamable,
                    "line_state": line.line_state,
                    "developer_id": line.developer_id,
                }
                for line in self.lines
            ],
        }


def _blame_args(lane: str) -> List[str]:
    if lane not in LANES:
        raise ContractError("unknown blame lane {!r}".format(lane))
    args = [
        "-c",
        "blame.markIgnoredLines=true",
        "-c",
        "blame.markUnblamableLines=true",
        "blame",
        "--line-porcelain",
    ]
    if lane in ("W", "WMC"):
        args.append("-w")
    if lane == "WMC":
        args.extend(["-M", "-C"])
    return args


def measure_blame(
    repository: GitRunner,
    commit: str,
    path: str,
    lane: str,
    ignore_set_shas: Iterable[str] = (),
    identity: Optional[IdentityNormalizer] = None,
) -> LaneMeasurement:
    ignore_set = tuple(sorted(set(ignore_set_shas))) if lane.startswith("DECLARED_") else ()
    args = _blame_args(lane)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="reposem-ignore-") as handle:
        if lane.startswith("DECLARED_"):
            handle.write("\n".join(ignore_set))
            if ignore_set:
                handle.write("\n")
            args.extend(["--ignore-revs-file", handle.name])
        handle.flush()
        args.extend([commit, "--", path])
        result = repository.run(args)
    parsed = parse_line_porcelain(result.stdout)
    measurement = LaneMeasurement(lane, commit, path, ignore_set, tuple(parsed))
    return measurement.with_identity(identity) if identity is not None else measurement


def measure_all_lanes(
    repository: GitRunner,
    commit: str,
    path: str,
    i_pre: Iterable[str],
    i_post: Iterable[str],
    identity: Optional[IdentityNormalizer] = None,
) -> Dict[str, LaneMeasurement]:
    measurements: Dict[str, LaneMeasurement] = {}
    for lane in LANES:
        ignore_set: Iterable[str] = ()
        if lane == "DECLARED_PRE":
            ignore_set = i_pre
        elif lane == "DECLARED_POST":
            ignore_set = i_post
        measurements[lane] = measure_blame(
            repository, commit, path, lane, ignore_set, identity
        )
    return measurements


def frozen_horizon_measurements(
    repository: GitRunner,
    horizon_commits: Mapping[int, str],
    path: str,
    i_pre: Iterable[str],
    i_post: Iterable[str],
    identity: Optional[IdentityNormalizer] = None,
) -> Dict[int, Dict[str, LaneMeasurement]]:
    """Measure every horizon with exactly the event-frozen declaration sets."""
    result: Dict[int, Dict[str, LaneMeasurement]] = {}
    for horizon, commit in sorted(horizon_commits.items()):
        result[horizon] = measure_all_lanes(
            repository, commit, path, tuple(i_pre), tuple(i_post), identity
        )
    return result


@dataclass(frozen=True)
class LineageStep:
    parent: str
    child: str
    old_path: str
    new_path: Optional[str]
    state: str
    records: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "parent": self.parent,
            "child": self.child,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "state": self.state,
            "records": [[status, list(paths)] for status, paths in self.records],
        }


def parse_name_status_z(output: str) -> List[Tuple[str, Tuple[str, ...]]]:
    tokens = output.split("\x00")
    records: List[Tuple[str, Tuple[str, ...]]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        if not status:
            continue
        kind = status[0]
        needed = 2 if kind in ("R", "C") else 1
        if i + needed > len(tokens):
            raise ContractError("truncated name-status record {!r}".format(status))
        paths = tuple(tokens[i : i + needed])
        i += needed
        records.append((status, paths))
    return records


def name_status_records(
    repository: GitRunner,
    parent: str,
    child: str,
    *,
    detect_copies: bool = True,
) -> List[Tuple[str, Tuple[str, ...]]]:
    args = ["diff-tree", "-r", "-M"]
    if detect_copies:
        # Two -C passes are required for unchanged-source copies: the second
        # pass enables Git's --find-copies-harder candidate search.
        args.extend(["-C", "-C"])
    args.extend(["--name-status", "-z", "--no-commit-id", parent, child])
    result = repository.run(args)
    return parse_name_status_z(result.stdout)


def _lineage_state_for_ambiguity(pre_declaration: bool) -> str:
    return "PREDECLARATION_LINEAGE_AMBIGUOUS" if pre_declaration else "LINEAGE_AMBIGUOUS"


def transition_lineage(
    repository: GitRunner,
    parent: str,
    child: str,
    current_path: str,
    *,
    pre_declaration: bool = False,
    records: Optional[Sequence[Tuple[str, Tuple[str, ...]]]] = None,
) -> LineageStep:
    records_list = list(records) if records is not None else name_status_records(repository, parent, child)
    serialized = tuple((status, tuple(paths)) for status, paths in records_list)
    copy_hits = [
        (status, paths)
        for status, paths in records_list
        if status.startswith("C") and current_path in paths
    ]
    if copy_hits:
        return LineageStep(
            parent,
            child,
            current_path,
            None,
            _lineage_state_for_ambiguity(pre_declaration),
            serialized,
        )
    rename_hits = [
        (status, paths)
        for status, paths in records_list
        if status.startswith("R") and paths[0] == current_path
    ]
    competing = [
        (status, paths)
        for status, paths in records_list
        if status.startswith("R") and (paths[0] == current_path or paths[1] == current_path)
    ]
    if len(rename_hits) != 1 or (len(competing) > 1):
        if rename_hits or (len(competing) > 1):
            return LineageStep(
                parent,
                child,
                current_path,
                None,
                _lineage_state_for_ambiguity(pre_declaration),
                serialized,
            )
    if len(rename_hits) == 1:
        new_path = rename_hits[0][1][1]
        if not repository.file_exists(child, new_path):
            return LineageStep(
                parent,
                child,
                current_path,
                None,
                "SNAPSHOT_UNAVAILABLE",
                serialized,
            )
        return LineageStep(parent, child, current_path, new_path, "PATH_RENAMED", serialized)
    deleted = any(
        status == "D" and paths[0] == current_path for status, paths in records_list
    )
    if deleted or not repository.file_exists(child, current_path):
        return LineageStep(
            parent,
            child,
            current_path,
            None,
            "PATH_DELETED" if deleted else "SNAPSHOT_UNAVAILABLE",
            serialized,
        )
    return LineageStep(parent, child, current_path, current_path, "PATH_OBSERVABLE", serialized)


def trace_lineage(
    repository: GitRunner,
    start_path: str,
    transitions: Sequence[Tuple[str, str]],
    *,
    pre_declaration: bool = False,
) -> Dict[str, Any]:
    current = start_path
    steps: List[LineageStep] = []
    renamed = False
    for parent, child in transitions:
        step = transition_lineage(
            repository,
            parent,
            child,
            current,
            pre_declaration=pre_declaration,
        )
        steps.append(step)
        if step.state == "PATH_RENAMED" and step.new_path is not None:
            current = step.new_path
            renamed = True
            continue
        if step.state != "PATH_OBSERVABLE":
            return {
                "state": step.state,
                "path": None,
                "renamed": renamed,
                "steps": [item.as_dict() for item in steps],
            }
        current = step.new_path or current
    return {
        "state": "PATH_OBSERVABLE",
        "path": current,
        "renamed": renamed,
        "steps": [item.as_dict() for item in steps],
    }


@dataclass(frozen=True)
class HorizonSelection:
    horizon_days: int
    cutoff: int
    state: str
    selected_commit: Optional[str]
    selected_timestamp: Optional[int]
    head_timestamp: int
    timestamp_inversions: Tuple[Tuple[str, str], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "horizon_days": self.horizon_days,
            "cutoff": self.cutoff,
            "state": self.state,
            "selected_commit": self.selected_commit,
            "selected_timestamp": self.selected_timestamp,
            "head_timestamp": self.head_timestamp,
            "timestamp_inversions": [list(pair) for pair in self.timestamp_inversions],
        }


def _first_parent_chain(repository: GitRunner, head: str) -> List[str]:
    reverse_chain: List[str] = []
    current: Optional[str] = head
    while current is not None:
        reverse_chain.append(current)
        parents = repository.parents(current)
        current = parents[0] if parents else None
    return list(reversed(reverse_chain))


def select_horizon_snapshots(
    repository: GitRunner,
    declaration_commit: str,
    horizons: Sequence[int] = (90, 180, 365),
    *,
    head: Optional[str] = None,
) -> Dict[int, HorizonSelection]:
    head_commit = head or repository.head()
    if not repository.is_ancestor(declaration_commit, head_commit):
        raise ContractError("declaration is not on the frozen default-branch history")
    chain = _first_parent_chain(repository, head_commit)
    if declaration_commit not in chain:
        raise ContractError("declaration is not on the first-parent chain")
    event_position = chain.index(declaration_commit)
    timestamps = {commit: repository.commit_timestamp(commit) for commit in chain}
    inversions: List[Tuple[str, str]] = []
    for left, right in zip(chain, chain[1:]):
        if timestamps[right] < timestamps[left]:
            inversions.append((left, right))
    t_decl = timestamps[declaration_commit]
    head_timestamp = timestamps[head_commit]
    selections: Dict[int, HorizonSelection] = {}
    for horizon in sorted(set(int(value) for value in horizons)):
        cutoff = t_decl + horizon * 86400
        if head_timestamp < cutoff:
            selections[horizon] = HorizonSelection(
                horizon,
                cutoff,
                "INSUFFICIENT_FUTURE_WINDOW",
                None,
                None,
                head_timestamp,
                tuple(inversions),
            )
            continue
        candidates = [
            (position, commit)
            for position, commit in enumerate(chain)
            if position >= event_position and timestamps[commit] <= cutoff
        ]
        if not candidates:
            selections[horizon] = HorizonSelection(
                horizon,
                cutoff,
                "UNAVAILABLE",
                None,
                None,
                head_timestamp,
                tuple(inversions),
            )
            continue
        _position, selected = max(candidates, key=lambda item: item[0])
        selections[horizon] = HorizonSelection(
            horizon,
            cutoff,
            "PATH_OBSERVABLE",
            selected,
            timestamps[selected],
            head_timestamp,
            tuple(inversions),
        )
    return selections


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int


def parse_unified_hunks(diff_text: str) -> List[DiffHunk]:
    hunks: List[DiffHunk] = []
    for line in diff_text.splitlines():
        match = _HUNK_RE.match(line)
        if match:
            old_start, old_count, new_start, new_count = match.groups()
            hunks.append(
                DiffHunk(
                    int(old_start),
                    int(old_count or "1"),
                    int(new_start),
                    int(new_count or "1"),
                )
            )
    return hunks


def _parse_context_mappings(diff_text: str) -> Dict[int, int]:
    """Parse unchanged context pairs emitted by the actual Myers patch."""
    mapping: Dict[int, int] = {}
    old_cursor: Optional[int] = None
    new_cursor: Optional[int] = None
    for line in diff_text.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_start, old_count_text, new_start, _new_count = hunk.groups()
            old_cursor = int(old_start)
            new_cursor = int(new_start)
            # With unified=0 Git may put the unchanged anchor after the
            # second @@ on the hunk-header line (not on a separate context
            # line).  Preserve that Myers mapping explicitly.
            suffix = line[hunk.end() :]
            if suffix.startswith(" ") and (old_count_text or "1") == "0":
                mapping[int(old_start)] = int(new_start) - 1
                old_cursor = int(old_start) + 1
                new_cursor = int(new_start)
            continue
        if old_cursor is None or new_cursor is None:
            continue
        if line.startswith("\\"):
            continue
        if line.startswith(" "):
            mapping[old_cursor] = new_cursor
            old_cursor += 1
            new_cursor += 1
        elif line.startswith("-"):
            old_cursor += 1
        elif line.startswith("+"):
            new_cursor += 1
    return mapping


def _file_lines(repository: GitRunner, commit: str, path: str) -> List[str]:
    return repository.show_file(commit, path).splitlines()


def same_path_myers_survival(
    repository: GitRunner,
    baseline_commit: str,
    horizon_commit: str,
    path: str,
) -> Dict[str, Any]:
    if not repository.file_exists(baseline_commit, path):
        return {
            "state": "FILE_ABSENT_AT_DECLARATION",
            "baseline_line_count": 0,
            "surviving_lines": [],
            "horizon_line_count": 0,
        }
    if not repository.file_exists(horizon_commit, path):
        return {
            "state": "PATH_DELETED",
            "baseline_line_count": len(_file_lines(repository, baseline_commit, path)),
            "surviving_lines": [],
            "horizon_line_count": 0,
        }
    result = repository.run(
        [
            "diff",
            "--no-ext-diff",
            "--diff-algorithm=myers",
            "--no-renames",
            "--unified=0",
            baseline_commit,
            horizon_commit,
            "--",
            path,
        ]
    )
    hunks = parse_unified_hunks(result.stdout)
    context_mapping = _parse_context_mappings(result.stdout)
    old_lines = _file_lines(repository, baseline_commit, path)
    new_lines = _file_lines(repository, horizon_commit, path)
    changed_old: Set[int] = set()
    for hunk in hunks:
        if hunk.old_count:
            changed_old.update(range(hunk.old_start, hunk.old_start + hunk.old_count))
    surviving: List[int] = []
    for line_number, value in enumerate(old_lines, start=1):
        explicit_mapping = context_mapping.get(line_number)
        if explicit_mapping is not None:
            if 1 <= explicit_mapping <= len(new_lines) and value == new_lines[explicit_mapping - 1]:
                surviving.append(line_number)
            continue
        if line_number in changed_old:
            continue
        offset = 0
        for hunk in hunks:
            boundary = hunk.old_start + hunk.old_count
            if line_number >= boundary:
                offset += hunk.new_count - hunk.old_count
            elif hunk.old_count == 0 and line_number >= hunk.old_start:
                offset += hunk.new_count
        new_line_number = line_number + offset
        if (
            1 <= new_line_number <= len(new_lines)
            and old_lines[line_number - 1] == new_lines[new_line_number - 1]
        ):
            surviving.append(line_number)
    return {
        "state": "PATH_OBSERVABLE",
        "baseline_line_count": len(old_lines),
        "surviving_lines": surviving,
        "surviving_count": len(surviving),
        "horizon_line_count": len(new_lines),
        "hunks": [asdict(hunk) for hunk in hunks],
    }


def _parse_numstat(output: str) -> Tuple[int, bool]:
    total = 0
    binary = False
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t", 2)
        if len(fields) < 2:
            continue
        added, deleted = fields[0], fields[1]
        if added == "-" or deleted == "-":
            binary = True
            continue
        try:
            total += int(added) + int(deleted)
        except ValueError as exc:
            raise ContractError("malformed numstat line {!r}".format(line)) from exc
    return total, binary


def non_whitespace_line_mass(
    repository: GitRunner,
    parent: str,
    child: str,
    parent_path: str,
    child_path: str,
) -> Tuple[int, bool]:
    paths = [parent_path] if parent_path == child_path else [parent_path, child_path]
    result = repository.run(
        [
            "diff",
            "--no-ext-diff",
            "--ignore-all-space",
            "--no-renames",
            "--numstat",
            parent,
            child,
            "--",
        ]
        + paths
    )
    return _parse_numstat(result.stdout)


@dataclass(frozen=True)
class FutureTouch:
    commit: str
    developer_id: str
    line_mass: int
    merge: bool
    declaration_filtered: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "commit": self.commit,
            "developer_id": self.developer_id,
            "line_mass": self.line_mass,
            "merge": self.merge,
            "declaration_filtered": self.declaration_filtered,
        }


def _path_state_from_ancestor(
    repository: GitRunner,
    ancestor: str,
    commit: str,
    start_path: str,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Follow first-parent transitions from an ancestor to a descendant."""
    if ancestor == commit:
        return "PATH_OBSERVABLE", start_path, None
    if not repository.is_ancestor(ancestor, commit):
        return "SNAPSHOT_UNAVAILABLE", None, None
    reverse: List[str] = []
    current = commit
    while current != ancestor:
        reverse.append(current)
        parents = repository.parents(current)
        if not parents:
            return "SNAPSHOT_UNAVAILABLE", None, None
        current = parents[0]
    reverse.reverse()
    path = start_path
    last_transition: Optional[str] = None
    for child in reverse:
        parent = repository.parents(child)[0]
        step = transition_lineage(repository, parent, child, path)
        if step.state == "PATH_RENAMED" and step.new_path is not None:
            path = step.new_path
            last_transition = "PATH_RENAMED"
        elif step.state == "PATH_OBSERVABLE":
            last_transition = "PATH_OBSERVABLE"
        else:
            return step.state, None, child
    return "PATH_OBSERVABLE", path, last_transition


def future_ledger(
    repository: GitRunner,
    declaration_commit: str,
    horizon_commit: str,
    start_path: str,
    identity: IdentityNormalizer,
    *,
    baseline_developer_ids: Iterable[str] = (),
    declaration_filter_shas: Iterable[str] = (),
) -> Dict[str, Any]:
    if not repository.is_ancestor(declaration_commit, horizon_commit):
        return {
            "state": "SNAPSHOT_UNAVAILABLE",
            "commits": [],
            "primary_touches": [],
            "merge_commit_count": 0,
            "integration_touches": [],
            "developer_touches": {},
            "developer_line_mass": {},
            "baseline_known_touch_count": 0,
            "baseline_known_line_mass": 0,
        }
    commits = repository.rev_list("--reverse", "--topo-order", horizon_commit, "--not", declaration_commit)
    filter_set = set(declaration_filter_shas)
    baseline_set = set(baseline_developer_ids)
    primary: List[FutureTouch] = []
    integration: List[FutureTouch] = []
    merge_count = 0
    for commit in commits:
        parents = repository.parents(commit)
        if not parents:
            continue
        parent = parents[0]
        state, child_path, _detail = _path_state_from_ancestor(
            repository, declaration_commit, commit, start_path
        )
        if state not in ("PATH_OBSERVABLE", "PATH_RENAMED") or child_path is None:
            continue
        parent_state, parent_path, _parent_detail = _path_state_from_ancestor(
            repository, declaration_commit, parent, start_path
        )
        if parent_state not in ("PATH_OBSERVABLE", "PATH_RENAMED") or parent_path is None:
            continue
        mass, binary = non_whitespace_line_mass(
            repository, parent, commit, parent_path, child_path
        )
        is_merge = len(parents) > 1
        if is_merge:
            merge_count += 1
        if mass <= 0 or binary:
            continue
        name, email = repository.commit_author(commit)
        touch = FutureTouch(
            commit,
            identity.developer_id(name, email),
            mass,
            is_merge,
            commit in filter_set,
        )
        if is_merge:
            # Primary ledger excludes all merge commits.  This is the
            # first-parent integration-centric sensitivity only.
            integration.append(touch)
        else:
            primary.append(touch)
    developer_touches = Counter(touch.developer_id for touch in primary)
    developer_line_mass = Counter()
    for touch in primary:
        developer_line_mass[touch.developer_id] += touch.line_mass
    primary_count = len(primary)
    baseline_known_touch_count = sum(
        count for developer, count in developer_touches.items() if developer in baseline_set
    )
    baseline_known_line_mass = sum(
        mass for developer, mass in developer_line_mass.items() if developer in baseline_set
    )
    if primary_count == 0:
        state = "NO_FUTURE_ACTIVITY"
    elif baseline_known_touch_count == 0:
        state = "NO_BASELINE_KNOWN_FUTURE_ACTIVITY"
    else:
        state = "OBSERVED"
    return {
        "state": state,
        "commits": commits,
        "primary_touches": [touch.as_dict() for touch in primary],
        "merge_commit_count": merge_count,
        "integration_touches": [touch.as_dict() for touch in integration],
        "developer_touches": dict(sorted(developer_touches.items())),
        "developer_line_mass": dict(sorted(developer_line_mass.items())),
        "baseline_known_touch_count": baseline_known_touch_count,
        "baseline_known_line_mass": baseline_known_line_mass,
        "primary_touch_count": primary_count,
        "integration_touch_count": len(integration),
        "declaration_filtered_primary_touch_count": sum(
            1 for touch in primary if not touch.declaration_filtered
        ),
    }


def developer_shares(developer_line_counts: Mapping[str, int]) -> Dict[str, float]:
    total = sum(int(value) for value in developer_line_counts.values())
    if total <= 0:
        return {}
    return {
        key: float(value) / float(total)
        for key, value in sorted(developer_line_counts.items())
        if int(value) > 0
    }


def common_baseline_universe(
    raw_shares: Mapping[str, float], declared_post_shares: Mapping[str, float]
) -> Tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key in set(raw_shares) | set(declared_post_shares)
            if float(raw_shares.get(key, 0.0)) > 0.0
            or float(declared_post_shares.get(key, 0.0)) > 0.0
        )
    )


def _score_groups(scores: Mapping[str, float], universe: Sequence[str]) -> List[List[str]]:
    grouped: Dict[float, List[str]] = defaultdict(list)
    for developer in universe:
        grouped[float(scores.get(developer, 0.0))].append(developer)
    return [
        sorted(grouped[value])
        for value in sorted(grouped, reverse=True)
    ]


def tie_aware_ndcg_at_k(
    predicted_scores: Mapping[str, float],
    relevance_scores: Mapping[str, float],
    universe: Sequence[str],
    k: int = 5,
) -> float:
    if k <= 0:
        raise ContractError("NDCG k must be positive")
    groups = _score_groups(predicted_scores, universe)
    gains = [2.0 ** float(relevance_scores.get(developer, 0.0)) - 1.0 for developer in universe]
    ideal = sorted(gains, reverse=True)[:k]
    ideal_dcg = sum(gain / _discount(position) for position, gain in enumerate(ideal, start=1))
    if ideal_dcg == 0.0:
        return 0.0
    dcg = 0.0
    position = 1
    for group in groups:
        take = min(len(group), k - position + 1)
        if take <= 0:
            break
        expected_gain = sum(
            2.0 ** float(relevance_scores.get(developer, 0.0)) - 1.0
            for developer in group
        ) / float(len(group))
        for offset in range(take):
            dcg += expected_gain / _discount(position + offset)
        position += take
    return dcg / ideal_dcg


def _discount(position: int) -> float:
    # Position is one-based and this form is deterministic for float output.
    import math

    return math.log2(float(position) + 1.0)


def validate_blame_line_record(record: Mapping[str, Any]) -> None:
    required = {
        "line_no",
        "commit_sha",
        "author_name",
        "author_email",
        "content",
        "ignored",
        "unblamable",
        "line_state",
        "developer_id",
    }
    if set(record) != required:
        raise ContractError("strict blame-line schema mismatch")
    if not isinstance(record["line_no"], int) or record["line_no"] < 1:
        raise ContractError("invalid blame line number")
    if record["line_state"] not in {
        "NORMAL",
        "IGNORED",
        "UNBLAMABLE",
        "IGNORED_AND_UNBLAMABLE",
    }:
        raise ContractError("invalid blame line state")
    if bool(record["unblamable"]) and record["developer_id"] is not None:
        raise ContractError("unblamable line must not receive a developer")


def validate_horizon_record(record: Mapping[str, Any]) -> None:
    required = {
        "horizon_days",
        "cutoff",
        "state",
        "selected_commit",
        "selected_timestamp",
        "head_timestamp",
        "timestamp_inversions",
    }
    if set(record) != required:
        raise ContractError("strict horizon schema mismatch")
    if not record.get("state") or record["state"] not in VALID_STATES:
        raise ContractError("horizon state is missing or invalid")
    if record["state"] == "INSUFFICIENT_FUTURE_WINDOW" and record["selected_commit"] is not None:
        raise ContractError("ineligible horizon cannot have a selected commit")


def validate_case_record(record: Mapping[str, Any]) -> None:
    required = {"case_id", "status", "expected", "observed", "notes"}
    if set(record) != required:
        raise ContractError("strict case schema mismatch")
    if not record["case_id"] or record["status"] not in {"PASS", "FAIL"}:
        raise ContractError("invalid case record")
    if not isinstance(record["observed"], Mapping):
        raise ContractError("case observed evidence must be an object")


def validate_results_payload(payload: Mapping[str, Any]) -> None:
    required = {"schema", "git", "inputs", "execution", "cases", "summary"}
    if set(payload) != required:
        raise ContractError("strict results schema mismatch")
    if payload["schema"] != "reposem.phase0.synthetic.v1":
        raise ContractError("unexpected synthetic results schema")
    git = payload["git"]
    if set(git) != {"executable", "version"} or git["version"] != "git version 2.55.0":
        raise ContractError("results do not carry the pinned Git identity")
    execution = payload["execution"]
    for key in ("synthetic_only", "real_repository_accessed", "network_accessed", "production_operation"):
        if key not in execution:
            raise ContractError("execution field missing: {}".format(key))
    if execution["synthetic_only"] is not True or execution["real_repository_accessed"] is not False:
        raise ContractError("synthetic-only guard was not preserved")
    if not isinstance(payload["cases"], list) or not payload["cases"]:
        raise ContractError("results must contain cases")
    for case in payload["cases"]:
        validate_case_record(case)
    summary = payload["summary"]
    if set(summary) != {"case_count", "passed", "failed", "status"}:
        raise ContractError("strict summary schema mismatch")
    if summary["case_count"] != len(payload["cases"]):
        raise ContractError("case count mismatch")
    if summary["passed"] + summary["failed"] != summary["case_count"]:
        raise ContractError("summary counts do not balance")
    expected_status = "PASS" if summary["failed"] == 0 else "FAIL"
    if summary["status"] != expected_status:
        raise ContractError("summary status mismatch")


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "as_dict"):
        return json_safe(value.as_dict())
    if isinstance(value, Path):
        return str(value)
    return value
