from __future__ import annotations

import io
import json
import os
import re
import zipfile

from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from skill_evolution.models import SkillPackage


_VISIBILITY = {
    "public": "PUBLIC",
    "namespace-only": "NAMESPACE_ONLY",
    "private": "PRIVATE",
}
_MAX_ARCHIVE_FILES = 256
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class SkillHubClient:
    """Async client for SkillHub's native `/api/v1` HTTP API."""

    def __init__(
        self,
        registry_url: str,
        *,
        token: str = "",
        timeout_seconds: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_environment(cls) -> SkillHubClient:
        return cls(
            os.getenv("SKILLHUB_REGISTRY", "http://127.0.0.1:8080").strip(),
            token=(
                os.getenv("SKILLHUB_API_TOKEN", "")
                or os.getenv("SKILLHUB_TOKEN", "")
            ),
            timeout_seconds=float(os.getenv("SKILLHUB_TIMEOUT_SECONDS", "300")),
        )

    def _client(self) -> httpx.AsyncClient:
        if not self.registry_url:
            raise RuntimeError("SKILLHUB_REGISTRY is required")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.AsyncClient(
            base_url=self.registry_url,
            headers=headers,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=self.transport,
        )

    @staticmethod
    def _error(response: httpx.Response, operation: str) -> RuntimeError:
        detail = response.text.strip()
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("msg") or payload.get("message") or detail)
        except (json.JSONDecodeError, ValueError):
            pass
        if len(detail) > 500:
            detail = detail[:500] + "..."
        return RuntimeError(
            f"SkillHub {operation} failed: HTTP {response.status_code}: {detail}"
        )

    @staticmethod
    def _is_not_found(response: httpx.Response) -> bool:
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        message = str(payload.get("msg") or payload.get("message") or "").lower()
        return "skill not found" in message or "skill不存在" in message

    @classmethod
    def _data(cls, response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.is_error:
            raise cls._error(response, operation)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"SkillHub {operation} returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"SkillHub {operation} returned an invalid response")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"SkillHub {operation} response has no data object")
        return data

    @staticmethod
    def _unpack_archive(content: bytes) -> dict[str, bytes]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise RuntimeError("SkillHub download was not a valid ZIP package") from exc
        files: dict[str, bytes] = {}
        total_bytes = 0
        with archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > _MAX_ARCHIVE_FILES:
                raise RuntimeError("SkillHub package contains too many files")
            for member in members:
                normalized = member.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise RuntimeError(
                        f"SkillHub package contains an unsafe path: {member.filename}"
                    )
                total_bytes += member.file_size
                if total_bytes > _MAX_ARCHIVE_BYTES:
                    raise RuntimeError("SkillHub package is too large")
                files[path.as_posix()] = archive.read(member)

        if "SKILL.md" not in files:
            nested = [path for path in files if path.endswith("/SKILL.md")]
            roots = {path.rsplit("/", 1)[0] for path in nested}
            if len(roots) != 1:
                raise RuntimeError("SkillHub package does not contain one root SKILL.md")
            prefix = next(iter(roots)) + "/"
            files = {
                path[len(prefix) :]: value
                for path, value in files.items()
                if path.startswith(prefix)
            }
        if "SKILL.md" not in files:
            raise RuntimeError("SkillHub package does not contain SKILL.md")
        return files

    @staticmethod
    def _skill_markdown_with_version(content: bytes, version: str) -> bytes:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("SKILL.md must be UTF-8") from exc
        normalized = text.replace("\r\n", "\n")
        if not normalized.startswith("---\n"):
            raise RuntimeError("SKILL.md must start with YAML frontmatter")
        closing = normalized.find("\n---\n", 4)
        if closing < 0:
            raise RuntimeError("SKILL.md frontmatter is incomplete")
        frontmatter = normalized[4:closing]
        version_line = re.compile(r"(?m)^version:\s*.*$")
        if version_line.search(frontmatter):
            frontmatter = version_line.sub(f"version: {version}", frontmatter, count=1)
        else:
            frontmatter = frontmatter.rstrip() + f"\nversion: {version}"
        result = f"---\n{frontmatter}\n---\n" + normalized[closing + 5 :]
        return result.encode("utf-8")

    @classmethod
    def _build_archive(
        cls,
        package_path: Path,
        version: str,
    ) -> tuple[bytes, dict[str, bytes]]:
        if not package_path.is_dir():
            raise RuntimeError(f"Skill package directory was not found: {package_path}")
        files: dict[str, bytes] = {}
        for path in sorted(package_path.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(package_path).as_posix()
            pure_path = PurePosixPath(relative)
            if pure_path.is_absolute() or ".." in pure_path.parts:
                raise RuntimeError(f"Unsafe package path: {relative}")
            files[relative] = path.read_bytes()
        if "SKILL.md" not in files:
            raise RuntimeError("Skill package does not contain SKILL.md")
        files["SKILL.md"] = cls._skill_markdown_with_version(
            files["SKILL.md"], version
        )
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative, content in sorted(files.items()):
                archive.writestr(relative, content)
        return stream.getvalue(), files

    async def fetch_current(
        self,
        *,
        namespace: str,
        skill: str,
        destination: Path,
    ) -> SkillPackage | None:
        del destination  # Kept for compatibility with the service interface.
        path = f"/api/v1/skills/{namespace}/{skill}/resolve"
        async with self._client() as client:
            response = await client.get(path)
            if self._is_not_found(response):
                return None
            data = self._data(response, "resolve")
            version = str(data.get("version") or "").strip()
            if not version:
                raise RuntimeError("SkillHub resolve response has no version")
            download = await client.get(
                f"/api/v1/skills/{namespace}/{skill}/versions/{version}/download",
                headers={"Accept": "application/zip"},
            )
            if self._is_not_found(download):
                return None
            if download.is_error:
                raise self._error(download, "download")
        return SkillPackage(self._unpack_archive(download.content), version=version)

    async def publish(
        self,
        package_path: Path,
        *,
        namespace: str,
        skill: str,
        version: str,
        visibility: str,
    ) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError(
                "SKILLHUB_API_TOKEN is required for SkillHub publishing"
            )
        try:
            api_visibility = _VISIBILITY[visibility]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported SkillHub visibility: {visibility}") from exc
        archive, expected_files = self._build_archive(package_path, version)
        async with self._client() as client:
            response = await client.post(
                f"/api/v1/skills/{namespace}/publish",
                params={"confirmWarnings": "true"},
                data={"visibility": api_visibility},
                files={
                    "file": (
                        f"{skill}-{version}.zip",
                        archive,
                        "application/zip",
                    )
                },
            )
            data = self._data(response, "publish")
            returned = {
                "namespace": str(data.get("namespace") or ""),
                "slug": str(data.get("slug") or ""),
                "version": str(data.get("version") or ""),
            }
            expected = {
                "namespace": namespace,
                "slug": skill,
                "version": version,
            }
            if returned != expected:
                raise RuntimeError(
                    "SkillHub publish response did not match the requested package: "
                    f"expected={expected}, returned={returned}"
                )
            verify = await client.get(
                f"/api/v1/skills/{namespace}/{skill}/versions/{version}/download",
                headers={"Accept": "application/zip"},
            )
            if verify.is_error:
                raise self._error(verify, "post-publish verification")
        actual_files = self._unpack_archive(verify.content)
        if actual_files != expected_files:
            missing = sorted(set(expected_files) - set(actual_files))
            changed = sorted(
                path
                for path in set(expected_files) & set(actual_files)
                if expected_files[path] != actual_files[path]
            )
            raise RuntimeError(
                "SkillHub post-publish verification failed: "
                f"missing={missing}, changed={changed}"
            )
        return data
