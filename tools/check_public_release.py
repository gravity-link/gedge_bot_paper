#!/usr/bin/env python3
"""Pre-publication gate for the arXiv:2611.11698 artifact repository.

Verifies that no excluded/private file or credential-like string is present and
that every ``script_sha256`` recorded in the evidence JSON still matches the
shipped script byte-for-byte. Exits non-zero on any failure.
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", ".venv", "build", "dist"}
TEXT_EXT = {".py", ".sh", ".json", ".md", ".tex", ".bib", ".bbl",
            ".cff", ".txt", ".yml", ".yaml"}

BANNED_PATH = [
    re.compile(r"(^|/)materials(/|$)"),
    re.compile(r"\.current\.(py|c|txt|md)$"),
    re.compile(r"(^|/)source_manifest\.json$"),
    re.compile(r"(^|/)source_(audit|tree|README|requirements)", re.I),
    re.compile(r"(^|/)reference_audit\.md$"),
    re.compile(r"(^|/)ziJPFatU$"),
    re.compile(r"\.(wav|log|docx|xlsx|pdf)$"),
]
CRED = [
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"(?:password|passwd|api[_-]?key|secret)\s*[:=]\s*[\"'][^\"']{8,}[\"']", re.I),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            yield os.path.relpath(os.path.join(dirpath, name), ROOT)


def json_nodes(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from json_nodes(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from json_nodes(value)


def main():
    all_files = sorted(repo_files())
    problems = []
    try:
        with open(os.path.join(ROOT, "tools", "known_hash_drift.json"), encoding="utf-8") as f:
            KNOWN_FILES = {e["file"] for e in json.load(f)["entries"]}
    except (OSError, ValueError, KeyError):
        KNOWN_FILES = set()
    for rel in all_files:
        if any(p.search(rel) for p in BANNED_PATH):
            problems.append("excluded file present: " + rel)
            continue
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for pat in CRED:
            if pat.search(text):
                problems.append("credential-like string in " + rel)
                break

    script_dir = os.path.join(ROOT, "scripts")
    hashes = {n: sha256(os.path.join(script_dir, n))
              for n in os.listdir(script_dir) if n.endswith(".py")}
    checked = matched = known = mismatched = unresolved = 0
    for dirpath, _, filenames in os.walk(os.path.join(ROOT, "evidence")):
        for name in sorted(filenames):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (ValueError, OSError):
                continue
            for node in json_nodes(data):
                digest = node.get("script_sha256")
                if not isinstance(digest, str):
                    continue
                checked += 1
                candidate = os.path.basename(str(node.get("script", "")))
                if candidate not in hashes:
                    unresolved += 1
                elif hashes[candidate] == digest:
                    matched += 1
                else:
                    relative = os.path.relpath(path, ROOT)
                    if relative in KNOWN_FILES:
                        known += 1
                    else:
                        mismatched += 1
                        problems.append("script_sha256 mismatch: " +
                                        relative + " -> " + candidate)

    print("files scanned:        ", len(all_files))
    print("scripts indexed:      ", len(hashes))
    print("script_sha256 checked:", checked, "matched:", matched,
          "known drift:", known, "new mismatch:", mismatched,
          "unresolved:", unresolved)
    if problems:
        print("FAIL:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("PASS: no private material, no credentials, no new hash drift")
    if known:
        print("NOTE:", known, "historical hash drifts are documented in tools/known_hash_drift.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
