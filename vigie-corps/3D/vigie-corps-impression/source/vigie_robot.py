"""Vigie : corps de robot imprimable, bicolore explorations360.

Repère d'assemblage : X vers la droite, Y vers le haut, Z vers l'arrière.
La face avant du corps et de la tête est en z = 0 : ces deux pièces
s'impriment telles quelles, face avant sur le plateau.
Toutes les cotes en mm.
"""
import glob, json, os, sys, zipfile
import numpy as np
import manifold3d as mf
import trimesh
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties

CS, M = mf.CrossSection, mf.Manifold
SEG = 192

# ---------------------------------------------------------------- paramètres
# Écran Waveshare ESP32-P4-WIFI6-Touch-LCD-3.4C
VERRE_R = 57.5          # verre Ø115
VERRE_EP = 6.0          # épaisseur verre + dalle
JEU_VERRE = 0.3         # jeu radial dans la poche
# Corps
R = 66.0                # rayon extérieur du corps (Ø132)
D = 38.0                # profondeur de la coque (sans capot)
EP_FACE = 2.0           # épaisseur de la lèvre avant
R_OUV = 55.0            # ouverture de la vitre (recouvre 2,5 mm de la bordure noire)
R_CAV = 62.6            # cavité intérieure
CAPOT = 3.0
# Logo : anneau orange
ORANGE_RI, ORANGE_RE = 57.0, 62.5
INCR = 0.6              # profondeur des incrustations (3 couches à 0,2)
# Bague de maintien de la vitre
BAGUE_RI, BAGUE_RE, BAGUE_EP, BAGUE_LEVRE = 54.3, 57.2, 2.4, 0.4
R_VIS_BAGUE = 59.9
# Module DualEye 0,71"
OEIL_X = 15.325         # demi-entraxe des yeux (30,65 mm, mesuré sur le module)
OEIL_AFF_R = 9.1        # ouverture Ø18,2 (afficheur Ø18)
T_OEIL = 6.0            # épaisseur estimée verre + carte du module (à vérifier)
ERGOT_X, ERGOT_R = 20.4, 1.75  # ergots au dos : 44,3 mm bord à bord extérieurs, Ø3,5 (mesuré)
ERGOT_FOURREAU = 12.0   # profondeur du fourreau depuis le dos de la carte (longueur d'ergot maximale)
# ---- Proportions : échelle géométrique de raison φ partant du rayon du corps
#   R/φ = 40,8 (tête) · R/φ² = 25,2 (caméra) · R/φ³ = 15,6 (grand nœud du bras)
#   R/φ⁴ = 9,6 (écarts, nœuds moyens) · R/φ⁵ = 5,9 · R/φ⁶ = 3,6 (tiges, petits points)
PHI = (1 + 5 ** 0.5) / 2
def ech(n):
    return R / PHI ** n
