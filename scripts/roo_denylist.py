#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Injection sure, reversible et idempotente de la liste des commandes refusees
par Roo Code dans la base d'etat de VS Code (state.vscdb).

Emplacement reel du reglage (Roo Code 3.5x) :
    base  : %APPDATA%\\Code\\User\\globalStorage\\state.vscdb
    table : ItemTable
    cle   : RooVeterinaryInc.roo-cline
    champ : deniedCommands  (tableau JSON de chaines, au premier niveau)

Le script ne touche qu'a ce seul tableau : tous les autres champs de l'objet
JSON sont conserves a l'identique (verification explicite avant/apres).

Utilisation :
    python scripts/roo_denylist.py --db <chemin>                 # dry-run
    python scripts/roo_denylist.py --db <chemin> --apply         # ecriture
    python scripts/roo_denylist.py --db <chemin> --apply --force # VS Code ouvert
    python scripts/roo_denylist.py --db <chemin> --key <cle>     # cle imposee

Aucune dependance externe (bibliotheque standard uniquement, Python 3.10+).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Liste des commandes a refuser (une entree par ligne, chaine exacte)
# ---------------------------------------------------------------------------
COMMANDES_REFUSEES: tuple[str, ...] = (
    "del /s",
    "del /f",
    "del C:\\",
    "erase /s",
    "rd /s",
    "rmdir /s",
    "rm -rf",
    "format",
    "diskpart",
    "cipher /w",
    "vssadmin delete",
    "wmic shadowcopy delete",
    "wbadmin delete",
    "bcdedit",
    "bootrec",
    "reg delete",
    "reg add HKLM",
    "reg save",
    "regedit",
    "secedit",
    "wevtutil cl",
    "net user",
    "net localgroup",
    "runas",
    "takeown",
    "icacls",
    "cacls",
    "cmdkey",
    "ntdsutil",
    "procdump",
    "taskkill /f",
    "sc create",
    "sc delete",
    "sc stop",
    "schtasks /create",
    "schtasks /delete",
    "net stop",
    "curl",
    "certutil -urlcache",
    "certutil -decode",
    "bitsadmin",
    "ftp",
    "scp",
    "ssh",
    "nc",
    "telnet",
    "mshta",
    "rundll32",
    "regsvr32",
    "wscript",
    "cscript",
    "powershell",
    "pwsh",
    "cmd /c",
    "netsh advfirewall",
    "netsh int ip reset",
    "shutdown",
    "logoff",
    "git reset --hard",
    "git clean",
    "git push --force",
    "git branch -D",
    "git stash clear",
    "git filter-branch",
    "git reflog expire",
    "python -c",
    "python -",
    "pip uninstall",
    "npm install -g",
    "*format.com*",
    "*diskpart*",
    "*Remove-Item*",
    "*git reset --hard*",
)

# Nom du champ a modifier et cle preferee dans la base
NOM_CHAMP = "deniedCommands"
CLE_PREFEREE = "RooVeterinaryInc.roo-cline"
MOTIF_CLE_ROO = re.compile(r"roo", re.IGNORECASE)

# Chemin de la base "vivante" (celle utilisee par VS Code en cours d'execution)
CHEMIN_VIVANT = (
    Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "globalStorage" / "state.vscdb"
)


class ErreurInjection(RuntimeError):
    """Erreur fonctionnelle (message explicite pour l'utilisateur)."""


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def horodatage() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def normaliser(entree: str) -> str:
    """Forme de comparaison utilisee pour la deduplication."""
    return " ".join(entree.split()).casefold()


# Editeurs derives de VS Code : ils partagent tous %APPDATA%\Code\User,
# donc tous peuvent ecrire dans la meme base state.vscdb.
MOTIF_EDITEUR = re.compile(
    r"^(code|code - insiders|cursor|codium|trae|windsurf|positron|devin)\.exe$",
    re.IGNORECASE,
)


