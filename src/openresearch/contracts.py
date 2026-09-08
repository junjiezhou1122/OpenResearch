from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "openresearch.remote-job.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobRequest:
    job_id: str
    task_id: str
    repository: str
    commit_sha: str
    command: tuple[str, ...]
    protocol_id: str
    timeout_seconds: int
    created_at: str
    checkout_source: str | None = None

    def validate(self) -> None:
        for label, value in (("job_id", self.job_id), ("task_id", self.task_id), ("protocol_id", self.protocol_id)):
            if not _ID_RE.fullmatch(value):
                raise ValueError(f"invalid {label}: {value!r}")
        if not _SHA_RE.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be a full 40-character lowercase Git SHA")
        if not self.repository:
            raise ValueError("repository is required")
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise ValueError("command must be a non-empty argv array")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "job_id": self.job_id,
            "task_id": self.task_id,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "command": list(self.command),
            "protocol_id": self.protocol_id,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
            "checkout_source": self.checkout_source,
        }

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        payload = self.unsigned_payload()
        payload["request_sha256"] = sha256_json(payload)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "JobRequest":
        expected = payload.get("request_sha256")
        unsigned = {key: value for key, value in payload.items() if key != "request_sha256"}
        actual = sha256_json(unsigned)
        if expected != actual:
            raise ValueError(f"request hash mismatch: expected {expected!r}, calculated {actual}")
        if unsigned.get("schema") != SCHEMA:
            raise ValueError(f"unsupported request schema: {unsigned.get('schema')!r}")
        request = cls(
            job_id=unsigned["job_id"],
            task_id=unsigned["task_id"],
            repository=unsigned["repository"],
            commit_sha=unsigned["commit_sha"],
            command=tuple(unsigned["command"]),
            protocol_id=unsigned["protocol_id"],
            timeout_seconds=int(unsigned["timeout_seconds"]),
            created_at=unsigned["created_at"],
            checkout_source=unsigned.get("checkout_source"),
        )
        request.validate()
        return request


def append_ledger(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"recorded_at": utc_now(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