ECART = ech(4)          # vide entre deux disques voisins
# Tête ronde : yeux sur la ligne d'or (0,618 du diamètre depuis le haut, schéma « tête de bébé »)
R_T, TD = 30.5, 34.0     # au plus juste autour du cadre des yeux (Ø61)
T_BAS = R + ech(6)      # bas de la tête : cou court (3,6 mm de vide à l'axe)
TCY = T_BAS + R_T       # centre de la tête
OEIL_Y = TCY             # yeux sur le diamètre horizontal de la tête
ERGOT_Y = OEIL_Y         # hauteur supposée alignée sur le centre de l'œil (d'après la photo, à mesurer)
# Satellite caméra : OV5647 standard, format Raspberry Pi v1.3 (carte 25 x 24, trous M2 à 21 x 12,5)
SAT_ANG = 90.0 / PHI ** 2                       # 34,4° au-dessus de l'horizontale
SAT_R, SAT_D = ech(2), 26.0
_d = R + ECART + SAT_R
SAT_C = (_d * float(np.cos(np.radians(SAT_ANG))), _d * float(np.sin(np.radians(SAT_ANG))))
SAT_BOSS = SAT_R - 4.2
CAM_DY = -2.5           # objectif 2,5 mm sous le centre de la carte : côté du connecteur de nappe (en bas)
CAM_H = 5.0             # hauteur du bloc objectif au-dessus de la carte
CAM_TROUS = [(-10.5, 0.0), (10.5, 0.0), (-10.5, 12.5), (10.5, 12.5)]  # par rapport à l'objectif : paire du bas dans l'axe de l'objectif (retour de Philippe 29/10)
NAPPE_Z0 = EP_FACE + VERRE_EP + 0.2     # gorge de la nappe : 17,8 mm de haut pour une nappe de 16 mm
BOSS_ANG = (25, 155, 205, 305)          # bossages du capot arrière (dégagent caméra, guides et prise USB)
# Carte vue de face : USB-C en haut à GAUCHE (en haut à droite vue de dos), BOOT et RESET sur le flanc gauche,
# boutons latéraux qui s'enfoncent vers la droite. Positions relevées sur la photo Waveshare.
BOUTONS = {"RESET": 16.3, "BOOT": -7.9}   # y du bouton (mm), sur le bord x = -42,75 de la carte
Z_BOUTON = 11.5                           # hauteur estimée de l'actionneur (dos de la carte + 1,5 mm)
X_USB = (-36.0, 0.0)                      # emprise des deux USB-C et de leurs fiches
X_UART = -27.4                            # port « USB TO UART » (alimentation + flashage), vu de face
# Prise USB-C de façade : rallonge HuaLiSiJi B0HFBR7CL5 (USB-C 3.2, 30 cm, mâle coudé haut/bas,
# femelle à platine vissée : platine 25 mm, 2 vis M3 fournies, longueur totale 35 mm).
# La platine se pose À L'INTÉRIEUR, contre une paroi amincie à 1,6 mm, grand axe selon la profondeur
# (la génératrice du flanc est droite dans ce sens) ; les vis entrent par l'extérieur.
PRISE_ANG = 328.0                         # sur le flanc droit vu de face, sous la caméra
PRISE_Z = 24.5                            # centre de la prise (profondeur)
PRISE_R_INT = 58.6                        # face intérieure du bossage
PRISE_DEMI_L = 9.0                        # demi-largeur (tangentielle) du bossage intérieur
PLATINE_L, PLATINE_W = 27.0, 11.6         # logement de la platine (cote réelle ≈ 25 mm, jeu compris)
PLATINE_SIEGE = R - 1.6                   # appui de la platine : paroi de 1,6 mm
FENETRE_L, FENETRE_W = 13.0, 7.6          # passage de la fiche de l'utilisateur (surmoulage compris)
VIS_PLATINE = (8.75, 10.25)               # demi-entraxe des vis, en boutonnière (entraxe 17,5 à 20,5)
PRISE_FLANC = False                       # ancienne implantation sur le flanc (version 12), désactivée
# Implantation retenue : prise au dos du SOCLE, câble qui sort vers l'arrière au ras du bureau.
# Platine contre la paroi arrière du socle (1,6 mm), logements ouverts par-dessous (invisibles),
# câble intérieur : gorge sous le socle puis fente verticale qui monte dans le corps.
SP_X, SP_Y = 45.0, -65.0                  # centre de la prise (décalée : sous le berceau, le socle est trop mince)
SP_SIEGE = 44.0 - 1.6                     # appui de la platine (face arrière du socle à z = 44)
PLATINE_EP = 7.0                          # profondeur du logement : flasque réelle de 5 à 6 mm + jeu
FENTE_X, FENTE_Z = (8.0, 20.0), (9.0, 25.0)   # passage du câble socle -> corps (la fiche coudée y passe)
PAROI = 2.4
EP_FACE_T = 1.6
# Cou
COU_X, COU_HAUT = 14.0, T_BAS + 9.0
CANAL_X, CANAL_Z0, CANAL_Z1 = 9.0, 9.0, 25.0
COU_Z = 30.0
VIS_COU_X, VIS_COU_Y = 10.8, T_BAS + 5.5
# Socle
SOCLE_HAUT, SOCLE_BAS = -56.0, -74.0
SOCLE_Z0, SOCLE_Z1, SOCLE_X = -26.0, 44.0, R        # socle aussi large que le corps
VIS_SOCLE_Z = (20.0, 32.0)
# Haut-parleur livré avec l'écran (70 x 30 x 16, 8 Ω 2 W) : collé au double face au dos de la
# carte, membranes vers l'arrière, face au triangle d'aération du capot (aucune pièce dédiée)
HP_L, HP_P, HP_H = 70.0, 30.0, 16.0
HP_XY = (0.0, 5.0)       # entre les fiches USB-C (haut) et le connecteur 40 broches (bas), dégage la prise

BLEU = "#172242"
ORANGE = "#F07702"


# ---------------------------------------------------------------- outils 2D/3D
def circ(r, x=0.0, y=0.0, seg=SEG):
    return CS.circle(r, seg).translate((x, y))


def rect(x0, y0, x1, y1):
    return CS.square((x1 - x0, y1 - y0)).translate((x0, y0))


def ext(cs, z0, z1):
    return cs.extrude(z1 - z0).translate((0, 0, z0))


def trait(p0, p1, w):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    a = np.degrees(np.arctan2(d[1], d[0]))
    corps = CS.square((np.linalg.norm(d), w)).translate((0, -w / 2)).rotate(a).translate(tuple(p0))
    return corps + circ(w / 2, *p0, seg=48) + circ(w / 2, *p1, seg=48)


def gelule(w, h, cx=0.0, cy=0.0):
    rr = h / 2
    return rect(cx - w / 2 + rr, cy - rr, cx + w / 2 - rr, cy + rr) + circ(rr, cx - w / 2 + rr, cy) + circ(rr, cx + w / 2 - rr, cy)


