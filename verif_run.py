from pathlib import Path
import json

racine = Path("data/processed/normalise")
runs = sorted([d for d in racine.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime)
print(f"{len(runs)} dossier(s) dans {racine} :")
for d in runs:
    mes = d / "mesures"
    n = len(list(mes.iterdir())) if mes.exists() else "absent"
    print(f"  - {d.name} | fichiers mesures/ : {n}")

dernier = runs[-1]
print(f"\n=== Dernier run : {dernier.name} ===")
for f in sorted(dernier.glob("*")):
    if f.is_file() and f.suffix in (".json", ".md", ".txt", ".log"):
        print(f"\n--- {f.name} ---")
        txt = f.read_text(encoding="utf-8", errors="replace")
        if f.suffix == ".json":
            try:
                print(json.dumps(json.loads(txt), ensure_ascii=False, indent=1)[:3000])
                continue
            except Exception:
                pass
        lignes = [l for l in txt.splitlines()
                  if any(k in l.lower() for k in ("statut", "status", "contr", "oui", "non", "textes", "erreur"))]
        print("\n".join(lignes[:60]))