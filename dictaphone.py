"""Dictaphone : enregistre le micro en appliquant la chaîne de traitement de la voix (moteurs/audio.py),
avec vumètre et forme d'onde en direct. L'enregistrement est écrit sur le disque au fil de l'eau :
même en cas de plantage, l'audio déjà capté reste lisible."""

from __future__ import annotations

import os
import queue
import struct
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from moteurs.audio import Chaine, Reglages, db

DOSSIER_DEFAUT = Path.home() / "Documents" / "Enregistrements"
FORMATS = {"wav": "WAV (qualité maximale)", "flac": "FLAC (sans perte, 2× plus léger)",
           "m4a": "M4A (léger, idéal pour les longs cours)"}


class _EcrivainWav:
    """WAV 16 bits mono dont l'en-tête est remis à jour toutes les 5 s (fichier toujours lisible)."""

    def __init__(self, chemin: Path, taux: int):
        self.f = open(chemin, "wb")
        self.taux = taux
        self.octets = 0
        self.derniere_maj = time.monotonic()
        self._entete()

    def _entete(self) -> None:
        self.f.seek(0)
        self.f.write(b"RIFF" + struct.pack("<I", 36 + self.octets) + b"WAVEfmt "
                     + struct.pack("<IHHIIHH", 16, 1, 1, self.taux, self.taux * 2, 2, 16)
                     + b"data" + struct.pack("<I", self.octets))
        self.f.seek(0, os.SEEK_END)

    def ecrire(self, x: np.ndarray) -> None:
        donnees = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        self.f.write(donnees)
        self.octets += len(donnees)
        if time.monotonic() - self.derniere_maj > 5:
            self._entete()
            self.f.flush()
            self.derniere_maj = time.monotonic()

    def fermer(self) -> None:
        self._entete()
        self.f.close()


def convertir(source: Path, destination: Path) -> None:
    """WAV → FLAC ou M4A (AAC) avec PyAV (déjà présent pour la transcription)."""
    import av

    codec = {".flac": "flac", ".m4a": "aac"}[destination.suffix]
    with av.open(str(source)) as entree, av.open(str(destination), "w") as sortie:
        flux_entree = entree.streams.audio[0]
        flux = sortie.add_stream(codec, rate=flux_entree.rate)
        if codec == "aac":
            flux.bit_rate = 96_000
        for trame in entree.decode(flux_entree):
            trame.pts = None
            for paquet in flux.encode(trame):
                sortie.mux(paquet)
        for paquet in flux.encode(None):
            sortie.mux(paquet)


def nom_libre(dossier: Path, extension: str) -> Path:
    base = f"Enregistrement {datetime.now():%Y-%m-%d %Hh%M}"
    chemin = dossier / f"{base}{extension}"
    n = 2
    while chemin.exists() or chemin.with_suffix(".wav").exists():
        chemin = dossier / f"{base} ({n}){extension}"
        n += 1
    return chemin