def rect_arrondi(x0, y0, x1, y1, r):
    return (rect(x0 + r, y0, x1 - r, y1) + rect(x0, y0 + r, x1, y1 - r)
            + circ(r, x0 + r, y0 + r, 64) + circ(r, x1 - r, y0 + r, 64)
            + circ(r, x0 + r, y1 - r, 64) + circ(r, x1 - r, y1 - r, 64))


def cyl_y(r, x, y0, y1, z):
    """Cylindre d'axe Y (trou vertical dans le repère robot)."""
    return M.cylinder(y1 - y0, r, r, 40).rotate((-90, 0, 0)).translate((x, y0, z))


def fraisure(x, y, z_face, d_tete=6.4, d_trou=3.4, vers_plus=True):
    """Fraisure conique à 45°, ouverte sur la face z_face."""
    h = (d_tete - d_trou) / 2
    c = M.cylinder(h, d_trou / 2, d_tete / 2, 48)
    if vers_plus:   # face extérieure côté +z
        return c.translate((x, y, z_face - h))
    return c.rotate((180, 0, 0)).translate((x, y, z_face + h))


def local_prise(m_):
    """Repère local (X radial vers l'extérieur, Y tangentiel, Z profondeur) -> repère robot."""
    return m_.rotate((0, 0, PRISE_ANG))


def goutte_radiale(d, x0, x1):
    """Trou d'axe radial à profil en goutte (pointe vers +z) : imprimable sans support."""
    r = d / 2
    # arc de 3π/4 à 9π/4 (le bas du cercle) puis la pointe en haut : pentes de 45° au plus
    pts = [(r * np.cos(t), r * np.sin(t)) for t in np.linspace(np.pi * 0.75, np.pi * 2.25, 64)] + [(0.0, r * 2 ** 0.5)]
    cs = CS([np.array(pts)])
    if cs.area() < 0:
        cs = CS([np.array(pts[::-1])])
    m_ = cs.extrude(x1 - x0)
    # (a, b, e) -> (e + x0, a, b + PRISE_Z) : permutation circulaire (rotation propre)
    m_ = m_.transform([[0, 0, 1, x0], [1, 0, 0, 0], [0, 1, 0, PRISE_Z]])
    return local_prise(m_)


def union(lst):
    out = lst[0]
    for p in lst[1:]:
        out = out + p
    return out


def texte(s, hauteur, police):
    tp = TextPath((0, 0), s, size=hauteur, prop=FontProperties(fname=police))
    polys = [p for p in tp.to_polygons(closed_only=True) if len(p) >= 3]
    return CS([np.asarray(p, float)[:-1] for p in polys], mf.FillRule.EvenOdd)


# Grappe du bras et cheveux : angles réglés pour équilibrer la silhouette (voir equilibre.py)
BRAS_N2_ANG, BRAS_P_ANG = 190.0, 130.0
# cheveux : (angle, distance du centre de la tête, rayon du nœud, style) ; écarts et rayons pris sur l'échelle φ
CHEVEUX = [
    (118.0, R_T + ech(3) + ech(4), ech(4), "anneau"),   # grand anneau, à l'opposé de la caméra
    (82.0,  R_T + ech(4) + ech(5), ech(5), "plein"),
    (52.0,  R_T + ech(5) + ech(5), ech(5), "plein"),
    (150.0, R_T + ech(5) + ech(6), ech(6) + 0.6, "point"),
]

