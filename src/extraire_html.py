"""Extraction du texte d'une page à partir de son rendu HTML (API MediaWiki, action=parse).

Utilisé quand le wikitexte ne donne aucun texte : page faite uniquement de modèles
(pages d'années en amharique, par exemple) ou page d'accueil du wiki.
Mêmes règles que pour le wikitexte (tâche 2) :
- on garde les paragraphes, les titres de section, le texte des liens, les listes et
  le texte de chaque cellule des tableaux (une cellule par ligne) ;
- on retire les images et leurs légendes, logos et icônes, boutons, champs de saisie,
  bannières et bandeaux, notes et références, liens « modifier », sommaire, catégories.
"""
import re

from bs4 import BeautifulSoup, NavigableString

from extraire_texte import nettoyer_restes

# Éléments retirés avec tout leur contenu
RETIRES = [
    "style", "script", "noscript", "link", "meta", "img", "picture", "svg", "audio", "video",
    "figure", "figcaption", "[typeof^='mw:File']", ".mw-file-description", ".infobox-image", ".infobox-caption", "map", "math", "input", "button", "select", "textarea", "form",
    "sup.reference", "ol.references", ".reference", ".references", ".mw-references-wrap",
    ".mw-cite-backlink", ".mw-editsection", ".thumb", ".gallery", ".mw-gallery-traditional",
    ".noprint", ".metadata", ".ambox", ".mbox-small", ".ombox", ".tmbox", ".navbox", ".vertical-navbox",
    ".catlinks", "#toc", ".toc", ".mw-jump-link", ".mw-empty-elt", ".sistersitebox",
    "[style*='display:none']", "[style*='display: none']",
]
BLOCS = {"p", "div", "li", "dd", "dt", "ul", "ol", "dl", "h1", "h2", "h3", "h4", "h5", "h6",
         "blockquote", "pre", "center", "section", "br", "tr", "table", "caption", "hr"}


def _texte_cellule(el):
    return " ".join(el.get_text(" ").split())


def extraire_texte_html(page_html):
    """Renvoie le texte affiché de la page, normalisé en Unicode NFC (chaîne vide si aucun texte)."""
    soupe = BeautifulSoup(page_html, "html.parser")
    for selecteur in RETIRES:
        for el in soupe.select(selecteur):
            el.decompose()

    # Tableaux : le texte de chaque cellule (et de la légende), une cellule par ligne.
    # Les tableaux imbriqués sont traités en premier (ordre inverse).
    for table in reversed(soupe.find_all("table")):
        lignes = [t for el in table.find_all(["caption", "th", "td"]) if (t := _texte_cellule(el))]
        table.replace_with(NavigableString("\n" + "\n".join(lignes) + "\n" if lignes else ""))

    # Retour à la ligne autour des blocs ; les éléments en ligne (liens, gras) restent collés au texte
    for el in soupe.find_all(BLOCS):
        el.insert_before(NavigableString("\n"))
        el.insert_after(NavigableString("\n"))

    texte = soupe.get_text("")
    texte = re.sub(r"\n\s*\n+", "\n\n", texte)
    return nettoyer_restes(texte)
