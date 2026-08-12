from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, cast

import certifi


class ResourceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ResourceManager:
    def __init__(self, schema_dir: Path, cache_dir: Path, allow_network: bool) -> None:
        self.schema_dir = schema_dir
        self.cache_dir = cache_dir
        self.allow_network = allow_network
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock: dict[str, Any] = json.loads((schema_dir / "upstream-lock.json").read_text())

    def _locked(self, resource_id: str) -> dict[str, Any]:
        for item in self.lock["schemas"]:
            if item["id"] == resource_id:
                return cast(dict[str, Any], item)
        raise ResourceError(f"Resource {resource_id!r} is not pinned by upstream-lock.json")

    def fetch(self, item: dict[str, Any]) -> Path:
        suffix = ".zip" if item["kind"] == "archive" else ".xsd"
        target = self.cache_dir / f"{item['id']}{suffix}"
        if target.exists() and sha256_file(target) == item["sha256"]:
            return target
        if target.exists():
            target.unlink()
        if not self.allow_network:
            raise ResourceError(f"Locked resource is not in cache: {item['id']}")
        temporary = target.with_suffix(target.suffix + ".download")
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with (
                urllib.request.urlopen(item["url"], timeout=60, context=context) as response,
                temporary.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            actual = sha256_file(temporary)
            if actual != item["sha256"]:
                raise ResourceError(
                    f"SHA-256 mismatch for {item['id']}: expected {item['sha256']}, got {actual}"
                )
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def xsd_entrypoint(self, profile: str) -> Path:
        resource_id = "jats-journal-publishing-1.3-mathml2-xsd" if profile == "jats-1.3" else "bits-2.1-xsd"
        item = self._locked(resource_id)
        archive = self.fetch(item)
        extract_root = self.cache_dir / item["id"]
        marker = extract_root / ".verified"
        if not marker.exists() or marker.read_text() != item["sha256"]:
            if extract_root.exists():
                shutil.rmtree(extract_root)
            extract_root.mkdir(parents=True)
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    destination = (extract_root / info.filename).resolve()
                    if (
                        extract_root.resolve() not in destination.parents
                        and destination != extract_root.resolve()
                    ):
                        raise ResourceError("Unsafe path in upstream schema archive")
                bundle.extractall(extract_root)
            marker.write_text(item["sha256"])
        entrypoint = extract_root / cast(str, item["entrypoint"])
        if not entrypoint.is_file():
            raise ResourceError(f"Locked XSD entrypoint missing: {item['entrypoint']}")
        xml_item = self._locked("w3c-xml-namespace-xsd")
        xml_xsd = self.fetch(xml_item)
        # BITS imports the XML namespace without schemaLocation. libxml2 cannot
        # resolve a namespace-only import, so compile from a deterministic derived
        # copy whose sole change points that import to the locked xml.xsd. The
        # verified upstream extraction itself remains byte-for-byte untouched.
        if profile == "bits-2.1":
            derived_root = self.cache_dir / "bits-2.1-resolved"
            derived_marker = derived_root / ".verified"
            signature = f"resolved-v2:{item['sha256']}:{xml_item['sha256']}"
            if not derived_marker.exists() or derived_marker.read_text() != signature:
                if derived_root.exists():
                    shutil.rmtree(derived_root)
                shutil.copytree(extract_root, derived_root)
                for schema_path in derived_root.rglob("*.xsd"):
                    try:
                        text = schema_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        # Release archives may contain macOS resource-fork files.
                        continue
                    needle = 'namespace="http://www.w3.org/XML/1998/namespace"'
                    if (
                        needle in text
                        and "schemaLocation" not in text[text.index(needle) : text.index(needle) + 200]
                    ):
                        text = text.replace(needle, f'{needle} schemaLocation="{xml_xsd.as_uri()}"')
                        schema_path.write_text(text, encoding="utf-8")
                derived_marker.write_text(signature)
            entrypoint = derived_root / cast(str, item["entrypoint"])
        return cast(Path, entrypoint)

    def schxslt_pipeline(self) -> Path:
        item = {
            "id": "schxslt-1.10.1",
            "kind": "archive",
            "url": "https://repo1.maven.org/maven2/name/dmaus/schxslt/schxslt/1.10.1/schxslt-1.10.1.jar",
            "sha256": "4f4f21edab7b37f96ad59ae12a344d3510f1092ac46b6d81a4efa0120b73cb58",
        }
        archive = self.fetch(item)
        root = self.cache_dir / item["id"]
        entry = root / "xslt/2.0/pipeline-for-svrl.xsl"
        if not entry.exists():
            root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
        return entry
