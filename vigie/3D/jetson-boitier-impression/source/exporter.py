"""Exports du boîtier Jetson : 3MF PrusaSlicer tricolores, STL, aperçu 3D.

Se lance depuis n'importe où : les fichiers sont écrits dans le dossier parent de source/.
"""
import base64, json, os, zipfile
import numpy as np
from jetson_boitier import *

ICI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(ICI)
os.makedirs(f"{OUT}/stl", exist_ok=True)
EXTRUDEUR = {"bleu": 1, "orange": 2, "ciel": 3}
DATE_ZIP = (2026, 1, 1, 0, 0, 0)    # date fixe : à modèle égal, 3MF identique octet pour octet


def maillage(m_):
    mesh = m_.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3].astype(float), np.asarray(mesh.tri_verts).astype(np.int64)


def ecrire_stl(path, v, f):
    tri = v[f].astype("<f4")
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"Jetson explorations360".ljust(80, b" "))
        fh.write(np.uint32(len(f)).tobytes())
        fh.write(rec.tobytes())


def ecrire_3mf(path, nom, volumes):
    verts, tris, plages = [], [], []
    off = 0
    for couleur in ("bleu", "orange", "ciel"):
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
             f'<metadata name="Title">Jetson - {nom}</metadata>\n'
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
        z.writestr(zipfile.ZipInfo("[Content_Types].xml", DATE_ZIP), ct, zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("_rels/.rels", DATE_ZIP), rels, zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("3D/3dmodel.model", DATE_ZIP), model, zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("Metadata/Slic3r_PE_model.config", DATE_ZIP), "\n".join(cfg), zipfile.ZIP_DEFLATED)
    return plages



def place(nom, vols):
    vols = {c: vers_impression(nom, m_) for c, m_ in vols.items()}
    bb = union(list(vols.values())).bounding_box()
    dx, dy, dz = 125 - (bb[0] + bb[3]) / 2, 105 - (bb[1] + bb[4]) / 2, -bb[2]
    return {c: m_.translate((dx, dy, dz)) for c, m_ in vols.items()}

parts = construire()
for i, nom in enumerate(["bac", "couvercle"], 1):
    vols = place(nom, parts[nom])
    ecrire_3mf(f"{OUT}/{i:02d}_{nom}.3mf", nom, vols)
    for c, m_ in vols.items():
        v, f = maillage(m_)
        ecrire_stl(f"{OUT}/stl/{i:02d}_{nom}_{c}.stl", v, f)
    bb = union(list(vols.values())).bounding_box()
    print(nom, [round(bb[k + 3] - bb[k], 1) for k in range(3)])

vue = []
for nom, d in list(parts.items()) + [("kit", kit_fantome())]:
    for c, m_ in d.items():
        v, f = maillage(m_)
        vue.append({"nom": nom, "couleur": c, "v": base64.b64encode(v.astype("<f4").tobytes()).decode(),
                    "f": base64.b64encode(f.astype("<u4").tobytes()).decode()})
texte_vue = json.dumps(vue)
chemin_vue = os.path.join(ICI, "vue.json")
with open(chemin_vue, "w", encoding="utf-8") as fh:
    fh.write(texte_vue)
print("vue.json", round(os.path.getsize(chemin_vue) / 1e6, 2), "Mo")

# L'aperçu porte ses données : on les remplace sur place, le reste de la page ne bouge pas.
# Ses textes (cotes affichées) restent à relire à la main si une cote change.
chemin_page = f"{OUT}/apercu-3d.html"
with open(chemin_page, encoding="utf-8", newline="") as fh:
    page = fh.read()
ouvre = '<script id="data" type="application/json">'
debut = page.index(ouvre) + len(ouvre)
fin = page.index("</script>", debut)
with open(chemin_page, "w", encoding="utf-8", newline="") as fh:
    fh.write(page[:debut] + texte_vue + page[fin:])
print("apercu-3d.html : donnees remplacees")
