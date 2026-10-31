"""Exports : un 3MF PrusaSlicer par pièce (volumes bleu = extrudeur 1, orange = extrudeur 2),
STL par couleur, et données légères pour la visionneuse 3D."""
import base64, io, json, os, zipfile
import numpy as np
from vigie_robot import *

OUT = "vigie-corps-impression"
os.makedirs(f"{OUT}/stl", exist_ok=True)
EXTRUDEUR = {"bleu": 1, "orange": 2}


def maillage(m_):
    mesh = m_.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3].astype(float), np.asarray(mesh.tri_verts).astype(np.int64)


# Le repère du modèle (X à droite, Y en haut, Z vers l'arrière) est INDIRECT : pris tel quel par
# un trancheur (repère direct), chaque pièce sortait en miroir. On remet donc tout dans le bon
# sens par une symétrie selon X, sauf la tête, que Philippe préfère dans son sens imprimé.
SANS_SYMETRIE = {"tete"}


def sens_reel(nom, m_):
    return m_ if nom in SANS_SYMETRIE else m_.mirror((1, 0, 0))


def place_sur_plateau(nom, volumes):
    volumes = {c: vers_impression(nom, sens_reel(nom, m_)) for c, m_ in volumes.items()}
    bb = union(list(volumes.values())).bounding_box()
    # centré sur le plateau MK4S (250 x 210)
    dx, dy, dz = 125 - (bb[0] + bb[3]) / 2, 105 - (bb[1] + bb[4]) / 2, -bb[2]
    return {c: m_.translate((dx, dy, dz)) for c, m_ in volumes.items()}


def ecrire_stl(path, v, f):
    tri = v[f].astype("<f4")
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"Vigie explorations360".ljust(80, b" "))
        fh.write(np.uint32(len(f)).tobytes())
        fh.write(rec.tobytes())


def ecrire_3mf(path, nom, volumes):
    verts, tris, plages = [], [], []
    off = 0
    for couleur in ("bleu", "orange"):
        if couleur not in volumes:
            continue
        v, f = maillage(volumes[couleur])
        if len(f) == 0:
            continue
        verts.append(v); tris.append(f + off)
        debut = sum(len(t) for t in tris[:-1])
        plages.append((couleur, debut, debut + len(f) - 1))
        off += len(v)
    V, F = np.vstack(verts), np.vstack(tris)
    vx = "".join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>' for a, b, c in V)
    tx = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06">\n'
             '<metadata name="slic3rpe:Version3mf">1</metadata>\n'
             f'<metadata name="Title">Vigie - {nom}</metadata>\n'
             f'<resources><object id="1" type="model"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object></resources>\n'
             '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/></build>\n</model>')
    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>", '<object id="1" instances_count="1">',
           f'<metadata type="object" key="name" value="{nom}"/>',
           '<metadata type="object" key="extruder" value="1"/>']
    for couleur, a, b in plages:
        cfg += [f'<volume firstid="{a}" lastid="{b}">',
                f'<metadata type="volume" key="name" value="{nom}_{couleur}"/>',
                '<metadata type="volume" key="volume_type" value="ModelPart"/>',
                '<metadata type="volume" key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>',
                f'<metadata type="volume" key="extruder" value="{EXTRUDEUR[couleur]}"/>',
                '<mesh edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>',
                "</volume>"]
    cfg += ["</object>", "</config>"]
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
          '<Default Extension="config" ContentType="text/xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/Slic3r_PE_model.config", "\n".join(cfg))
    return plages


parts, _ = construire()
ORDRE = ["corps", "tete", "socle", "capot_corps", "capot_tete", "capot_camera", "bague_ecran", "grille_capot"]
num = {n: i + 1 for i, n in enumerate(ORDRE)}
resume = []
for nom in ORDRE:
    vols = place_sur_plateau(nom, parts[nom])
    p3 = f"{OUT}/{num[nom]:02d}_{nom}.3mf"
    plages = ecrire_3mf(p3, nom, vols)
    for c, m_ in vols.items():
        v, f = maillage(m_)
        ecrire_stl(f"{OUT}/stl/{num[nom]:02d}_{nom}_{c}.stl", v, f)
    bb = union(list(vols.values())).bounding_box()
    resume.append((nom, [c for c, *_ in plages], round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)))
for r in resume:
    print(r)

# ---- données de la visionneuse : assemblage en place, maillage allégé
vue = []
for nom in ORDRE:
    for c, m_ in parts[nom].items():
        # l'aperçu (repère indirect, z retourné à l'affichage) montre déjà le bon sens :
        # seule la tête, gardée telle qu'imprimée, y est inversée
        v, f = maillage(m_.mirror((1, 0, 0)) if nom in SANS_SYMETRIE else m_)
        vue.append({"nom": nom, "couleur": c,
                    "v": base64.b64encode(v.astype("<f4").tobytes()).decode(),
                    "f": base64.b64encode(f.astype("<u4").tobytes()).decode()})
json.dump(vue, open("vue.json", "w"))
print("vue.json", round(os.path.getsize("vue.json") / 1e6, 2), "Mo")