# ---------------------------------------------------------------- corps
def corps():
    a = np.radians
    sx_, sy_ = SAT_C
    theta = np.degrees(np.arctan2(sy_, sx_))
    p_bras_c = (60.0 * np.cos(a(theta)), 60.0 * np.sin(a(theta)))
    # Bras droit du robot (à gauche vu de face) : miroir de la caméra, même écart au corps,
    # grand nœud-anneau R/φ³, puis un nœud R/φ⁵ et un point R/φ⁶ en grappe comme sur le logo
    BRAS_ANG = 180.0 - SAT_ANG
    d1 = R + ECART + ech(3)
    noeud_g1 = (d1 * np.cos(a(BRAS_ANG)), d1 * np.sin(a(BRAS_ANG)))
    def depuis(p, ang, dist):
        return (p[0] + dist * np.cos(a(ang)), p[1] + dist * np.sin(a(ang)))
    noeud_g2 = depuis(noeud_g1, BRAS_N2_ANG, ech(3) + ECART + ech(5))
    point_g = depuis(noeud_g1, BRAS_P_ANG, ech(3) + ech(5) + ech(6))
    p_bras_g = depuis((0.0, 0.0), BRAS_ANG, R - 3.0)

    bras2d = (trait(p_bras_g, noeud_g1, ech(5)) + trait(noeud_g1, noeud_g2, ech(6) + 0.6) + trait(noeud_g1, point_g, ech(6))
              + circ(ech(3), *noeud_g1, 128) + circ(ech(5), *noeud_g2, 64) + circ(ech(6), *point_g, 48)) - circ(R - 1.0)
    bras = ext(bras2d, 0, 10.0)

    # Satellite caméra : branche creuse + capsule ronde
    branche = ext(trait(p_bras_c, SAT_C, ech(4)) - circ(R - 1.0) - circ(SAT_R - 1.0, *SAT_C), 0, SAT_D)
    capsule = ext(circ(SAT_R, *SAT_C, 128), 0, SAT_D) - ext(circ(SAT_R - PAROI, *SAT_C, 128), EP_FACE_T, SAT_D + 1)
    cam = (sx_, sy_ + CAM_DY)
    for dx, dy in CAM_TROUS:
        capsule += ext(circ(2.2, cam[0] + dx, cam[1] + dy, 40), EP_FACE_T, EP_FACE_T + CAM_H)
    for ang in (30, 150):                                  # bossages du capot de capsule
        bx, by = sx_ + SAT_BOSS * np.cos(a(ang)), sy_ + SAT_BOSS * np.sin(a(ang))
        capsule += ext(circ(2.2, bx, by, 32), 12.0, SAT_D) + M.cylinder(2.2, 0.0, 2.2, 32).translate((bx, by, 9.8))

    coque = ext(circ(R), 0, D)
    coque -= ext(circ(R_OUV), -1, EP_FACE + 0.01)
    guides = []
    coque -= ext(circ(VERRE_R + JEU_VERRE), EP_FACE, EP_FACE + VERRE_EP)
    coque -= ext(circ(R_CAV), EP_FACE + VERRE_EP, D + 1)

    # bossages du capot arrière, base conique à 45° (aucun support)
    bossages = []
    for ang in BOSS_ANG:
        cx, cy = 61.5 * np.cos(a(ang)), 61.5 * np.sin(a(ang))
        bossages.append(ext(circ(3.2, cx, cy, 48), 20.0, D))
        bossages.append(M.cylinder(3.2, 0.0, 3.2, 48).translate((cx, cy, 16.8)))
    coque += union(bossages)
    # bossage intérieur de la prise USB (implantation de flanc, désactivée)
    if PRISE_FLANC:
        h_bas = PRISE_Z - PLATINE_L / 2 - 1.5
        prof = CS([np.array([(PRISE_R_INT, h_bas), (R_CAV + 1.0, h_bas - (R_CAV + 1.0 - PRISE_R_INT)),
                             (R_CAV + 1.0, D), (PRISE_R_INT, D)])])
        if prof.area() < 0:
            prof = CS([np.array(prof.to_polygons()[0][::-1])])
        pad = prof.extrude(2 * PRISE_DEMI_L)
        # (x, z, e) -> (x, e - L, z)
        pad = pad.transform([[1, 0, 0, 0], [0, 0, 1, -PRISE_DEMI_L], [0, 1, 0, 0]])
        coque += local_prise(pad) ^ ext(circ(R - 0.5), 0, D)
    # fourreaux de guidage BOOT / RESET : posés sur la feuillure de l'écran (r > 57,8), donc sans porte-à-faux
    for nom_b, yb in BOUTONS.items():
        g = ext((rect(-R, yb - 3.2, -VERRE_R - JEU_VERRE - 0.2, yb + 3.2) ^ circ(R - 0.5)) - circ(VERRE_R + JEU_VERRE + 0.2), EP_FACE + VERRE_EP, Z_BOUTON + 3.5)
        coque += g

    # cou (prisme depuis la face avant : rien en porte-à-faux)
    cou2d = rect(-COU_X, 55, COU_X, COU_HAUT) - circ(R_CAV - 0.35)
    coque += ext(cou2d, 0, COU_Z)
    coque += bras + branche + capsule

    trous = [ext(rect(-CANAL_X, 50, CANAL_X, COU_HAUT + 1), CANAL_Z0, CANAL_Z1)]
    # gorge de la nappe caméra, ouverte à l'arrière (aucun pont)
    trous.append(ext(trait((52.0 * np.cos(a(theta)), 52.0 * np.sin(a(theta))), SAT_C, 1.9), NAPPE_Z0, SAT_D + 1))
    # fenêtre de l'objectif, évasée vers l'avant
    trous.append(ext(circ(4.9, *cam, 64), -1, 0.01))
    trous.append(M.cylinder(EP_FACE_T + 0.02, 4.9, 3.75, 64).translate((cam[0], cam[1], 0.0)))
    for dx, dy in CAM_TROUS:
        trous.append(ext(circ(0.9, cam[0] + dx, cam[1] + dy, 32), EP_FACE_T + 0.6, EP_FACE_T + CAM_H + 0.1))
    for ang in (30, 150):
        trous.append(ext(circ(0.9, sx_ + SAT_BOSS * np.cos(a(ang)), sy_ + SAT_BOSS * np.sin(a(ang)), 24), SAT_D - 7, SAT_D + 0.1))
    for ang in (45, 135, 225, 315):                       # vis M2 de la bague
        trous.append(ext(circ(0.9, R_VIS_BAGUE * np.cos(a(ang)), R_VIS_BAGUE * np.sin(a(ang)), 32),
                         EP_FACE + 1.0, EP_FACE + VERRE_EP + 0.1))
    for ang in BOSS_ANG:                                  # vis M3 du capot
        trous.append(ext(circ(1.25, 61.5 * np.cos(a(ang)), 61.5 * np.sin(a(ang)), 32), D - 12, D + 0.1))
    for sx in (-1, 1):                                    # vis M3 tête -> cou
        trous.append(ext(circ(1.25, sx * VIS_COU_X, VIS_COU_Y, 32), COU_Z - 10, COU_Z + 0.1))
    for z in VIS_SOCLE_Z:                                 # vis M3 socle -> corps
        trous.append(cyl_y(1.25, 0.0, -R - 1, -R + 8, z))
    if PRISE_FLANC:
        # prise USB-C : logement de la platine (ouvert vers l'intérieur et vers le capot, fond plat),
        # fenêtre pour la fiche, deux boutonnières Ø3,3 pour les vis M3
        log = rect(PRISE_R_INT - 1.0, PRISE_Z - PLATINE_L / 2, PLATINE_SIEGE, D + 1.0)
        log = log.extrude(PLATINE_W).transform([[1, 0, 0, 0], [0, 0, 1, -PLATINE_W / 2], [0, 1, 0, 0]])
        trous.append(local_prise(log))
        fen = rect_arrondi(-FENETRE_W / 2, PRISE_Z - FENETRE_L / 2, FENETRE_W / 2, PRISE_Z + FENETRE_L / 2, 2.0)
        # (le haut de la fenêtre est un pont de 7,6 mm : imprimable sans support)
        for sz in (-1, 1):
            a_, b_ = PRISE_Z + sz * VIS_PLATINE[0], PRISE_Z + sz * VIS_PLATINE[1]
            fen = fen + rect(-1.65, min(a_, b_), 1.65, max(a_, b_)) + circ(1.65, 0, a_, 24) + circ(1.65, 0, b_, 24)
        # (y, z) du profil -> extrusion selon x (radial)
        fen_m = fen.extrude(R + 3.0 - (PLATINE_SIEGE - 0.5)).transform([[0, 0, 1, PLATINE_SIEGE - 0.5], [1, 0, 0, 0], [0, 1, 0, 0]])
        trous.append(local_prise(fen_m))
    # passage du câble de la prise USB : fente au fond du corps, en face de celle du socle
    trous.append(ext(rect(FENTE_X[0], -R - 2.0, FENTE_X[1], -55.0), FENTE_Z[0], FENTE_Z[1]))
    # trous des trombones (Ø2,2) avec entonnoir à l'extérieur
    for nom_b, yb in BOUTONS.items():
        trous.append(M.cylinder(30.0, 1.1, 1.1, 24).rotate((0, 90, 0)).translate((-R - 1.0, yb, Z_BOUTON)))
        trous.append(M.cylinder(2.0, 1.1, 2.6, 32).rotate((0, -90, 0)).translate((-R + 1.2, yb, Z_BOUTON)))
    coque -= union(trous)

    orange = ext(circ(ORANGE_RE) - circ(ORANGE_RI), 0, INCR)
    orange += ext(circ(SAT_R - ech(6), *SAT_C, 128) - circ(ech(3), *SAT_C, 128), 0, INCR)   # anneau : 15,6 → 21,5
    orange += ext(circ(ech(3) - ech(6), *noeud_g1, 128) - circ(ech(5), *noeud_g1, 96), 0, INCR)   # anneau : 5,9 → 12,0
    orange += ext(circ(ech(5) - 1.5, *noeud_g2, 64), 0, INCR)
    bleu = coque - orange
    return {"bleu": bleu, "orange": orange}