def editeurs_actifs() -> list[str]:
    """Liste les processus d'editeurs VS Code-like actuellement actifs.

    On ne se limite pas a code.exe : sur cette machine l'editeur reel est
    Cursor.exe (base VS Code), qui utilise les memes chemins %APPDATA%\\Code.
    Un journal SQLite (-wal/-shm) present pres de la base vivante est aussi
    un indice fort que la base est ouverte par un processus.
    """
    try:
        res = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    trouves: set[str] = set()
    for ligne in (res.stdout or "").splitlines():
        champs = ligne.strip().split(",")
        if not champs:
            continue
        nom = champs[0].strip().strip('"')
        if MOTIF_EDITEUR.match(nom):
            trouves.add(nom.lower())
    for suffixe in ("-wal", "-shm"):
        if CHEMIN_VIVANT.with_name(CHEMIN_VIVANT.name + suffixe).exists():
            trouves.add("journal " + suffixe + " present")
    return sorted(trouves)


def code_est_ouvert() -> bool:
    """Vrai si un editeur VS Code-like est actif (compatibilite ancienne API)."""
    return bool(editeurs_actifs())


def meme_fichier(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def connecter(db: Path, lecture_seule: bool) -> sqlite3.Connection:
    """Ouvre la base ; en lecture seule si demande (mode=ro)."""
    if lecture_seule:
        uri = "file:" + db.resolve().as_posix() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=30)
    return sqlite3.connect(str(db), timeout=30, isolation_level=None)


