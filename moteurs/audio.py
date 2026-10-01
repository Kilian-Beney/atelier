"""Traitement de la voix, en continu (dictaphone) ou d'un bloc (avant transcription).

Chaîne : coupe-bas (grondements, souffle de ventilation) → réduction de bruit → sensibilité (gain fixe)
→ gain automatique (rapproche les voix lointaines des voix proches) → limiteur (jamais de saturation).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi


@dataclass
class Reglages:
    sensibilite_db: float = 0.0     # gain fixe ajouté au micro
    gain_auto: bool = True          # nivelle le volume (voix faibles remontées, fortes contenues)
    reduction_bruit: bool = True    # atténue le bruit de fond constant (souffle, ventilation, bourdonnement)
    force_reduction: float = 0.6    # 0 = légère … 1 = forte
    coupe_bas: bool = True          # supprime les graves sous 80 Hz (chocs, vibrations, vent)


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


class _CoupeBas:
    def __init__(self, taux: int, frequence: float = 80.0):
        self.sos = butter(2, frequence, btype="highpass", fs=taux, output="sos")
        self.etat = sosfilt_zi(self.sos) * 0.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self.etat = sosfilt(self.sos, x, zi=self.etat)
        return y.astype(np.float32)


class _ReducteurBruit:
    """Soustraction spectrale en continu. Le bruit est estimé en suivant le minimum de l'énergie
    de chaque fréquence (le bruit de fond ne disparaît jamais, la voix si) : aucun « profil » à capturer."""

    def __init__(self, taux: int):
        self.n = 1024 if taux > 24000 else 512
        self.saut = self.n // 2
        self.fenetre = np.sqrt(np.hanning(self.n + 1)[:-1]).astype(np.float32)
        self.entree = np.zeros(0, dtype=np.float32)
        self.sortie = np.zeros(self.n, dtype=np.float32)
        self.lisse: np.ndarray | None = None
        self.bruit: np.ndarray | None = None
        self.gains: np.ndarray | None = None
        # Remontée lente de l'estimation du bruit : environ +6 dB par seconde.
        self.montee = 10 ** (6 / 10 * self.saut / taux)
        self.trames = 0

    def __call__(self, x: np.ndarray, force: float) -> np.ndarray:
        self.entree = np.concatenate([self.entree, x])
        morceaux = []
        while len(self.entree) >= self.n:
            trame = self.entree[: self.n]
            self.entree = self.entree[self.saut:]
            spectre = np.fft.rfft(trame * self.fenetre)
            puissance = spectre.real ** 2 + spectre.imag ** 2
            if self.lisse is None:
                self.lisse = puissance.copy()
                self.bruit = puissance.copy()
                self.gains = np.ones_like(puissance)
            self.lisse = 0.8 * self.lisse + 0.2 * puissance
            self.bruit = np.minimum(self.bruit * self.montee, self.lisse)
            self.trames += 1
            if self.trames > 20:  # ~0,2 s d'écoute avant d'agir
                sur = 1.0 + 3.0 * force            # sur-soustraction
                plancher = 10 ** (-(8 + 14 * force) / 20)  # le bruit est atténué, jamais coupé net
                g = np.maximum(1.0 - sur * self.bruit / (puissance + 1e-12), 0.0)
                g = np.maximum(np.sqrt(g), plancher)
                # Lissage temporel : évite le « bruit musical » (petits sifflements).
                self.gains = np.where(g > self.gains, 0.5 * self.gains + 0.5 * g, 0.8 * self.gains + 0.2 * g)
                spectre = spectre * self.gains
            trame = np.fft.irfft(spectre, self.n).astype(np.float32) * self.fenetre
            self.sortie[: self.n] += trame
            morceaux.append(self.sortie[: self.saut].copy())
            self.sortie = np.concatenate([self.sortie[self.saut:], np.zeros(self.saut, dtype=np.float32)])
        return np.concatenate(morceaux) if morceaux else np.zeros(0, dtype=np.float32)


class _GainAuto:
    """Niveleur de volume : vise ~-20 dBFS sur la voix, monte doucement, redescend vite.
    Le silence n'est pas amplifié (sinon on remonte le bruit de fond entre les phrases)."""

    CIBLE = -20.0
    MAXI = 24.0     # dB d'amplification automatique au maximum
    SEUIL = -55.0   # en dessous (après sensibilité), c'est du silence : le gain ne bouge plus

    def __init__(self, taux: int):
        self.taux = taux
        self.gain_db = 0.0
        self.energie: float | None = None  # énergie moyenne lissée sur ~0,3 s

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if not len(x):
            return x
        duree = len(x) / self.taux
        energie = float(np.mean(x ** 2))
        lissage = float(np.exp(-duree / 0.3))
        self.energie = energie if self.energie is None else lissage * self.energie + (1 - lissage) * energie
        niveau = db(float(np.sqrt(self.energie)))
        # Plancher de bruit suivi en continu : descend tout de suite, remonte de 1 dB/s.
        self.plancher = min(getattr(self, "plancher", niveau) + 1.0 * duree, niveau)
        precedent = self.gain_db
        if niveau > self.SEUIL and niveau > self.plancher + 10:  # de la voix, pas du bruit de fond
            voulu = float(np.clip(self.CIBLE - niveau, -12.0, self.MAXI))
            vitesse = 40.0 if voulu < self.gain_db else 6.0  # dB/s : baisse rapide, remontée douce
            pas = vitesse * duree
            self.gain_db += float(np.clip(voulu - self.gain_db, -pas, pas))
        rampe = np.linspace(10 ** (precedent / 20), 10 ** (self.gain_db / 20), len(x), dtype=np.float32)
        return x * rampe


