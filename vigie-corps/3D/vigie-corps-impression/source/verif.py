"""Vérifications : surplombs en orientation d'impression, collisions d'assemblage, encombrement plateau."""
import numpy as np
import trimesh
from vigie_robot import *


def to_tm(m_):
    mesh = m_.to_mesh()
    return trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=True)


parts, _ = construire()

print("== Surplombs en orientation d'impression (faces orientées vers le bas à plus de 45°, hors plateau)")
for nom, d in parts.items():
    m_ = union(list(d.values()))
    m_ = vers_impression(nom, m_)
    bb = m_.bounding_box()
    m_ = m_.translate((-(bb[0] + bb[3]) / 2, -(bb[1] + bb[4]) / 2, -bb[2]))
    tm = to_tm(m_)
    n = tm.face_normals
    zc = tm.triangles_center[:, 2]
    a = tm.area_faces
    hors_plateau = zc > 0.05
    pont = hors_plateau & (n[:, 2] < -0.999)
    pente = hors_plateau & (n[:, 2] < -0.7072) & ~pont
    dims = m_.bounding_box()
    print(f"{nom:12s} {dims[3]-dims[0]:6.1f} x {dims[4]-dims[1]:6.1f} x {dims[5]-dims[2]:5.1f} mm | "
          f"ponts horizontaux {a[pont].sum():7.1f} mm² | pentes >45° {a[pente].sum():6.2f} mm²")
    if a[pont].sum() > 0:
        # largeur des ponts : regroupe les faces par hauteur
        for z in sorted(set(np.round(zc[pont], 1))):
            sel = pont & (np.round(zc, 1) == z)
            pts = tm.triangles[sel].reshape(-1, 3)
            print(f"      pont à z={z:5.1f} : surface {a[sel].sum():6.1f} mm², emprise {np.ptp(pts[:,0]):.1f} x {np.ptp(pts[:,1]):.1f}")

print("\n== Collisions avec le matériel (volumes d'intersection, doivent être ~0)")
verre = ext(circ(57.5), EP_FACE, EP_FACE + VERRE_EP)
# carte 85,5 x 65 derrière la dalle, composants compris (15 mm au total depuis la face du verre)
carte = ext(rect(-42.75, -32.5, 42.75, 32.5), EP_FACE + VERRE_EP + 0.3, EP_FACE + 15.0)
usb_a = ext(rect(42.75, 0.5, 47.0, 14.5), 9.0, 17.0)                 # USB-A, flanc droit vu de face
# fiche coudée haut/bas de la rallonge HuaLiSiJi dans le port UART : tête de 17 mm vers l'arrière
fiches_usbc = ext(rect(X_UART - 6.0, 32.5, X_UART + 6.0, 46.0), 8.6, 27.0)
# prise femelle au dos du socle : platine 25 × 10 × 3, corps 14 × 8 sur 32 mm vers l'avant,
# fiche de l'utilisateur (surmoulage 12 × 6,5) qui sort sur 35 mm vers l'arrière, 2 vis M3
cable_usb = (ext(rect(SP_X - 12.5, SP_Y - 5.0, SP_X + 12.5, SP_Y + 5.5), SP_SIEGE - 6.0, SP_SIEGE - 0.05)
             + ext(rect(SP_X - 7.0, SP_Y - 4.0, SP_X + 7.0, SP_Y + 4.0), SP_SIEGE - 35.0, SP_SIEGE - 6.0))
prise_ext = ext(rect(SP_X - 6.0, SP_Y - 3.25, SP_X + 6.0, SP_Y + 3.25), SP_SIEGE + 0.05, SOCLE_Z1 + 35.0)
vis_platine = union([M.cylinder(8.0, 1.5, 1.5, 16).translate((SP_X + s_ * 9.5, SP_Y, SP_SIEGE - 3.0)) for s_ in (-1, 1)])
# câble intérieur Ø4,5 : du nez de la prise, boucle vers l'avant, gorge, fente, puis dans le corps
cable_int = (ext(rect(SP_X - 2.25, SOCLE_BAS + 0.5, SP_X + 2.25, SOCLE_BAS + 5.0), -3.5, SP_SIEGE - 35.0)
             + ext(rect(FENTE_X[0] + 3.0, SOCLE_BAS + 0.5, SP_X + 2.25, SOCLE_BAS + 5.0), -3.5, 1.0)
             + ext(rect(FENTE_X[0] + 3.0, SOCLE_BAS + 0.5, FENTE_X[0] + 7.5, SOCLE_BAS + 5.0), -3.5, 15.0)
             + ext(rect(FENTE_X[0] + 3.0, SOCLE_BAS + 0.5, FENTE_X[0] + 7.5, -50.0), 12.0, 16.5))
