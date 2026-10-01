"""Différences Windows / macOS regroupées ici : dossiers de l'application, ouverture de fichiers, matériel."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
PUCE_APPLE = MAC and platform.machine() == "arm64"


def dossier_donnees() -> Path:
    """Réglages et composants téléchargés (accélération GPU…)."""
    if WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Atelier"


def dossier_journal() -> Path:
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Atelier"
    if MAC:
        return Path.home() / "Library" / "Logs" / "Atelier"
    return dossier_donnees()


def ouvrir(chemin: str | Path) -> None:
    """Ouvre un fichier ou un dossier avec l'application par défaut du système."""
    if WINDOWS:
        os.startfile(chemin)
    elif MAC:
        subprocess.Popen(["open", str(chemin)])
    else:
        subprocess.Popen(["xdg-open", str(chemin)])


def sans_fenetre() -> dict:
    """Options de subprocess pour ne pas faire clignoter de console sous Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
