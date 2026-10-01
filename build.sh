#!/bin/bash
# Compile l'application pour Mac à puce Apple : dist/Atelier.app puis Atelier-Mac.dmg
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/pip install --no-deps mlx-whisper==0.4.3
fi
[ -f icone.icns ] || .venv/bin/python creer_icone.py
.venv/bin/pyinstaller --noconfirm --clean Atelier.spec
# Signature « ad hoc » (obligatoire sur puce Apple pour que l'application se lance)
codesign --force --deep --sign - dist/Atelier.app
# Image disque avec un raccourci vers Applications (glisser-déposer pour installer)
rm -rf dist/dmg && mkdir -p dist/dmg && cp -R dist/Atelier.app dist/dmg/ && ln -s /Applications dist/dmg/Applications
hdiutil create -volname Atelier -srcfolder dist/dmg -ov -format UDZO dist/Atelier-Mac.dmg
echo "Terminé : dist/Atelier-Mac.dmg"