def triangle_play():
    """Triangle « play » arrondi, centré ; il pointe vers la droite quand on regarde le robot de dos."""
    rc, ang0 = 40.0, 180.0
    pts = np.array([[rc * np.cos(np.radians(ang0 + k * 120)), rc * np.sin(np.radians(ang0 + k * 120))] for k in range(3)])
    if CS([pts]).area() < 0:
        pts = pts[::-1]
    return CS([pts]).offset(-5.0, mf.JoinType.Round).offset(5.0, mf.JoinType.Round)


def grille_capot():
    """Grille orange en forme de « play » : collerette à l'extérieur, bouchon dans la découpe, tenue à force."""
    tri = triangle_play()
    z_ext = D + CAPOT
    collerette = ext(tri.offset(2.5, mf.JoinType.Round), z_ext, z_ext + 1.2)
    bouchon = ext(tri.offset(-0.2, mf.JoinType.Round), D, z_ext)
    g = collerette + bouchon
    # perçage hexagonal
    trous = None
    zone = tri.offset(-3.0, mf.JoinType.Round)
    pas = 3.8
    for j in range(-14, 15):
        for i in range(-14, 15):
            x = (i + 0.5 * (j % 2)) * pas
            y = j * pas * 0.866
            c = circ(1.25, x, y, 18)
            if (zone ^ c).area() > 0.999 * c.area():
                trous = c if trous is None else trous + c
    g -= ext(trous, D - 1, z_ext + 2)
    # fixation à force par nervures d'écrasement : rien ne dépasse sous le capot (z ≥ D),
    # le haut-parleur collé au dos de la carte n'est plus gêné. 3 nervures Ø1,2 par côté,
    # serrage ≈ 0,2 mm dans la découpe ; elles s'écrasent légèrement à l'enfoncement.
    d = 40.0 * np.cos(np.radians(60))          # apothème du triangle = distance centre-bord
    nerv = []
    for k in range(3):
        ang = np.radians(240.0 + k * 120.0)      # direction du milieu de chaque côté
        n = np.array([np.cos(ang), np.sin(ang)])
        t = np.array([-n[1], n[0]])
        for u in (-12.0, 0.0, 12.0):
            p = n * (d - 0.4) + t * u
            nerv.append(ext(circ(0.6, p[0], p[1], 16), D + 0.6, z_ext))
            nerv.append(M.cylinder(0.6, 0.2, 0.6, 16).translate((p[0], p[1], D)))   # entrée conique
    g += union(nerv)
    return {"orange": g}


