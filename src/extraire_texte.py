"""Extraction du texte d'un article à partir de son wikitexte (règles de la tâche 2).

On garde : les paragraphes, les titres de section (chacun sur sa ligne), le texte
affiché des liens (internes et externes) et le texte de chaque cellule des tableaux
(une cellule par ligne, légende du tableau comprise).
On retire : images et fichiers (avec leur légende), galeries, logos et icônes,
catégories, liens interlangues, modèles (bannières, boutons, encadrés, palettes),
notes et références, URL, commentaires, mots magiques, feuilles de style.
{{PAGENAME}} et ses variantes sont remplacés par le titre de la page.
Les restes de balisage laissés par un wikitexte mal formé (crochets ou accolades non
fermés, HTML collé, entités comme &amp;) sont retirés : le texte autour est gardé.
Les éléments d'interface (menu, recherche, barre latérale, onglets) n'existent pas
dans le wikitexte : ils ne sont jamais extraits.
"""
import html
import re
import unicodedata

import mwparserfromhell as mw

# Balises dont tout le contenu est retiré
BALISES_RETIREES = {"ref", "references", "gallery", "templatestyles", "imagemap", "timeline",
                    "graph", "mapframe", "maplink", "score", "chart", "indicator"}
URL = re.compile(r"(?:https?:)?//[^\s\]\)\|]+")
MOT_MAGIQUE = re.compile(r"__[A-Z]+__")
# Mots magiques qui affichent le titre de la page
NOM_PAGE = re.compile(r"\{\{\s*(?:FULL|BASE|SUB|ROOT|ARTICLE)?PAGENAMEE?\s*\}\}")

# Restes de balisage wiki ou HTML qui ne doivent plus figurer dans un texte nettoyé
# (utilisé aussi par src/valider_records.py)
BALISAGE = re.compile(
    r"\[\[|\]\]|\{\{|\}\}|\{\||\|\}"                 # liens, modèles, tableaux
    r"|<ref|</?[a-zA-Z][a-zA-Z0-9]*[\s>/]"                     # balises HTML
    r"|&[a-zA-Z]+;|&#x?[0-9a-fA-F]+;"                          # entités HTML
    r"|__[A-Z]+__|https?://"                                   # mots magiques, adresses web
    r"|(?:^|\|)(?:thumb|thumbnail|vignette|right|left|center|centre|upright|frameless|frame|[0-9]+px)\|"  # paramètres d'image
)
BALISE_HTML = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")
REF_COMPLETE = re.compile(r"<ref[^<>]*>.*?</ref\s*>|<ref[^<>]*/>", re.S | re.I)
PARAMETRE_MODELE = re.compile(r"^\s*\|\s*[^\n|=]{1,60}=.*$", re.M)        # « | nom = valeur » d'un modèle non fermé
LIGNE_TABLEAU = re.compile(r"^\s*(?:\|[-+]?|!)\s*$", re.M)               # « |- », « | », « ! » seuls
_EXT = r"\.(?:jpe?g|png|svg|gif|tiff?|webp|pdf|ogg|ogv|oga|webm|mp3|wav|djvu)\b"
# Lien de fichier cassé : « [[Fichier:Nom du fichier.jpg|… », « Fichier:Nom.jpg|… » en début de ligne,
# ou « nom.jpg| » suivi de paramètres
NOM_FICHIER = re.compile(r"(?<=\[\[)[^\n|\[\]]*?" + _EXT + r"\|?"
                         r"|^[^\n|\[\]:]{1,40}:[^\n|\[\]]*?" + _EXT + r"\|?"
                         r"|[^\s|\[\]]+" + _EXT + r"\|", re.I | re.M)
DEBUT_MODELE = re.compile(r"\{\{[^{}\n|]*")             # « {{Nom du modèle » resté ouvert
DEBUT_TABLEAU = re.compile(r"\{\|[^\n]*")               # « {| style=… » d'un tableau mal formé
PARAMETRE_IMAGE = re.compile(r"(?:(?<=^)|(?<=[\s|\[]))(?:thumb|thumbnail|vignette|mini|miniatur|right|left|center|centre|none|"
                             r"upright(?:=[\d.]+)?|frameless|frame|border|\d+(?:x\d+)?px)\|", re.M | re.I)
PARENTHESES_VIDES = re.compile(r"\s?\(\s*[;,:.]?\s*\)")


def _prefixe(titre):
    """Préfixe d'espace de noms d'un lien interne, en minuscules (None s'il n'y en a pas).
    Un lien qui commence par « : » ([[:Catégorie:X]]) est un lien visible : on le garde."""
    t = titre.strip()
    if t.startswith(":") or ":" not in t:
        return None
    return t.split(":", 1)[0].strip().lower()


