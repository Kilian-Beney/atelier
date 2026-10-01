"""Accélération NVIDIA à la demande (Windows) : les bibliothèques CUDA pèsent 2 Go, elles ne sont donc pas
livrées avec l'application. Si le PC a une carte NVIDIA, l'utilisateur peut les télécharger une fois
(paquets officiels NVIDIA publiés sur PyPI) ; seules les DLL utiles sont gardées."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from plateforme import WINDOWS, dossier_donnees

PAQUETS = [  # (nom PyPI, version, sous-dossier)
    ("nvidia-cublas-cu12", "12.9.2.10", "cublas"),
    ("nvidia-cudnn-cu12", "9.26.0.51", "cudnn"),
    ("nvidia-cuda-nvrtc-cu12", "12.9.86", "cuda_nvrtc"),
]
DOSSIER = dossier_donnees() / "cuda"


def bases() -> list[Path]:
    """Dossiers « nvidia » possibles : téléchargé à la demande, livré dans l'exe, ou installé par pip."""
    trouves = [DOSSIER / "nvidia"]
    if getattr(sys, "frozen", False):
        trouves.append(Path(sys._MEIPASS) / "nvidia")
    else:
        try:
            import nvidia

            trouves += [Path(p) for p in nvidia.__path__]
        except ImportError:
            pass
    return [b for b in trouves if b.is_dir()]


def installe() -> bool:
    return any(all((b / s / "bin").is_dir() for _, _, s in PAQUETS) for b in bases())


def ajouter_dll() -> None:
    if not WINDOWS:
        return
    for base in bases():
        for _, _, sous_dossier in PAQUETS:
            bin_dir = base / sous_dossier / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]


def _url(nom: str, version: str) -> tuple[str, int]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{nom}/{version}/json", timeout=30) as r:
        fichiers = json.load(r)["urls"]
    f = next(f for f in fichiers if f["filename"].endswith("win_amd64.whl"))
    return f["url"], f["size"]


def telecharger(progression: Callable[[float, str], None], arret: Callable[[], bool] = lambda: False) -> bool:
    """Télécharge et installe les DLL CUDA. Renvoie False si l'arrêt a été demandé."""
    infos = [(_url(nom, version), sous) for nom, version, sous in PAQUETS]
    total = sum(taille for (_, taille), _ in infos)
    fait = 0
    temporaire = DOSSIER.parent / "cuda.part"
    shutil.rmtree(temporaire, ignore_errors=True)
    (temporaire / "nvidia").mkdir(parents=True)
    for (url, taille), sous in infos:
        with tempfile.TemporaryDirectory() as dossier:
            roue = Path(dossier) / "paquet.whl"
            with urllib.request.urlopen(url, timeout=60) as r, open(roue, "wb") as f:
                while bloc := r.read(1 << 20):
                    if arret():
                        shutil.rmtree(temporaire, ignore_errors=True)
                        return False
                    f.write(bloc)
                    fait += len(bloc)
                    progression(fait / total, f"{fait / 1e9:.2f} / {total / 1e9:.2f} Go")
            progression(fait / total, f"Installation de {sous}…")
            with zipfile.ZipFile(roue) as z:
                for membre in z.namelist():
                    if membre.startswith(f"nvidia/{sous}/bin/") and membre.lower().endswith(".dll"):
                        destination = temporaire / membre
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(membre) as source, open(destination, "wb") as cible:
                            shutil.copyfileobj(source, cible)
    shutil.rmtree(DOSSIER, ignore_errors=True)
    temporaire.rename(DOSSIER)
    return True
