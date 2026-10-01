"""Transcription de fichiers audio (.wav, .mp3, ...) en texte avec Whisper, en local,
avec identification des personnes qui parlent (entretiens).

Usage :
    python -m moteurs.transcrire fichier.wav
    python -m moteurs.transcrire entretien.wav --personnes 2
    python -m moteurs.transcrire dossier/ --modele large-v3 --langue auto --personnes auto
"""

import argparse
import importlib.util
import os
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from plateforme import PUCE_APPLE  # noqa: E402

EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}
TAILLE_LOT = 16  # passages de 30 s traités en parallèle par le GPU (adapté aux 8 Go de la RTX 2080)
# Sur Mac à puce Apple (M1, M2…), Whisper tourne sur la puce graphique via MLX (Metal) : mêmes modèles,
# convertis pour MLX par la communauté.
DEPOTS_MLX = {
    "small": "mlx-community/whisper-small-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}
FENETRE_MLX = 28.0  # s : Whisper lit l'audio par fenêtres de 30 s


@lru_cache(maxsize=1)
def moteur_mlx() -> bool:
    return PUCE_APPLE and importlib.util.find_spec("mlx_whisper") is not None


def ajouter_dll_cuda() -> None:
    """Rend visibles les DLL CUDA (téléchargées à la demande, livrées ou installées via pip)."""
    from .cuda import ajouter_dll

    ajouter_dll()


def materiel() -> dict:
    """Accélération disponible : {"acceleration": "mlx" | "cuda" | "cpu", "nvidia": carte NVIDIA présente}."""
    if moteur_mlx():
        return {"acceleration": "mlx", "nvidia": False}
    if sys.platform != "win32":
        return {"acceleration": "cpu", "nvidia": False}
    from . import cuda

    cuda.ajouter_dll()
    try:
        import ctranslate2

        carte = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        carte = False
    if carte and cuda.installe():
        return {"acceleration": "cuda", "nvidia": True}
    return {"acceleration": "cpu", "nvidia": carte}


class ModeleIndisponible(Exception):
    pass


def modele_installe(nom: str) -> bool:
    try:
        if moteur_mlx():
            from huggingface_hub import snapshot_download

            snapshot_download(DEPOTS_MLX[nom], local_files_only=True)
        else:
            from faster_whisper.utils import download_model

            download_model(nom, local_files_only=True)
        return True
    except Exception:
        return False


def localiser_modele(nom: str) -> str:
    """Chemin local du modèle : cache d'abord (hors ligne), sinon téléchargement (une seule fois)."""
    if moteur_mlx():
        from huggingface_hub import snapshot_download

        telecharger = lambda local: snapshot_download(DEPOTS_MLX[nom], local_files_only=local)  # noqa: E731
    else:
        from faster_whisper.utils import download_model

        telecharger = lambda local: download_model(nom, local_files_only=local)  # noqa: E731
    try:
        return telecharger(True)
    except Exception:
        pass
    try:
        return telecharger(False)
    except Exception as e:
        raise ModeleIndisponible(
            f"Impossible de télécharger le modèle « {nom} ». Vérifie ta connexion internet : "
            f"elle n'est nécessaire qu'une seule fois par modèle."
        ) from e


class ModeleMLX:
    """Whisper sur puce Apple. Le modèle est gardé en mémoire par mlx_whisper entre deux fichiers."""

    def __init__(self, chemin: str):
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        self.chemin = chemin
        ModelHolder.get_model(chemin, mx.float16)  # chargement immédiat : la 1re transcription démarre vite

    def segments(self, audio, langue: str | None, options_vad: dict | None):
        """Découpe l'audio en fenêtres de parole (détection de voix de faster-whisper) puis transcrit
        chaque fenêtre : le texte arrive au fil de l'eau, comme sur Windows."""
        import mlx_whisper
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        taux = 16000
        vad = VadOptions(**{"min_silence_duration_ms": 500, **(options_vad or {}),
                            "max_speech_duration_s": FENETRE_MLX})
        fenetres: list[list[int]] = []
        for r in get_speech_timestamps(audio, vad):
            if fenetres and r["end"] - fenetres[-1][0] <= FENETRE_MLX * taux:
                fenetres[-1][1] = r["end"]
            else:
                fenetres.append([r["start"], r["end"]])
        for debut, fin in fenetres:
            res = mlx_whisper.transcribe(
                audio[debut:fin], path_or_hf_repo=self.chemin, language=langue, word_timestamps=True,
                condition_on_previous_text=False, verbose=None)
            decalage = debut / taux
            for s in res["segments"]:
                mots = [SimpleNamespace(start=m["start"] + decalage, end=m["end"] + decalage, word=m["word"])
                        for m in s.get("words", [])]
                yield SimpleNamespace(start=s["start"] + decalage, end=s["end"] + decalage, text=s["text"],
                                      words=mots)


def charger_modele(nom: str, device: str):
    """Charge le modèle Whisper ; renvoie (modele, device_effectif) avec device = "mlx", "cuda" ou "cpu"."""
    chemin = localiser_modele(nom)
    if moteur_mlx():
        return ModeleMLX(chemin), "mlx"

    from faster_whisper import WhisperModel

    # Carte NVIDIA sans les DLL CUDA : le modèle se créerait sur le GPU puis échouerait à la 1re phrase.
    if device == "auto" and materiel()["acceleration"] != "cuda":
        device = "cpu"
    if device in ("auto", "cuda"):
        try:
            modele = WhisperModel(chemin, device="cuda", compute_type="float16")
            print(f"Modèle '{nom}' chargé sur GPU.")
            return modele, "cuda"
        except Exception as e:
            if device == "cuda":
                raise
            print(f"GPU indisponible ({e.__class__.__name__}), passage sur CPU.")
    modele = WhisperModel(chemin, device="cpu", compute_type="int8")
    print(f"Modèle '{nom}' chargé sur CPU.")
    return modele, "cpu"


# ---------- Transcription ----------

@dataclass
class Resultat:
    fichier: Path
    duree: float
    mots: list          # list[personnes.Mot]
    repliques: list     # list[personnes.Replique] ; personne = None sans identification

    @property
    def avec_personnes(self) -> bool:
        return any(r.personne is not None for r in self.repliques)

    @property
    def personnes(self) -> list[int]:
        return sorted({r.personne for r in self.repliques if r.personne is not None})


def transcrire_audio(
    modele,
    fichier: Path,
    langue: str | None,
    nb_personnes: int | None,
    sur_segment: Callable[[float, str, float], None] | None = None,
    sur_etape: Callable[[str, float | None], None] | None = None,
    arret: Callable[[], bool] = lambda: False,
    device: str = "cuda",
    ameliorer: bool = False,
) -> Resultat | None:
    """nb_personnes : 1 = pas d'identification, 0 = détection automatique, n = n personnes.
    ameliorer : remonte les voix faibles / lointaines et atténue le bruit avant Whisper
    (les empreintes vocales restent calculées sur l'audio d'origine, plus fidèle aux timbres).
    Renvoie None si l'arrêt a été demandé."""
    from faster_whisper import BatchedInferencePipeline, decode_audio

    from . import personnes

    audio = decode_audio(str(fichier), sampling_rate=personnes.TAUX)
    duree = len(audio) / personnes.TAUX
    audio_whisper = audio
    options_vad = None
    if ameliorer:
        from .audio import ameliorer as ameliorer_audio

        if sur_etape:
            sur_etape("Amélioration des voix…", None)
        audio_whisper = ameliorer_audio(audio, personnes.TAUX)
        options_vad = {"threshold": 0.4}  # capte aussi les voix les plus faibles
    if isinstance(modele, ModeleMLX):
        segments = modele.segments(audio_whisper, langue, options_vad)
    else:
        pipeline = BatchedInferencePipeline(modele)
        segments, _ = pipeline.transcribe(
            audio_whisper, language=langue, batch_size=TAILLE_LOT if device == "cuda" else 4,
            word_timestamps=True, vad_filter=True, vad_parameters=options_vad, beam_size=5,
        )
    # Les empreintes vocales sont calculées par le processeur pendant que le GPU transcrit la suite.
    identificateur = personnes.Identificateur(audio) if nb_personnes != 1 else None
    mots: list[personnes.Mot] = []
    for seg in segments:
        if arret():
            return None
        nouveaux = [personnes.Mot(w.start, w.end, w.word) for w in (seg.words or [])]
        mots += nouveaux
        if identificateur:
            identificateur.ajouter(nouveaux)
        if sur_segment:
            sur_segment(seg.start, seg.text.strip(), min(seg.end / duree, 1.0) if duree else 0.0)

    if identificateur is None:
        repliques = personnes.paragraphes(mots)
    else:
        etape = "Identification des personnes qui parlent…"
        if sur_etape:
            sur_etape(etape, 0.0)
        repliques = identificateur.terminer(
            nb_personnes or None, (lambda x: sur_etape(etape, x)) if sur_etape else None)
    return Resultat(fichier, duree, mots, repliques)


# ---------- Mise en forme et enregistrement ----------

def format_srt(secondes: float) -> str:
    ms = int(round(secondes * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def format_hms(secondes: float) -> str:
    m, s = divmod(int(secondes), 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02}"


def nom_personne(personne: int, noms: dict[int, str] | None = None) -> str:
    return (noms or {}).get(personne) or f"Personne {personne + 1}"


def texte_final(res: Resultat, noms: dict[int, str] | None = None) -> str:
    if res.avec_personnes:
        return "\n\n".join(f"[{format_hms(r.debut)}] {nom_personne(r.personne, noms)} : {r.texte}"
                           for r in res.repliques) + "\n"
    return "\n\n".join(r.texte for r in res.repliques) + "\n"


def sous_titres(res: Resultat, noms: dict[int, str] | None = None) -> str:
    """Sous-titres courts (≤ 7 s, ≤ 90 caractères), jamais à cheval sur deux personnes."""
    blocs: list[str] = []

    def ajouter(debut: float, fin: float, texte: str) -> None:
        blocs.append(f"{len(blocs) + 1}\n{format_srt(debut)} --> {format_srt(fin)}\n{prefixe}{texte}\n")

    for r in res.repliques:
        prefixe = f"{nom_personne(r.personne, noms)} : " if r.personne is not None else ""
        if r.correction is not None:
            # Texte corrigé à la main : découpé en sous-titres répartis sur la durée de la réplique.
            morceaux, courant_txt = [], ""
            for mot in r.correction.split():
                if courant_txt and len(courant_txt) + len(mot) > 90:
                    morceaux.append(courant_txt)
                    courant_txt = ""
                courant_txt = f"{courant_txt} {mot}".strip()
            if courant_txt:
                morceaux.append(courant_txt)
            total = sum(len(m) for m in morceaux) or 1
            t = r.debut
            for m in morceaux:
                d = (r.fin - r.debut) * len(m) / total
                ajouter(t, t + d, m)
                t += d
            continue
        courant: list = []
        for mot in r.mots + [None]:
            if courant and (mot is None or mot.fin - courant[0].debut > 7
                            or len("".join(m.texte for m in courant)) > 90):
                texte = "".join(m.texte for m in courant).strip()
                blocs.append(f"{len(blocs) + 1}\n{format_srt(courant[0].debut)} --> "
                             f"{format_srt(courant[-1].fin)}\n{prefixe}{texte}\n")
                courant = []
            if mot is not None:
                courant.append(mot)
    return "\n".join(blocs)


def enregistrer(res: Resultat, noms: dict[int, str] | None = None, dossier: Path | None = None) -> Path:
    dossier = dossier or res.fichier.parent
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / f"{res.fichier.stem}.txt"
    sortie.write_text(texte_final(res, noms), encoding="utf-8")
    (dossier / f"{res.fichier.stem}.srt").write_text(sous_titres(res, noms), encoding="utf-8")
    return sortie


# ---------- Ligne de commande ----------

def lister_fichiers(chemins: list[str]) -> list[Path]:
    fichiers: list[Path] = []
    for c in chemins:
        p = Path(c)
        if p.is_dir():
            fichiers.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in EXTENSIONS))
        elif p.is_file():
            fichiers.append(p)
        else:
            print(f"Introuvable : {c}", file=sys.stderr)
    return fichiers


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcrit des fichiers audio en texte (Whisper local).")
    parser.add_argument("entrees", nargs="+", help="Fichier(s) audio ou dossier(s)")
    parser.add_argument("--modele", default="large-v3-turbo",
                        help="tiny, base, small, medium, large-v3, large-v3-turbo (défaut)")
    parser.add_argument("--langue", default="fr", help="Code langue (fr, en, ...) ou 'auto' (défaut : fr)")
    parser.add_argument("--personnes", default="1",
                        help="1 = pas d'identification (défaut), 'auto', ou nombre de personnes (2, 3…)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--sortie", default=None, help="Dossier de sortie (défaut : à côté de l'audio)")
    args = parser.parse_args()

    fichiers = lister_fichiers(args.entrees)
    if not fichiers:
        sys.exit("Aucun fichier audio à transcrire.")

    ajouter_dll_cuda()
    modele, device = charger_modele(args.modele, args.device)
    langue = None if args.langue == "auto" else args.langue
    nb = 0 if args.personnes == "auto" else int(args.personnes)

    for f in fichiers:
        print(f"\n▶ {f.name}")
        debut = time.time()
        res = transcrire_audio(modele, f, langue, nb, device=device,
                               sur_segment=lambda t, texte, a: print(f"  [{a:6.1%}] {texte}"))
        sortie = enregistrer(res, dossier=Path(args.sortie) if args.sortie else None)
        extra = f", {len(res.personnes)} personne(s)" if res.avec_personnes else ""
        print(f"  ✔ {res.duree:.0f}s d'audio en {time.time() - debut:.0f}s{extra} → {sortie}")


if __name__ == "__main__":
    main()
