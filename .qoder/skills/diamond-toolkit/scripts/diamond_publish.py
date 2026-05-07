#!/usr/bin/env python3
"""
Diamond publish helper for Codex skill usage.

Subcommands:
- build-payload: build a Diamond publish payload from CLI args
- validate: validate a Diamond publish payload JSON file locally
- is-exist: query whether a publish order already exists
- publish: submit a Diamond publish order
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PUBLISH_URL = "https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/publish"
IS_EXIST_URL = "https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/isExist"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TARGET_ENVS = 10
REQUIRED_FIELDS = [
    "dataId",
    "group",
    "appName",
    "targetEnvs",
    "content",
    "empId",
    "systemName",
]
ALLOWED_TYPES = {"json", "xml", "yaml", "text/html", "properties", "text"}


class ValidationError(Exception):
    pass


def read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("Payload root must be a JSON object.")
    return payload


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def normalize_target_envs(target_envs: list[Any]) -> list[str]:
    normalized: list[str] = []
    for item in target_envs:
        value = str(item).strip()
        if value == "center":
            value = "sh"
        if value:
            normalized.append(value)
    return normalized


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ValidationError(f"Missing required fields: {', '.join(missing)}")

    normalized = dict(payload)

    if not isinstance(normalized["dataId"], str) or not normalized["dataId"].strip():
        raise ValidationError("dataId must be a non-empty string.")
    if not isinstance(normalized["group"], str) or not normalized["group"].strip():
        raise ValidationError("group must be a non-empty string.")
    if not isinstance(normalized["appName"], str) or not normalized["appName"].strip():
        raise ValidationError("appName must be a non-empty string.")
    if not isinstance(normalized["content"], str):
        raise ValidationError("content must be a string.")
    if not isinstance(normalized["empId"], str) or not normalized["empId"].strip():
        raise ValidationError("empId must be a non-empty string.")
    if not isinstance(normalized["systemName"], str) or not normalized["systemName"].strip():
        raise ValidationError("systemName must be a non-empty string.")

    if not isinstance(normalized["targetEnvs"], list):
        raise ValidationError("targetEnvs must be an array.")
    normalized["targetEnvs"] = normalize_target_envs(normalized["targetEnvs"])
    if not normalized["targetEnvs"]:
        raise ValidationError("targetEnvs must contain at least one environment.")
    if len(normalized["targetEnvs"]) > MAX_TARGET_ENVS:
        raise ValidationError("targetEnvs must not exceed 10 items.")

    config_type = normalized.get("type")
    if config_type is not None:
        if not isinstance(config_type, str) or config_type not in ALLOWED_TYPES:
            raise ValidationError(
                "type must be one of: json, xml, yaml, text/html, properties, text"
            )

    callback_url = normalized.get("callbackUrl")
    if callback_url is not None and not isinstance(callback_url, str):
        raise ValidationError("callbackUrl must be a string when provided.")

    extra_params = normalized.get("extraParams")
    if extra_params is not None and not isinstance(extra_params, dict):
        raise ValidationError("extraParams must be an object when provided.")

    return normalized


def http_get_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def http_post_json(url: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def cmd_validate(args: argparse.Namespace) -> int:
    payload = read_json(args.payload_file)
    normalized = validate_payload(payload)
    print(json.dumps({"ok": True, "payload": normalized}, ensure_ascii=False, indent=2))
    return 0


def cmd_build_payload(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {
        "dataId": args.data_id,
        "group": args.group,
        "appName": args.app_name,
        "targetEnvs": args.target_envs,
        "content": read_text(args.content_file),
        "empId": args.emp_id,
        "systemName": args.system_name,
    }

    if args.type:
        payload["type"] = args.type
    if args.desc:
        payload["desc"] = args.desc
    if args.callback_url:
        payload["callbackUrl"] = args.callback_url
    if args.extra_params_file:
        payload["extraParams"] = read_json(args.extra_params_file)

    normalized = validate_payload(payload)
    print(json.dumps(normalized, ensure_ascii=False, indent=2))
    return 0


def cmd_is_exist(args: argparse.Namespace) -> int:
    query = urllib.parse.urlencode({"dataId": args.data_id, "group": args.group})
    url = f"{IS_EXIST_URL}?{query}"
    response = http_get_json(url, args.timeout)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    payload = read_json(args.payload_file)
    normalized = validate_payload(payload)
    response = http_post_json(PUBLISH_URL, normalized, args.timeout)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diamond publish helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-payload", help="Build a publish payload from args")
    build_parser.add_argument("--data-id", required=True, help="Diamond dataId")
    build_parser.add_argument("--group", required=True, help="Diamond group")
    build_parser.add_argument("--app-name", required=True, help="App name")
    build_parser.add_argument("--target-envs", required=True, nargs="+", help="Target envs")
    build_parser.add_argument("--content-file", required=True, help="Path to config content file")
    build_parser.add_argument("--emp-id", required=True, help="Employee id")
    build_parser.add_argument("--system-name", required=True, help="System name")
    build_parser.add_argument("--type", help="Config type")
    build_parser.add_argument("--desc", help="Config description")
    build_parser.add_argument("--callback-url", help="Callback URL")
    build_parser.add_argument("--extra-params-file", help="Path to extraParams JSON file")
    build_parser.set_defaults(func=cmd_build_payload)

    validate_parser = subparsers.add_parser("validate", help="Validate a publish payload file")
    validate_parser.add_argument("--payload-file", required=True, help="Path to publish payload JSON")
    validate_parser.set_defaults(func=cmd_validate)

    exist_parser = subparsers.add_parser("is-exist", help="Check whether a publish order exists")
    exist_parser.add_argument("--data-id", required=True, help="Diamond dataId")
    exist_parser.add_argument("--group", required=True, help="Diamond group")
    exist_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds")
    exist_parser.set_defaults(func=cmd_is_exist)

    publish_parser = subparsers.add_parser("publish", help="Publish a Diamond config payload")
    publish_parser.add_argument("--payload-file", required=True, help="Path to publish payload JSON")
    publish_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds")
    publish_parser.set_defaults(func=cmd_publish)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"HTTP {exc.code}",
                    "body": body,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