def nettoyer_restes(texte):
    """Retire les restes de balisage d'un texte déjà extrait, sans écarter le texte.

    Cas vus au prototype (tâche 4) : crochets ou accolades non fermés dans la source,
    HTML collé par l'outil de traduction, entités déjà encodées (&amp;), paramètres
    d'un modèle resté ouvert, paramètres d'image d'un lien de fichier cassé,
    parenthèses vidées par le retrait d'un modèle de prononciation.
    """
    for _ in range(3):                                      # &amp;amp; -> &amp; -> &
        nouveau = html.unescape(texte)
        if nouveau == texte:
            break
        texte = nouveau
    texte = texte.replace("\ufffd", "")                    # caractère cassé : retiré seul
    texte = REF_COMPLETE.sub("", texte)
    texte = BALISE_HTML.sub("", texte)
    texte = PARAMETRE_MODELE.sub("", texte)
    texte = LIGNE_TABLEAU.sub("", texte)
    texte = NOM_FICHIER.sub("", texte)
    texte = DEBUT_TABLEAU.sub("", DEBUT_MODELE.sub("", texte))
    for _ in range(5):
        nouveau = PARAMETRE_IMAGE.sub("", texte)
        if nouveau == texte:
            break
        texte = nouveau
    texte = MOT_MAGIQUE.sub("", URL.sub("", texte))
    for _ in range(5):                                      # ce qui reste, motif par motif
        nouveau = BALISAGE.sub("", texte)
        if nouveau == texte:
            break
        texte = nouveau
    texte = PARENTHESES_VIDES.sub("", texte)
    return _mise_en_forme(texte)


def _mise_en_forme(texte):
    lignes = [re.sub(r"[ \t]+", " ", l).strip() for l in texte.splitlines()]
    texte = "\n".join(l for l in lignes if l.strip("|!-+ ") or not l)
    texte = re.sub(r"\n{3,}", "\n\n", texte).strip()
    return unicodedata.normalize("NFC", texte)


def extraire_texte(wikitexte, noms_fichier, noms_categorie, codes_interlangue, titre=None):
    """Renvoie le texte de l'article, normalisé en Unicode NFC.

    noms_fichier / noms_categorie : noms (locaux, canoniques et alias) des espaces
    de noms Fichier (6) et Catégorie (14) du wiki, fournis par l'API siteinfo.
    codes_interlangue : codes des autres éditions (liens interlangues [[fr:...]]).
    titre : titre de la page, qui remplace {{PAGENAME}}.
    """
    if titre:
        wikitexte = NOM_PAGE.sub(lambda m: titre, wikitexte)
    retires = {n.lower() for n in noms_fichier} | {n.lower() for n in noms_categorie} \
              | {c.lower() for c in codes_interlangue}
    code = mw.parse(wikitexte)

    for noeud in code.filter_tags(recursive=True):
        if str(noeud.tag).strip().lower() == "br":          # saut de ligne : ne pas coller les mots
            try:
                code.replace(noeud, "\n")
            except ValueError:
                pass
            continue
        if str(noeud.tag).strip().lower() in BALISES_RETIREES:
            try:
                code.remove(noeud)
            except ValueError:
                pass
    for lien in code.filter_wikilinks(recursive=True):
        if _prefixe(str(lien.title)) in retires:
            try:
                code.remove(lien)
            except ValueError:
                pass
    for lien in code.filter_external_links(recursive=True):
        try:
            if lien.title is not None and str(lien.title).strip():
                code.replace(lien, str(lien.title))      # on garde le texte du lien
            else:
                code.remove(lien)                          # lien sans texte : retiré
        except ValueError:
            pass

    # Tableaux : le texte de chaque cellule (et de la légende), une cellule par ligne.
    # Les tableaux imbriqués sont traités en premier (ordre inverse).
    for table in reversed(code.filter_tags(recursive=True, matches=lambda n: str(n.tag).strip().lower() == "table")):
        lignes = []
        for el in table.contents.filter_tags(recursive=True,
                                             matches=lambda n: str(n.tag).strip().lower() in ("caption", "th", "td")):
            t = el.contents.strip_code(normalize=True, collapse=True)
            # La légende « |+ … » est lue comme une cellule placée hors de toute ligne.
            # Comparaison par identité (is) : « el in nodes » compare les textes des nœuds un à un
            # et devient extrêmement lent sur les très grands tableaux (ex. ary, 47 000 lignes).
            if t.lstrip().startswith("+") and any(n is el for n in table.contents.nodes):
                t = t.lstrip()[1:]
            t = " ".join(URL.sub("", t).split())
            if t:
                lignes.append(t)
        try:
            code.replace(table, "\n" + "\n".join(lignes) + "\n" if lignes else "")
        except ValueError:
            pass

    # Titre de section resté sans contenu (ex. « Références » une fois les notes retirées) : retiré
    for section in code.get_sections(include_lead=False, flat=False):
        titres = section.filter_headings(recursive=False)
        if not titres:
            continue
        corps = str(section).replace(str(titres[0]), "", 1)
        if not URL.sub("", mw.parse(corps).strip_code(normalize=True, collapse=True)).strip():
            try:
                code.remove(titres[0])
            except ValueError:
                pass

    texte = code.strip_code(normalize=True, collapse=True)
    texte = MOT_MAGIQUE.sub("", URL.sub("", texte))
    return nettoyer_restes(_mise_en_forme(texte))
