#!/usr/bin/env python3
"""
BARKLY DEPLOY

Human-friendly automatic deployment system for Barkly Labs.

Normal pipeline:

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
    Local Barkly QA
      ↓
    Immutable release
      ↓
    Atomic current symlink
      ↓
    Nginx reload
      ↓
    Public deployment QA
      ↓
    Deployment complete


Bootstrap pipeline:

    Server provisioning
      ↓
    Git
      ↓
    npm ci
      ↓
    Astro build
      ↓
    Immutable release
      ↓
    Atomic current symlink
      ↓
    Nginx reload
      ↓
    Deployment complete

Bootstrap mode intentionally skips QA for the initial server setup.

After the bootstrap deployment succeeds, watch mode automatically
returns to the normal QA-protected deployment pipeline.

Safety:

    - The live release is never replaced unless the new build
      passes local QA during normal deployments.
    - Public QA failure automatically rolls the live release back.
    - A missing / empty releases directory automatically triggers
      the first deployment filesystem setup.
    - A missing current symlink automatically triggers deployment.
    - Bootstrap mode is explicit and intended for first-time setup.
    - Bootstrap mode is automatically disabled after a successful
      deployment when running in watch mode.
    - Nginx is never reloaded without configuration validation.
    - Releases are immutable once created.
    - The current symlink is atomically replaced.
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

PUBLIC_IP = "97.107.133.215"

DOMAIN = "barklylabs.space"
WWW_DOMAIN = "www.barklylabs.space"

SERVER_SCRIPT = "barkly-server.py"

RELEASE_METADATA = ".barkly-release.json"

QA_START_TIMEOUT = 15
QA_START_POLL_INTERVAL = 0.25


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str = "") -> None:
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    print(
        f"[{timestamp}] {message}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------

def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """
    Run a command.

    stdout and stderr are captured so callers can inspect output,
    while the output is also printed immediately to the deploy log.
    """

    log(
        "$ " + " ".join(command)
    )

    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    if result.stdout:

        print(
            result.stdout,
            end="",
            flush=True,
        )

    if check and result.returncode != 0:

        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
        )

    return result


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def current_commit(
    project: Path,
) -> str:

    result = run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=project,
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
        cwd=project,
    )

    output = result.stdout.strip()

    if not output:

        raise RuntimeError(
            f"Could not determine remote commit "
            f"for origin/{branch}."
        )

    return output.split()[0]


def update_repository(
    project: Path,
    branch: str,
) -> str:

    log(
        "Fetching latest repository state..."
    )

    run(
        [
            "git",
            "fetch",
            "origin",
            branch,
        ],
        cwd=project,
    )

    run(
        [
            "git",
            "checkout",
            branch,
        ],
        cwd=project,
    )

    run(
        [
            "git",
            "reset",
            "--hard",
            f"origin/{branch}",
        ],
        cwd=project,
    )

    commit = current_commit(
        project
    )

    log(
        f"Preparing commit: {commit}"
    )

    return commit


# ---------------------------------------------------------------------------
# Release directory preparation
# ---------------------------------------------------------------------------

def prepare_release_directories(
    releases: Path,
    current: Path,
) -> bool:
    """
    Ensure the deployment filesystem exists.

    Returns True when this is a first deployment.

    A first deployment is detected when:

        - releases does not exist
        - releases exists but contains no releases
        - current does not exist
        - current exists but is not a symlink
        - current is a broken symlink
    """

    first_deployment = False

    log(
        f"Preparing release directory: {releases}"
    )

    releases.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        not current.exists()
        and not current.is_symlink()
    ):

        log(
            "No current release is configured."
        )

        first_deployment = True

    elif not current.is_symlink():

        log(
            "WARNING: current exists but is not a symlink."
        )

        first_deployment = True

    else:

        try:

            current.resolve(
                strict=True
            )

        except FileNotFoundError:

            log(
                "Current symlink is broken."
            )

            first_deployment = True

    release_directories = [
        path
        for path in releases.iterdir()
        if path.is_dir()
    ]

    if not release_directories:

        log(
            "No releases are currently installed."
        )

        first_deployment = True

    if first_deployment:

        log(
            "Barkly deployment filesystem requires "
            "an initial release."
        )

    return first_deployment


# ---------------------------------------------------------------------------
# Live release detection
# ---------------------------------------------------------------------------

def deployed_commit(
    current_link: Path,
) -> str | None:

    if (
        not current_link.exists()
        and not current_link.is_symlink()
    ):

        return None

    if not current_link.is_symlink():

        log(
            "WARNING: current path exists but is "
            f"not a symlink: {current_link}"
        )

        return None

    try:

        release = current_link.resolve(
            strict=True
        )

    except FileNotFoundError:

        log(
            "Current symlink points to a missing release."
        )

        return None

    metadata_file = (
        release / RELEASE_METADATA
    )

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

        commit = metadata.get(
            "commit"
        )

        if not commit:
            return None

        return str(
            commit
        )

    except Exception as error:

        log(
            "Unable to read current release metadata: "
            f"{error}"
        )

        return None


# ---------------------------------------------------------------------------
# Server provisioning
# ---------------------------------------------------------------------------

def provision_server(
    project: Path,
) -> None:

    script = (
        project / SERVER_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Server provisioning script not found: "
            f"{script}"
        )

    log(
        "Provisioning Barkly server..."
    )

    log(
        f"Running {SERVER_SCRIPT}"
    )

    run(
        [
            "python3",
            SERVER_SCRIPT,
        ],
        cwd=project,
    )

    log(
        "Barkly server provisioning completed."
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def install_dependencies(
    project: Path,
) -> None:

    log(
        "Installing dependencies..."
    )

    run(
        [
            "npm",
            "ci",
        ],
        cwd=project,
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(
    project: Path,
) -> Path:

    log(
        "Building website..."
    )

    run(
        BUILD_COMMAND,
        cwd=project,
    )

    dist = (
        project / "dist"
    )

    if not dist.is_dir():

        raise RuntimeError(
            "Build completed but dist directory "
            f"does not exist: {dist}"
        )

    log(
        "Build completed successfully."
    )

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

        sock.settimeout(
            0.5
        )

        result = sock.connect_ex(
            (
                host,
                port,
            )
        )

        return result != 0


def start_qa_server(
    dist: Path,
) -> subprocess.Popen[str]:
    """
    Start a temporary static HTTP server against the freshly
    generated Astro dist directory.
    """

    if not port_available(
        QA_HOST,
        QA_PORT,
    ):

        raise RuntimeError(
            f"QA port {QA_PORT} is already in use."
        )

    log(
        "Starting temporary QA server: "
        f"{QA_URL}"
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

    deadline = (
        time.monotonic()
        + QA_START_TIMEOUT
    )

    while time.monotonic() < deadline:

        if process.poll() is not None:

            output = ""

            if process.stdout:

                output = (
                    process.stdout.read()
                )

            raise RuntimeError(
                "Temporary QA server exited before "
                "becoming available.\n"
                f"{output}"
            )

        if not port_available(
            QA_HOST,
            QA_PORT,
        ):

            log(
                "Temporary QA server is ready."
            )

            return process

        time.sleep(
            QA_START_POLL_INTERVAL
        )

    stop_qa_server(
        process
    )

    raise RuntimeError(
        "Timed out waiting for temporary QA "
        f"server on {QA_HOST}:{QA_PORT}."
    )


def stop_qa_server(
    process: subprocess.Popen[str] | None,
) -> None:

    if process is None:
        return

    if process.poll() is not None:
        return

    log(
        "Stopping temporary QA server..."
    )

    try:

        os.killpg(
            process.pid,
            signal.SIGTERM,
        )

    except ProcessLookupError:

        return

    try:

        process.wait(
            timeout=5
        )

    except subprocess.TimeoutExpired:

        log(
            "QA server did not stop cleanly. "
            "Killing it."
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

def qa_local(
    project: Path,
) -> None:

    script = (
        project / QA_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Barkly QA script not found: {script}"
        )

    log(
        "Running local Barkly QA..."
    )

    run(
        [
            "python3",
            QA_SCRIPT,
            "--target",
            f"local={QA_URL}",
        ],
        cwd=project,
    )

    log(
        "Local Barkly QA passed."
    )


def qa_public(
    project: Path,
) -> None:
    """
    Test the published deployment from the public
    server IP while forcing the Barkly Host header.

    The URL remains the IP address.

    The Host header selects the Nginx virtual host.
    """

    script = (
        project / QA_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Barkly QA script not found: {script}"
        )

    log(
        "Running public IP Barkly QA..."
    )

    run(
        [
            "python3",
            QA_SCRIPT,
            "--target",
            f"public=http://{PUBLIC_IP}/|{DOMAIN}",
        ],
        cwd=project,
    )

    log(
        "Public IP Barkly QA passed."
    )


def qa_domain(
    project: Path,
) -> None:

    script = (
        project / QA_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Barkly QA script not found: {script}"
        )

    log(
        "Running domain Barkly QA..."
    )

    run(
        [
            "python3",
            QA_SCRIPT,
            "--target",
            f"domain=http://{DOMAIN}/|{DOMAIN}",
        ],
        cwd=project,
    )

    log(
        "Domain Barkly QA passed."
    )


def qa_www(
    project: Path,
) -> None:

    script = (
        project / QA_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Barkly QA script not found: {script}"
        )

    log(
        "Running WWW Barkly QA..."
    )

    run(
        [
            "python3",
            QA_SCRIPT,
            "--target",
            f"www=http://{WWW_DOMAIN}/|{WWW_DOMAIN}",
        ],
        cwd=project,
    )

    log(
        "WWW Barkly QA passed."
    )


def qa_public_deployment(
    project: Path,
) -> None:
    """
    Run all post-publication deployment checks.

    Local QA proves that the newly generated build works.

    Public QA proves that:

        Nginx
        current symlink
        public IP
        domain
        WWW domain

    all work after publication.
    """

    log(
        "=" * 40
    )

    log(
        "BARKLY PUBLIC DEPLOYMENT QA"
    )

    log(
        "=" * 40
    )

    qa_public(
        project
    )

    qa_domain(
        project
    )

    qa_www(
        project
    )

    log(
        "=" * 40
    )

    log(
        "PUBLIC DEPLOYMENT QA PASSED"
    )

    log(
        "=" * 40
    )


# ---------------------------------------------------------------------------
# Release creation
# ---------------------------------------------------------------------------

def release_id(
    commit: str,
) -> str:

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )

    short_commit = (
        commit[:8]
    )

    return (
        f"{timestamp}-{short_commit}"
    )


def create_release(
    dist: Path,
    releases: Path,
    commit: str,
) -> Path:

    releases.mkdir(
        parents=True,
        exist_ok=True,
    )

    name = release_id(
        commit
    )

    release = (
        releases / name
    )

    log(
        f"Creating release: {release}"
    )

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

    metadata_file = (
        release / RELEASE_METADATA
    )

    metadata_file.write_text(
        json.dumps(
            metadata,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    log(
        "Release created successfully."
    )

    return release


# ---------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------

def publish(
    release: Path,
    current_link: Path,
) -> Path | None:
    """
    Atomically switch the live symlink to the new release.

    Returns the previous release path when one exists.
    """

    current_link.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    previous_release: Path | None = None

    if current_link.is_symlink():

        try:

            previous_release = (
                current_link.resolve(
                    strict=True
                )
            )

        except FileNotFoundError:

            previous_release = None

    temporary_link = (
        current_link.parent
        /
        (
            f".current-{os.getpid()}-"
            f"{time.time_ns()}"
        )
    )

    log(
        f"Publishing release: {release}"
    )

    if (
        temporary_link.exists()
        or temporary_link.is_symlink()
    ):

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

    return previous_release


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback(
    previous_release: Path | None,
    current_link: Path,
) -> None:
    """
    Restore the previous live release.

    Used when post-publication QA fails.
    """

    if previous_release is None:

        log(
            "No previous release exists."
        )

        if (
            current_link.exists()
            or current_link.is_symlink()
        ):

            log(
                "Removing failed first deployment "
                "from current."
            )

            current_link.unlink()

        return

    if not previous_release.exists():

        raise RuntimeError(
            "Cannot rollback: previous release "
            f"does not exist: {previous_release}"
        )

    temporary_link = (
        current_link.parent
        /
        (
            f".rollback-{os.getpid()}-"
            f"{time.time_ns()}"
        )
    )

    log(
        f"Rolling back to: {previous_release}"
    )

    if (
        temporary_link.exists()
        or temporary_link.is_symlink()
    ):

        temporary_link.unlink()

    temporary_link.symlink_to(
        previous_release,
        target_is_directory=True,
    )

    os.replace(
        temporary_link,
        current_link,
    )

    log(
        f"Rollback complete: "
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

    current_link = (
        releases.parent / "current"
    )

    if current_link.is_symlink():

        try:

            current_target = (
                current_link.resolve(
                    strict=True
                )
            )

        except FileNotFoundError:

            current_target = None

    retained = 0

    for release in candidates:

        if (
            current_target
            and release.resolve()
            == current_target
        ):

            continue

        if retained < max(
            0,
            keep - 1,
        ):

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

def reload_nginx(
    project: Path,
) -> None:
    """
    Validate and reload Nginx through Barkly's
    server management layer.
    """

    script = (
        project / SERVER_SCRIPT
    )

    if not script.exists():

        raise RuntimeError(
            f"Server management script not found: "
            f"{script}"
        )

    log(
        "Reloading Nginx through "
        "Barkly server manager..."
    )

    run(
        [
            "python3",
            SERVER_SCRIPT,
            "--reload",
        ],
        cwd=project,
    )

    log(
        "Nginx reload completed successfully."
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
    skip_qa: bool = False,
) -> bool:

    log(
        "=" * 40
    )

    log(
        "BARKLY DEPLOY"
    )

    log(
        "=" * 40
    )

    # ---------------------------------------------------------------
    # Server
    # ---------------------------------------------------------------

    provision_server(
        project
    )

    # ---------------------------------------------------------------
    # Deployment filesystem
    # ---------------------------------------------------------------

    first_deployment = (
        prepare_release_directories(
            releases,
            current,
        )
    )

    if first_deployment:

        log(
            "This deployment is the initial "
            "Barkly release."
        )

    if skip_qa:

        log(
            "BOOTSTRAP MODE REQUESTED."
        )

        log(
            "QA will be skipped for this deployment only."
        )

    # ---------------------------------------------------------------
    # Git state
    # ---------------------------------------------------------------

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

    live_commit = deployed_commit(
        current,
    )

    if live_commit:

        log(
            f"Live release commit: {live_commit}"
        )

    else:

        log(
            "No valid live release detected."
        )

    # ---------------------------------------------------------------
    # Already current
    # ---------------------------------------------------------------

    if (
        not first_deployment
        and not skip_qa
        and local_commit == remote
        and live_commit == remote
    ):

        log(
            "Repository and live release "
            "are already current."
        )

        log(
            "No deployment required."
        )

        return False

    # ---------------------------------------------------------------
    # Repository update
    # ---------------------------------------------------------------

    if local_commit != remote:

        log(
            "New commit detected."
        )

        commit = update_repository(
            project,
            branch,
        )

    elif first_deployment:

        log(
            "Initial Barkly deployment detected."
        )

        commit = local_commit

    else:

        log(
            "Repository is current, but the "
            "live release is not."
        )

        commit = local_commit

    # ---------------------------------------------------------------
    # Dependencies
    # ---------------------------------------------------------------

    install_dependencies(
        project
    )

    # ---------------------------------------------------------------
    # Build
    # ---------------------------------------------------------------

    dist = build(
        project
    )

    # ---------------------------------------------------------------
    # LOCAL QA
    # ---------------------------------------------------------------

    if skip_qa:

        log(
            "=" * 40
        )

        log(
            "BARKLY BOOTSTRAP"
        )

        log(
            "=" * 40
        )

        log(
            "Skipping temporary QA server."
        )

        log(
            "Skipping local Barkly QA."
        )

        log(
            "Fresh build will be published directly."
        )

        log(
            "=" * 40
        )

    else:

        qa_server: subprocess.Popen[str] | None = None

        try:

            qa_server = start_qa_server(
                dist
            )

            qa_local(
                project
            )

        finally:

            stop_qa_server(
                qa_server
            )

    # ---------------------------------------------------------------
    # Local QA passed, or bootstrap explicitly skipped QA.
    # The release may now be created.
    # ---------------------------------------------------------------

    release = create_release(
        dist,
        releases,
        commit,
    )

    # ---------------------------------------------------------------
    # Publish
    # ---------------------------------------------------------------

    previous_release = publish(
        release,
        current,
    )

    # ---------------------------------------------------------------
    # Nginx
    # ---------------------------------------------------------------

    try:

        reload_nginx(
            project
        )

    except Exception:

        log(
            "Nginx reload failed."
        )

        log(
            "Rolling back published release..."
        )

        rollback(
            previous_release,
            current,
        )

        raise

    # ---------------------------------------------------------------
    # PUBLIC QA
    # ---------------------------------------------------------------

    if skip_qa:

        log(
            "=" * 40
        )

        log(
            "BOOTSTRAP DEPLOYMENT COMPLETE"
        )

        log(
            "Public deployment QA intentionally skipped."
        )

        log(
            "=" * 40
        )

    else:

        try:

            qa_public_deployment(
                project
            )

        except Exception as error:

            log(
                "=" * 40
            )

            log(
                "PUBLIC QA FAILED"
            )

            log(
                str(error)
            )

            log(
                "Rolling back live release..."
            )

            rollback(
                previous_release,
                current,
            )

            try:

                reload_nginx(
                    project
                )

            except Exception as reload_error:

                log(
                    "WARNING: Nginx reload after "
                    f"rollback failed: {reload_error}"
                )

            raise RuntimeError(
                "Public deployment QA failed. "
                "Live release was rolled back."
            ) from error

    # ---------------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------------

    cleanup_releases(
        releases,
        keep_releases,
    )

    # ---------------------------------------------------------------
    # Complete
    # ---------------------------------------------------------------

    log(
        "=" * 40
    )

    if skip_qa:

        log(
            "BOOTSTRAP DEPLOYMENT SUCCESSFUL"
        )

    else:

        log(
            "DEPLOYMENT SUCCESSFUL"
        )

    log(
        "=" * 40
    )

    log(
        f"Commit:   {commit}"
    )

    log(
        f"Release:  {release}"
    )

    log(
        f"Current:  {current.resolve()}"
    )

    log(
        "Public IP: "
        f"http://{PUBLIC_IP}/"
    )

    log(
        "Domain:    "
        f"http://{DOMAIN}/"
    )

    log(
        "WWW:       "
        f"http://{WWW_DOMAIN}/"
    )

    if skip_qa:

        log(
            "QA:       SKIPPED FOR BOOTSTRAP"
        )

    else:

        log(
            "QA:       PASSED"
        )

    log(
        "=" * 40
    )

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
    bootstrap: bool = False,
) -> None:

    log(
        "Status: watching"
    )

    log(
        f"Project: {project}"
    )

    log(
        f"Branch: {branch}"
    )

    log(
        f"Interval: {interval}s"
    )

    log(
        f"Releases kept: {keep_releases}"
    )

    log(
        f"Current: {current}"
    )

    if bootstrap:

        log(
            "Bootstrap mode: ENABLED"
        )

        log(
            "QA will be skipped for the first "
            "successful deployment only."
        )

    else:

        log(
            "Bootstrap mode: disabled"
        )

    log(
        "=" * 40
    )

    while True:

        try:

            deployed = deploy(
                project=project,
                releases=releases,
                current=current,
                branch=branch,
                keep_releases=keep_releases,
                skip_qa=bootstrap,
            )

            # -------------------------------------------------------
            # Bootstrap is intentionally ONE TIME only.
            #
            # It is disabled only after the deployment succeeds.
            #
            # If bootstrap deployment fails, it remains enabled
            # so the next watch cycle can continue setup.
            # -------------------------------------------------------

            if bootstrap and deployed:

                log(
                    "=" * 40
                )

                log(
                    "INITIAL BOOTSTRAP SUCCEEDED"
                )

                log(
                    "Re-enabling full QA pipeline."
                )

                log(
                    "=" * 40
                )

                bootstrap = False

        except KeyboardInterrupt:

            log(
                "Stopping Barkly Deploy."
            )

            return

        except Exception as error:

            log(
                ""
            )

            log(
                "=" * 40
            )

            log(
                "DEPLOYMENT FAILED"
            )

            log(
                str(error)
            )

            log(
                "CURRENT RELEASE WAS NOT REPLACED"
            )

            log(
                "=" * 40
            )

            log(
                ""
            )

        time.sleep(
            interval
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description=(
            "Barkly Labs deployment system."
        )
    )

    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help=(
            "Path to the Git project."
        ),
    )

    parser.add_argument(
        "--releases",
        type=Path,
        required=True,
        help=(
            "Directory containing immutable releases."
        ),
    )

    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help=(
            "Symlink pointing to the active release."
        ),
    )

    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=(
            "Git branch to deploy."
        ),
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=(
            "Watch interval in seconds."
        ),
    )

    parser.add_argument(
        "--keep-releases",
        type=int,
        default=DEFAULT_KEEP_RELEASES,
        help=(
            "Number of releases to retain."
        ),
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Deploy once and exit."
        ),
    )

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Perform the initial server deployment "
            "without local or public QA. In watch "
            "mode this applies only to the first "
            "successful deployment."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:

    args = parse_args()

    project = (
        args.project.resolve()
    )

    releases = (
        args.releases.resolve()
    )

    # IMPORTANT:
    #
    # Do NOT call resolve() on current.
    #
    # current may itself be a symlink and must remain
    # the symlink that we atomically replace.

    current = (
        args.current.absolute()
    )

    if not project.exists():

        print(
            f"ERROR: project does not exist: "
            f"{project}",
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
                skip_qa=args.bootstrap,
            )

            return 0

        except KeyboardInterrupt:

            log(
                "Interrupted."
            )

            return 130

        except Exception as error:

            log(
                ""
            )

            log(
                "=" * 40
            )

            log(
                "DEPLOYMENT FAILED"
            )

            log(
                str(error)
            )

            log(
                "CURRENT RELEASE WAS NOT REPLACED"
            )

            log(
                "=" * 40
            )

            return 1

    log(
        "Status: watching"
    )

    log(
        f"Project: {project}"
    )

    log(
        f"Branch: {args.branch}"
    )

    log(
        f"Interval: {args.interval}s"
    )

    log(
        f"Releases kept: {args.keep_releases}"
    )

    log(
        f"Current: {current}"
    )

    if args.bootstrap:

        log(
            "Bootstrap: ENABLED"
        )

        log(
            "QA will be skipped for the first "
            "successful deployment only."
        )

    else:

        log(
            "Bootstrap: disabled"
        )

    log(
        "=" * 40
    )

    watch(
        project=project,
        releases=releases,
        current=current,
        branch=args.branch,
        interval=args.interval,
        keep_releases=args.keep_releases,
        bootstrap=args.bootstrap,
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )