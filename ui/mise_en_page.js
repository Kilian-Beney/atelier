// Mise en page : notes → belle page (IA locale Ollama) → PDF.
const AIDES_MODES = {
  fidele: "Tout le contenu est gardé, corrigé et bien organisé.",
  fiche: "Résumé de l'essentiel : notions, définitions, exemples clés.",
};
const MEP = { mode: "fidele", theme: "moderne", enCours: false, iaPrete: false, aUnResultat: false, verification: false };

function mepBoutons() {
  const notes = $("mep-notes").value.trim();
  $("mep-lancer").disabled = !MEP.iaPrete || (!MEP.enCours && !notes);
  $("mep-copier").disabled = $("mep-pdf").disabled = $("mep-gdocs").disabled = !MEP.aUnResultat || MEP.enCours;
}
function mepCompter() {
  const mots = $("mep-notes").value.trim().split(/\s+/).filter(Boolean).length;
  const minutes = Math.max(1, Math.round(mots * 1.6 / 40 / 60));
  $("mep-compteur").textContent = mots ? `${mots} mots · environ ${minutes} min de mise en page` : "";
  mepBoutons();
}
function mepZoom() {
  const dispo = $("mep-apercu").clientWidth - 48;
  if (dispo > 0) $("mep-page").style.zoom = Math.min(1, dispo / 794);
}

segmente("mep-modes", null, (v) => { if (MEP.enCours) return; MEP.mode = v; $("mep-aide-mode").textContent = AIDES_MODES[v]; R.mep_mode = v; sauverReglages(); });
$("mep-themes").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) mepTheme(b.dataset.theme);
});
function mepTheme(t) {
  MEP.theme = t;
  document.querySelectorAll("#mep-themes button").forEach((x) => x.classList.toggle("actif", x.dataset.theme === t));
  $("mep-page").className = "page theme-" + t;
  R.mep_theme = t;
  sauverReglages();
}
$("mep-notes").addEventListener("input", mepCompter);

// Remplit les notes (depuis la transcription, un fichier ouvert ou déposé).
window.mepRemplir = (texte, nom) => {
  if (MEP.enCours) { toast("Une mise en page est en cours : attends la fin ou arrête-la.", "ko"); return; }
  $("mep-notes").value = texte;
  mepCompter();
  statut("mep", `Texte de « ${nom} » chargé. Clique sur « Mettre en page ».`);
};
$("mep-ouvrir").addEventListener("click", async () => {
  const f = await api.mise_en_page.ouvrir_fichier();
  if (f) window.mepRemplir(f.texte, f.nom);
});
receveurs["mise-en-page"] = async (chemins) => {
  const f = await api.mise_en_page.lire(chemins[0]);
  if (f) window.mepRemplir(f.texte, f.nom);
  else toast("Ce fichier ne peut pas être lu comme du texte.", "ko");
};

$("mep-lancer").addEventListener("click", async () => {
  if (MEP.enCours) { api.mise_en_page.arreter(); $("mep-lancer").disabled = true; $("mep-lancer").textContent = "Arrêt…"; return; }
  MEP.enCours = true;
  MEP.aUnResultat = false;
  $("mep-lancer").textContent = "Arrêter";
  $("mep-lancer").classList.add("arret");
  $("mep-page").contentEditable = "false";
  $("mep-page").innerHTML = "";
  $("mep-indice").textContent = "";
  statut("mep", "L'IA lit tes notes…");
  progression("mep", null);
  mepBoutons();
  const r = await api.mise_en_page.lancer($("mep-notes").value, MEP.mode);
  MEP.enCours = false;
  $("mep-lancer").textContent = "✨ Mettre en page";
  $("mep-lancer").classList.remove("arret");
  $("mep-page").classList.remove("clignote");
  if (r.erreur) { statut("mep", "Erreur : " + r.erreur, "ko"); progression("mep", 0); }
  else if (r.arrete) statut("mep", "Arrêté. Tu peux garder ce qui a été fait ou relancer.", "attente");
  else { statut("mep", `✓ Page prête en ${r.duree} s. Clique dans la page pour la retoucher, puis exporte en PDF.`, "ok"); progression("mep", 1); }
  MEP.aUnResultat = $("mep-page").textContent.trim().length > 0;
  if (MEP.aUnResultat) {
    $("mep-page").contentEditable = "true";
    $("mep-indice").textContent = "✎ Clique dans la page pour la retoucher";
    $("mep-apercu").scrollTop = 0;
  }
  mepBoutons();
});

sur("mep_page", (d) => {
  const apercu = $("mep-apercu");
  const enBas = apercu.scrollTop + apercu.clientHeight >= apercu.scrollHeight - 80;
  $("mep-page").innerHTML = d.html;
  $("mep-page").classList.add("clignote");
  if (enBas) apercu.scrollTop = apercu.scrollHeight;
  progression("mep", (d.bloc + Math.min(d.mots_ecrits / Math.max(d.mots_bloc, 1), 0.98)) / d.nb);
  statut("mep", d.nb > 1 ? `Mise en page… partie ${d.bloc + 1}/${d.nb}` : "Mise en page…");
});

$("mep-pdf").addEventListener("click", async () => {
  $("mep-pdf").disabled = true;
  statut("mep", "Création du PDF…");
  progression("mep", null);
  const titre = ($("mep-page").querySelector("h1")?.textContent || "Notes").trim();
  const r = await api.mise_en_page.exporter_pdf($("mep-page").innerHTML, MEP.theme, titre);
  progression("mep", r && r.chemin ? 1 : 0);
  if (!r) statut("mep", "Export annulé.");
  else if (r.erreur) statut("mep", "Erreur : " + r.erreur, "ko");
  else statut("mep", `✓ PDF enregistré : ${r.chemin}`, "ok");
  mepBoutons();
});
// Copie la page affichée : le navigateur embarque les styles du thème (couleurs, encadrés) dans le presse-papiers.
function mepCopier() {
  const sel = window.getSelection();
  const plage = document.createRange();
  plage.selectNodeContents($("mep-page"));
  sel.removeAllRanges(); sel.addRange(plage);
  const ok = document.execCommand("copy");
  sel.removeAllRanges();
  return ok;
}
$("mep-copier").addEventListener("click", () => {
  if (mepCopier()) statut("mep", "✓ Copié avec la mise en forme : colle-le dans Google Docs ou Word.", "ok");
});
$("mep-gdocs").addEventListener("click", () => {
  if (!mepCopier()) { statut("mep", "Impossible de copier la page.", "ko"); return; }
  api.ouvrir_google_docs();
  statut("mep", `✓ Page copiée : colle-la (${coller()}) dans le Google Docs qui vient de s'ouvrir.`, "ok");
});

// L'IA (Ollama) n'est démarrée qu'à la première ouverture de l'outil : pas de charge inutile au lancement.
async function mepVerifierIA() {
  const e = await api.mise_en_page.etat();
  if (e.pret) { MEP.iaPrete = true; badge("mep-badge", "IA prête", "ok"); }
  else { badge("mep-badge", e.message, e.bloquant ? "ko" : "attente"); setTimeout(mepVerifierIA, 4000); }
  mepBoutons();
}
document.addEventListener("outil", (e) => {
  if (e.detail !== "mise-en-page") return;
  requestAnimationFrame(mepZoom);
  if (!MEP.verification) { MEP.verification = true; badge("mep-badge", "Démarrage de l'IA…", "attente"); mepVerifierIA(); }
});
window.addEventListener("resize", mepZoom);

demarrage(() => {
  if (R.mep_mode) { MEP.mode = R.mep_mode; document.querySelectorAll("#mep-modes button").forEach((b) => b.classList.toggle("actif", b.dataset.mode === MEP.mode)); }
  if (R.mep_theme) mepTheme(R.mep_theme);
  $("mep-aide-mode").textContent = AIDES_MODES[MEP.mode];
  mepCompter();
});
