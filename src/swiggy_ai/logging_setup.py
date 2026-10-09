"""Structured JSON logs. Secrets and PII are scrubbed before anything is written."""
import json
import logging
import re
import sys
from datetime import UTC, datetime

# Order matters: emails first so the phone rule does not eat digits inside them.
_SCRUBBERS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "[api-key]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"), "[phone]"),
]


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update({k: scrub(str(v)) for k, v in extra.items()})
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
