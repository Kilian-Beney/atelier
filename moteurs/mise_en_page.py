"""Moteur de mise en page : notes brutes → Markdown structuré (IA locale via Ollama) → HTML."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

import markdown

from plateforme import MAC, WINDOWS, sans_fenetre

OLLAMA = "http://127.0.0.1:11434"
MODELE = "qwen2.5:7b"
MOTS_PAR_BLOC = 900

CONSIGNES_COMMUNES = """Tu es un assistant de mise en page de notes de cours pour un étudiant français.
Tu reçois des notes brutes (tapées vite, pleines de fautes, parfois une transcription orale).
Tu réponds UNIQUEMENT en Markdown, sans aucune phrase d'introduction ni de conclusion de ta part.

Règles de mise en forme :
- Corrige l'orthographe, la grammaire et la ponctuation. Rétablis les mots mal tapés d'après le contexte.
- N'invente AUCUNE information qui n'est pas dans les notes.
- Organise en sections avec des titres « ## » et, si utile, des sous-titres « ### ».
- Utilise des listes à puces « - » pour les énumérations.
- Mets en **gras** les notions et mots-clés importants.
- Pour une définition, écris exactement : > **Définition — terme :** explication
- Pour un exemple, écris exactement : > **Exemple :** texte
- Pour un point essentiel à retenir, écris exactement : > **À retenir :** texte
- Pour une mise en garde ou une exception, écris exactement : > **Attention :** texte
- Les encadrés (lignes « > ») se placent DANS la section concernée : ne crée jamais de section « Attention »,
  « Exemple » ou « À retenir ».
- Titres à la française : majuscule uniquement au premier mot et aux noms propres.
- N'ajoute pas d'explication d'un mot si elle n'est pas dans les notes.

