#!/usr/bin/env python3
"""
BARKLY SERVER
First-run server provisioning for Barkly Labs deployments.

Responsibilities:
- Install Nginx when missing
- Configure Nginx for the Barkly site
- Validate Nginx configuration
- Enable and start Nginx
- Reload Nginx safely
- Never reload an invalid configuration

Designed to be called by barkly-deployer.py.
"""

from __future__ import annotations

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

NGINX_AVAILABLE = Path("/etc/nginx/sites-available/barkly")
NGINX_ENABLED = Path("/etc/nginx/sites-enabled/barkly")


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

def log(message: str) -> None:
    print(f"[BARKLY SERVER] {message}")


# ---------------------------------------------------------------------------
# Commands
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
    if hasattr(__import__("os"), "geteuid"):
        if __import__("os").geteuid() != 0:
            raise RuntimeError(
                "Barkly server provisioning must run as root."
            )


# ---------------------------------------------------------------------------
# Nginx
# ---------------------------------------------------------------------------

def nginx_installed() -> bool:
    return shutil.which("nginx") is not None


def install_nginx() -> None:
    if nginx_installed():
        log("Nginx is already installed.")
        return

    log("Nginx is not installed.")
    log("Installing Nginx...")

    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "nginx"])

    if not nginx_installed():
        raise RuntimeError(
            "Nginx installation completed but nginx was not found."
        )

    log("Nginx installed successfully.")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def write_nginx_config() -> None:
    log(f"Writing Nginx configuration: {NGINX_AVAILABLE}")

    NGINX_AVAILABLE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    NGINX_AVAILABLE.write_text(
        NGINX_CONFIG,
        encoding="utf-8",
    )


def enable_nginx_site() -> None:
    log("Enabling Barkly Nginx site.")

    NGINX_ENABLED.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if NGINX_ENABLED.is_symlink():
        if NGINX_ENABLED.resolve() == NGINX_AVAILABLE.resolve():
            return

        NGINX_ENABLED.unlink()

    elif NGINX_ENABLED.exists():
        raise RuntimeError(
            f"Refusing to replace existing Nginx path: {NGINX_ENABLED}"
        )

    NGINX_ENABLED.symlink_to(NGINX_AVAILABLE)


def disable_default_site() -> None:
    default = Path("/etc/nginx/sites-enabled/default")

    if default.exists() or default.is_symlink():
        log("Disabling Nginx default site.")
        default.unlink()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_nginx() -> None:
    log("Validating Nginx configuration.")

    result = run(
        ["nginx", "-t"],
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Nginx configuration validation failed. "
            "Nginx was NOT reloaded."
        )

    log("Nginx configuration is valid.")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def enable_and_start_nginx() -> None:
    log("Enabling Nginx at boot.")

    run([
        "systemctl",
        "enable",
        "nginx",
    ])

    status = subprocess.run(
        ["systemctl", "is-active", "--quiet", "nginx"],
    )

    if status.returncode != 0:
        log("Starting Nginx.")
        run([
            "systemctl",
            "start",
            "nginx",
        ])
    else:
        log("Nginx is already running.")


def reload_nginx() -> None:
    validate_nginx()

    log("Reloading Nginx.")

    run([
        "systemctl",
        "reload",
        "nginx",
    ])


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------

def provision() -> None:
    require_root()

    log("Starting Barkly server provisioning.")
    log(f"Domain: {DOMAIN}")
    log(f"Web root: {CURRENT}")

    BARKLY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    install_nginx()
    write_nginx_config()
    enable_nginx_site()
    disable_default_site()

    validate_nginx()
    enable_and_start_nginx()

    log("Barkly server provisioning complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        provision()
        return 0

    except subprocess.CalledProcessError as error:
        log(
            f"Command failed with exit code "
            f"{error.returncode}."
        )
        return error.returncode or 1

    except Exception as error:
        log(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())