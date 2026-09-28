#!/usr/bin/env python3
"""
BARKLY SERVER
Automatic server provisioning and Nginx management for Barkly Labs.

Responsibilities:
- Install Nginx when missing
- Configure Nginx for barklylabs.space
- Enable the Barkly Nginx site
- Disable the default Nginx site
- Validate Nginx configuration
- Enable Nginx at boot
- Start Nginx when necessary
- Safely reload Nginx
- Never reload an invalid configuration

Usage:

    python3 barkly-server.py
        Provision the server.

    python3 barkly-server.py --reload
        Validate and reload Nginx.

Designed to be called by barkly-deployer.py.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOMAIN = "barklylabs.space"
WWW_DOMAIN = "www.barklylabs.space"

BARKLY_ROOT = Path("/srv/barkly")
CURRENT = BARKLY_ROOT / "current"

NGINX_AVAILABLE = Path(
    "/etc/nginx/sites-available/barkly"
)

NGINX_ENABLED = Path(
    "/etc/nginx/sites-enabled/barkly"
)

NGINX_DEFAULT = Path(
    "/etc/nginx/sites-enabled/default"
)


NGINX_CONFIG = f"""\
server {{
    listen 80;
    listen [::]:80;

    server_name {DOMAIN} {WWW_DOMAIN};

    root {CURRENT};
    index index.html;

    location / {{
        try_files $uri $uri/ $uri.html =404;
    }}

    location ~* \\.(?:css|js|mjs|map|json|xml|txt|ico|png|jpg|jpeg|gif|svg|webp|avif|woff|woff2|ttf)$ {{
        try_files $uri =404;
    }}
}}
"""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(message: str = "") -> None:
    print(
        f"[BARKLY SERVER] {message}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Command runner
# ---------------------------------------------------------------------------

def run(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:

    log("$ " + " ".join(command))

    return subprocess.run(
        command,
        text=True,
        check=check,
    )


# ---------------------------------------------------------------------------
# Root check
# ---------------------------------------------------------------------------

def require_root() -> None:
    if hasattr(os, "geteuid"):
        if os.geteuid() != 0:
            raise RuntimeError(
                "Barkly server provisioning must run as root."
            )


# ---------------------------------------------------------------------------
# Platform checks
# ---------------------------------------------------------------------------

def require_apt() -> None:
    if shutil.which("apt-get") is None:
        raise RuntimeError(
            "Barkly server provisioning currently requires "
            "an apt-based Linux distribution."
        )


# ---------------------------------------------------------------------------
# Nginx detection
# ---------------------------------------------------------------------------

def nginx_installed() -> bool:
    return shutil.which("nginx") is not None


def nginx_service_exists() -> bool:
    result = subprocess.run(
        [
            "systemctl",
            "list-unit-files",
            "nginx.service",
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return result.returncode == 0


# ---------------------------------------------------------------------------
# Nginx installation
# ---------------------------------------------------------------------------

def install_nginx() -> None:
    if nginx_installed():
        log("Nginx is already installed.")
        return

    require_apt()

    log("Nginx is not installed.")
    log("Updating package information...")

    run([
        "apt-get",
        "update",
    ])

    log("Installing Nginx...")

    run([
        "apt-get",
        "install",
        "-y",
        "nginx",
    ])

    if not nginx_installed():
        raise RuntimeError(
            "Nginx installation completed, "
            "but the nginx executable was not found."
        )

    log("Nginx installed successfully.")


# ---------------------------------------------------------------------------
# Directory preparation
# ---------------------------------------------------------------------------

def prepare_barkly_directories() -> None:
    log(
        f"Preparing Barkly directory: {BARKLY_ROOT}"
    )

    BARKLY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    log(
        f"Nginx will serve: {CURRENT}"
    )


# ---------------------------------------------------------------------------
# Nginx configuration
# ---------------------------------------------------------------------------

def write_nginx_config() -> None:
    log(
        f"Writing Nginx configuration: "
        f"{NGINX_AVAILABLE}"
    )

    NGINX_AVAILABLE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    NGINX_AVAILABLE.write_text(
        NGINX_CONFIG,
        encoding="utf-8",
    )

    log("Nginx configuration written.")


def enable_nginx_site() -> None:
    log("Enabling Barkly Nginx site.")

    NGINX_ENABLED.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if NGINX_ENABLED.is_symlink():

        if (
            NGINX_ENABLED.resolve()
            == NGINX_AVAILABLE.resolve()
        ):
            log(
                "Barkly Nginx site is already enabled."
            )
            return

        log(
            "Replacing incorrect Barkly Nginx symlink."
        )

        NGINX_ENABLED.unlink()

    elif NGINX_ENABLED.exists():

        raise RuntimeError(
            "Refusing to replace existing path: "
            f"{NGINX_ENABLED}"
        )

    NGINX_ENABLED.symlink_to(
        NGINX_AVAILABLE,
    )

    log("Barkly Nginx site enabled.")


def disable_default_site() -> None:
    if not (
        NGINX_DEFAULT.exists()
        or NGINX_DEFAULT.is_symlink()
    ):
        log(
            "Nginx default site is already disabled."
        )
        return

    log("Disabling Nginx default site.")

    NGINX_DEFAULT.unlink()

    log("Nginx default site disabled.")


# ---------------------------------------------------------------------------
# Nginx validation
# ---------------------------------------------------------------------------

def validate_nginx() -> None:
    log("Validating Nginx configuration.")

    result = run(
        [
            "nginx",
            "-t",
        ],
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Nginx configuration validation failed. "
            "Nginx was NOT reloaded."
        )

    log(
        "Nginx configuration is valid."
    )


# ---------------------------------------------------------------------------
# Nginx service
# ---------------------------------------------------------------------------

def enable_nginx_service() -> None:
    log("Enabling Nginx at boot.")

    run([
        "systemctl",
        "enable",
        "nginx",
    ])


def nginx_is_active() -> bool:
    result = subprocess.run(
        [
            "systemctl",
            "is-active",
            "--quiet",
            "nginx",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return result.returncode == 0


def start_nginx() -> None:
    if nginx_is_active():
        log(
            "Nginx is already running."
        )
        return

    log("Starting Nginx.")

    run([
        "systemctl",
        "start",
        "nginx",
    ])

    if not nginx_is_active():
        raise RuntimeError(
            "Nginx start command completed, "
            "but Nginx is not active."
        )

    log("Nginx is running.")


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

def reload_nginx() -> None:
    """
    Validate first.

    If validation fails, do not reload.
    """

    log("Preparing to reload Nginx.")

    validate_nginx()

    log("Reloading Nginx.")

    run([
        "systemctl",
        "reload",
        "nginx",
    ])

    log(
        "Nginx reloaded successfully."
    )


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------

def provision() -> None:
    require_root()

    log("=" * 40)
    log("BARKLY SERVER PROVISION")
    log("=" * 40)

    log(
        f"Domain:   {DOMAIN}"
    )

    log(
        f"Web root: {CURRENT}"
    )

    log("")

    prepare_barkly_directories()

    install_nginx()

    if not nginx_service_exists():
        raise RuntimeError(
            "Nginx is installed, but its systemd "
            "service was not detected."
        )

    write_nginx_config()

    enable_nginx_site()

    disable_default_site()

    validate_nginx()

    enable_nginx_service()

    start_nginx()

    log("")
    log("=" * 40)
    log("BARKLY SERVER READY")
    log("=" * 40)

    log(
        f"Nginx is serving: {CURRENT}"
    )

    log(
        f"Domain: {DOMAIN}"
    )

    log("=" * 40)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Provision and manage the Barkly Labs web server."
        )
    )

    parser.add_argument(
        "--reload",
        action="store_true",
        help=(
            "Validate and safely reload Nginx "
            "without reprovisioning."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    try:

        if args.reload:
            require_root()

            log("=" * 40)
            log("BARKLY NGINX RELOAD")
            log("=" * 40)

            reload_nginx()

            log("=" * 40)
            log("BARKLY NGINX RELOAD COMPLETE")
            log("=" * 40)

            return 0

        provision()

        return 0

    except subprocess.CalledProcessError as error:

        log(
            f"Command failed with exit code "
            f"{error.returncode}."
        )

        return error.returncode or 1

    except KeyboardInterrupt:

        log(
            "Server provisioning interrupted."
        )

        return 130

    except Exception as error:

        log(
            f"ERROR: {error}"
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())