def capot_corps():
    a = np.radians
    p = ext(circ(R), D, D + CAPOT)
    trous = []
    for ang in BOSS_ANG:
        x, y = 61.5 * np.cos(a(ang)), 61.5 * np.sin(a(ang))
        trous.append(ext(circ(1.7, x, y, 32), D - 1, D + CAPOT + 1))
        trous.append(fraisure(x, y, D + CAPOT))
    # aération et sortie du son : le triangle « play » du logo, centré, fermé par la grille clipsée
    trous.append(ext(triangle_play(), D - 1, D + CAPOT + 1))
    p -= union(trous)
    return {"bleu": p}


def bague_ecran():
    a = np.radians
    anneau = circ(BAGUE_RE) - circ(BAGUE_RI)
    oreilles = []
    for ang in (45, 135, 225, 315):
        x, y = R_VIS_BAGUE * np.cos(a(ang)), R_VIS_BAGUE * np.sin(a(ang))
        oreilles.append(circ(2.4, x, y, 48))
        oreilles.append(CS.square((4.0, 4.8)).translate((BAGUE_RE - 1.5, -2.4)).rotate(ang))
    plat = anneau + union(oreilles)
    for ang in (45, 135, 225, 315):
        plat -= circ(1.2, R_VIS_BAGUE * np.cos(a(ang)), R_VIS_BAGUE * np.sin(a(ang)), 32)
    plat -= rect(X_USB[0] + 1.0, 38.0, X_USB[1] - 1.0, 62.0)          # dégagement sous les fiches USB-C coudées
    z0 = EP_FACE + VERRE_EP
    levre = ext(circ(57.0) - circ(54.6) - rect(X_USB[0] + 1.0, 38.0, X_USB[1] - 1.0, 62.0), z0, z0 + BAGUE_LEVRE)          # appuie sur le verre
    corps_b = ext(plat, z0 + BAGUE_LEVRE, z0 + BAGUE_LEVRE + BAGUE_EP)
    return {"bleu": levre + corps_b}


# ---------------------------------------------------------------- tête
def contour_module():
    oeil = CS.circle(1.0, 96).scale((10.4, 10.4))  # circuit de 51,2 mm de large
    return oeil.translate((-OEIL_X, OEIL_Y)) + oeil.translate((OEIL_X, OEIL_Y)) + rect(-8, OEIL_Y - 7, 8, OEIL_Y + 7)


def forme_tete(retrait=0.0):
    return rect_arrondi(-TW / 2 + retrait, T_BAS + retrait, TW / 2 - retrait, T_BAS + TH - retrait, T_RAYON - retrait)


