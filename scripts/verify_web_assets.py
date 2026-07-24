#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys


TEXT_SUFFIXES = {
    ".cjs",
    ".css",
    ".env",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".less",
    ".md",
    ".mdx",
    ".mjs",
    ".sass",
    ".scss",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
FONT_SUFFIXES = {".dfont", ".eot", ".otf", ".ttc", ".ttf", ".woff", ".woff2"}
FONT_MAGICS = (b"OTTO", b"ttcf", b"true", b"typ1", b"wOFF", b"wOF2", b"\x00\x01\x00\x00")
FORBIDDEN_SOURCE_PATTERN = re.compile(
    r"sf[-_ ]*pro(?:[-_ ]*rounded)?|font[-_ ]*sf[-_ ]*pro|@font-face|\bFontFace\s*\(|data\s*:\s*font/|(?:T1RUTw|AAEAAA|d09GRg|d09GMg)[A-Za-z0-9+/=]{32,}",
    re.IGNORECASE,
)
DOCKERFILE_FRONTEND = (
    "docker/dockerfile:1.7@sha256:"
    "a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e"
)
NODE_IMAGE = (
    "node:22.22.0-bookworm-slim@sha256:"
    "dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94"
)


class WebAssetError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WebAssetError(message)


def _files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WebAssetError(f"deployable web tree contains a symbolic link: {path}")
        if path.is_file():
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_font(path: Path) -> bool:
    if path.suffix.lower() in FONT_SUFFIXES:
        return True
    with path.open("rb") as handle:
        head = handle.read(4096)
    if head.startswith(FONT_MAGICS):
        return True
    if len(head) >= 36 and head[34:36] == b"LP":  # EOT MagicNumber 0x504c, little-endian.
        return True
    if path.suffix.lower() == ".svg":
        if path.stat().st_size > 10 * 1024 * 1024:
            raise WebAssetError(f"deployable SVG is unexpectedly larger than 10 MiB: {path}")
        if re.search(br"<\s*(?:font|font-face)\b", path.read_bytes(), re.IGNORECASE):
            return True
    return False


def _source_files(web_dir: Path):
    excluded_roots = {".next", "font", "node_modules"}
    for path in web_dir.rglob("*"):
        relative = path.relative_to(web_dir)
        if relative.parts and relative.parts[0] in excluded_roots:
            continue
        if path.is_symlink():
            raise WebAssetError(f"public web source contains a symbolic link: {path}")
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith(".env"):
            yield path


def _verify_docker_boundary(root: Path) -> None:
    dockerignore = root / ".dockerignore"
    _require(dockerignore.is_file() and not dockerignore.is_symlink(), ".dockerignore is missing or unsafe")
    ignored_lines = [
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    _require("web/font" in ignored_lines, ".dockerignore must exclude web/font from the production build context")
    font_rule_index = max(index for index, line in enumerate(ignored_lines) if line == "web/font")
    _require(
        not any(line.startswith("!") for line in ignored_lines[font_rule_index + 1 :]),
        ".dockerignore must not contain a re-include rule after the final web/font exclusion",
    )

    dockerfile = root / "Dockerfile.web"
    _require(dockerfile.is_file() and not dockerfile.is_symlink(), "Dockerfile.web is missing or unsafe")
    logical_lines: list[str] = []
    pending = ""
    for raw_line in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not pending and line.startswith("#"):
            if line.startswith("# syntax="):
                logical_lines.append(line)
            continue
        continued = line.endswith("\\")
        piece = line[:-1].strip() if continued else line
        pending = f"{pending} {piece}".strip()
        if not continued:
            logical_lines.append(" ".join(pending.split()))
            pending = ""
    _require(not pending, "Dockerfile.web ends with an incomplete continuation")

    expected_dockerfile = (
        f"# syntax={DOCKERFILE_FRONTEND}",
        f"FROM {NODE_IMAGE} AS dependencies",
        "WORKDIR /app",
        "COPY package.json package-lock.json tsconfig.base.json ./",
        "COPY packages/types/package.json packages/types/tsconfig.json ./packages/types/",
        "COPY web/package.json web/tsconfig.json web/next.config.mjs ./web/",
        "RUN npm ci",
        "FROM dependencies AS build",
        "COPY packages ./packages",
        "COPY web ./web",
        "RUN npm run web:build",
        f"FROM {NODE_IMAGE} AS runtime",
        "ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=5173",
        "RUN groupadd --system electronicmail && useradd --system --gid electronicmail --home-dir /app electronicmail",
        "WORKDIR /app",
        "COPY --from=build --chown=electronicmail:electronicmail /app/web/.next/standalone ./",
        "COPY --from=build --chown=electronicmail:electronicmail /app/web/.next/static ./web/.next/static",
        "USER electronicmail",
        "EXPOSE 5173",
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD node -e \"fetch('http://127.0.0.1:' + process.env.PORT + '/healthz').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))\"",
        'CMD ["node", "web/server.js"]',
    )
    _require(
        tuple(logical_lines) == expected_dockerfile,
        "Dockerfile.web differs from the audited fail-closed instruction allowlist",
    )

    runtime_indexes = [
        index
        for index, line in enumerate(logical_lines)
        if re.fullmatch(r"FROM\s+\S+\s+AS\s+runtime", line, re.IGNORECASE)
    ]
    _require(len(runtime_indexes) == 1, "Dockerfile.web must define exactly one named runtime stage")
    runtime_lines = logical_lines[runtime_indexes[0] :]
    expected = (
        f"FROM {NODE_IMAGE} AS runtime",
        "ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=5173",
        "RUN groupadd --system electronicmail && useradd --system --gid electronicmail --home-dir /app electronicmail",
        "WORKDIR /app",
        "COPY --from=build --chown=electronicmail:electronicmail /app/web/.next/standalone ./",
        "COPY --from=build --chown=electronicmail:electronicmail /app/web/.next/static ./web/.next/static",
        "USER electronicmail",
        "EXPOSE 5173",
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD node -e \"fetch('http://127.0.0.1:' + process.env.PORT + '/healthz').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))\"",
        'CMD ["node", "web/server.js"]',
    )
    _require(tuple(runtime_lines) == expected, "Dockerfile.web runtime stage differs from the audited fail-closed instruction allowlist")


def _verify_source_boundary(web_dir: Path) -> None:
    for path in _source_files(web_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise WebAssetError(f"web source file is not valid UTF-8: {path}: {error}") from error
        if FORBIDDEN_SOURCE_PATTERN.search(content):
            raise WebAssetError(f"public web source contains a custom/Apple font reference: {path}")


def _forbidden_font_hashes(web_dir: Path) -> set[str]:
    return {
        _sha256(path)
        for path in (web_dir / "font").rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _verify_runtime_roots(runtime_roots: tuple[Path, ...], forbidden_hashes: set[str]) -> None:
    for runtime_root in runtime_roots:
        if not runtime_root.exists():
            continue
        _require(runtime_root.is_dir(), f"deployable web tree is not a directory: {runtime_root}")
        _require(not runtime_root.is_symlink(), f"deployable web tree is a symbolic link: {runtime_root}")
        for path in _files(runtime_root):
            if _looks_like_font(path):
                raise WebAssetError(f"deployable web tree contains a font binary: {path}")
            if forbidden_hashes and _sha256(path) in forbidden_hashes:
                raise WebAssetError(f"deployable web tree contains a renamed forbidden font: {path}")


def validate_web_assets(root: Path) -> None:
    root = root.resolve()
    web_dir = root / "web"
    static_dir = web_dir / ".next" / "static"
    standalone_dir = web_dir / ".next" / "standalone"
    _require((web_dir / ".next" / "BUILD_ID").is_file(), "web production BUILD_ID is missing; run npm run web:build")
    _require(static_dir.is_dir(), "web production static output is missing; run npm run web:build")
    _require((standalone_dir / "web").is_dir(), "web standalone runtime is missing; run npm run web:build")
    _verify_docker_boundary(root)
    _verify_source_boundary(web_dir)
    _verify_runtime_roots(
        (web_dir / "public", static_dir, standalone_dir),
        _forbidden_font_hashes(web_dir),
    )


def validate_deployed_web_assets(root: Path, deployed_root: Path) -> None:
    root = root.resolve()
    _require(not deployed_root.is_symlink(), f"deployed /app extraction is a symbolic link: {deployed_root}")
    deployed_root = deployed_root.resolve()
    web_dir = root / "web"
    _require(deployed_root.is_dir(), f"deployed /app extraction is missing: {deployed_root}")
    _require((deployed_root / "web/server.js").is_file(), "deployed container is missing /app/web/server.js")
    _require((deployed_root / "web/.next/static").is_dir(), "deployed container is missing /app/web/.next/static")
    _verify_docker_boundary(root)
    _verify_source_boundary(web_dir)
    _verify_runtime_roots((deployed_root,), _forbidden_font_hashes(web_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify that the public web runtime uses system fonts only")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--deployed-root",
        type=Path,
        help="scan an exact extraction of the built container's /app tree instead of the host build",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.deployed_root is None:
            validate_web_assets(arguments.root)
        else:
            validate_deployed_web_assets(arguments.root, arguments.deployed_root)
    except (OSError, UnicodeError, WebAssetError) as error:
        print(f"web asset verification failed: {error}", file=sys.stderr)
        return 1
    target = "exact deployed container /app" if arguments.deployed_root is not None else "deployable web trees"
    print(f"Web asset verification passed: {target} contains no custom or Apple font binaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
