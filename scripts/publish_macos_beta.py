#!/usr/bin/env python3
"""Safely stage, verify, and publish one exact-SHA macOS beta prerelease."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence


FULL_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
BUILD_NUMBER = re.compile(r"^[1-9][0-9]*$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_HOST = "github.com"


class PublicationError(RuntimeError):
    """A fail-closed beta publication policy violation."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BetaArtifacts:
    metadata: Path
    dmg: Path
    notes: Path
    checksums: Path
    version: str
    build_number: str
    source_commit: str
    tag: str
    expected_sha256: tuple[str, ...]
    expected_sizes: tuple[int, ...]

    @property
    def expected_paths(self) -> tuple[Path, ...]:
        return (self.dmg, self.metadata, self.notes, self.checksums)

    @property
    def expected_names(self) -> tuple[str, ...]:
        return tuple(path.name for path in self.expected_paths)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicationError(f"{label} is not valid readable JSON: {error}") from error
    require(isinstance(value, dict), f"{label} must contain a JSON object")
    return value


def _require_regular_file(path: Path, label: str) -> None:
    require(not path.is_symlink(), f"{label} must not be a symbolic link: {path}")
    require(path.is_file(), f"{label} is missing or is not a regular file: {path}")


def load_beta_artifacts(metadata_path: Path) -> BetaArtifacts:
    metadata_path = metadata_path.expanduser().absolute()
    _require_regular_file(metadata_path, "beta metadata")
    metadata = _load_json_object(metadata_path, "beta metadata")

    version = str(metadata.get("version") or "")
    build_number = str(metadata.get("build_number") or "")
    source_commit = str(metadata.get("source_commit") or "")
    require(VERSION.fullmatch(version) is not None, "metadata version is not numeric")
    require(BUILD_NUMBER.fullmatch(build_number) is not None, "metadata build_number is not a positive integer")
    require(FULL_SHA.fullmatch(source_commit) is not None, "metadata source_commit must be a full lowercase Git SHA")

    stem = f"ElectronicMail-Beta-{version}-{build_number}"
    expected_metadata_name = f"{stem}-metadata.json"
    require(
        metadata_path.name == expected_metadata_name,
        f"metadata filename must be {expected_metadata_name}",
    )

    require(metadata.get("schema_version") == 1, "metadata schema_version must be 1")
    require(
        metadata.get("artifact_kind") == "macos-local-testing-beta-dmg",
        "metadata artifact_kind is not the reviewed beta kind",
    )
    require(
        metadata.get("distribution_channel") == "github-prerelease-testing",
        "metadata distribution_channel is not GitHub prerelease testing",
    )
    require(metadata.get("local_testing_only") is True, "metadata must mark the artifact local_testing_only")
    require(metadata.get("production_release") is False, "metadata must not claim a production release")
    require(metadata.get("gatekeeper_acceptance_claimed") is False, "metadata must not claim Gatekeeper acceptance")
    require(metadata.get("source_tree_clean") is True, "metadata must bind a clean committed source tree")
    require(
        metadata.get("bundle_identifier") == "app.electronicmail.mac.beta",
        "metadata must use the isolated beta bundle identifier",
    )

    signing = metadata.get("signing")
    notarization = metadata.get("notarization")
    require(isinstance(signing, dict), "metadata signing policy is missing")
    require(isinstance(notarization, dict), "metadata notarization policy is missing")
    require(
        signing.get("mode") == "ad-hoc" and signing.get("developer_id") is False,
        "metadata must describe an ad-hoc non-Developer-ID beta",
    )
    require(signing.get("hardened_runtime") is True, "metadata must confirm Hardened Runtime")
    require(signing.get("library_validation") is False, "metadata must disclose disabled library validation")
    require(
        notarization.get("performed") is False and notarization.get("stapled") is False,
        "metadata must describe an unnotarized, unstapled beta",
    )

    dmg_policy = metadata.get("dmg")
    require(isinstance(dmg_policy, dict), "metadata DMG policy is missing")
    dmg_name = f"{stem}.dmg"
    require(dmg_policy.get("filename") == dmg_name, f"metadata DMG filename must be {dmg_name}")
    expected_dmg_digest = str(dmg_policy.get("sha256") or "")
    require(LOWER_SHA256.fullmatch(expected_dmg_digest) is not None, "metadata DMG SHA-256 is invalid")
    expected_dmg_size = dmg_policy.get("size_bytes")
    require(isinstance(expected_dmg_size, int) and expected_dmg_size > 0, "metadata DMG size_bytes must be positive")

    output_dir = metadata_path.parent
    dmg_path = output_dir / dmg_name
    notes_path = output_dir / f"{stem}-release-notes.md"
    checksums_path = output_dir / f"{stem}-SHA256SUMS.txt"
    for path, label in (
        (dmg_path, "beta DMG"),
        (notes_path, "beta release notes"),
        (checksums_path, "beta checksum manifest"),
    ):
        _require_regular_file(path, label)

    require(dmg_path.stat().st_size == expected_dmg_size, "DMG size differs from metadata")
    actual_dmg_digest = sha256(dmg_path)
    require(actual_dmg_digest == expected_dmg_digest, "DMG SHA-256 differs from metadata")

    expected_manifest_names = (dmg_path.name, metadata_path.name, notes_path.name)
    parsed_manifest: dict[str, str] = {}
    try:
        manifest_lines = checksums_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise PublicationError(f"beta checksum manifest is unreadable: {error}") from error
    require(
        len(manifest_lines) == len(expected_manifest_names),
        "checksum manifest must contain exactly the DMG, metadata, and notes",
    )
    for line in manifest_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        require(match is not None, "checksum manifest contains a noncanonical entry")
        digest, name = match.groups()
        require(name not in parsed_manifest, f"checksum manifest contains duplicate filename {name}")
        parsed_manifest[name] = digest
    require(
        set(parsed_manifest) == set(expected_manifest_names),
        "checksum manifest filenames differ from the exact beta artifact set",
    )
    for path in (dmg_path, metadata_path, notes_path):
        require(parsed_manifest[path.name] == sha256(path), f"checksum mismatch for {path.name}")

    try:
        notes = notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PublicationError(f"beta release notes are unreadable: {error}") from error
    for marker in (
        source_commit,
        expected_dmg_digest,
        dmg_path.name,
        metadata_path.name,
        checksums_path.name,
        "Unnotarized test build",
        "not a production release",
    ):
        require(marker in notes, f"beta release notes are missing exact marker: {marker}")

    return BetaArtifacts(
        metadata=metadata_path,
        dmg=dmg_path,
        notes=notes_path,
        checksums=checksums_path,
        version=version,
        build_number=build_number,
        source_commit=source_commit,
        tag=f"v{version}-beta.{build_number}",
        expected_sha256=tuple(sha256(path) for path in (dmg_path, metadata_path, notes_path, checksums_path)),
        expected_sizes=tuple(path.stat().st_size for path in (dmg_path, metadata_path, notes_path, checksums_path)),
    )


