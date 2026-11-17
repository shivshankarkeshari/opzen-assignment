"""Detached, resumable evaluation jobs: start, status and stop."""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from .checkpoints import atomic_json

ROOT = Path(__file__).resolve().parents[2]


def active(job):
    if not job:
        return False
    result = subprocess.run(
        ["ps", "-p", str(job["pid"]), "-o", "command="], capture_output=True, text=True
    )
    return "evaluation.deepeval_suite.run" in result.stdout and job["output"] in result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--max-cases", type=int, default=30)
    parser.add_argument("--integrity-only", action="store_true", default=None)
    parser.add_argument("--retry-errors", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = output / "job.json"
        job = json.loads(path.read_text()) if path.exists() else None
        running = active(job)
        if args.action == "stop":
            if running:
                os.killpg(job["pid"], signal.SIGTERM)
            print("Stop requested; completed metric checkpoints are retained.")
        elif args.action == "start" and not running:
            predictions = (
                args.predictions.resolve()
                if args.predictions
                else Path(job["predictions"])
                if job
                else None
            )
            if not predictions or not predictions.is_file():
                parser.error("--predictions must point to an existing JSONL file")
            command = [
                sys.executable,
                "-u",
                "-m",
                "evaluation.deepeval_suite.run",
                "--predictions",
                str(predictions),
                "--output",
                str(output),
                "--max-cases",
                str(args.max_cases),
                "--resume",
            ]
            integrity_only = args.integrity_only or (job or {}).get("integrity_only", False)
            if integrity_only:
                command.append("--integrity-only")
            retry_errors = (
                str(args.retry_errors.resolve())
                if args.retry_errors
                else (job or {}).get("retry_errors")
            )
            if retry_errors:
                command.extend(["--retry-errors", retry_errors])
            with (output / "run.log").open("a") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            job = {
                "pid": process.pid,
                "output": str(output),
                "predictions": str(predictions),
                "integrity_only": integrity_only,
                "retry_errors": retry_errors,
            }
            atomic_json(path, job)
            print(json.dumps({"state": "started", **job}))
        else:
            progress = output / "progress.json"
            state = json.loads(progress.read_text()) if progress.exists() else {}
            if not running and state.get("state") == "running":
                state["state"] = "interrupted"
                atomic_json(progress, state)
            print(
                json.dumps(
                    {"running": running, "pid": job["pid"] if job else None, "progress": state},
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
