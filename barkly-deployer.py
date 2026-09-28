#!/usr/bin/env python3
"""
BARKLY DEPLOY
Tiny automatic deployment daemon for Barkly Labs.

Watches a Git repository for changes and deploys the newest commit.

Pipeline:

    Check Git
        ↓
    New commit?
        ↓
    Fetch
        ↓
    Checkout
        ↓
    Install dependencies
        ↓
    Build
        ↓
    QA
        ↓
    Publish
        ↓
    Report

Design principle:
Small tools. Clear stages. Never destroy a working deployment.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL = 30

PROJECT_NAME = "Barkly Labs Website"

BUILD_COMMAND = ["npm", "run", "build"]

# Change this to the location of barkly_qa.py on the VPS.
QA_COMMAND = ["python3", "barkly_qa.py", "http://localhost:4321/"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def run(
    command: list[str],
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    log(f"$ {' '.join(command)}")

    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


def run_checked(
    command: list[str],
    cwd: Path | None = None,
) -> None:
    result = run(command, cwd)

    if result.stdout:
        print(result.stdout, end="")

    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: "
            f"{' '.join(command)}"
        )


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def current_commit(project: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], project)

    if result.returncode != 0:
        raise RuntimeError("Unable to determine current Git commit.")

    return result.stdout.strip()


def remote_commit(project: Path, branch: str) -> str:
    result = run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        project,
    )

    if result.returncode != 0:
        raise RuntimeError("Unable to check remote Git repository.")

    line = result.stdout.strip()

    if not line:
        raise RuntimeError(
            f"No remote commit found for branch '{branch}'."
        )

    return line.split()[0]


def update_repository(
    project: Path,
    branch: str,
) -> str:
    log("Fetching latest repository state...")

    run_checked(
        ["git", "fetch", "origin", branch],
        project,
    )

    run_checked(
        ["git", "checkout", branch],
        project,
    )

    run_checked(
        ["git", "reset", "--hard", f"origin/{branch}"],
        project,
    )

    return current_commit(project)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(project: Path) -> None:
    log("Building website...")

    run_checked(
        ["npm", "ci"],
        project,
    )

    run_checked(
        BUILD_COMMAND,
        project,
    )

    log("Build completed successfully.")


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------

def qa(project: Path) -> None:
    log("Running Barkly QA...")

    result = run(
        QA_COMMAND,
        project,
    )

    if result.stdout:
        print(result.stdout, end="")

    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError("Barkly QA failed.")

    log("QA passed.")


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------

def publish(
    project: Path,
    publish_directory: Path,
) -> None:
    dist = project / "dist"

    if not dist.exists():
        raise RuntimeError(
            f"Build output does not exist: {dist}"
        )

    publish_directory.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = publish_directory.with_name(
        publish_directory.name + ".next"
    )

    if temporary.exists():
        shutil.rmtree(temporary)

    log(f"Preparing publication directory: {temporary}")

    shutil.copytree(
        dist,
        temporary,
    )

    log("Replacing live website...")

    backup = publish_directory.with_name(
        publish_directory.name + ".previous"
    )

    if backup.exists():
        shutil.rmtree(backup)

    if publish_directory.exists():
        publish_directory.rename(backup)

    temporary.rename(publish_directory)

    if backup.exists():
        shutil.rmtree(backup)

    log("Website published successfully.")


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

def deploy(
    project: Path,
    branch: str,
    publish_directory: Path,
    dry_run: bool = False,
) -> bool:

    old_commit = current_commit(project)

    log(f"Current deployment commit: {old_commit}")

    new_commit = remote_commit(
        project,
        branch,
    )

    log(f"Remote commit: {new_commit}")

    if old_commit == new_commit:
        log("No new commit. Nothing to deploy.")
        return False

    log("New commit detected.")

    if dry_run:
        log("DRY RUN: deployment would begin here.")
        return True

    try:
        deployed_commit = update_repository(
            project,
            branch,
        )

        log(f"Preparing commit: {deployed_commit}")

        build(project)

        qa(project)

        publish(
            project,
            publish_directory,
        )

        write_report(
            project,
            deployed_commit,
            "success",
        )

        log("========================================")
        log("DEPLOYMENT SUCCESSFUL")
        log(f"Commit: {deployed_commit}")
        log("========================================")

        return True

    except Exception as error:
        log("========================================")
        log("DEPLOYMENT FAILED")
        log(str(error))
        log("Current published website was preserved.")
        log("========================================")

        write_report(
            project,
            new_commit,
            "failed",
            str(error),
        )

        return False


# ---------------------------------------------------------------------------
# Deployment report
# ---------------------------------------------------------------------------

def write_report(
    project: Path,
    commit: str,
    status: str,
    error: str | None = None,
) -> None:

    report_directory = project / ".barkly" / "deployments"
    report_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = {
        "timestamp": timestamp(),
        "project": PROJECT_NAME,
        "commit": commit,
        "status": status,
    }

    if error:
        report["error"] = error

    filename = (
        report_directory
        / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )

    filename.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------

def watch(
    project: Path,
    branch: str,
    publish_directory: Path,
    interval: int,
    dry_run: bool,
) -> None:

    log("========================================")
    log("BARKLY DEPLOY")
    log("========================================")
    log(f"Project: {PROJECT_NAME}")
    log(f"Repository: {project}")
    log(f"Branch: {branch}")
    log(f"Check interval: {interval}s")
    log("Status: watching")
    log("========================================")

    while True:
        try:
            deploy(
                project=project,
                branch=branch,
                publish_directory=publish_directory,
                dry_run=dry_run,
            )

        except Exception as error:
            log(f"Watcher error: {error}")

        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Barkly Labs automatic deployment daemon."
    )

    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Path to the Git repository.",
    )

    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch to watch.",
    )

    parser.add_argument(
        "--publish",
        type=Path,
        required=True,
        help="Directory served by the web server.",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Seconds between repository checks.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Check and deploy once, then exit.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect a new commit without deploying.",
    )

    args = parser.parse_args()

    project = args.project.resolve()
    publish_directory = args.publish.resolve()

    if not project.exists():
        raise SystemExit(
            f"Project directory does not exist: {project}"
        )

    if not (project / ".git").exists():
        raise SystemExit(
            f"Not a Git repository: {project}"
        )

    if args.once:
        deploy(
            project=project,
            branch=args.branch,
            publish_directory=publish_directory,
            dry_run=args.dry_run,
        )
        return

    watch(
        project=project,
        branch=args.branch,
        publish_directory=publish_directory,
        interval=args.interval,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()