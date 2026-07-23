from __future__ import annotations

import base64
import importlib.util
import json
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "verify-reproducibility.py"
SPEC = importlib.util.spec_from_file_location("verify_reproducibility", SCRIPT)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

VALID_SHA512_SRI = "sha512-" + base64.b64encode(bytes(64)).decode("ascii")


class ReproducibilityVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_root = verifier.ROOT
        verifier.ROOT = self.root
        (self.root / "web").mkdir()
        (self.root / "packages/types").mkdir(parents=True)
        (self.root / "backend").mkdir()
        (self.root / ".github/workflows").mkdir(parents=True)
        self.write_json("package.json", {"devDependencies": {"typescript": "5.9.3"}})
        self.write_json("web/package.json", {"dependencies": {"react": "19.2.7"}})
        self.write_json("packages/types/package.json", {"name": "types"})
        self.write_json(
            "package-lock.json",
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": {"typescript": "5.9.3"}},
                    "web": {"dependencies": {"react": "19.2.7"}},
                    "packages/types": {"name": "types"},
                    "node_modules/react": {"version": "19.2.7", "integrity": VALID_SHA512_SRI},
                    "node_modules/typescript": {"version": "5.9.3", "integrity": VALID_SHA512_SRI},
                },
            },
        )
        (self.root / ".nvmrc").write_text("22.22.0\n", encoding="utf-8")
        (self.root / ".npmrc").write_text("engine-strict=true\n", encoding="utf-8")
        (self.root / ".python-version").write_text("3.12.13\n", encoding="utf-8")
        (self.root / "backend/requirements.txt").write_text(
            "fastapi>=0.115.0,<1.0.0\n",
            encoding="utf-8",
        )
        (self.root / "backend/requirements.lock").write_text("fastapi==0.139.0\n", encoding="utf-8")
        pinned = "sha256:" + "a" * 64
        dockerfile = f"# syntax=docker/dockerfile:1.7@{pinned}\nFROM example.invalid/runtime:1@{pinned}\nRUN pip install --no-deps -r requirements.lock && python -m pip check\n"
        (self.root / "Dockerfile.web").write_text(dockerfile, encoding="utf-8")
        (self.root / "backend/Dockerfile").write_text(dockerfile, encoding="utf-8")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/bootstrap.sh").write_text(
            "pip install --no-deps -r backend/requirements.lock\n"
            '"$VENV_DIR/bin/pip" check\n',
            encoding="utf-8",
        )
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n  - uses: actions/checkout@{action}\n  - node-version: 22.22.0\n  - run: pip install --no-deps -r backend/requirements.lock\n  - run: pip install --no-deps -r backend/requirements.lock\n  - run: .venv/bin/pip check\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        verifier.ROOT = self.original_root
        self.temporary.cleanup()

    def write_json(self, relative: str, payload: dict) -> None:
        (self.root / relative).write_text(json.dumps(payload), encoding="utf-8")

    def test_valid_fixture_passes_manifest_and_lock_policies(self) -> None:
        verifier.verify_node_manifests()
        verifier.verify_python_lock()
        verifier.verify_container_inputs()
        verifier.verify_actions()

    def test_executing_python_must_match_python_version(self) -> None:
        with (
            patch.object(verifier.platform, "python_version", return_value="3.14.6"),
            self.assertRaisesRegex(verifier.ReproducibilityError, "executing Python must match"),
        ):
            verifier.verify_runtime_environment()

    def test_executing_node_must_match_nvmrc(self) -> None:
        with (
            patch.object(verifier.platform, "python_version", return_value="3.12.13"),
            patch.object(
                verifier.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="v26.4.0\n"),
            ),
            self.assertRaisesRegex(verifier.ReproducibilityError, "executing Node must match"),
        ):
            verifier.verify_runtime_environment()

    def test_local_venv_provenance_must_match_python_pin(self) -> None:
        (self.root / ".venv").mkdir()
        (self.root / ".venv/pyvenv.cfg").write_text(
            "home = /opt/python/3.14/bin\n"
            "version = 3.14.6\n"
            "executable = /opt/python/3.14/bin/python3.14\n"
            f"command = /opt/python/3.14/bin/python3.14 -m venv {self.root / '.venv'}\n",
            encoding="utf-8",
        )
        with (
            patch.object(verifier.platform, "python_version", return_value="3.12.13"),
            patch.object(
                verifier.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="v22.22.0\n"),
            ),
            self.assertRaisesRegex(verifier.ReproducibilityError, "pyvenv.cfg version must match"),
        ):
            verifier.verify_runtime_environment()

    def test_local_venv_without_provenance_is_rejected(self) -> None:
        (self.root / ".venv").mkdir()
        with (
            patch.object(verifier.platform, "python_version", return_value="3.12.13"),
            patch.object(
                verifier.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout="v22.22.0\n"),
            ),
            self.assertRaisesRegex(verifier.ReproducibilityError, "without pyvenv.cfg provenance"),
        ):
            verifier.verify_runtime_environment()

    def test_local_venv_interpreter_must_match_its_recorded_provenance(self) -> None:
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").touch()
        (self.root / ".venv/pyvenv.cfg").write_text(
            "home = /opt/python/3.12/bin\n"
            "version = 3.12.13\n"
            "executable = /opt/python/3.12/bin/python3.12\n"
            f"command = /opt/python/3.12/bin/python3.12 -m venv {self.root / '.venv'}\n",
            encoding="utf-8",
        )
        with (
            patch.object(verifier.platform, "python_version", return_value="3.12.13"),
            patch.object(
                verifier.subprocess,
                "run",
                side_effect=(
                    SimpleNamespace(returncode=0, stdout="v22.22.0\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="Python 3.14.6\n", stderr=""),
                ),
            ),
            self.assertRaisesRegex(verifier.ReproducibilityError, "venv/bin/python must match"),
        ):
            verifier.verify_runtime_environment()

    def test_latest_direct_dependency_is_rejected(self) -> None:
        self.write_json("web/package.json", {"dependencies": {"react": "latest"}})
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        lock["packages"]["web"]["dependencies"]["react"] = "latest"
        self.write_json("package-lock.json", lock)
        with self.assertRaisesRegex(verifier.ReproducibilityError, "exact version"):
            verifier.verify_node_manifests()

    def test_manifest_lock_drift_is_rejected(self) -> None:
        self.write_json("web/package.json", {"dependencies": {"react": "19.2.6"}})
        with self.assertRaisesRegex(verifier.ReproducibilityError, "does not exactly match"):
            verifier.verify_node_manifests()

    def test_malformed_node_integrity_digest_is_rejected(self) -> None:
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        lock["packages"]["node_modules/react"]["integrity"] = "sha512-not-valid-base64!"
        self.write_json("package-lock.json", lock)
        with self.assertRaisesRegex(verifier.ReproducibilityError, "valid sha512 integrity digest"):
            verifier.verify_node_manifests()

    def test_node_integrity_digest_must_decode_to_sha512_length(self) -> None:
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        lock["packages"]["node_modules/react"]["integrity"] = (
            "sha512-" + base64.b64encode(bytes(32)).decode("ascii")
        )
        self.write_json("package-lock.json", lock)
        with self.assertRaisesRegex(verifier.ReproducibilityError, "valid sha512 integrity digest"):
            verifier.verify_node_manifests()

    def test_python_lock_must_include_every_direct_requirement(self) -> None:
        (self.root / "backend/requirements.txt").write_text(
            "fastapi>=0.115.0,<1.0.0\nuvicorn>=0.30.0,<1.0.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "missing direct package uvicorn"):
            verifier.verify_python_lock()

    def test_python_lock_must_satisfy_direct_requirement_bounds(self) -> None:
        (self.root / "backend/requirements.lock").write_text("fastapi==1.0.0\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.ReproducibilityError, "violates <1.0.0"):
            verifier.verify_python_lock()

    def test_python_lock_must_include_reviewed_extra_provider(self) -> None:
        (self.root / "backend/requirements.txt").write_text(
            "psycopg[binary]>=3.2.0,<4.0.0\n",
            encoding="utf-8",
        )
        (self.root / "backend/requirements.lock").write_text("psycopg==3.3.4\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.ReproducibilityError, "missing extra provider psycopg-binary"):
            verifier.verify_python_lock()

    def test_mutable_override_is_rejected(self) -> None:
        self.write_json(
            "package.json",
            {"devDependencies": {"typescript": "5.9.3"}, "overrides": {"postcss": "^8.5.10"}},
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "overrides.postcss must use an exact version"):
            verifier.verify_node_manifests()

    def test_python_runtime_install_must_disable_dependency_resolution(self) -> None:
        (self.root / "scripts/bootstrap.sh").write_text(
            "pip install -r backend/requirements.lock\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "must install requirements.lock with --no-deps"):
            verifier.verify_python_lock()

    def test_mutable_container_base_is_rejected(self) -> None:
        (self.root / "backend/Dockerfile").write_text(
            "# syntax=docker/dockerfile:1.7@sha256:" + "a" * 64 + "\nFROM python:3.12.13-slim\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "mutable base image"):
            verifier.verify_container_inputs()

    def test_lowercase_indented_mutable_container_base_is_rejected(self) -> None:
        (self.root / "backend/Dockerfile").write_text(
            "# syntax=docker/dockerfile:1.7@sha256:"
            + "a" * 64
            + "\n  from   --platform = linux/amd64   python:3.12.13-slim   as runtime\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "mutable base image"):
            verifier.verify_container_inputs()

    def test_case_insensitive_platform_and_named_stage_from_instructions_are_supported(self) -> None:
        pinned = "sha256:" + "a" * 64
        (self.root / "backend/Dockerfile").write_text(
            f"# syntax=docker/dockerfile:1.7@{pinned}\n"
            f"  from --platform=$BUILDPLATFORM example.invalid/build:1@{pinned} as BuildStage\n"
            "\tFrOm buildstage AS runtime\n"
            "RUN pip install --no-deps -r requirements.lock && python -m pip check\n",
            encoding="utf-8",
        )
        verifier.verify_container_inputs()

    def test_action_tag_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n  - uses: actions/checkout@v6\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "not commit-pinned"):
            verifier.verify_actions()

    def test_action_tag_with_whitespace_before_colon_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n  - uses : actions/checkout@v6\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "not commit-pinned"):
            verifier.verify_actions()

    def test_split_multiline_action_value_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n"
            "  - uses:\n"
            "      actions/checkout@main\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "uses must use a nonempty single-line scalar"):
            verifier.verify_actions()

    def test_quoted_uses_mapping_key_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            'steps:\n  - "uses": actions/checkout@main\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported quoted YAML mapping keys"):
            verifier.verify_actions()

    def test_flow_mapping_uses_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n  - { uses: actions/checkout@main }\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML flow mapping syntax"):
            verifier.verify_actions()

    def test_anchor_prefixed_flow_mapping_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n  - &mutable { uses: actions/checkout@main }\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML anchors, aliases, tags"):
            verifier.verify_actions()

    def test_block_scalar_does_not_hide_mutable_sibling_action(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n"
            "  - name: |\n"
            "      mutable checkout\n"
            "    uses: actions/checkout@main\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "not commit-pinned"):
            verifier.verify_actions()

    def test_block_scalar_with_indent_indicator_allows_pinned_sibling_action(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps:\n"
            "  - name: |2-\n"
            "      pinned checkout\n"
            f"    uses: actions/checkout@{action}\n",
            encoding="utf-8",
        )
        verifier.verify_actions()

    def test_alias_tag_and_merge_workflow_nodes_are_rejected(self) -> None:
        fixtures = (
            "defaults: &mutable\n  uses: actions/checkout@main\n",
            "steps:\n  - *mutable\n",
            "steps:\n  - !custom\n    uses: actions/checkout@main\n",
            "steps:\n  - <<: *mutable\n",
            "steps:\n  - << : *mutable\n",
        )
        for workflow_text in fixtures:
            with self.subTest(workflow_text=workflow_text):
                (self.root / ".github/workflows/quality.yml").write_text(
                    workflow_text,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML anchors, aliases, tags"):
                    verifier.verify_actions()

    def test_yaml_document_controls_are_rejected_from_supported_subset(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "--- { uses: actions/checkout@main }\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML directives or document markers"):
            verifier.verify_actions()

    def test_flow_sequence_containing_mapping_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "steps: [ { uses: actions/checkout@main } ]\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML flow mapping syntax"):
            verifier.verify_actions()

    def test_implicit_mapping_pairs_inside_flow_sequences_are_rejected(self) -> None:
        fixtures = (
            "steps: [ uses: actions/checkout@main ]\n",
            'steps: [ "uses": actions/checkout@main ]\n',
        )
        for workflow_text in fixtures:
            with self.subTest(workflow_text=workflow_text):
                (self.root / ".github/workflows/quality.yml").write_text(
                    workflow_text,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML flow mapping syntax"):
                    verifier.verify_actions()

    def test_scalar_flow_sequences_remain_supported(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "on:\n"
            "  push:\n"
            "    branches: [main, \"codex/**\", 'release:stable']\n",
            encoding="utf-8",
        )
        verifier.verify_actions()

    def test_mutable_workflow_service_image_is_rejected(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"jobs:\n  test:\n    services:\n      postgres:\n        image: postgres:17\n    steps:\n      - uses: actions/checkout@{action}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "service/container image is mutable"):
            verifier.verify_actions()

    def test_split_multiline_image_value_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "jobs:\n"
            "  test:\n"
            "    services:\n"
            "      postgres:\n"
            "        image:\n"
            "          postgres:latest\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "image must use a nonempty single-line scalar"):
            verifier.verify_actions()

    def test_mutable_workflow_image_with_whitespace_before_colon_is_rejected(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"jobs:\n  test:\n    services:\n      postgres:\n        image : postgres:17\n"
            f"    steps:\n      - uses: actions/checkout@{action}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "service/container image is mutable"):
            verifier.verify_actions()

    def test_quoted_image_mapping_key_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            '"image": postgres:latest\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported quoted YAML mapping keys"):
            verifier.verify_actions()

    def test_nested_image_flow_mapping_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "jobs:\n  test:\n    services:\n      postgres: { image: postgres:latest }\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML flow mapping syntax"):
            verifier.verify_actions()

    def test_mutable_job_container_short_form_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "jobs:\n  test:\n    container: node:latest\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "job container image is mutable"):
            verifier.verify_actions()

    def test_split_multiline_job_container_value_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "jobs:\n"
            "  test:\n"
            "    container:\n"
            "      node:latest\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "container long form must define one direct nonempty image"):
            verifier.verify_actions()

    def test_digest_pinned_job_container_short_form_is_supported(self) -> None:
        pinned = "sha256:" + "a" * 64
        (self.root / ".github/workflows/quality.yml").write_text(
            f"jobs:\n  test:\n    container: \"node:22@{pinned}\" # immutable job runtime\n",
            encoding="utf-8",
        )
        verifier.verify_actions()

    def test_digest_pinned_job_container_long_form_is_supported(self) -> None:
        pinned = "sha256:" + "a" * 64
        (self.root / ".github/workflows/quality.yml").write_text(
            f"jobs:\n"
            f"  test:\n"
            f"    container:\n"
            f"      credentials:\n"
            f"        username: ci-user\n"
            f"      image: node:22@{pinned}\n",
            encoding="utf-8",
        )
        verifier.verify_actions()

    def test_explicit_yaml_mapping_key_syntax_is_rejected(self) -> None:
        (self.root / ".github/workflows/quality.yml").write_text(
            "? uses\n: actions/checkout@main\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "unsupported YAML explicit mapping-key syntax"):
            verifier.verify_actions()

    def test_node_version_with_whitespace_before_colon_is_checked(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n  - uses: actions/setup-node@{action}\n    with:\n      node-version : 20.19.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "must pin Node 22.22.0"):
            verifier.verify_actions()

    def test_every_setup_python_version_must_match_python_version_file(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n"
            f"  - uses: actions/setup-python@{action}\n"
            "    with:\n"
            "      python-version: \"3.12.13\"\n"
            f"  - uses : actions/setup-python@{action}\n"
            "    with:\n"
            "      python-version : '3.11.9'\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "setup-python must pin Python 3.12.13"):
            verifier.verify_actions()

    def test_commented_python_version_does_not_satisfy_setup_python(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n"
            f"  - uses: actions/setup-python@{action}\n"
            "    with:\n"
            "      # python-version: \"3.12.13\"\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "setup-python must pin python-version"):
            verifier.verify_actions()

    def test_commented_node_version_does_not_satisfy_setup_node(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n"
            f"  - uses: actions/setup-node@{action}\n"
            "    with:\n"
            "      # node-version: 22.22.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "setup-node must pin node-version"):
            verifier.verify_actions()

    def test_block_scalar_text_does_not_satisfy_setup_runtime_pin(self) -> None:
        action = "a" * 40
        fixtures = (
            (
                f"steps:\n  - uses: actions/setup-node@{action}\n"
                "    name: |\n      node-version: 22.22.0\n",
                "setup-node must pin node-version",
            ),
            (
                f"steps:\n  - uses: actions/setup-python@{action}\n"
                "    name: |\n      python-version: 3.12.13\n",
                "setup-python must pin python-version",
            ),
        )
        for workflow_text, expected_error in fixtures:
            with self.subTest(workflow_text=workflow_text):
                (self.root / ".github/workflows/quality.yml").write_text(
                    workflow_text,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(verifier.ReproducibilityError, expected_error):
                    verifier.verify_actions()

    def test_bare_sequence_item_starts_a_new_runtime_step(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n"
            f"  - uses: actions/setup-node@{action}\n"
            "  -\n"
            "    node-version: 22.22.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.ReproducibilityError, "setup-node must pin node-version"):
            verifier.verify_actions()

    def test_runtime_pin_must_be_a_unique_direct_with_input(self) -> None:
        action = "a" * 40
        fixtures = (
            (
                f"steps:\n  - uses: actions/setup-node@{action}\n"
                "    env:\n      node-version: 22.22.0\n",
                "setup-node must pin node-version",
            ),
            (
                f"steps:\n  - uses: actions/setup-python@{action}\n"
                "    with:\n      cache-options:\n        python-version: 3.12.13\n",
                "setup-python must pin python-version",
            ),
            (
                f"steps:\n  - uses: actions/setup-node@{action}\n"
                "    with:\n"
                "      node-version: 22.22.0\n"
                "      node-version: ${{ matrix.node }}\n",
                "setup-node must pin node-version",
            ),
        )
        for workflow_text, expected_error in fixtures:
            with self.subTest(workflow_text=workflow_text):
                (self.root / ".github/workflows/quality.yml").write_text(
                    workflow_text,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(verifier.ReproducibilityError, expected_error):
                    verifier.verify_actions()

    def test_setup_runtime_pins_allow_safe_inline_comments(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            f"steps:\n"
            f"  - uses: actions/setup-node@{action}\n"
            "    with:\n"
            "      node-version : 22.22.0 # repository Node pin\n"
            f"  - uses: actions/setup-python@{action}\n"
            "    with:\n"
            "      python-version : \"3.12.13\" # repository Python pin\n",
            encoding="utf-8",
        )
        verifier.verify_actions()

    def test_quoted_scalars_expressions_and_block_scalar_contents_are_not_mapping_syntax(self) -> None:
        action = "a" * 40
        (self.root / ".github/workflows/quality.yml").write_text(
            "name: '\"uses\": actions/checkout@main'\n"
            "env:\n"
            "  SAMPLE: \"{ image: postgres:latest }\"\n"
            "  REF: ${{ github.ref }}\n"
            "steps:\n"
            f"  - uses: actions/checkout@{action}\n"
            "  - run: |\n"
            "      \"image\": postgres:latest\n"
            "      payload = {\"uses\": \"actions/checkout@main\"}\n",
            encoding="utf-8",
        )
        verifier.verify_actions()


if __name__ == "__main__":
    unittest.main()
