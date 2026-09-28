#!/usr/bin/env python3
"""
BARKLY DEPLOY
Automatic Git-based deployment daemon for Barkly Labs.

Pipeline:

    Git push
       ↓
    detect commit
       ↓
    fetch repository
       ↓
    build
       ↓
    QA
       ↓
    create release
       ↓
    atomic switch
       ↓
    current → release

A failed deployment never replaces the current live release.

Design principle:
Small tools. Clear stages. Safe publication.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_INTERVAL = 30

BUILD_COMMAND = ["npm", "run", "build"]

QA_COMMAND = [
    "python3",
    "barkly_qa.py",
    "http://localhost:4321/",
]


# ============================================================================
# LOGGING
# ============================================================================

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(
        f"[{timestamp()}] {message}",
        flush=True,
    )


# ============================================================================
# COMMAND EXECUTION
# ============================================================================

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
        print(
            result.stdout,
            end="",
        )

    if result.stderr:
        print(
            result.stderr,
            end="",
            file=sys.stderr,
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code "
            f"{result.returncode}: "
            f"{' '.join(command)}"
        )


# ============================================================================
# GIT
# ============================================================================

def current_commit(project: Path) -> str:

    result = run(
        ["git", "rev-parse", "HEAD"],
        project,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to determine current Git commit."
        )

    return result.stdout.strip()


def remote_commit(
    project: Path,
    branch: str,
) -> str:

    result = run(
        [
            "git",
            "ls-remote",
            "origin",
            f"refs/heads/{branch}",
        ],
        project,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Unable to check remote Git repository."
        )

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
        [
            "git",
            "fetch",
            "origin",
            branch,
        ],
        project,
    )

    run_checked(
        [
            "git",
            "checkout",
            branch,
        ],
        project,
    )

    run_checked(
        [
            "git",
            "reset",
            "--hard",
            f"origin/{branch}",
        ],
        project,
    )

    return current_commit(project)


# ============================================================================
# BUILD
# ============================================================================

def build(project: Path) -> None:

    log("Installing dependencies...")

    run_checked(
        ["npm", "ci"],
        project,
    )

    log("Building website...")

    run_checked(
        BUILD_COMMAND,
        project,
    )

    dist = project / "dist"

    if not dist.exists():
        raise RuntimeError(
            "Build completed but dist/ was not created."
        )

    log("Build completed successfully.")


# ============================================================================
# QA
# ============================================================================

def qa(project: Path) -> None:

    log("Running Barkly QA...")

    result = run(
        QA_COMMAND,
        project,
    )

    if result.stdout:
        print(
            result.stdout,
            end="",
        )

    if result.stderr:
        print(
            result.stderr,
            end="",
            file=sys.stderr,
        )

    if result.returncode != 0:
        raise RuntimeError(
            "Barkly QA failed."
        )

    log("QA passed.")


# ============================================================================
# RELEASE MANAGEMENT
# ============================================================================

def release_name(commit: str) -> str:

    now = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d-%H%M%S")

    short_commit = commit[:12]

    return f"{now}-{short_commit}"


def create_release(
    project: Path,
    releases_directory: Path,
    commit: str,
) -> Path:

    dist = project / "dist"

    if not dist.exists():
        raise RuntimeError(
            "Cannot create release: dist/ does not exist."
        )

    releases_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    release = (
        releases_directory
        / release_name(commit)
    )

    if release.exists():
        raise RuntimeError(
            f"Release already exists: {release}"
        )

    log(
        f"Creating release: {release.name}"
    )

    shutil.copytree(
        dist,
        release,
    )

    metadata = {
        "commit": commit,
        "created": timestamp(),
        "project": "Barkly Labs Website",
    }

    (release / ".barkly-release.json").write_text(
        json.dumps(
            metadata,
            indent=2,
        ),
        encoding="utf-8",
    )

    return release


# ============================================================================
# CURRENT RELEASE DETECTION
# ============================================================================

def deployed_commit(
    current_link: Path,
) -> str | None:
    """
    Return the Git commit currently published by /current.

    The current path is intentionally NOT resolved before this function.
    We need to preserve the symlink itself.
    """

    if not current_link.exists() and not current_link.is_symlink():
        return None

    if not current_link.is_symlink():
        log(
            f"WARNING: current path exists but is not a symlink: "
            f"{current_link}"
        )
        return None

    release = current_link.resolve()

    metadata_file = (
        release
        / ".barkly-release.json"
    )

    if not metadata_file.exists():
        log(
            "Current release has no .barkly-release.json metadata."
        )
        return None

    try:

        metadata = json.loads(
            metadata_file.read_text(
                encoding="utf-8"
            )
        )

        commit = metadata.get("commit")

        if not commit:
            return None

        return str(commit)

    except Exception as error:

        log(
            f"Unable to read current release metadata: {error}"
        )

        return None


# ============================================================================
# ATOMIC PUBLICATION
# ============================================================================

def publish(
    release: Path,
    current_link: Path,
) -> None:

    current_link.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_link = current_link.with_name(
        current_link.name + ".next"
    )

    if temporary_link.exists() or temporary_link.is_symlink():
        temporary_link.unlink()

    log(
        f"Preparing atomic switch → {release}"
    )

    temporary_link.symlink_to(
        release,
        target_is_directory=True,
    )

    os.replace(
        temporary_link,
        current_link,
    )

    log(
        f"Live site now points to: {release.name}"
    )


# ============================================================================
# RELEASE CLEANUP
# ============================================================================

def cleanup_releases(
    releases_directory: Path,
    keep: int,
) -> None:

    if not releases_directory.exists():
        return

    releases = sorted(
        [
            path
            for path in releases_directory.iterdir()
            if path.is_dir()
        ],
        key=lambda path: path.name,
        reverse=True,
    )

    for old_release in releases[keep:]:

        log(
            f"Removing old release: "
            f"{old_release.name}"
        )

        shutil.rmtree(
            old_release,
        )


# ============================================================================
# REPORTING
# ============================================================================

def write_report(
    project: Path,
    commit: str,
    status: str,
    release: Path | None = None,
    error: str | None = None,
) -> None:

    reports = (
        project
        / ".barkly"
        / "deployments"
    )

    reports.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = {
        "timestamp": timestamp(),
        "project": "Barkly Labs Website",
        "commit": commit,
        "status": status,
    }

    if release:
        report["release"] = release.name

    if error:
        report["error"] = error

    filename = (
        reports
        / (
            datetime.now(timezone.utc)
            .strftime("%Y%m%d-%H%M%S-%f")
            + ".json"
        )
    )

    filename.write_text(
        json.dumps(
            report,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================================
# DEPLOYMENT
# ============================================================================

def deploy(
    project: Path,
    branch: str,
    releases_directory: Path,
    current_link: Path,
    keep_releases: int,
    dry_run: bool = False,
) -> bool:

    local_commit = current_commit(
        project
    )

    remote = remote_commit(
        project,
        branch,
    )

    log(
        f"Local repository:  {local_commit}"
    )

    log(
        f"Remote repository: {remote}"
    )

    # ------------------------------------------------------------------------
    # Determine what is actually live.
    # ------------------------------------------------------------------------

    live_commit = deployed_commit(
        current_link
    )

    if live_commit:
        log(
            f"Live release commit: {live_commit}"
        )
    else:
        log(
            "No valid live release detected."
        )

    # ------------------------------------------------------------------------
    # The repository may already be current while the website is not
    # published yet. In that case we MUST still deploy.
    # ------------------------------------------------------------------------

    if (
        local_commit == remote
        and live_commit == remote
    ):

        log(
            "Repository and live release are already current."
        )

        log(
            "No deployment required."
        )

        return False

    if local_commit != remote:

        log(
            "New commit detected."
        )

    elif live_commit != remote:

        log(
            "Repository is current, "
            "but the live release is not."
        )

        log(
            "Initial publication or repair deployment required."
        )

    if dry_run:

        log(
            "DRY RUN: deployment would begin."
        )

        return True

    try:

        # --------------------------------------------------------------------
        # Synchronize repository only when it is behind the remote.
        # --------------------------------------------------------------------

        if local_commit != remote:

            commit = update_repository(
                project,
                branch,
            )

        else:

            commit = local_commit

        log(
            f"Preparing commit: {commit}"
        )

        # --------------------------------------------------------------------
        # Build
        # --------------------------------------------------------------------

        build(
            project,
        )

        # --------------------------------------------------------------------
        # QA
        # --------------------------------------------------------------------

        qa(
            project,
        )

        # --------------------------------------------------------------------
        # Create immutable release
        # --------------------------------------------------------------------

        release = create_release(
            project,
            releases_directory,
            commit,
        )

        # --------------------------------------------------------------------
        # Atomically make the new release live
        # --------------------------------------------------------------------

        publish(
            release,
            current_link,
        )

        # --------------------------------------------------------------------
        # Cleanup old releases
        # --------------------------------------------------------------------

        cleanup_releases(
            releases_directory,
            keep_releases,
        )

        # --------------------------------------------------------------------
        # Record successful deployment
        # --------------------------------------------------------------------

        write_report(
            project,
            commit,
            "success",
            release,
        )

        log("")
        log("========================================")
        log("DEPLOYMENT SUCCESSFUL")
        log(f"Commit:  {commit}")
        log(f"Release: {release.name}")
        log(f"Live:    {current_link}")
        log("========================================")
        log("")

        return True

    except Exception as error:

        log("")
        log("========================================")
        log("DEPLOYMENT FAILED")
        log(str(error))
        log("CURRENT RELEASE WAS NOT REPLACED")
        log("========================================")
        log("")

        write_report(
            project,
            remote,
            "failed",
            error=str(error),
        )

        return False


# ============================================================================
# WATCHER
# ============================================================================

def watch(
    project: Path,
    branch: str,
    releases_directory: Path,
    current_link: Path,
    interval: int,
    keep_releases: int,
    dry_run: bool,
) -> None:

    log("========================================")
    log("BARKLY DEPLOY")
    log("========================================")
    log("Status: watching")
    log(f"Project: {project}")
    log(f"Branch: {branch}")
    log(f"Interval: {interval}s")
    log(f"Releases kept: {keep_releases}")
    log(f"Current: {current_link}")
    log("========================================")

    while True:

        try:

            deploy(
                project=project,
                branch=branch,
                releases_directory=releases_directory,
                current_link=current_link,
                keep_releases=keep_releases,
                dry_run=dry_run,
            )

        except KeyboardInterrupt:

            log(
                "Barkly Deploy stopped."
            )

            break

        except Exception as error:

            log(
                f"Watcher error: {error}"
            )

        time.sleep(
            interval
        )


# ============================================================================
# CLI
# ============================================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Barkly Labs automatic "
            "website deployment daemon."
        )
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
        "--releases",
        type=Path,
        required=True,
        help="Directory containing releases.",
    )

    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="Symlink representing the live website.",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Seconds between checks.",
    )

    parser.add_argument(
        "--keep",
        type=int,
        default=5,
        help="Number of releases to retain.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Deploy once and exit.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect changes without deploying.",
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT call .resolve() on current.
    #
    # current is supposed to be the symlink itself.
    # ------------------------------------------------------------------------

    project = args.project.resolve()
    releases = args.releases.resolve()
    current = args.current.absolute()

    if not project.exists():
        raise SystemExit(
            f"Project does not exist: {project}"
        )

    if not (project / ".git").exists():
        raise SystemExit(
            f"Not a Git repository: {project}"
        )

    if args.keep < 1:
        raise SystemExit(
            "--keep must be at least 1."
        )

    if args.once:

        deploy(
            project=project,
            branch=args.branch,
            releases_directory=releases,
            current_link=current,
            keep_releases=args.keep,
            dry_run=args.dry_run,
        )

        return

    watch(
        project=project,
        branch=args.branch,
        releases_directory=releases,
        current_link=current,
        interval=args.interval,
        keep_releases=args.keep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()