from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_web_assets import (  # noqa: E402
    WebAssetError,
    validate_deployed_web_assets,
    validate_web_assets,
)


class WebAssetVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "web/app").mkdir(parents=True)
        (self.root / "web/font").mkdir(parents=True)
        (self.root / "web/public").mkdir(parents=True)
        (self.root / "web/.next/static").mkdir(parents=True)
        (self.root / "web/.next/standalone/web").mkdir(parents=True)
        (self.root / "web/.next/BUILD_ID").write_text("fixture", encoding="utf-8")
        (self.root / "web/app/layout.tsx").write_text("export default function Layout() {}", encoding="utf-8")
        (self.root / ".dockerignore").write_text("web/font\n", encoding="utf-8")
        (self.root / "Dockerfile.web").write_text(
            "\n".join(
                (
                    "# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e",
                    "FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS dependencies",
                    "WORKDIR /app",
                    "COPY package.json package-lock.json tsconfig.base.json ./",
                    "COPY packages/types/package.json packages/types/tsconfig.json ./packages/types/",
                    "COPY web/package.json web/tsconfig.json web/next.config.mjs ./web/",
                    "RUN npm ci",
                    "FROM dependencies AS build",
                    "COPY packages ./packages",
                    "COPY web ./web",
                    "RUN npm run web:build",
                    "FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS runtime",
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
            ),
            encoding="utf-8",
        )

    def test_clean_system_font_build_passes(self) -> None:
        validate_web_assets(self.root)

    def test_renamed_and_converted_font_magic_fails(self) -> None:
        for name, content in (("font.bin", b"OTTOfixture"), ("opaque.asset", b"wOF2fixture")):
            with self.subTest(name=name):
                path = self.root / "web/.next/static" / name
                path.write_bytes(content)
                with self.assertRaises(WebAssetError):
                    validate_web_assets(self.root)
                path.unlink()

    def test_known_font_hash_fails_even_without_magic_or_extension(self) -> None:
        content = b"fixture-known-font-without-magic"
        (self.root / "web/font/reference.otf").write_bytes(content)
        (self.root / "web/.next/standalone/web/renamed.bin").write_bytes(content)
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_font_outside_standalone_web_subdirectory_fails(self) -> None:
        path = self.root / "web/.next/standalone/node_modules/vendor/renamed.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"wOF2fixture")
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_mixed_case_source_reference_fails(self) -> None:
        (self.root / "web/app/globals.css").write_text("font-family: sF_pRo RoUnDeD", encoding="utf-8")
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_missing_build_and_docker_boundary_fail_closed(self) -> None:
        (self.root / "web/.next/BUILD_ID").unlink()
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        (self.root / "web/.next/BUILD_ID").write_text("fixture", encoding="utf-8")
        (self.root / ".dockerignore").write_text("node_modules\n", encoding="utf-8")
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_unverified_runtime_copy_and_symlink_fail(self) -> None:
        valid_dockerfile = (self.root / "Dockerfile.web").read_text(encoding="utf-8")
        with (self.root / "Dockerfile.web").open("a", encoding="utf-8") as handle:
            handle.write("\nCOPY --from=build /app/web/font ./web/font\n")
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        (self.root / "Dockerfile.web").write_text(
            valid_dockerfile.replace(
                "RUN npm run web:build",
                "RUN npm run web:build\nRUN curl https://fonts.invalid/font.woff2 -o /app/web/.next/standalone/renamed.bin",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        (self.root / "Dockerfile.web").write_text(
            valid_dockerfile.replace(
                "# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e",
                "# syntax=untrusted/frontend:latest",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        (self.root / "Dockerfile.web").write_text(
            valid_dockerfile.replace(
                "USER electronicmail",
                "RUN curl https://fonts.invalid/renamed.bin -o /app/renamed.bin\nUSER electronicmail",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        (self.root / "Dockerfile.web").write_text(
            valid_dockerfile + "\nFROM node:22 AS unverified-final\n",
            encoding="utf-8",
        )
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

        self.setUp()
        target = self.root / "web/.next/static/target.txt"
        target.write_text("safe", encoding="utf-8")
        (self.root / "web/.next/static/link.txt").symlink_to(target)
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_dockerignore_cannot_reinclude_font_assets(self) -> None:
        for reinclude in ("!web/font/renamed.bin", "!web/**"):
            with self.subTest(reinclude=reinclude):
                (self.root / ".dockerignore").write_text(f"web/font\n{reinclude}\n", encoding="utf-8")
                with self.assertRaises(WebAssetError):
                    validate_web_assets(self.root)

    def test_svg_font_after_large_preamble_fails(self) -> None:
        path = self.root / "web/public/renamed.svg"
        path.write_bytes(b" " * 5000 + b"<font id='embedded-font'></font>")
        with self.assertRaises(WebAssetError):
            validate_web_assets(self.root)

    def test_exact_deployed_container_tree_is_scanned(self) -> None:
        deployed_root = self.root / "container-app"
        (deployed_root / "web/.next/static").mkdir(parents=True)
        (deployed_root / "web/server.js").write_text("server", encoding="utf-8")
        validate_deployed_web_assets(self.root, deployed_root)

        (deployed_root / "node_modules/vendor/opaque.bin").parent.mkdir(parents=True)
        (deployed_root / "node_modules/vendor/opaque.bin").write_bytes(b"wOF2fixture")
        with self.assertRaises(WebAssetError):
            validate_deployed_web_assets(self.root, deployed_root)


if __name__ == "__main__":
    unittest.main()