def run_gh(arguments: Sequence[str]) -> str:
    require(bool(arguments), "GitHub CLI command is missing")
    if arguments[0] == "api":
        require(
            list(arguments[1:3]) == ["--hostname", GITHUB_HOST]
            and list(arguments).count("--hostname") == 1,
            "GitHub API commands must be explicitly bound to github.com",
        )
    elif arguments[0] == "release":
        repository_flags = [index for index, value in enumerate(arguments) if value == "--repo"]
        require(len(repository_flags) == 1, "GitHub release commands must select one explicit repository")
        repository_index = repository_flags[0] + 1
        require(repository_index < len(arguments), "GitHub release repository is missing")
        repository = arguments[repository_index]
        require(
            repository.startswith(f"{GITHUB_HOST}/")
            and REPOSITORY.fullmatch(repository.removeprefix(f"{GITHUB_HOST}/")) is not None,
            "GitHub release commands must be explicitly bound to github.com",
        )
    else:
        raise PublicationError("unsupported GitHub CLI command")

    require(shutil.which("gh") is not None, "GitHub CLI is required")
    command = ["gh", *arguments]
    environment = os.environ.copy()
    # gh otherwise inherits GH_HOST and can silently redirect owner/repository
    # operations to a configured GitHub Enterprise host.
    environment["GH_HOST"] = GITHUB_HOST
    environment["GH_PROMPT_DISABLED"] = "1"
    environment["GH_PAGER"] = ""
    result = subprocess.run(
        command,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PublicationError(f"GitHub CLI command failed ({' '.join(command[:3])}): {detail}")
    return result.stdout


def verify_local_artifacts_unchanged(artifacts: BetaArtifacts) -> None:
    for path, expected_size, expected_digest in zip(
        artifacts.expected_paths,
        artifacts.expected_sizes,
        artifacts.expected_sha256,
        strict=True,
    ):
        _require_regular_file(path, "local beta artifact")
        require(path.stat().st_size == expected_size, f"local artifact size changed during publication: {path.name}")
        require(sha256(path) == expected_digest, f"local artifact SHA-256 changed during publication: {path.name}")


def _github_commit(repository: str, revision: str) -> str:
    response = run_gh(["api", "--hostname", GITHUB_HOST, f"repos/{repository}/commits/{revision}"])
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise PublicationError("GitHub commit response was not valid JSON") from error
    require(isinstance(payload, dict), "GitHub commit response must be an object")
    commit = str(payload.get("sha") or "")
    require(FULL_SHA.fullmatch(commit) is not None, "GitHub did not return a full commit SHA")
    return commit


def verify_source_commit(repository: str, artifacts: BetaArtifacts) -> None:
    remote = _github_commit(repository, artifacts.source_commit)
    require(remote == artifacts.source_commit, "metadata source_commit is not the exact remote GitHub commit")


def ensure_remote_tag(repository: str, artifacts: BetaArtifacts) -> None:
    response = run_gh(
        [
            "api",
            "--hostname",
            GITHUB_HOST,
            f"repos/{repository}/git/matching-refs/tags/{artifacts.tag}",
        ]
    )
    try:
        refs = json.loads(response)
    except json.JSONDecodeError as error:
        raise PublicationError("GitHub tag-list response was not valid JSON") from error
    require(isinstance(refs, list), "GitHub tag-list response must be an array")
    exact_ref = f"refs/tags/{artifacts.tag}"
    exact_matches = [item for item in refs if isinstance(item, dict) and item.get("ref") == exact_ref]
    require(len(exact_matches) <= 1, "GitHub returned duplicate exact tag references")
    if not exact_matches:
        run_gh(
            [
                "api",
                "--hostname",
                GITHUB_HOST,
                "--method",
                "POST",
                f"repos/{repository}/git/refs",
                "--field",
                f"ref={exact_ref}",
                "--field",
                f"sha={artifacts.source_commit}",
            ]
        )
    tag_commit = _github_commit(repository, artifacts.tag)
    require(tag_commit == artifacts.source_commit, "GitHub beta tag does not resolve to metadata source_commit")


def _release_state(repository: str, tag: str) -> dict[str, Any]:
    response = run_gh(
        [
            "release",
            "view",
            tag,
            "--repo",
            f"{GITHUB_HOST}/{repository}",
            "--json",
            "tagName,isDraft,isPrerelease,assets",
        ]
    )
    try:
        state = json.loads(response)
    except json.JSONDecodeError as error:
        raise PublicationError("GitHub release response was not valid JSON") from error
    require(isinstance(state, dict), "GitHub release response must be an object")
    return state


def verify_remote_release(repository: str, artifacts: BetaArtifacts, *, require_draft: bool) -> None:
    verify_local_artifacts_unchanged(artifacts)
    tag_commit = _github_commit(repository, artifacts.tag)
    require(tag_commit == artifacts.source_commit, "GitHub release tag does not resolve to metadata source_commit")

    state = _release_state(repository, artifacts.tag)
    require(state.get("tagName") == artifacts.tag, "GitHub release tag name differs from the expected beta tag")
    require(state.get("isDraft") is require_draft, "GitHub release draft state differs from the required state")
    require(state.get("isPrerelease") is True, "GitHub release must remain a prerelease")
    assets = state.get("assets")
    require(isinstance(assets, list), "GitHub release asset list is missing")
    asset_names = [item.get("name") for item in assets if isinstance(item, dict)]
    require(len(asset_names) == len(assets), "GitHub release asset list is malformed")
    require(len(asset_names) == len(set(asset_names)), "GitHub release contains duplicate asset names")
    require(
        set(asset_names) == set(artifacts.expected_names),
        "GitHub release assets differ from the exact beta artifact set",
    )

    with tempfile.TemporaryDirectory(prefix="electronic-mail-beta-release-") as temporary:
        download_dir = Path(temporary)
        command = [
            "release",
            "download",
            artifacts.tag,
            "--repo",
            f"{GITHUB_HOST}/{repository}",
            "--dir",
            str(download_dir),
        ]
        for name in artifacts.expected_names:
            command.extend(["--pattern", name])
        run_gh(command)
        downloaded = tuple(download_dir / name for name in artifacts.expected_names)
        for path in downloaded:
            _require_regular_file(path, "downloaded GitHub release asset")
        require(
            {path.name for path in download_dir.iterdir()} == set(artifacts.expected_names),
            "downloaded release directory contains unexpected files",
        )
        for local, remote, expected_size, expected_digest in zip(
            artifacts.expected_paths,
            downloaded,
            artifacts.expected_sizes,
            artifacts.expected_sha256,
            strict=True,
        ):
            require(remote.stat().st_size == expected_size, f"GitHub asset size differs for {local.name}")
            require(sha256(remote) == expected_digest, f"GitHub asset SHA-256 differs for {local.name}")


def create_draft(repository: str, artifacts: BetaArtifacts) -> None:
    verify_local_artifacts_unchanged(artifacts)
    verify_source_commit(repository, artifacts)
    # GitHub does not materialize a new tag for a draft release until publish.
    # Create or verify the exact lightweight ref first, then require gh to use it.
    ensure_remote_tag(repository, artifacts)
    verify_local_artifacts_unchanged(artifacts)
    run_gh(
        [
            "release",
            "create",
            artifacts.tag,
            "--repo",
            f"{GITHUB_HOST}/{repository}",
            "--target",
            artifacts.source_commit,
            "--verify-tag",
            "--draft",
            "--prerelease",
            "--latest=false",
            "--title",
            f"Electronic Mail {artifacts.version} ({artifacts.build_number}) beta",
            "--notes-file",
            str(artifacts.notes),
            *(str(path) for path in artifacts.expected_paths),
        ]
    )
    verify_remote_release(repository, artifacts, require_draft=True)


def publish_verified_draft(repository: str, artifacts: BetaArtifacts, confirmation: str) -> None:
    require(
        confirmation == artifacts.source_commit,
        "--confirm-source-commit must exactly match metadata source_commit",
    )
    verify_source_commit(repository, artifacts)
    # Download and byte-verify every remote asset immediately before the only
    # command that can make the draft public.
    verify_remote_release(repository, artifacts, require_draft=True)
    run_gh(
        [
            "release",
            "edit",
            artifacts.tag,
            "--repo",
            f"{GITHUB_HOST}/{repository}",
            "--verify-tag",
            "--target",
            artifacts.source_commit,
            "--prerelease",
            "--latest=false",
            "--draft=false",
        ]
    )
    verify_remote_release(repository, artifacts, require_draft=False)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create, verify, or explicitly publish an exact-SHA GitHub macOS beta prerelease"
    )
    parser.add_argument("action", choices=("create-draft", "verify-draft", "publish"))
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--repo", required=True, help="Exact GitHub owner/repository")
    parser.add_argument(
        "--confirm-source-commit",
        default="",
        help="Required only for publish; must equal the full SHA in metadata",
    )
    arguments = parser.parse_args(argv)
    if REPOSITORY.fullmatch(arguments.repo) is None:
        parser.error("--repo must be an exact owner/repository name")
    if arguments.action != "publish" and arguments.confirm_source_commit:
        parser.error("--confirm-source-commit is accepted only for publish")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        artifacts = load_beta_artifacts(arguments.metadata)
        if arguments.action == "create-draft":
            create_draft(arguments.repo, artifacts)
            print(f"Verified draft prerelease {artifacts.tag} for {artifacts.source_commit}; it remains unpublished.")
        elif arguments.action == "verify-draft":
            verify_source_commit(arguments.repo, artifacts)
            verify_remote_release(arguments.repo, artifacts, require_draft=True)
            print(f"Draft prerelease {artifacts.tag} and all exact assets are verified; it remains unpublished.")
        else:
            publish_verified_draft(arguments.repo, artifacts, arguments.confirm_source_commit)
            print(f"Published verified prerelease {artifacts.tag} for exact source commit {artifacts.source_commit}.")
    except PublicationError as error:
        print(f"beta publication error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