def tete():
    a = np.radians
    ext2d = circ(R_T, 0, TCY, 192)
    int2d = circ(R_T - PAROI, 0, TCY, 192)
    t = ext(ext2d, 0, TD) - ext(int2d, EP_FACE_T, TD + 1)

    # « cheveux » : branches du réseau du logo, terminées par des nœuds
    cheveux = CHEVEUX
    ch2d = None
    noeuds = []
    for ang, long_, rn, style in cheveux:
        p0 = (0.6 * R_T * np.cos(a(ang)), TCY + 0.6 * R_T * np.sin(a(ang)))
        p1 = (long_ * np.cos(a(ang)), TCY + long_ * np.sin(a(ang)))
        forme = trait(p0, p1, ech(6)) + circ(rn, *p1, 64)
        ch2d = forme if ch2d is None else ch2d + forme
        noeuds.append((p1, rn, style))
    t += ext(ch2d - circ(R_T - PAROI + 0.4, 0, TCY, 96), 0, 8.0)

    # cadre de positionnement du module DualEye
    cm = contour_module()
    t += ext(cm.offset(1.6, mf.JoinType.Round) - cm.offset(0.25, mf.JoinType.Round), EP_FACE_T, EP_FACE_T + 3.0)
    # bossages du capot de tête, fondus dans la paroi, base conique
    boss = [((R_T - 4.0) * np.cos(a(g)), TCY + (R_T - 4.0) * np.sin(a(g))) for g in (220, 320, 90)]
    for bx, by in boss:
        t += (ext(circ(3.0, bx, by, 48), 22.0, TD) + M.cylinder(3.0, 0.0, 3.0, 48).translate((bx, by, 19.0))) ^ ext(ext2d, 0, TD)

    trous = [ext(circ(OEIL_AFF_R, -OEIL_X, OEIL_Y) + circ(OEIL_AFF_R, OEIL_X, OEIL_Y), -1, EP_FACE_T + 1)]
    trous.append(ext(rect(-COU_X - 0.25, T_BAS - 1, COU_X + 0.25, TCY - np.sqrt((R_T - PAROI) ** 2 - (COU_X + 0.25) ** 2) + 0.4), -1, TD + 1))  # encoche du cou
    trous.append(ext(rect(-COU_X - 0.25, T_BAS - 1, COU_X + 0.25, COU_HAUT + 0.25), -1, EP_FACE_T + 0.01))  # le cou affleure en face avant
    for bx, by in boss:
        trous.append(ext(circ(0.9, bx, by, 32), TD - 8.0, TD + 0.1))
    t -= union(trous)

    orange = ext(circ(12.6, -OEIL_X, OEIL_Y) - circ(9.8, -OEIL_X, OEIL_Y), 0, INCR)
    orange += ext(circ(12.6, OEIL_X, OEIL_Y) - circ(9.8, OEIL_X, OEIL_Y), 0, INCR)
    for (x, y), rn, style in noeuds:
        if style == "anneau":
            orange += ext(circ(rn - 2.0, x, y, 64) - circ(rn / PHI ** 2, x, y, 64), 0, INCR)
        elif style == "plein":
            orange += ext(circ(rn - 1.4, x, y, 64), 0, INCR)
    return {"bleu": t - orange, "orange": orange ^ t}


def capot_tete():
    a = np.radians
    p = ext(circ(R_T, 0, TCY, 192), TD, TD + 2.0)
    for sx in (-1, 1):
        p += ext(circ(3.0, sx * VIS_COU_X, VIS_COU_Y, 48), COU_Z + 0.2, TD)     # plots qui viennent sur le cou
        # fourreau autour de chaque ergot : le fond appuie sur l'ergot par une pastille de mousse
        # à couper à la bonne épaisseur. Il reste au-dessus des composants du circuit.
        z_dos = EP_FACE_T + T_OEIL
        p += ext(circ(ERGOT_R + 1.95, sx * ERGOT_X, ERGOT_Y, 48), z_dos + 3.0, TD)
        p -= ext(circ(ERGOT_R + 0.5, sx * ERGOT_X, ERGOT_Y, 48), z_dos + 2.0, z_dos + ERGOT_FOURREAU)
    trous = []
    for sx in (-1, 1):
        trous.append(ext(circ(1.7, sx * VIS_COU_X, VIS_COU_Y, 32), COU_Z - 1, TD + 3))
        trous.append(fraisure(sx * VIS_COU_X, VIS_COU_Y, TD + 2.0))
    for g in (220, 320, 90):
        bx, by = (R_T - 4.0) * np.cos(a(g)), TCY + (R_T - 4.0) * np.sin(a(g))
        trous.append(ext(circ(1.2, bx, by, 32), TD - 1, TD + 3))
        trous.append(fraisure(bx, by, TD + 2.0, d_tete=4.4, d_trou=2.4))
    # ouïes verticales
    for i in (-2, -1, 0, 1, 2):    # groupées au centre : aucune fente sous les ergots
        trous.append(ext(rect_arrondi(i * 7.0 - 1.5, OEIL_Y - 8.0, i * 7.0 + 1.5, OEIL_Y + 8.0, 1.5), TD - 1, TD + 3))
    p -= union(trous)
    return {"bleu": p}


def capot_camera():
    a = np.radians
    p = ext(circ(SAT_R, *SAT_C, 128), SAT_D, SAT_D + 2.0)
    for ang in (30, 150):
        x, y = SAT_C[0] + SAT_BOSS * np.cos(a(ang)), SAT_C[1] + SAT_BOSS * np.sin(a(ang))
        p -= ext(circ(1.2, x, y, 24), SAT_D - 1, SAT_D + 3)
        p -= fraisure(x, y, SAT_D + 2.0, d_tete=4.4, d_trou=2.4)
    return {"bleu": p}


# ---------------------------------------------------------------- socle
POLICE = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"