def limiter(x: np.ndarray, seuil: float = 0.85) -> np.ndarray:
    """Écrêtage doux : au-dessus du seuil, compression progressive jusqu'à 1 sans jamais le dépasser."""
    a = np.abs(x)
    haut = a > seuil
    if not haut.any():
        return x
    y = x.copy()
    marge = 1.0 - seuil
    y[haut] = np.sign(x[haut]) * (seuil + marge * np.tanh((a[haut] - seuil) / marge))
    return y


class Chaine:
    def __init__(self, taux: int, reglages: Reglages):
        self.taux = taux
        self.reglages = reglages
        self.coupe_bas = _CoupeBas(taux)
        self.reducteur = _ReducteurBruit(taux)
        self.gain_auto = _GainAuto(taux)
        self._en_attente = np.zeros(0, dtype=np.float32)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        r = self.reglages
        x = x.astype(np.float32, copy=False)
        if r.coupe_bas:
            x = self.coupe_bas(x)
        # Le réducteur tourne toujours (il garde son estimation du bruit à jour) : activer ou désactiver
        # l'option en cours d'enregistrement ne crée ni trou ni décalage.
        propre = self.reducteur(x, r.force_reduction)
        x = propre if r.reduction_bruit else self._retarder(x, len(propre))
        x = x * np.float32(10 ** (r.sensibilite_db / 20))
        if r.gain_auto:
            x = self.gain_auto(x)
        return limiter(x)

    def _retarder(self, x: np.ndarray, n: int) -> np.ndarray:
        """Même latence que le réducteur de bruit, pour pouvoir basculer sans à-coup ni décalage."""
        tout = np.concatenate([self._en_attente, x])
        self._en_attente = tout[n:]
        return tout[:n]


def ameliorer(audio: np.ndarray, taux: int, force: float = 0.5) -> np.ndarray:
    """Version « fichier entier » (avant transcription) : voix faibles remontées, bruit de fond atténué."""
    chaine = Chaine(taux, Reglages(gain_auto=True, reduction_bruit=True, force_reduction=force, coupe_bas=True))
    bloc = taux // 10
    morceaux = [chaine(audio[i: i + bloc]) for i in range(0, len(audio), bloc)]
    morceaux.append(chaine(np.zeros(chaine.reducteur.n, dtype=np.float32)))  # vide la latence du réducteur
    return np.concatenate(morceaux)[: len(audio)]