module = ext(contour_module(), EP_FACE_T, EP_FACE_T + T_OEIL)
ergots = union([ext(circ(ERGOT_R, sx * ERGOT_X, ERGOT_Y, 32), EP_FACE_T + T_OEIL, EP_FACE_T + T_OEIL + ERGOT_FOURREAU - 0.5) for sx in (-1, 1)])
z_cam = EP_FACE_T + CAM_H
cx, cy = SAT_C[0], SAT_C[1] + CAM_DY
cam_carte = ext(rect(cx - 12.5, SAT_C[1] - 12.0, cx + 12.5, SAT_C[1] + 12.0) - union([circ(1.1, cx + dx, cy + dy, 24) for dx, dy in CAM_TROUS]), z_cam, z_cam + 1.0)
cam_bloc = ext(rect(cx - 4.25, cy - 4.25, cx + 4.25, cy + 4.25), EP_FACE_T + 0.05, z_cam)
cam_dos = ext(rect(cx - 10.0, SAT_C[1] - 12.0, cx + 10.0, SAT_C[1] - 5.5), z_cam + 1.0, z_cam + 4.0)     # connecteur de nappe au dos
# haut-parleur collé au dos de la carte (face arrière des composants à z = 17)
hp = ext(rect(HP_XY[0] - HP_L / 2, HP_XY[1] - HP_P / 2, HP_XY[0] + HP_L / 2, HP_XY[1] + HP_P / 2), EP_FACE + 15.5, EP_FACE + 15.5 + HP_H)
# connecteur 40 broches + fiches Dupont (bas de la carte)
dupont = ext(rect(-41.0, -25.0, 16.0, -16.5), EP_FACE + 9.0, EP_FACE + 32.0)
# trajets des trombones jusqu'aux boutons, et les boutons eux-mêmes
trombones = union([M.cylinder(21.0, 0.5, 0.5, 16).rotate((0, 90, 0)).translate((-R - 0.5, yb, Z_BOUTON)) for yb in BOUTONS.values()])
boutons = union([ext(rect(-44.3, yb - 2.0, -42.75, yb + 2.0), Z_BOUTON - 1.5, Z_BOUTON + 1.5) for yb in BOUTONS.values()])
materiel = {"trajet_trombones": trombones, "boutons": boutons, "haut_parleur": hp, "fiches_dupont": dupont, "camera_carte": cam_carte, "camera_objectif": cam_bloc, "camera_connecteur": cam_dos, "verre": verre, "carte": carte, "usb_a": usb_a, "fiches_usbc": fiches_usbc, "prise_usb": cable_usb, "fiche_exterieure": prise_ext, "vis_platine": vis_platine, "cable_interieur": cable_int, "module_yeux": module, "ergots_yeux": ergots}
pieces = {n: union(list(d.values())) for n, d in parts.items()}
for nm, mat in materiel.items():
    for np_, p in pieces.items():
        v = (mat ^ p).volume()
        if v > 0.05:
            print(f"  ! {nm} touche {np_} : {v:.2f} mm³")
noms = list(pieces)
for i in range(len(noms)):
    for j in range(i + 1, len(noms)):
        v = (pieces[noms[i]] ^ pieces[noms[j]]).volume()
        if v > 0.05:
            print(f"  ! {noms[i]} ∩ {noms[j]} : {v:.2f} mm³")
print("  (fin des collisions)")

print("\n== Jeux de montage")
# la vitre doit passer entre les bossages du capot
print("  vitre Ø115 vs bossages : jeu radial", round(61.5 - 3.2 - 57.5, 2), "mm")
print("  bague vs bossages : jeu radial", round(61.5 - 3.2 - BAGUE_RE, 2), "mm")
print("  verre dans la poche : jeu radial", JEU_VERRE, "mm")
print("  cou dans l'encoche de tête : jeu", 0.25, "mm par côté")

# stabilité : centre de gravité (PLA 15 % de remplissage approx. = coque pleine pondérée) + écran ~120 g
bbt = pieces["tete"].bounding_box(); bbc = pieces["corps"].bounding_box()
print("\n== Hauteur totale :", round(bbt[4] - SOCLE_BAS, 1), "mm ; largeur", round(bbc[3] - bbc[0], 1), "mm")
