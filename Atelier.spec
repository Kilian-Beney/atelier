# -*- mode: python ; coding: utf-8 -*-
# Compilation Windows (dist/Atelier/Atelier.exe) et macOS (dist/Atelier.app) :
#   pyinstaller --noconfirm --clean Atelier.spec
# ATELIER_CUDA=1 (Windows) : embarque les DLL CUDA installées par pip (+2 Go). Sinon, l'application
# les télécharge à la demande via le bouton « Activer le GPU NVIDIA ».
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

MAC = sys.platform == "darwin"
VERSION = os.environ.get("ATELIER_VERSION", "1.0.0").lstrip("v")

datas = [("ui", "ui"), ("modeles", "modeles"), ("icone.ico", ".")]
binaries = []
hiddenimports = collect_submodules("scipy") + collect_submodules("sklearn")
datas += collect_data_files("pymupdf") + collect_data_files("faster_whisper")
binaries += collect_dynamic_libs("ctranslate2")

paquets = ["webview", "rembg", "onnxruntime", "pymatting", "pillow_heif", "sherpa_onnx", "sounddevice", "_sounddevice_data"]
if MAC:
    paquets += ["mlx", "mlx_whisper"]
    hiddenimports += ["tiktoken_ext.openai_public", "tiktoken_ext"]
for paquet in paquets:
    d, b, h = collect_all(paquet)
    datas += d
    binaries += b
    hiddenimports += h

# Mise en page : les extensions Markdown (« extra »…) sont chargées par leur nom, invisibles pour PyInstaller.
hiddenimports += collect_submodules("markdown")

# Identification des voix : le module compilé de sherpa-onnx est dans sherpa_onnx/lib, un dossier sans
# __init__.py que PyInstaller ignore. On le copie tel quel avec ses bibliothèques (onnxruntime, API C…).
import sherpa_onnx

dossier_sherpa = os.path.join(os.path.dirname(sherpa_onnx.__file__), "lib")
for fichier in os.listdir(dossier_sherpa):
    if fichier.endswith((".pyd", ".so", ".dll", ".dylib")):
        binaries.append((os.path.join(dossier_sherpa, fichier), "sherpa_onnx/lib"))

if not MAC:
    # pywebview exige les 3 dossiers win-arm64 / win-x64 / win-x86 et WebBrowserInterop.*.dll : copiés tels
    # quels (en « binaires », PyInstaller n'en garde qu'une partie et la fenêtre ne s'ouvre plus).
    import webview

    datas.append((os.path.join(os.path.dirname(webview.__file__), "lib"), "webview/lib"))

    # DLL privées des roues « réparées » (dossiers <paquet>.libs : llvmlite, numpy, scipy, av…).
    # Sans elles, llvmlite.dll ne se charge pas → pymatting → rembg : le détourage ne démarre plus.
    import site

    for racine in site.getsitepackages():
        for dossier in glob.glob(os.path.join(racine, "*.libs")):
            for dll in glob.glob(os.path.join(dossier, "*.dll")):
                binaries.append((dll, os.path.basename(dossier)))

if not MAC and os.environ.get("ATELIER_CUDA") == "1":
    import nvidia

    for base in nvidia.__path__:
        for sous in ("cublas", "cudnn", "cuda_nvrtc"):
            for dll in glob.glob(os.path.join(base, sous, "bin", "*.dll")):
                binaries.append((dll, f"nvidia/{sous}/bin"))

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6", "torch", "nvidia"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Atelier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icone.icns" if MAC else "icone.ico"],
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Atelier")

if MAC:
    app = BUNDLE(
        coll,
        name="Atelier.app",
        icon="icone.icns",
        bundle_identifier="fr.kilianbeney.atelier",
        version=VERSION,
        info_plist={
            "CFBundleName": "Atelier",
            "CFBundleDisplayName": "Atelier",
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            # Sans cette phrase, macOS refuse l'accès au micro (dictaphone silencieux).
            "NSMicrophoneUsageDescription": "Atelier utilise le micro pour le dictaphone. "
                                            "L'audio reste sur ton Mac.",
        },
    )