class Dictaphone:
    def __init__(self, emettre):
        self._emettre = emettre
        self._flux = None
        self._file: queue.Queue = queue.Queue()
        self._reglages = Reglages()
        self._chaine: Chaine | None = None
        self._taux = 48000
        self._canaux = 1
        self._peripherique: int | None = None
        self._ecrivain: _EcrivainWav | None = None
        self._chemin_wav: Path | None = None
        self._format = "wav"
        self._enregistre = 0          # échantillons écrits
        self._en_pause = False
        self._verrou = threading.Lock()
        self._actif = threading.Event()

    # ---------- Périphériques ----------

    def peripheriques(self) -> dict:
        import sounddevice as sd

        try:
            defaut = sd.default.device[0]
            api_defaut = sd.query_devices(defaut)["hostapi"] if defaut is not None and defaut >= 0 else 0
            tous = sd.query_devices()
            # L'API par défaut (MME) coupe les noms à 31 caractères : on reprend le nom complet ailleurs.
            complets = [d["name"] for d in tous if d["max_input_channels"] > 0 and d["hostapi"] != api_defaut]
            liste = [{"id": i, "nom": next((c for c in complets if c.startswith(d["name"])), d["name"])}
                     for i, d in enumerate(tous) if d["max_input_channels"] > 0 and d["hostapi"] == api_defaut]
        except Exception as e:
            return {"liste": [], "defaut": None, "erreur": str(e)}
        return {"liste": liste, "defaut": defaut if defaut is not None and defaut >= 0 else None}

    # ---------- Réglages (appliqués en direct, même pendant l'enregistrement) ----------

    def regler(self, sensibilite_db: float, gain_auto: bool, reduction_bruit: bool, force_reduction: float,
               coupe_bas: bool) -> None:
        r = self._reglages
        r.sensibilite_db = float(sensibilite_db)
        r.gain_auto = bool(gain_auto)
        r.reduction_bruit = bool(reduction_bruit)
        r.force_reduction = float(force_reduction)
        r.coupe_bas = bool(coupe_bas)

    # ---------- Micro ouvert (test ou enregistrement) ----------

    def ecouter(self, peripherique: int | None) -> dict:
        """Ouvre le micro pour le vumètre (sans enregistrer). Change de micro si besoin."""
        with self._verrou:
            if self._flux is not None and peripherique == self._peripherique:
                return {"ok": True}
            if self._ecrivain is not None:
                return {"erreur": "Impossible de changer de micro pendant un enregistrement."}
            self._fermer_flux()
            try:
                self._ouvrir_flux(peripherique)
            except Exception as e:
                return {"erreur": f"Micro inaccessible : {e}"}
        return {"ok": True}

    def couper(self) -> None:
        """Ferme le micro (sauf pendant un enregistrement)."""
        with self._verrou:
            if self._ecrivain is None:
                self._fermer_flux()

    def _ouvrir_flux(self, peripherique: int | None) -> None:
        import sounddevice as sd

        info = sd.query_devices(peripherique, "input")
        self._taux = int(info["default_samplerate"]) or 48000
        self._peripherique = peripherique
        dernier = None
        self._file = queue.Queue()
        for canaux in (1, min(2, int(info["max_input_channels"]))):
            try:
                self._canaux = canaux
                flux = sd.InputStream(device=peripherique, channels=canaux, samplerate=self._taux,
                                      dtype="float32", blocksize=self._taux // 50, callback=self._rappel)
                flux.start()
                break
            except Exception as e:
                dernier = e
        else:
            raise dernier
        self._flux = flux
        self._chaine = Chaine(self._taux, self._reglages)
        # File, signal d'arrêt et chaîne propres à ce flux : un ancien fil ne peut rien voler au nouveau.
        self._actif = threading.Event()
        self._actif.set()
        threading.Thread(target=self._traiter, args=(self._file, self._actif, self._chaine), daemon=True).start()

    def _fermer_flux(self) -> None:
        self._actif.clear()
        if self._flux is not None:
            try:
                self._flux.stop()
                self._flux.close()
            except Exception:
                pass
            self._flux = None

    def _rappel(self, donnees, _trames, _temps, _statut) -> None:
        self._file.put(donnees.mean(axis=1) if self._canaux > 1 else donnees[:, 0].copy())

    # ---------- Traitement (fil dédié : jamais dans le rappel audio) ----------

    def _traiter(self, file: queue.Queue, actif: threading.Event, chaine: Chaine) -> None:
        onde: list[float] = []
        crete = 0.0
        energie, n = 0.0, 0
        sature = False
        dernier_envoi = time.monotonic()
        pas_onde = self._taux // 40  # un point de forme d'onde toutes les 25 ms
        reste = np.zeros(0, dtype=np.float32)
        while actif.is_set():
            try:
                brut = file.get(timeout=0.2)
            except queue.Empty:
                continue
            sature |= bool(np.abs(brut).max() >= 0.99)
            propre = chaine(brut)
            with self._verrou:
                if self._ecrivain is not None and not self._en_pause:
                    self._ecrivain.ecrire(propre)
                    self._enregistre += len(propre)
            if len(propre):
                crete = max(crete, float(np.abs(propre).max()))
                energie += float(np.sum(propre ** 2))
                n += len(propre)
                reste = np.concatenate([reste, np.abs(propre)])
                while len(reste) >= pas_onde:
                    onde.append(round(float(reste[:pas_onde].max()), 3))
                    reste = reste[pas_onde:]
            if time.monotonic() - dernier_envoi >= 0.05:
                self._emettre("dic_niveau", rms=round(db(np.sqrt(energie / max(n, 1))), 1),
                              crete=round(db(crete), 1), sature=sature, onde=onde,
                              gain_auto=round(chaine.gain_auto.gain_db, 1), **self._etat())
                onde, crete, energie, n, sature = [], 0.0, 0.0, 0, False
                dernier_envoi = time.monotonic()
        # Micro fermé : l'interface repasse à l'état de repos.
        self._emettre("dic_niveau", rms=-90.0, crete=-90.0, sature=False, onde=[], gain_auto=0.0, **self._etat())

    def _etat(self) -> dict:
        etat = "arret"
        if self._ecrivain is not None:
            etat = "pause" if self._en_pause else "enregistrement"
        elif self._flux is not None:
            etat = "ecoute"
        return {"etat": etat, "duree": self._enregistre / self._taux if self._taux else 0}

    # ---------- Enregistrement ----------

    def demarrer(self, peripherique: int | None, dossier: str, format_: str) -> dict:
        ouvert = self.ecouter(peripherique)
        if "erreur" in ouvert:
            return ouvert
        dossier_p = Path(dossier) if dossier else DOSSIER_DEFAUT
        try:
            dossier_p.mkdir(parents=True, exist_ok=True)
            self._format = format_ if format_ in FORMATS else "wav"
            self._chemin_wav = nom_libre(dossier_p, "." + self._format).with_suffix(".wav")
            with self._verrou:
                self._ecrivain = _EcrivainWav(self._chemin_wav, self._taux)
                self._enregistre = 0
                self._en_pause = False
        except OSError as e:
            return {"erreur": f"Impossible de créer le fichier : {e}"}
        return {"ok": True, "chemin": str(self._chemin_wav)}

    def pause(self, en_pause: bool) -> None:
        with self._verrou:
            self._en_pause = bool(en_pause)

    def arreter(self, garder_micro: bool = True) -> dict:
        with self._verrou:
            ecrivain, self._ecrivain = self._ecrivain, None
            duree = self._enregistre / self._taux
        if ecrivain is None:
            return {"erreur": "Aucun enregistrement en cours."}
        ecrivain.fermer()
        if not garder_micro:
            self.couper()
        chemin = self._chemin_wav
        if duree < 0.5:
            chemin.unlink(missing_ok=True)
            return {"erreur": "Enregistrement trop court : rien n'a été gardé."}
        if self._format != "wav":
            final = chemin.with_suffix("." + self._format)
            try:
                convertir(chemin, final)
                chemin.unlink()
                chemin = final
            except Exception as e:
                final.unlink(missing_ok=True)
                return {"chemin": str(chemin), "nom": chemin.name, "duree": duree,
                        "avertissement": f"Conversion en {self._format.upper()} impossible ({e}) : gardé en WAV."}
        return {"chemin": str(chemin), "nom": chemin.name, "duree": duree}

    def fermer(self) -> None:
        if self._ecrivain is not None:
            self.arreter(garder_micro=False)
        with self._verrou:
            self._fermer_flux()