EXEMPLE (pour le format uniquement, n'en reprends pas le contenu) :

Notes brutes :
histoire moderne cours 2
la renaissance = periode de renouveau culturel en europe 15e 16e siecle
cause : redecouverte des textes antiques, imprimerie (gutenberg vers 1450)
ex : leonard de vinci peintre ingenieur
attention la renaissance commence en italie pas en france
a retenir l imprimerie diffuse les idées tres vite

Résultat attendu :
# Histoire moderne — cours 2

## La Renaissance

> **Définition — Renaissance :** période de renouveau culturel en Europe aux XVe et XVIe siècles.

Ses causes principales :
- la **redécouverte des textes antiques** ;
- l'**imprimerie**, inventée par Gutenberg vers 1450.

> **Exemple :** Léonard de Vinci, peintre et ingénieur.

> **Attention :** la Renaissance commence en Italie, pas en France.

> **À retenir :** l'imprimerie diffuse les idées très rapidement.

FIN DE L'EXEMPLE."""

MODES = {
    "fidele": "Garde TOUT le contenu des notes : reformule proprement, mais ne résume pas et ne supprime rien d'important.",
    "fiche": "Transforme les notes en fiche de révision synthétique : garde l'essentiel (notions, définitions, "
             "exemples clés), supprime les répétitions et les digressions orales.",
}


class OllamaIndisponible(Exception):
    pass


def _requete(chemin: str, donnees: dict | None = None, timeout: float = 5):
    corps = json.dumps(donnees).encode() if donnees is not None else None
    req = urllib.request.Request(OLLAMA + chemin, data=corps, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _executable_ollama() -> list[str] | None:
    """Commande qui lance le serveur Ollama (Windows, macOS ou Linux)."""
    if WINDOWS:
        exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        return [str(exe), "serve"] if exe.exists() else None
    if MAC and Path("/Applications/Ollama.app").exists():
        return ["open", "-g", "-a", "Ollama"]  # l'application Ollama démarre son serveur en arrière-plan
    # Une application macOS n'hérite pas du PATH du Terminal : on regarde aussi les emplacements habituels.
    for chemin in (shutil.which("ollama"), "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama"):
        if chemin and Path(chemin).exists():
            return [chemin, "serve"]
    return None


def demarrer_ollama() -> None:
    """Lance le serveur Ollama s'il ne tourne pas déjà."""
    try:
        _requete("/api/version").close()
        return
    except (urllib.error.URLError, OSError):
        pass
    commande = _executable_ollama()
    if not commande:
        raise OllamaIndisponible("Ollama n'est pas installé (ollama.com).")
    subprocess.Popen(commande, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **sans_fenetre())
    for _ in range(40):
        time.sleep(0.5)
        try:
            _requete("/api/version").close()
            return
        except (urllib.error.URLError, OSError):
            continue
    raise OllamaIndisponible("Impossible de démarrer Ollama.")


def modele_present() -> bool:
    with _requete("/api/tags") as r:
        noms = [m["name"] for m in json.load(r).get("models", [])]
    return any(n == MODELE or n.startswith(MODELE + "-") for n in noms)


def telecharger_modele() -> Iterator[dict]:
    """Demande à Ollama de télécharger le modèle ; produit les étapes de progression."""
    with _requete("/api/pull", {"model": MODELE, "stream": True}, timeout=3600) as r:
        for ligne in r:
            etape = json.loads(ligne)
            if "error" in etape:
                raise OllamaIndisponible(etape["error"])
            yield etape


def decouper(notes: str, mots_max: int = MOTS_PAR_BLOC) -> list[str]:
    """Découpe les notes en blocs d'environ mots_max mots, en coupant entre les lignes."""
    blocs: list[str] = []
    courant: list[str] = []
    compte = 0
    for ligne in notes.strip().splitlines():
        n = len(ligne.split())
        if courant and compte + n > mots_max:
            blocs.append("\n".join(courant))
            courant, compte = [], 0
        # Une transcription peut n'être qu'une seule ligne géante : on la coupe par phrases.
        if n > mots_max:
            phrases = re.split(r"(?<=[.!?])\s+", ligne)
            for p in phrases:
                m = len(p.split())
                if courant and compte + m > mots_max:
                    blocs.append(" ".join(courant))
                    courant, compte = [], 0
                courant.append(p)
                compte += m
            continue
        courant.append(ligne)
        compte += n
    if courant:
        blocs.append("\n".join(courant))
    return blocs


def generer(notes: str, mode: str, arret: Callable[[], bool]) -> Iterator[tuple[str, int, int]]:
    """Produit (morceau_markdown, bloc_courant, nb_blocs) au fil de la génération."""
    blocs = decouper(notes)
    for i, bloc in enumerate(blocs):
        if i == 0:
            position = "Commence par un titre principal « # » qui résume le sujet des notes."
        else:
            position = ("Ces notes sont la SUITE d'un document déjà commencé : ne mets PAS de titre « # », "
                        "commence directement par une section « ## » ou par le texte.")
        systeme = f"{CONSIGNES_COMMUNES}\n- {MODES[mode]}\n- {position}"
        donnees = {
            "model": MODELE,
            "stream": True,
            "messages": [
                {"role": "system", "content": systeme},
                {"role": "user", "content": f"Voici les notes à mettre en page :\n\n{bloc}"},
            ],
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        if i > 0:
            yield "\n\n", i, len(blocs)
        with _requete("/api/chat", donnees, timeout=600) as r:
            for ligne in r:
                if arret():
                    return
                morceau = json.loads(ligne)
                if "error" in morceau:
                    raise OllamaIndisponible(morceau["error"])
                texte = morceau.get("message", {}).get("content", "")
                if texte:
                    yield texte, i, len(blocs)
                if morceau.get("done"):
                    break


CLASSES_ENCADRES = {
    "définition": "definition",
    "exemple": "exemple",
    "à retenir": "retenir",
    "attention": "attention",
}


LISTE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
ENCADRE_EN_LISTE = re.compile(r"^\s*(?:[-*+]\s+)?(\*\*(?:définition|exemple|à retenir|attention)\b.*)$", re.IGNORECASE)


def _normaliser(md: str) -> str:
    """Corrige les écarts de Markdown fréquents chez les IA (liste collée à un paragraphe, bloc de code,
    encadré écrit comme un élément de liste)."""
    md = re.sub(r"^```(?:markdown)?\s*$", "", md, flags=re.MULTILINE)
    lignes: list[str] = []
    for ligne in md.split("\n"):
        encadre = ENCADRE_EN_LISTE.match(ligne)
        if encadre:
            lignes += ["", "> " + encadre.group(1), ""]
            continue
        precedente = lignes[-1] if lignes else ""
        if (LISTE.match(ligne) and precedente.strip() and not LISTE.match(precedente)
                and not precedente.lstrip().startswith((">", "#"))):
            lignes.append("")
        lignes.append(ligne)
    return "\n".join(lignes)


def _classe_encadre(paragraphe: str) -> str | None:
    m = re.match(r"<p><strong>([^<]{0,40})", paragraphe)
    if not m:
        return None
    debut = m.group(1).lower()
    return next((c for cle, c in CLASSES_ENCADRES.items() if debut.startswith(cle)), None)


def _separer_encadres(m: re.Match) -> str:
    """Un bloc de citation peut contenir plusieurs encadrés à la suite : un encadré par type."""
    paragraphes = re.findall(r"<p>.*?</p>|<ul>.*?</ul>|<ol>.*?</ol>", m.group(1), flags=re.DOTALL)
    if not paragraphes:
        return m.group(0)
    groupes: list[tuple[str | None, list[str]]] = []
    for p in paragraphes:
        classe = _classe_encadre(p)
        if classe or not groupes:
            groupes.append((classe, [p]))
        else:
            groupes[-1][1].append(p)
    return "\n".join(
        (f'<blockquote class="encadre {c}">' if c else "<blockquote>") + "\n".join(ps) + "</blockquote>"
        for c, ps in groupes
    )


def en_html(md: str) -> str:
    """Markdown → HTML, avec des classes sur les encadrés (définition, exemple, …)."""
    html = markdown.markdown(_normaliser(md), extensions=["extra", "sane_lists"])
    return re.sub(r"<blockquote>(.*?)</blockquote>", _separer_encadres, html, flags=re.DOTALL)


def trouver_navigateur() -> str | None:
    """Navigateur Chromium (Edge, Chrome, Brave…) capable d'imprimer une page en PDF sans fenêtre."""
    if WINDOWS:
        candidats = [Path(base) / chemin
                     for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                                  os.environ.get("LOCALAPPDATA")) if base
                     for chemin in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe",
                                    "BraveSoftware/Brave-Browser/Application/brave.exe")]
    elif MAC:
        candidats = [Path(base) / f"{nom}.app" / "Contents" / "MacOS" / nom
                     for base in ("/Applications", Path.home() / "Applications")
                     for nom in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc")]
    else:
        candidats = [Path(p) for p in map(shutil.which, ("google-chrome", "chromium", "microsoft-edge")) if p]
    return next((str(c) for c in candidats if c.exists()), None)


def exporter_pdf(page_html: str, destination: str) -> None:
    """Imprime la page HTML en PDF avec un navigateur Chromium (sans fenêtre)."""
    navigateur = trouver_navigateur()
    if not navigateur:
        raise RuntimeError("Aucun navigateur compatible (Chrome, Edge ou Brave) n'a été trouvé : "
                           "installe Google Chrome pour exporter en PDF.")
    with tempfile.TemporaryDirectory() as dossier:
        source = Path(dossier) / "page.html"
        source.write_text(page_html, encoding="utf-8")
        subprocess.run(
            [navigateur, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
             f"--user-data-dir={Path(dossier) / 'profil'}", f"--print-to-pdf={destination}", source.as_uri()],
            check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **sans_fenetre(),
        )
    if not Path(destination).exists():
        raise RuntimeError("Le navigateur n'a pas produit le PDF.")
