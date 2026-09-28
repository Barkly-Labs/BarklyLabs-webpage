#!/usr/bin/env python3
"""
BARKLY DEPLOY
Human-friendly automatic deployment system for Barkly Labs.

Pipeline:

    Git
      ↓
    Server provisioning
      ↓
    npm ci
      ↓
    Astro build
      ↓
    Temporary QA server
      ↓
    Barkly QA
      ↓
    Immutable release
      ↓
    Atomic current symlink
      ↓
    Nginx reload

The live release is never replaced unless the new build passes QA.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BRANCH = "main"
DEFAULT_INTERVAL = 30
DEFAULT_KEEP_RELEASES = 5

BUILD_COMMAND = [
    "npm",
    "run",
    "build",
]

QA_SCRIPT = "Barkly-qa.py"
QA_HOST = "127.0.0.1"
QA_PORT = 4321
QA_URL = f"http://{QA_HOST}:{QA_PORT}/"

SERVER_SCRIPT = "barkly-server.py"

RELEASE_METADATA = ".barkly-release.json"

QA_START_TIMEOUT = 15
QA_START_POLL_INTERVAL = 0.25


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str = "") -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------

def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))

    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def current_commit(project: Path) -> str:
    result = run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
    )

    return result.stdout.strip()


def remote_commit(project: Path, branch: str) -> str:
    result = run(
        [
            "git",
            "ls-remote",
            "origin",
            f"refs/heads/{branch}",
        ],
        cwd=project,
    )

    output = result.stdout.strip()

    if not output:
        raise RuntimeError(
            f"Could not determine remote commit for origin/{branch}."
        )

    return output.split()[0]


def update_repository(
    project: Path,
    branch: str,
) -> str:
    log("Fetching latest repository state...")

    run(
        ["git", "fetch", "origin", branch],
        cwd=project,
    )

    run(
        ["git", "checkout", branch],
        cwd=project,
    )

    run(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=project,
    )

    commit = current_commit(project)

    log(f"Preparing commit: {commit}")

    return commit


# ---------------------------------------------------------------------------
# Live release detection
# ---------------------------------------------------------------------------

def deployed_commit(
    current_link: Path,
) -> str | None:
    if not current_link.exists() and not current_link.is_symlink():
        return None

    if not current_link.is_symlink():
        log(
            "WARNING: current path exists but is not a symlink: "
            f"{current_link}"
        )
        return None

    release = current_link.resolve()

    metadata_file = release / RELEASE_METADATA

    if not metadata_file.exists():
        log(
            "Current release has no "
            f"{RELEASE_METADATA} metadata."
        )
        return None

    try:
        metadata = json.loads(
            metadata_file.read_text(
                encoding="utf-8",
            )
        )

        commit = metadata.get("commit")

        if not commit:
            return None

        return str(commit)

    except Exception as error:
        log(
            "Unable to read current release metadata: "
            f"{error}"
        )
        return None


# ---------------------------------------------------------------------------
# Server provisioning
# ---------------------------------------------------------------------------

def provision_server(project: Path) -> None:
    """
    Run Barkly's server provisioning layer.

    This installs/configures Nginx and ensures the host
    is ready to serve /srv/barkly/current.
    """

    script = project / SERVER_SCRIPT

    if not script.exists():
        raise RuntimeError(
            f"Server provisioning script not found: {script}"
        )

    log("Provisioning Barkly server...")
    log(f"Running {SERVER_SCRIPT}")

    run(
        [
            "python3",
            SERVER_SCRIPT,
        ],
        cwd=project,
    )

    log("Barkly server provisioning completed.")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def install_dependencies(project: Path) -> None:
    log("Installing dependencies...")

    run(
        ["npm", "ci"],
        cwd=project,
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(project: Path) -> Path:
    log("Building website...")

    run(
        BUILD_COMMAND,
        cwd=project,
    )

    dist = project / "dist"

    if not dist.is_dir():
        raise RuntimeError(
            f"Build completed but dist directory does not exist: {dist}"
        )

    log("Build completed successfully.")

    return dist


# ---------------------------------------------------------------------------
# Temporary QA server
# ---------------------------------------------------------------------------

def port_available(
    host: str,
    port: int,
) -> bool:
    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as sock:
        sock.settimeout(0.5)

        result = sock.connect_ex(
            (host, port)
        )

        return result != 0


def start_qa_server(
    dist: Path,
) -> subprocess.Popen[str]:
    """
    Start a temporary static HTTP server against the freshly
    generated Astro dist directory.

    This guarantees Barkly QA tests exactly what was just built.
    """

    if not port_available(QA_HOST, QA_PORT):
        raise RuntimeError(
            f"QA port {QA_PORT} is already in use."
        )

    log(
        f"Starting temporary QA server: "
        f"http://{QA_HOST}:{QA_PORT}/"
    )

    log(
        f"Serving freshly built directory: {dist}"
    )

    process = subprocess.Popen(
        [
            "python3",
            "-m",
            "http.server",
            str(QA_PORT),
            "--bind",
            QA_HOST,
            "--directory",
            str(dist),
        ],
        cwd=dist,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    deadline = time.monotonic() + QA_START_TIMEOUT

    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = ""

            if process.stdout:
                output = process.stdout.read()

            raise RuntimeError(
                "Temporary QA server exited before "
                "becoming available.\n"
                f"{output}"
            )

        if not port_available(QA_HOST, QA_PORT):
            log("Temporary QA server is ready.")
            return process

        time.sleep(QA_START_POLL_INTERVAL)

    stop_qa_server(process)

    raise RuntimeError(
        "Timed out waiting for temporary QA server "
        f"on {QA_HOST}:{QA_PORT}."
    )


def stop_qa_server(
    process: subprocess.Popen[str] | None,
) -> None:
    if process is None:
        return

    if process.poll() is not None:
        return

    log("Stopping temporary QA server...")

    try:
        os.killpg(
            process.pid,
            signal.SIGTERM,
        )
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log(
            "QA server did not stop cleanly. "
            "Terminating it."
        )

        try:
            os.killpg(
                process.pid,
                signal.SIGKILL,
            )
        except ProcessLookupError:
            pass

        process.wait()


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------

def qa(project: Path) -> None:
    script = project / QA_SCRIPT

    if not script.exists():
        raise RuntimeError(
            f"Barkly QA script not found: {script}"
        )

    log("Running Barkly QA...")

    run(
        [
            "python3",
            QA_SCRIPT,
            QA_URL,
        ],
        cwd=project,
    )

    log("Barkly QA passed.")


# ---------------------------------------------------------------------------
# Release creation
# ---------------------------------------------------------------------------

def release_id(commit: str) -> str:
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d-%H%M%S")

    short_commit = commit[:8]

    return f"{timestamp}-{short_commit}"


def create_release(
    dist: Path,
    releases: Path,
    commit: str,
) -> Path:
    releases.mkdir(
        parents=True,
        exist_ok=True,
    )

    name = release_id(commit)
    release = releases / name

    log(f"Creating release: {release}")

    if release.exists():
        raise RuntimeError(
            f"Release already exists: {release}"
        )

    shutil.copytree(
        dist,
        release,
    )

    metadata = {
        "commit": commit,
        "release": name,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": str(dist),
    }

    metadata_file = release / RELEASE_METADATA

    metadata_file.write_text(
        json.dumps(
            metadata,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    log("Release created successfully.")

    return release


# ---------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------

def publish(
    release: Path,
    current_link: Path,
) -> None:
    """
    Atomically switch the live symlink to the new release.

    The old release remains untouched until the new symlink
    is successfully installed.
    """

    current_link.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_link = current_link.parent / (
        f".current-{os.getpid()}-{time.time_ns()}"
    )

    log(
        f"Publishing release: {release}"
    )

    if temporary_link.exists() or temporary_link.is_symlink():
        temporary_link.unlink()

    temporary_link.symlink_to(
        release,
        target_is_directory=True,
    )

    os.replace(
        temporary_link,
        current_link,
    )

    log(
        f"Current release is now: "
        f"{current_link.resolve()}"
    )


# ---------------------------------------------------------------------------
# Release cleanup
# ---------------------------------------------------------------------------

def cleanup_releases(
    releases: Path,
    keep: int,
) -> None:
    if not releases.exists():
        return

    candidates = [
        path
        for path in releases.iterdir()
        if path.is_dir()
    ]

    candidates.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    current_target: Path | None = None

    current_link = releases.parent / "current"

    if current_link.is_symlink():
        try:
            current_target = current_link.resolve()
        except OSError:
            current_target = None

    retained = 0

    for release in candidates:
        if current_target and release.resolve() == current_target:
            continue

        if retained < keep - 1:
            retained += 1
            continue

        log(
            f"Removing old release: {release}"
        )

        shutil.rmtree(
            release,
        )


# ---------------------------------------------------------------------------
# Nginx reload
# ---------------------------------------------------------------------------

def reload_nginx(project: Path) -> None:
    """
    Ask barkly-server.py to validate and reload Nginx.

    The server script owns Nginx configuration.
    """

    script = project / SERVER_SCRIPT

    log("Reloading Nginx through Barkly server manager...")

    # Importing the module would make this deployment system
    # depend on its implementation. Keep the boundary simple:
    # execute the server manager and let it perform provisioning.
    run(
        [
            "python3",
            "-c",
            (
                "import barkly_server; "
                "barkly_server.validate_nginx(); "
                "barkly_server.reload_nginx()"
            ),
        ],
        cwd=project,
    )


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

def deploy(
    project: Path,
    releases: Path,
    current: Path,
    branch: str,
    keep_releases: int,
) -> bool:

    log("=" * 40)
    log("BARKLY DEPLOY")
    log("=" * 40)

    # ---------------------------------------------------------------
    # Server
    # ---------------------------------------------------------------

    provision_server(project)

    # ---------------------------------------------------------------
    # Git state
    # ---------------------------------------------------------------

    local_commit = current_commit(project)
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

    live_commit = deployed_commit(
        current,
    )

    if live_commit:
        log(
            f"Live release commit: {live_commit}"
        )
    else:
        log("No valid live release detected.")

    if (
        local_commit == remote
        and live_commit == remote
    ):
        log(
            "Repository and live release are already current."
        )
        log("No deployment required.")
        return False

    if local_commit != remote:
        log("New commit detected.")
        commit = update_repository(
            project,
            branch,
        )
    else:
        log(
            "Repository is current, but the live release "
            "is not."
        )

        commit = local_commit

    # ---------------------------------------------------------------
    # Dependencies
    # ---------------------------------------------------------------

    install_dependencies(
        project,
    )

    # ---------------------------------------------------------------
    # Build
    # ---------------------------------------------------------------

    dist = build(
        project,
    )

    # ---------------------------------------------------------------
    # QA
    # ---------------------------------------------------------------

    qa_server: subprocess.Popen[str] | None = None

    try:
        qa_server = start_qa_server(
            dist,
        )

        qa(
            project,
        )

    finally:
        stop_qa_server(
            qa_server,
        )

    # ---------------------------------------------------------------
    # Release
    # ---------------------------------------------------------------

    release = create_release(
        dist,
        releases,
        commit,
    )

    # ---------------------------------------------------------------
    # Publish
    # ---------------------------------------------------------------

    publish(
        release,
        current,
    )

    # ---------------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------------

    cleanup_releases(
        releases,
        keep_releases,
    )

    # ---------------------------------------------------------------
    # Nginx
    # ---------------------------------------------------------------

    reload_nginx(
        project,
    )

    log("=" * 40)
    log("DEPLOYMENT SUCCESSFUL")
    log("=" * 40)

    log(
        f"Commit:  {commit}"
    )

    log(
        f"Release: {release}"
    )

    log(
        f"Current:  {current.resolve()}"
    )

    log("=" * 40)

    return True


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch(
    project: Path,
    releases: Path,
    current: Path,
    branch: str,
    interval: int,
    keep_releases: int,
) -> None:

    log("Status: watching")
    log(f"Project: {project}")
    log(f"Branch: {branch}")
    log(f"Interval: {interval}s")
    log(f"Releases kept: {keep_releases}")
    log(f"Current: {current}")

    log("=" * 40)

    while True:
        try:
            deploy(
                project=project,
                releases=releases,
                current=current,
                branch=branch,
                keep_releases=keep_releases,
            )

        except KeyboardInterrupt:
            log("Stopping Barkly Deploy.")
            return

        except Exception as error:
            log("")
            log("=" * 40)
            log("DEPLOYMENT FAILED")
            log(str(error))
            log("CURRENT RELEASE WAS NOT REPLACED")
            log("=" * 40)
            log("")

        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Barkly Labs deployment system."
    )

    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Path to the Git project.",
    )

    parser.add_argument(
        "--releases",
        type=Path,
        required=True,
        help="Directory containing immutable releases.",
    )

    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="Symlink pointing to the active release.",
    )

    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help="Git branch to deploy.",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Watch interval in seconds.",
    )

    parser.add_argument(
        "--keep-releases",
        type=int,
        default=DEFAULT_KEEP_RELEASES,
        help="Number of releases to retain.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Deploy once and exit.",
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    project = args.project.resolve()
    releases = args.releases.resolve()

    # IMPORTANT:
    # Do NOT call resolve() on current.
    #
    # current may itself be a symlink and must remain the symlink
    # that we atomically replace.
    current = args.current.absolute()

    if not project.exists():
        print(
            f"ERROR: project does not exist: {project}",
            file=sys.stderr,
        )
        return 1

    if args.once:
        try:
            deploy(
                project=project,
                releases=releases,
                current=current,
                branch=args.branch,
                keep_releases=args.keep_releases,
            )

            return 0

        except KeyboardInterrupt:
            log("Interrupted.")
            return 130

        except Exception as error:
            log("")
            log("=" * 40)
            log("DEPLOYMENT FAILED")
            log(str(error))
            log("CURRENT RELEASE WAS NOT REPLACED")
            log("=" * 40)
            return 1

    log("Status: watching")
    log(f"Project: {project}")
    log(f"Branch: {args.branch}")
    log(f"Interval: {args.interval}s")
    log(f"Releases kept: {args.keep_releases}")
    log(f"Current: {current}")
    log("=" * 40)

    watch(
        project=project,
        releases=releases,
        current=current,
        branch=args.branch,
        interval=args.interval,
        keep_releases=args.keep_releases,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())