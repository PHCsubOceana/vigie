import sys
import numpy as np, trimesh
from jetson_boitier import *
sys.stdout.reconfigure(encoding="utf-8")      # console Windows : « ∩ » et « ² »
parts = construire(); kit = dict(kit_fantome(), enveloppe=kit_enveloppe())
P = {n: union(list(d.values())) for n, d in parts.items()}
print("marge kit / colonne", round(np.hypot(CAV_X / 2 - KIT_X / 2, CAV_Y / 2 - KIT_Y / 2) - BOSS_R, 2), "mm")
print("bac ∩ couvercle", round((P["bac"] ^ P["couvercle"]).volume(), 2))
for kn, k in kit.items():
    for n, p in P.items():
        v = (k ^ p).volume()
        if v > 0.05: print("!", kn, "touche", n, round(v, 2))
for n, p in P.items():
    m_ = vers_impression(n, p); bb = m_.bounding_box(); m_ = m_.translate((0, 0, -bb[2]))
    mm = m_.to_mesh(); tm = trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts))
    nz = tm.face_normals[:, 2]; zc = tm.triangles_center[:, 2]; a = tm.area_faces
    hp = zc > 0.05
    pont = hp & (nz < -0.999); pente = hp & (nz < -0.7072) & ~pont
    print(n, "ponts", round(a[pont].sum(), 1), "mm² ; pentes>45°", round(a[pente].sum(), 2))
    for z in sorted(set(np.round(zc[pont], 1))):
        sel = pont & (np.round(zc, 1) == z); pts = tm.triangles[sel].reshape(-1, 3)
        print("   z", z, round(a[sel].sum(), 1), "emprise", round(np.ptp(pts[:, 0]), 1), "x", round(np.ptp(pts[:, 1]), 1))
