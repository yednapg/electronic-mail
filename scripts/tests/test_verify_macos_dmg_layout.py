from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_macos_dmg_layout import DMGLayoutError, validate_macos_dmg_layout  # noqa: E402


class MacOSDMGLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.mount = Path(self.temporary.name) / "mount"
        self.mount.mkdir()
        (self.mount / "Electronic Mail.app").mkdir()
        (self.mount / "Applications").symlink_to("/Applications")

    def test_exact_production_layout_passes(self) -> None:
        validate_macos_dmg_layout(self.mount, app_name="Electronic Mail.app")

    def test_exact_beta_layout_with_readme_passes(self) -> None:
        (self.mount / "Electronic Mail.app").rename(self.mount / "Electronic Mail Beta.app")
        (self.mount / "README-BETA.txt").write_text("test build", encoding="utf-8")
        validate_macos_dmg_layout(
            self.mount,
            app_name="Electronic Mail Beta.app",
            required_files=["README-BETA.txt"],
        )

    def test_unexpected_root_payload_fails(self) -> None:
        (self.mount / "Install.command").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(DMGLayoutError, "root contents differ"):
            validate_macos_dmg_layout(self.mount, app_name="Electronic Mail.app")

    def test_app_and_applications_links_fail_closed(self) -> None:
        (self.mount / "Applications").unlink()
        (self.mount / "Applications").symlink_to("/tmp")
        with self.assertRaisesRegex(DMGLayoutError, "must target /Applications"):
            validate_macos_dmg_layout(self.mount, app_name="Electronic Mail.app")

        (self.mount / "Applications").unlink()
        (self.mount / "Applications").symlink_to("/Applications")
        (self.mount / "Electronic Mail.app").rmdir()
        target = self.mount / "Elsewhere.app"
        target.mkdir()
        (self.mount / "Electronic Mail.app").symlink_to(target)
        with self.assertRaisesRegex(DMGLayoutError, "root contents differ|must not be a symbolic link"):
            validate_macos_dmg_layout(self.mount, app_name="Electronic Mail.app")

    def test_required_file_must_be_regular_and_names_cannot_escape(self) -> None:
        (self.mount / "README-BETA.txt").symlink_to("/dev/null")
        with self.assertRaisesRegex(DMGLayoutError, "must not be a symbolic link"):
            validate_macos_dmg_layout(
                self.mount,
                app_name="Electronic Mail.app",
                required_files=["README-BETA.txt"],
            )

        with self.assertRaisesRegex(DMGLayoutError, "root item name"):
            validate_macos_dmg_layout(self.mount, app_name="../Electronic Mail.app")


if __name__ == "__main__":
    unittest.main()
