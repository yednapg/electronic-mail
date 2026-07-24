from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/publish_macos_beta.py"
SPEC = importlib.util.spec_from_file_location("publish_macos_beta", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)


SOURCE_COMMIT = "a" * 40
REPOSITORY = "owner/electronic-mail"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_artifacts(directory: Path) -> Path:
    version = "0.1.0"
    build = "7"
    stem = f"ElectronicMail-Beta-{version}-{build}"
    dmg = directory / f"{stem}.dmg"
    metadata = directory / f"{stem}-metadata.json"
    notes = directory / f"{stem}-release-notes.md"
    checksums = directory / f"{stem}-SHA256SUMS.txt"

    dmg.write_bytes(b"deterministic-test-dmg")
    metadata_payload = {
        "schema_version": 1,
        "artifact_kind": "macos-local-testing-beta-dmg",
        "distribution_channel": "github-prerelease-testing",
        "local_testing_only": True,
        "production_release": False,
        "gatekeeper_acceptance_claimed": False,
        "version": version,
        "build_number": build,
        "source_commit": SOURCE_COMMIT,
        "source_tree_clean": True,
        "bundle_identifier": "app.electronicmail.mac.beta",
        "signing": {
            "mode": "ad-hoc",
            "developer_id": False,
            "hardened_runtime": True,
            "library_validation": False,
        },
        "notarization": {"performed": False, "stapled": False},
        "dmg": {
            "filename": dmg.name,
            "sha256": digest(dmg),
            "size_bytes": dmg.stat().st_size,
        },
    }
    metadata.write_text(json.dumps(metadata_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    notes.write_text(
        "\n".join(
            (
                "# Unnotarized test build — not a production release",
                f"Source commit: {SOURCE_COMMIT}",
                f"DMG: {dmg.name}",
                f"SHA-256: {digest(dmg)}",
                f"Metadata: {metadata.name}",
                f"Checksum manifest: {checksums.name}",
                "",
            )
        ),
        encoding="utf-8",
    )
    checksums.write_text(
        "".join(
            f"{digest(path)}  {path.name}\n"
            for path in (dmg, metadata, notes)
        ),
        encoding="ascii",
    )
    return metadata


class FakeGitHub:
    def __init__(self, artifacts: publisher.BetaArtifacts) -> None:
        self.artifacts = artifacts
        self.calls: list[list[str]] = []
        self.tag_exists = False
        self.release_exists = False
        self.draft = True
        self.remote_source_commit = artifacts.source_commit
        self.tag_commit = artifacts.source_commit
        self.extra_asset: str | None = None
        self.tamper_asset: str | None = None

    def __call__(self, arguments: list[str]) -> str:
        arguments = list(arguments)
        self.calls.append(arguments)
        api_arguments = arguments
        if arguments[:3] == ["api", "--hostname", publisher.GITHUB_HOST]:
            api_arguments = ["api", *arguments[3:]]

        if api_arguments[:2] == ["api", f"repos/{REPOSITORY}/commits/{self.artifacts.source_commit}"]:
            return json.dumps({"sha": self.remote_source_commit})
        if api_arguments[:2] == ["api", f"repos/{REPOSITORY}/commits/{self.artifacts.tag}"]:
            if not self.tag_exists:
                raise publisher.PublicationError("tag is absent")
            return json.dumps({"sha": self.tag_commit})
        if api_arguments[:2] == ["api", f"repos/{REPOSITORY}/git/matching-refs/tags/{self.artifacts.tag}"]:
            return json.dumps(
                [{"ref": f"refs/tags/{self.artifacts.tag}"}]
                if self.tag_exists
                else []
            )
        if api_arguments[:4] == ["api", "--method", "POST", f"repos/{REPOSITORY}/git/refs"]:
            self.tag_exists = True
            return json.dumps({"ref": f"refs/tags/{self.artifacts.tag}"})
        if arguments[:3] == ["release", "create", self.artifacts.tag]:
            self.release_exists = True
            self.draft = True
            return ""
        if arguments[:3] == ["release", "view", self.artifacts.tag]:
            if not self.release_exists:
                raise publisher.PublicationError("release is absent")
            names = list(self.artifacts.expected_names)
            if self.extra_asset:
                names.append(self.extra_asset)
            return json.dumps(
                {
                    "tagName": self.artifacts.tag,
                    "isDraft": self.draft,
                    "isPrerelease": True,
                    "assets": [{"name": name} for name in names],
                }
            )
        if arguments[:3] == ["release", "download", self.artifacts.tag]:
            output = Path(arguments[arguments.index("--dir") + 1])
            for source in self.artifacts.expected_paths:
                destination = output / source.name
                shutil.copyfile(source, destination)
                if self.tamper_asset == source.name:
                    destination.write_bytes(destination.read_bytes() + b"tampered")
            return ""
        if arguments[:3] == ["release", "edit", self.artifacts.tag]:
            self.draft = False
            return ""
        raise AssertionError(f"unexpected fake gh call: {arguments}")


class MacOSBetaPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.metadata = make_artifacts(self.directory)
        self.artifacts = publisher.load_beta_artifacts(self.metadata)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_all_calls_are_bound_to_github_com(self, calls: list[list[str]]) -> None:
        for call in calls:
            if call[0] == "api":
                self.assertEqual(call[1:3], ["--hostname", publisher.GITHUB_HOST])
            else:
                self.assertEqual(call[0], "release")
                self.assertEqual(
                    call[call.index("--repo") + 1],
                    f"{publisher.GITHUB_HOST}/{REPOSITORY}",
                )

    def test_local_policy_binds_metadata_notes_and_every_checksum(self) -> None:
        self.assertEqual(self.artifacts.source_commit, SOURCE_COMMIT)
        self.assertEqual(self.artifacts.tag, "v0.1.0-beta.7")
        self.assertEqual(
            set(self.artifacts.expected_names),
            {
                "ElectronicMail-Beta-0.1.0-7.dmg",
                "ElectronicMail-Beta-0.1.0-7-metadata.json",
                "ElectronicMail-Beta-0.1.0-7-release-notes.md",
                "ElectronicMail-Beta-0.1.0-7-SHA256SUMS.txt",
            },
        )

        self.artifacts.dmg.write_bytes(b"different bytes")
        with self.assertRaisesRegex(publisher.PublicationError, "DMG size differs|DMG SHA-256 differs"):
            publisher.load_beta_artifacts(self.metadata)

    def test_noncanonical_checksum_manifest_is_rejected(self) -> None:
        text = self.artifacts.checksums.read_text(encoding="ascii")
        self.artifacts.checksums.write_text(text.replace("  ElectronicMail", " *ElectronicMail", 1), encoding="ascii")
        with self.assertRaisesRegex(publisher.PublicationError, "noncanonical entry"):
            publisher.load_beta_artifacts(self.metadata)

    def test_create_draft_creates_and_verifies_exact_remote_tag(self) -> None:
        github = FakeGitHub(self.artifacts)
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            publisher.create_draft(REPOSITORY, self.artifacts)

        create = next(call for call in github.calls if call[:2] == ["release", "create"])
        self.assertIn("--draft", create)
        self.assertIn("--prerelease", create)
        self.assertIn("--latest=false", create)
        self.assertIn("--verify-tag", create)
        self.assertEqual(create[create.index("--target") + 1], SOURCE_COMMIT)
        for path in self.artifacts.expected_paths:
            self.assertIn(str(path), create)
        tag_create_index = next(
            index
            for index, call in enumerate(github.calls)
            if call[:6]
            == [
                "api",
                "--hostname",
                publisher.GITHUB_HOST,
                "--method",
                "POST",
                f"repos/{REPOSITORY}/git/refs",
            ]
        )
        release_create_index = github.calls.index(create)
        self.assertLess(tag_create_index, release_create_index)
        self.assertTrue(github.draft)
        self.assert_all_calls_are_bound_to_github_com(github.calls)

    def test_remote_source_mismatch_prevents_tag_or_release_creation(self) -> None:
        github = FakeGitHub(self.artifacts)
        github.remote_source_commit = "b" * 40
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            with self.assertRaisesRegex(publisher.PublicationError, "exact remote GitHub commit"):
                publisher.create_draft(REPOSITORY, self.artifacts)
        self.assertFalse(any(call[:2] == ["release", "create"] for call in github.calls))
        self.assertFalse(any("--method" in call and "POST" in call for call in github.calls))

    def test_local_artifact_change_prevents_every_remote_mutation(self) -> None:
        github = FakeGitHub(self.artifacts)
        self.artifacts.notes.write_text("changed after validation", encoding="utf-8")
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            with self.assertRaisesRegex(publisher.PublicationError, "changed during publication"):
                publisher.create_draft(REPOSITORY, self.artifacts)
        self.assertEqual(github.calls, [])

    def test_existing_tag_mismatch_prevents_release_creation(self) -> None:
        github = FakeGitHub(self.artifacts)
        github.tag_exists = True
        github.tag_commit = "b" * 40
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            with self.assertRaisesRegex(publisher.PublicationError, "does not resolve"):
                publisher.create_draft(REPOSITORY, self.artifacts)
        self.assertFalse(any(call[:2] == ["release", "create"] for call in github.calls))

    def test_publish_refuses_tampered_remote_asset_before_edit(self) -> None:
        github = FakeGitHub(self.artifacts)
        github.tag_exists = True
        github.release_exists = True
        github.tamper_asset = self.artifacts.dmg.name
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            with self.assertRaisesRegex(publisher.PublicationError, "size differs|SHA-256 differs"):
                publisher.publish_verified_draft(REPOSITORY, self.artifacts, SOURCE_COMMIT)
        self.assertFalse(any(call[:2] == ["release", "edit"] for call in github.calls))

    def test_publish_requires_explicit_full_source_confirmation(self) -> None:
        github = FakeGitHub(self.artifacts)
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            with self.assertRaisesRegex(publisher.PublicationError, "confirm-source-commit"):
                publisher.publish_verified_draft(REPOSITORY, self.artifacts, "a" * 39)
        self.assertEqual(github.calls, [])

    def test_publish_verifies_before_and_after_only_safe_edit(self) -> None:
        github = FakeGitHub(self.artifacts)
        github.tag_exists = True
        github.release_exists = True
        with mock.patch.object(publisher, "run_gh", side_effect=github):
            publisher.publish_verified_draft(REPOSITORY, self.artifacts, SOURCE_COMMIT)

        edit_index = next(index for index, call in enumerate(github.calls) if call[:2] == ["release", "edit"])
        downloads = [index for index, call in enumerate(github.calls) if call[:2] == ["release", "download"]]
        self.assertEqual(len(downloads), 2)
        self.assertLess(downloads[0], edit_index)
        self.assertGreater(downloads[1], edit_index)
        edit = github.calls[edit_index]
        for flag in ("--verify-tag", "--prerelease", "--latest=false", "--draft=false"):
            self.assertIn(flag, edit)
        self.assertEqual(edit[edit.index("--target") + 1], SOURCE_COMMIT)
        self.assertFalse(github.draft)
        self.assert_all_calls_are_bound_to_github_com(github.calls)

    def test_run_gh_overrides_inherited_enterprise_host_for_every_command(self) -> None:
        completed = mock.Mock(returncode=0, stdout="{}", stderr="")
        commands = (
            [
                "api",
                "--hostname",
                publisher.GITHUB_HOST,
                f"repos/{REPOSITORY}/commits/{SOURCE_COMMIT}",
            ],
            [
                "release",
                "view",
                self.artifacts.tag,
                "--repo",
                f"{publisher.GITHUB_HOST}/{REPOSITORY}",
            ],
        )
        with (
            mock.patch.dict("os.environ", {"GH_HOST": "enterprise.example"}),
            mock.patch.object(publisher.shutil, "which", return_value="/usr/local/bin/gh"),
            mock.patch.object(publisher.subprocess, "run", return_value=completed) as run,
        ):
            for command in commands:
                self.assertEqual(publisher.run_gh(command), "{}")

        self.assertEqual(run.call_count, len(commands))
        for call, arguments in zip(run.call_args_list, commands, strict=True):
            self.assertEqual(call.args[0], ["gh", *arguments])
            self.assertEqual(call.kwargs["env"]["GH_HOST"], publisher.GITHUB_HOST)

    def test_run_gh_rejects_commands_without_explicit_github_com_binding(self) -> None:
        cases = (
            ["api", f"repos/{REPOSITORY}/commits/{SOURCE_COMMIT}"],
            ["api", "--hostname", "enterprise.example", f"repos/{REPOSITORY}/commits/{SOURCE_COMMIT}"],
            ["release", "view", self.artifacts.tag, "--repo", REPOSITORY],
            ["release", "view", self.artifacts.tag, "--repo", f"enterprise.example/{REPOSITORY}"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(publisher.PublicationError, "github.com"):
                    publisher.run_gh(arguments)

    def test_invalid_repository_and_cross_action_confirmation_are_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                publisher.parse_arguments(
                    ["verify-draft", "--metadata", str(self.metadata), "--repo", "https://github.com/owner/repo"]
                )
            with self.assertRaises(SystemExit):
                publisher.parse_arguments(
                    [
                        "create-draft",
                        "--metadata",
                        str(self.metadata),
                        "--repo",
                        REPOSITORY,
                        "--confirm-source-commit",
                        SOURCE_COMMIT,
                    ]
                )

    def test_package_commands_and_docs_use_only_the_guarded_publisher(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        scripts = package["scripts"]
        self.assertEqual(
            scripts["release:macos:beta:github:create-draft"],
            "python3 scripts/publish_macos_beta.py create-draft",
        )
        self.assertEqual(
            scripts["release:macos:beta:github:verify-draft"],
            "python3 scripts/publish_macos_beta.py verify-draft",
        )
        self.assertEqual(
            scripts["release:macos:beta:github:publish"],
            "python3 scripts/publish_macos_beta.py publish",
        )
        self.assertIn("scripts/tests/test_publish_macos_beta.py", scripts["release:macos:beta:test"])

        guide = (ROOT / "docs/MACOS_BETA.md").read_text(encoding="utf-8")
        self.assertNotIn("gh release create", guide.replace("Do not call `gh release create` directly.", ""))
        self.assertIn("release:macos:beta:github:create-draft", guide)
        self.assertIn("release:macos:beta:github:verify-draft", guide)
        self.assertIn("release:macos:beta:github:publish", guide)
        self.assertIn("--confirm-source-commit", guide)


if __name__ == "__main__":
    unittest.main()