def socle():
    # empreinte dans le plan X-Z, extrudée selon Y
    emp = rect_arrondi(-SOCLE_X, SOCLE_Z0, SOCLE_X, SOCLE_Z1, 9.0)
    # rotation +90° autour de X : (x, y, z) -> (x, -z, y) ; y_2D devient z, l'extrusion devient +y
    s = emp.extrude(SOCLE_HAUT - SOCLE_BAS).translate((0, 0, -(SOCLE_HAUT - SOCLE_BAS))).rotate((90, 0, 0))
    s = s.translate((0, SOCLE_BAS, 0))
    # berceau cylindrique
    s -= M.cylinder(D + CAPOT + 0.6, R + 0.3, R + 0.3, SEG).translate((0, 0, -0.3))
    # vis M3 depuis le dessous, lamage Ø6,2
    for z in VIS_SOCLE_Z:
        s -= cyl_y(1.7, 0.0, SOCLE_BAS - 1, -R + 1, z)
        s -= M.cylinder(1.5, 1.7, 3.2, 48).rotate((90, 0, 0)).translate((0.0, SOCLE_BAS + 1.5, z))
        s -= cyl_y(3.2, 0.0, SOCLE_BAS - 1, SOCLE_BAS + 0.01, z)

    # prise USB-C au dos du socle (rallonge HuaLiSiJi) : logements ouverts par-dessous
    bas = SOCLE_BAS - 1.0
    s -= ext(rect(SP_X - 13.7, bas, SP_X + 13.7, SP_Y + 6.3), SP_SIEGE - PLATINE_EP, SP_SIEGE)   # platine (flasque moulée de 5-6 mm, retour de Philippe 30/10)
    s -= ext(rect(SP_X - 7.4, bas, SP_X + 7.4, SP_Y + 4.4), -4.0, SP_SIEGE - PLATINE_EP + 0.1)                 # corps de prise + boucle du câble
    fen = rect_arrondi(SP_X - FENETRE_L / 2, SP_Y - FENETRE_W / 2, SP_X + FENETRE_L / 2, SP_Y + FENETRE_W / 2, 2.0)
    for sx_ in (-1, 1):
        a_, b_ = SP_X + sx_ * VIS_PLATINE[0], SP_X + sx_ * VIS_PLATINE[1]
        fen = fen + rect(min(a_, b_), SP_Y - 1.65, max(a_, b_), SP_Y + 1.65) + circ(1.65, a_, SP_Y, 24) + circ(1.65, b_, SP_Y, 24)
    s -= ext(fen, SP_SIEGE - 0.5, SOCLE_Z1 + 1.0)
    # gorge sous le socle jusqu'à la fente, puis fente verticale vers le corps
    s -= ext(rect(FENTE_X[0], bas, SP_X, SOCLE_BAS + 6.0), -4.0, 4.0)
    s -= ext(rect(FENTE_X[0], bas, FENTE_X[1], SOCLE_BAS + 6.0), -4.0, FENTE_Z[1])
    s -= ext(rect(FENTE_X[0], bas, FENTE_X[1], SOCLE_HAUT + 1.0), FENTE_Z[0], FENTE_Z[1])

    # logotype en relief sur la tablette avant
    h = 12.0
    t1, t2 = texte("explorations", h, POLICE), texte("360", h, POLICE)
    (x0, y0), (x1, y1) = t1.bounds()[:2], t1.bounds()[2:]
    b1 = t1.bounds(); b2 = t2.bounds()
    esp = 0.9
    larg = (b1[2] - b1[0]) + esp + (b2[2] - b2[0])
    t1 = t1.translate((-larg / 2 - b1[0], 0))
    t2 = t2.translate((-larg / 2 + (b1[2] - b1[0]) + esp - b2[0], 0))
    z_centre = SOCLE_Z0 / 2 - 1.0
    y_c = (b1[1] + b1[3]) / 2

    def poser(cs, ep):
        cs = cs.translate((0, -y_c))
        m_ = cs.extrude(ep).translate((0, 0, -ep)).rotate((90, 0, 0))
        return m_.translate((0, SOCLE_HAUT, z_centre))

    relief = 0.8
    bleu_txt = poser(t1, relief)
    orange_txt = poser(t2, relief)
    return {"bleu": s + bleu_txt, "orange": orange_txt}, larg


# ---------------------------------------------------------------- orientation d'impression
def vers_impression(nom, m_):
    """Transforme du repère d'assemblage vers le plateau (z vers le haut, posé à z=0)."""
    if nom in ("capot_corps", "capot_tete", "capot_camera", "bague_ecran", "grille_capot"):
        m_ = m_.rotate((180, 0, 0))
    elif nom == "socle":
        m_ = m_.rotate((90, 0, 0))       # (x, y, z) -> (x, -z, y) : le dessous du socle descend sur le plateau
    return m_


def construire():
    parts = {
        "corps": corps(),
        "capot_corps": capot_corps(),
        "bague_ecran": bague_ecran(),
        "tete": tete(),
        "capot_tete": capot_tete(),
        "capot_camera": capot_camera(),
        "grille_capot": grille_capot(),
    }
    s, larg = socle()
    parts["socle"] = s
    return parts, larg


if __name__ == "__main__":
    parts, larg = construire()
    for n, d in parts.items():
        for c, m_ in d.items():
            print(n, c, m_.status(), round(m_.volume() / 1000, 1), "cm3", m_.bounding_box())
    print("largeur logotype", round(larg, 1))