# ---------------------------------------------------------------------------
# Localisation de l'objet d'approbation
# ---------------------------------------------------------------------------
def tables_avec_cle_valeur(con: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Renvoie les tables possedant deux colonnes assimilables a (cle, valeur)."""
    resultat: list[tuple[str, str, str]] = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        colonnes = [d[1] for d in con.execute(f'PRAGMA table_info("{table}")')]
        basses = [c.lower() for c in colonnes]
        if "key" in basses and "value" in basses:
            resultat.append(
                (table, colonnes[basses.index("key")], colonnes[basses.index("value")])
            )
    return resultat


def candidats(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Recense les entrees dont la valeur JSON est un objet contenant le champ."""
    trouves: list[dict[str, Any]] = []
    for table, col_cle, col_val in tables_avec_cle_valeur(con):
        requete = f'SELECT "{col_cle}", "{col_val}" FROM "{table}"'
        for cle, valeur in con.execute(requete):
            if not isinstance(cle, str) or not MOTIF_CLE_ROO.search(cle):
                continue
            if not isinstance(valeur, str):
                continue  # valeur binaire/compressée : non exploitable
            try:
                donnees = json.loads(valeur)
            except (ValueError, TypeError):
                continue
            if isinstance(donnees, dict) and NOM_CHAMP in donnees:
                trouves.append(
                    {
                        "table": table,
                        "col_cle": col_cle,
                        "col_val": col_val,
                        "cle": cle,
                        "donnees": donnees,
                        "json_brut": valeur,
                    }
                )
    return trouves


def choisir_candidat(
    liste: list[dict[str, Any]], cle_imposee: str | None
) -> dict[str, Any]:
    if not liste:
        raise ErreurInjection(
            f"Aucune entree 'roo*' contenant un objet JSON avec la cle '{NOM_CHAMP}' "
            "n'a ete trouvee dans cette base.\n"
            "-> Ouvrez une fois les parametres de Roo Code (Auto-Approve) afin "
            "d'initialiser le reglage, puis relancez le script."
        )
    if cle_imposee:
        for cand in liste:
            if cand["cle"].casefold() == cle_imposee.casefold():
                return cand
        raise ErreurInjection(
            f"La cle '{cle_imposee}' n'a pas ete trouvee (ou ne contient pas "
            f"'{NOM_CHAMP}'). Cles disponibles : "
            + ", ".join(sorted(c["cle"] for c in liste))
        )
    for cand in liste:
        if cand["cle"].casefold() == CLE_PREFEREE.casefold():
            return cand
    return liste[0]


# ---------------------------------------------------------------------------
# Fusion (union) et controles
# ---------------------------------------------------------------------------
def fusionner(existant: list[str]) -> tuple[list[str], list[str]]:
    """Union : ordre existant conserve, puis ajouts sans doublon.

    La deduplication est insensible a la casse et aux espaces multiples.
    """
    vues = {normaliser(e) for e in existant}
    ajouts: list[str] = []
    for entree in COMMANDES_REFUSEES:
        marque = normaliser(entree)
        if marque in vues:
            continue
        vues.add(marque)
        ajouts.append(entree)
    return existant + ajouts, ajouts


def verifier_invariants(
    avant: dict[str, Any], apres: dict[str, Any], attendu: list[str]
) -> None:
    """Verifie que seul le tableau cible a change, sans changer de type."""
    if set(avant.keys()) != set(apres.keys()):
        manquantes = set(avant) - set(apres)
        ajoutees = set(apres) - set(avant)
        raise ErreurInjection(
            f"Le nombre de champs a change (manquants={sorted(manquantes)}, "
            f"ajoutes={sorted(ajoutees)})."
        )
    for cle in avant:
        type_avant = type(avant[cle]).__name__
        type_apres = type(apres[cle]).__name__
        if type_avant != type_apres:
            raise ErreurInjection(
                f"Le type du champ '{cle}' a change ({type_avant} -> {type_apres})."
            )
    for cle in avant:
        if cle == NOM_CHAMP:
            continue
        if avant[cle] != apres[cle]:
            raise ErreurInjection(f"Le champ '{cle}' a ete modifie par erreur.")
    if apres[NOM_CHAMP] != attendu:
        raise ErreurInjection("Le tableau des commandes refusees n'est pas celui attendu.")
    if not all(isinstance(e, str) for e in apres[NOM_CHAMP]):
        raise ErreurInjection("Le tableau des commandes refusees contient un non-chaine.")


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def afficher_etat(titre: str, liste: list[str]) -> None:
    print(f"{titre} ({len(liste)} entree(s))")
    for entree in liste:
        print(f"    - {entree}")


def afficher_diff(existant: list[str], ajouts: list[str]) -> None:
    print("  diff lisible :")
    print(f"    conservees : {len(existant)}")
    print(f"    ajoutees   : {len(ajouts)}")
    for entree in ajouts:
        print(f"      + {entree}")
    print("    supprimees : 0")


# ---------------------------------------------------------------------------
# Ecriture
# ---------------------------------------------------------------------------
def appliquer(
    db: Path,
    cand: dict[str, Any],
    attendu: list[str],
    ajouts: list[str],
) -> Path:
    sauvegarde = db.with_name(f"{db.name}.bak-{horodatage()}")
    shutil.copy2(db, sauvegarde)  # sauvegarde obligatoire avant toute ecriture

    donnees_origine = cand["donnees"]
    nouvelles = dict(donnees_origine)
    nouvelles[NOM_CHAMP] = attendu
    # Serialisation compacte, identique en semantique au JSON d'origine
    nouveau_json = json.dumps(nouvelles, ensure_ascii=False, separators=(",", ":"))

    con = connecter(db, lecture_seule=False)
    try:
        con.execute("BEGIN IMMEDIATE")
        curseur = con.execute(
            f'UPDATE "{cand["table"]}" SET "{cand["col_val"]}" = ? '
            f'WHERE "{cand["col_cle"]}" = ?',
            (nouveau_json, cand["cle"]),
        )
        if curseur.rowcount != 1:
            raise ErreurInjection(
                f"Nombre de lignes mises a jour inattendu : {curseur.rowcount}."
            )
        con.execute("COMMIT")
        # Consolide le journal WAL pour que la valeur soit bien dans le fichier
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    except BaseException as exc:  # noqa: BLE001 - on restaure en cas de souci
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        con.close()
        shutil.copy2(sauvegarde, db)  # restauration depuis la sauvegarde
        if isinstance(exc, ErreurInjection):
            raise
        raise ErreurInjection(f"Echec de l'ecriture SQLite : {exc}") from exc

    # Relecture et validation de l'aller-retour JSON
    try:
        valeur = con.execute(
            f'SELECT "{cand["col_val"]}" FROM "{cand["table"]}" '
            f'WHERE "{cand["col_cle"]}" = ?',
            (cand["cle"],),
        ).fetchone()
        if valeur is None:
            raise ErreurInjection("La cle a disparu apres ecriture.")
        apres = json.loads(valeur[0])
        verifier_invariants(donnees_origine, apres, attendu)
        total = con.execute(
            f'SELECT COUNT(*) FROM "{cand["table"]}"'
        ).fetchone()[0]
        if total != cand["nb_lignes"]:
            raise ErreurInjection(
                f"Le nombre de lignes de la table a change ({cand['nb_lignes']} -> {total})."
            )
    except BaseException as exc:  # noqa: BLE001
        con.close()
        shutil.copy2(sauvegarde, db)
        if isinstance(exc, ErreurInjection):
            raise
        raise ErreurInjection(f"Validation apres ecriture impossible : {exc}") from exc
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass

    afficher_diff(cand["donnees"][NOM_CHAMP], ajouts)
    return sauvegarde


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Injecte la liste des commandes refusees dans une base "
        "state.vscdb de Roo Code (dry-run par defaut).",
    )
    p.add_argument("--db", required=True, help="Chemin de la base state.vscdb")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulation (defaut) : affiche l'etat et le resultat fusionne",
    )
    p.add_argument("--apply", action="store_true", help="Applique reellement la fusion")
    p.add_argument(
        "--force",
        action="store_true",
        help="Autorise l'ecriture sur la base vivante meme si VS Code est ouvert",
    )
    p.add_argument(
        "--key",
        default=None,
        help=f"Cle a modifier (defaut : detection automatique, prefere {CLE_PREFEREE})",
    )
    args = p.parse_args(argv)

    if args.apply and args.dry_run:
        p.error("--apply et --dry-run sont incompatibles.")
    mode = "APPLY" if args.apply else "DRY-RUN"

    db = Path(os.path.expandvars(args.db)).expanduser()
    if not db.is_file():
        print(f"[ERREUR] Base introuvable : {db}")
        return 2

    print(f"Mode        : {mode}")
    print(f"Base        : {db}")
    if CHEMIN_VIVANT.is_file() and meme_fichier(db, CHEMIN_VIVANT):
        actifs = editeurs_actifs()
        print(f"Base vivante: OUI | editeur actif : {actifs or 'aucun detecte'}")
        if args.apply and actifs and not args.force:
            print(
                "[SECURITE] Un editeur base VS Code est ouvert sur la base "
                f"vivante ({', '.join(actifs)}) : l'ecriture directe est "
                "deconseillee car les modifications seraient ecrasees au "
                "prochain enregistrement de l'etat.\n"
                "  -> Fermez completement l'editeur puis relancez, ou utilisez "
                "--force en connaissance de cause."
            )
            return 3
    else:
        print("Base vivante: non (copie)")

    con = connecter(db, lecture_seule=True)
    try:
        trouves = candidats(con)
        print(f"Cles candidates : {[c['cle'] for c in trouves] or 'aucune'}")
        cand = choisir_candidat(trouves, args.key)
        cand["nb_lignes"] = con.execute(
            f'SELECT COUNT(*) FROM "{cand["table"]}"'
        ).fetchone()[0]
    except (ErreurInjection, sqlite3.Error) as exc:
        print(f"[ERREUR] {exc}")
        return 2
    finally:
        con.close()

    existant = cand["donnees"][NOM_CHAMP]
    if not isinstance(existant, list) or not all(isinstance(e, str) for e in existant):
        print(
            f"[ERREUR] '{NOM_CHAMP}' n'est pas un tableau de chaines "
            f"({type(existant).__name__}) : format inattendu, aucune ecriture."
        )
        return 2

    print(f"Table / cle : {cand['table']} / {cand['cle']}")
    print(f"Champ       : {NOM_CHAMP} (chemin JSON : {NOM_CHAMP})")
    print(f"Taille JSON : {len(cand['json_brut'])} caracteres")
    afficher_etat("Refus actuels", existant)

    fusion, ajouts = fusionner(existant)
    afficher_etat("Resultat fusionne", fusion)
    afficher_diff(existant, ajouts)

    if not args.apply:
        if ajouts:
            print(f"[DRY-RUN] Aucune ecriture. {len(ajouts)} entree(s) seraient ajoutees.")
        else:
            print("[DRY-RUN] Aucune ecriture. Liste deja a jour (idempotent).")
        return 0

    if not ajouts:
        print("[APPLY] Rien a faire : la liste contient deja toutes les entrees.")
        return 0

    try:
        sauvegarde = appliquer(db, cand, fusion, ajouts)
    except (ErreurInjection, OSError, sqlite3.Error) as exc:
        print(f"[ERREUR] {exc}")
        return 4

    print(f"[APPLY] Ecriture validee. Sauvegarde : {sauvegarde}")
    print(f"[APPLY] Total en base : {len(fusion)} entree(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
