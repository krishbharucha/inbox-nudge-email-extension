import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest


@pytest.fixture
def packaging(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/package-extension.py"
    spec = importlib.util.spec_from_file_location("extension_packager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "dist/extension"
    source.mkdir(parents=True)
    for name in (
        "manifest.json",
        "index.html",
        "background.js",
        "content.js",
        "icon.svg",
        "notification-icon.png",
    ):
        (source / name).write_text("{}" if name == "manifest.json" else "generated")
    return module.package, source, tmp_path / "dist/extension.zip"


def test_archive_excludes_credentials_backups_source_and_unrelated_scripts(packaging):
    package, source, output = packaging
    for name in (".env", "inbox.db.backup-private", "private.py", "push-sw.js", "content.js.map"):
        (source / name).write_text("private-data")
    (source / "assets").mkdir()
    (source / "assets/index.js").write_text("packaged-code")
    package(source, output)
    with ZipFile(output) as archive:
        assert "assets/index.js" in archive.namelist()
        assert len(archive.namelist()) == 7
        assert json.loads(archive.read("manifest.json")) == {}
        assert not any(b"private-data" in archive.read(name) for name in archive.namelist())


def test_archive_rejects_symlinks_even_if_they_point_to_an_allowed_asset(packaging):
    package, source, output = packaging
    (source / "assets").mkdir()
    (source / "assets/private.js").symlink_to(source / ".env")
    with pytest.raises(ValueError, match="Symlinks"):
        package(source, output)


def test_archive_cannot_read_or_write_outside_dist(packaging):
    package, source, output = packaging
    with pytest.raises(ValueError, match="inside dist"):
        package(source, output.parent.parent / "outside.zip")
