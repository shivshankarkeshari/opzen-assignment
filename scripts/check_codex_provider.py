"""Bounded Codex connectivity probe; no project context or app configuration changes."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


def probe(model):
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="opzen-codex-smoke-", dir="/private/tmp") as directory:
        root = Path(directory)
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["OK"]}},
            "required": ["status"],
            "additionalProperties": False,
        }
        (root / "schema.json").write_text(json.dumps(schema))
        command = [
            "codex",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
        ]
        for feature in (
            "shell_tool",
            "apps",
            "plugins",
            "hooks",
            "browser_use",
            "computer_use",
            "image_generation",
            "view_image",
            "sleep_tool",
            "unbounded_connection_retries",
            "daemon_auto_start",
        ):
            command.extend(["--disable", feature])
        command.extend(
            [
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                'web_search="disabled"',
                "-c",
                'model_reasoning_effort="low"',
                "--output-schema",
                str(root / "schema.json"),
                "--output-last-message",
                str(root / "response.json"),
                "-m",
                model,
                "-",
            ]
        )
        allowed = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "CODEX_HOME",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env["CODEX_FORCE_AUTH_JSON"] = "1"
        report = {"requested_model": model, "force_auth_json_requested": True}
        try:
            process = subprocess.run(
                command,
                input='Reply only with {"status":"OK"}. Do not use tools or inspect files.',
                text=True,
                capture_output=True,
                cwd=root,
                env=env,
                timeout=90,
            )
            events = []
            for line in process.stdout.splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            report.update(
                exit_code=process.returncode,
                errors=[e for e in events if e.get("type") in ("error", "turn.failed")],
                item_types=sorted(
                    {e["item"]["type"] for e in events if isinstance(e.get("item"), dict)}
                ),
            )
            path = root / "response.json"
            answer = path.read_text().strip() if path.exists() else ""
            report["output_empty"] = not bool(answer)
            if answer:
                try:
                    report["response"] = json.loads(answer)
                except ValueError:
                    report["output_valid_json"] = False
            if process.returncode and not report["errors"]:
                report["stderr_summary"] = process.stderr[-1600:]
        except subprocess.TimeoutExpired:
            report["timeout"] = True
        report["seconds"] = round(time.monotonic() - start, 2)
        report["passed"] = report.get("exit_code") == 0 and report.get("response") == {
            "status": "OK"
        }
        # Preserve error diagnoses, never authentication tokens or private reasoning.
        cleaned = re.sub(
            r"\b(?:gsk_|sk-|Bearer\s+)[A-Za-z0-9_.-]{12,}", "[redacted]", json.dumps(report)
        )
        return json.loads(cleaned)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="openai/gpt-6.1-sol")
    parser.add_argument("--output", type=Path, default=Path("output/codex-smoke.json"))
    args = parser.parse_args()
    result = probe(args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
