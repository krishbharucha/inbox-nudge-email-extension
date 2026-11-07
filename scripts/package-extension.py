"""Package only generated extension assets; never source, credentials or databases."""

import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    dist = Path("dist").resolve()
    if source == dist or dist not in source.parents or destination.parent != dist:
        raise ValueError("Package source/output must be inside dist/")
    required = {
        "manifest.json",
        "index.html",
        "background.js",
        "content.js",
        "icon.svg",
        "notification-icon.png",
    }
    assets = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in extension packages")
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if str(relative) in required or (
            relative.parts[0] == "assets"
            and path.suffix in {".js", ".css", ".png", ".svg", ".woff", ".woff2"}
        ):
            assets.append((path, str(relative)))
    if not required <= {name for _, name in assets}:
        raise ValueError("Build the extension before packaging it")
    json.loads((source / "manifest.json").read_text())
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path, name in assets:
            archive.write(path, name)
    print(f"Packaged {len(assets)} generated assets: {destination.name}")


if __name__ == "__main__":
    package(*sys.argv[1:])
