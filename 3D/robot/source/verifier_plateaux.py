"""Contrôle du fichier de plateaux PrusaSlicer (3D/Vigie_Impressions.3mf).

Pour chaque pièce du fichier, compare sa forme aux STL actuels de stl/ : même forme, mais
aussi même sens (un miroir est signalé). Les pièces peuvent être tournées ou déplacées sur
le plateau, cela ne compte pas. Le boîtier du Jetson est contrôlé aussi s'il est sur les plateaux.

    python verifier_plateaux.py [chemin du 3MF]
"""
import glob, itertools, os, re, sys, zipfile
import numpy as np
import trimesh
from scipy.spatial import cKDTree

ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER = os.path.dirname(ICI)
PLATEAUX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(DOSSIER), "Vigie_Impressions.3mf")
TOLERANCE = 0.3      # mm : écart toléré (95 % des sommets)


def objets_3mf(chemin):
    z = zipfile.ZipFile(chemin)
    modele = z.read("3D/3dmodel.model").decode("utf-8")
    cfg = z.read("Metadata/Slic3r_PE_model.config").decode("utf-8") if "Metadata/Slic3r_PE_model.config" in z.namelist() else ""
    noms = dict(re.findall(r'<object id="(\d+)"[^>]*>\s*<metadata type="object" key="name" value="([^"]+)"', cfg))
    poses = dict(re.findall(r'<item objectid="(\d+)" transform="([^"]+)"', modele))
    for oid, corps in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', modele, re.S):
        v = np.array(re.findall(r'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"', corps), float)
        if not len(v):
            continue
        if oid in poses:
            t = np.array(poses[oid].split(), float).reshape(4, 3)
            v = v @ t[:3] + t[3]
        yield noms.get(oid, f"objet {oid}"), v


def centrer(p):
    return p - (p.min(0) + p.max(0)) / 2


# les 48 matrices de permutation signées : 24 rotations (det +1) et 24 miroirs (det -1)
MATRICES = []
for perm in itertools.permutations(range(3)):
    for signes in itertools.product((1, -1), repeat=3):
        m = np.zeros((3, 3))
        for i, j in enumerate(perm):
            m[i, j] = signes[i]
        MATRICES.append(m)

reference = {}
for f in glob.glob(os.path.join(DOSSIER, "stl", "*.stl")) + glob.glob(os.path.join(os.path.dirname(DOSSIER), "boitier-jetson", "stl", "*.stl")):
    nom = "_".join(os.path.basename(f)[3:-4].split("_")[:-1])
    reference.setdefault(nom, []).append(trimesh.load(f).vertices)
FACULTATIVES = {"bac", "couvercle"}     # boîtier du Jetson : il peut ne pas être sur les plateaux

print("Plateaux :", PLATEAUX)
ok = True
vus = set()
for nom, v in objets_3mf(PLATEAUX):
    court = re.sub(r"^\d+_", "", os.path.splitext(nom)[0])      # « 01_corps.3mf » ou « corps_bleu » -> corps
    cle = max((k for k in reference if court.startswith(k)), key=len, default=None)
    if cle is None:
        print(f"  ?  {nom} : pièce inconnue")
        continue
    vus.add(cle)
    ref = cKDTree(centrer(np.vstack(reference[cle])))
    ecarts = [(np.percentile(ref.query(centrer(v @ m.T))[0], 95), np.linalg.det(m) > 0) for m in MATRICES]
    droit = min(e for e, d in ecarts if d)          # meilleure pose sans miroir
    miroir = min(e for e, d in ecarts if not d)
    if droit <= TOLERANCE:                          # une pièce symétrique passe aussi en miroir : c'est bon
        print(f"  ok {nom}")
    elif miroir <= TOLERANCE:
        ok = False
        print(f"  !  {nom} : EN MIROIR par rapport à la version actuelle")
    else:
        ok = False
        print(f"  !  {nom} : forme différente de la version actuelle (écart {min(droit, miroir):.2f} mm)")
for k in sorted(set(reference) - vus - FACULTATIVES):
    ok = False
    print(f"  !  {k} : absente du fichier de plateaux")
print("Plateaux à jour." if ok else "Plateaux à refaire : recharger les 3MF du dossier dans PrusaSlicer.")
sys.exit(0 if ok else 1)
