"""Boîtier imprimable pour le Jetson Orin Nano (Super) Developer Kit, tricolore explorations360.

Repère DIRECT : X vers la droite, Y vers l'arrière (côté des prises), Z vers le haut.
Le fond du bac est en z = 0. Toutes les cotes en mm.

Deux pièces :
- bac : fond ventilé, parois à fentes verticales, grande fenêtre arrière pour les prises
  (ouverte jusqu'en haut : aucun pont), colonnes de vis dans les quatre coins ;
- couvercle : grande grille ronde au-dessus du ventilateur, satellite ventilé et logotype
  comme sur le logo, lèvre de centrage, rideau qui ferme le haut de la fenêtre arrière.
"""
import os
import numpy as np
import manifold3d as mf
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
from picto import reseau

CS, M = mf.CrossSection, mf.Manifold
PHI = (1 + 5 ** 0.5) / 2
POLICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Poppins-Medium.ttf")   # fournie, licence OFL

# ---------------------------------------------------------------- le Jetson (à vérifier sur la carte)
KIT_X, KIT_Y, KIT_H = 103.0, 91.0, 35.0      # mesuré par Philippe le 31/10 (pieds, carte, module, radiateur, ventilateur)
PRISES_Z = (3.0, 30.0)                       # bande des prises au-dessus du fond (carte sur ses pieds ≈ 6,6 mm, prises ≤ 16,7 mm au-dessus)

# ---------------------------------------------------------------- boîtier
JEU_X = 7.0                 # de chaque côté en X : les coins du kit passent à côté des colonnes de vis
JEU_Y = 1.0                 # de chaque côté en Y : le kit est tenu d'avant en arrière (cote mesurée à la règle)
CAV_X, CAV_Y = KIT_X + 2 * JEU_X, KIT_Y + 2 * JEU_Y      # 117 x 93
H_INT = 41.0                # hauteur intérieure : ~6 mm d'air au-dessus du ventilateur
PAROI, FOND, DESSUS = 2.4, 2.4, 2.4
R_EXT = 6.0
EXT_X, EXT_Y = CAV_X + 2 * PAROI, CAV_Y + 2 * PAROI
H_BAC = FOND + H_INT        # hauteur du bac (le couvercle se pose dessus)
INCR = 0.6                  # épaisseur des incrustations orange (3 couches)
BOSS_R = 6.5                # colonnes de vis (quarts de cylindre dans les coins)
LEVRE_P, LEVRE_E, LEVRE_JEU = 5.0, 1.8, 0.3


def circ(r, x=0.0, y=0.0, seg=128):
    return CS.circle(r, seg).translate((x, y))


def rect(x0, y0, x1, y1):
    return CS.square((x1 - x0, y1 - y0)).translate((x0, y0))


def ext(cs, z0, z1):
    return cs.extrude(z1 - z0).translate((0, 0, z0))


def rect_arrondi(x0, y0, x1, y1, r):
    return (rect(x0 + r, y0, x1 - r, y1) + rect(x0, y0 + r, x1, y1 - r)
            + circ(r, x0 + r, y0 + r, 48) + circ(r, x1 - r, y0 + r, 48)
            + circ(r, x0 + r, y1 - r, 48) + circ(r, x1 - r, y1 - r, 48))


def union(lst):
    out = lst[0]
    for p in lst[1:]:
        out = out + p
    return out


def texte(s, h):
    tp = TextPath((0, 0), s, size=h, prop=FontProperties(fname=POLICE))
    polys = [p for p in tp.to_polygons(closed_only=True) if len(p) >= 3]
    return CS([np.asarray(p, float)[:-1] for p in polys], mf.FillRule.EvenOdd)


def nid_abeille(zone, r_trou=2.6, pas=6.4):
    """Trous hexagonaux entièrement contenus dans la zone (CrossSection)."""
    bx = zone.bounds()
    trous = []
    for j in range(int((bx[3] - bx[1]) / (pas * 0.866)) + 3):
        y = bx[1] - pas + j * pas * 0.866
        for i in range(int((bx[2] - bx[0]) / pas) + 3):
            x = bx[0] - pas + i * pas + (pas / 2 if j % 2 else 0)
            h = CS.circle(r_trou, 6).rotate(30).translate((x, y))
            if (zone ^ h).area() > 0.999 * h.area():
                trous.append(h)
    return union(trous)


def contour_ext():
    return rect_arrondi(-EXT_X / 2, -EXT_Y / 2, EXT_X / 2, EXT_Y / 2, R_EXT)


def cavite():
    return rect_arrondi(-CAV_X / 2, -CAV_Y / 2, CAV_X / 2, CAV_Y / 2, R_EXT - PAROI)


BOSS = [(sx * CAV_X / 2, sy * CAV_Y / 2) for sx in (-1, 1) for sy in (-1, 1)]        # quarts de colonne dans les 4 coins
VIS = [(sx * (CAV_X / 2 - 2.6), sy * (CAV_Y / 2 - 2.6)) for sx in (-1, 1) for sy in (-1, 1)]   # axe des vis
FEN_X = 102.0               # longueur de la fenêtre des prises


# ---------------------------------------------------------------- bac
def bac():
    b = ext(contour_ext(), 0, H_BAC) - ext(cavite(), FOND, H_BAC + 1)
    # bossages de vis : quarts de colonne dans les quatre coins, du fond au haut
    for x, y in BOSS:
        b += ext(circ(BOSS_R, x, y, 48) ^ cavite(), FOND - 0.01, H_BAC)
    trous = []
    for x, y in VIS:
        trous.append(ext(circ(1.25, x, y, 32), H_BAC - 14, H_BAC + 1))        # M3, avant-trou Ø2,5
    # fenêtre des prises, à l'arrière, ouverte jusqu'en haut (fermée par le rideau du couvercle)
    trous.append(ext(rect(-FEN_X / 2, CAV_Y / 2 - 1, FEN_X / 2, EXT_Y / 2 + 1), FOND + PRISES_Z[0], H_BAC + 1))
    # fentes verticales : façade avant et petits côtés (sorties d'air du radiateur)
    z0, z1 = FOND + 6.0, H_BAC - 7.0
    for i in range(-7, 8):
        x = i * 7.0
        trous.append(ext(rect(x - 1.4, -EXT_Y / 2 - 1, x + 1.4, -CAV_Y / 2 + 1), z0, z1).translate((0, 0, 0)))
    for sx in (-1, 1):
        for j in range(-5, 6):
            y = j * 7.0
            if abs(y) > CAV_Y / 2 - BOSS_R - 2:      # pas de fente derrière les bossages d'angle
                continue
            trous.append(ext(rect(sx * CAV_X / 2 - 1 if sx > 0 else -EXT_X / 2 - 1, y - 1.4,
                                  EXT_X / 2 + 1 if sx > 0 else -CAV_X / 2 + 1, y + 1.4), z0, z1))
    # arrondir le haut et le bas des fentes : rien à faire (fentes de 2,8 mm, ponts minuscules)
    # fond ventilé sous le SSD et la carte : lumières oblongues
    for j in range(-4, 5):
        y = j * 8.0
        for x0, x1 in ((-44.0, -6.0), (6.0, 44.0)):
            trous.append(ext(rect(x0, y - 1.5, x1, y + 1.5) + circ(1.5, x0, y, 24) + circ(1.5, x1, y, 24), -1, FOND + 1))
    # logements de patins (Ø12 × 0,8) sous les quatre coins
    for sx in (-1, 1):
        for sy in (-1, 1):
            trous.append(ext(circ(6.0, sx * (EXT_X / 2 - 11), sy * (EXT_Y / 2 - 11), 48), -1, 0.8))
    return {"bleu": b - union(trous)}


# ---------------------------------------------------------------- couvercle
R_GRILLE = 36.0                         # grande grille ronde, au-dessus du ventilateur
C_GRILLE = (-14.0, 0.0)
ANG_SAT = 90.0 / PHI ** 2               # 34,4°, comme la caméra de la Vigie
R_SAT = R_GRILLE / PHI ** 2             # 14,1
D_SAT = R_GRILLE + R_GRILLE / PHI ** 4 + R_SAT
C_SAT = (C_GRILLE[0] + D_SAT * np.cos(np.radians(ANG_SAT)), C_GRILLE[1] + D_SAT * np.sin(np.radians(ANG_SAT)))
PICTO_C = (39.0, -17.5)                # pictogramme « centre du réseau » (centre du nœud)
ANNEAU = 3.4                            # largeur des anneaux orange


def couvercle():
    zt = H_BAC
    c = ext(contour_ext(), zt, zt + DESSUS)
    # lèvre de centrage tout autour (sauf devant les bossages), sous le dessus
    lev = (cavite().offset(-LEVRE_JEU, mf.JoinType.Round) - cavite().offset(-LEVRE_JEU - LEVRE_E, mf.JoinType.Round))
    for x, y in BOSS:
        lev -= circ(BOSS_R + 0.6, x, y, 48)
    c += ext(lev, zt - LEVRE_P, zt + 0.01)
    # rideau qui ferme le haut de la fenêtre des prises
    jeu = 0.25
    c += ext(rect(-FEN_X / 2 + jeu, CAV_Y / 2 + 0.01, FEN_X / 2 - jeu, EXT_Y / 2), FOND + PRISES_Z[1], zt + 0.01)
    trous = []
    # grilles : grande ronde et satellite
    g_zone = circ(R_GRILLE - ANNEAU - 1.2, *C_GRILLE)
    s_zone = circ(R_SAT - ANNEAU - 1.0, *C_SAT)
    trous.append(ext(nid_abeille(g_zone) + nid_abeille(s_zone, r_trou=2.2, pas=5.6), zt - 1, zt + DESSUS + 1))
    # vis M3 fraisées (tête Ø6) dans les bossages
    for xi, y in VIS:
        trous.append(ext(circ(1.7, xi, y, 32), zt - 1, zt + DESSUS + 1))
        trous.append(M.cylinder(1.5, 1.7, 3.2, 48).translate((xi, y, zt + DESSUS - 1.5)))
    c -= union(trous)
    # face extérieure = haut (z = zt + DESSUS) : incrustations orange dans les 0,6 derniers mm
    z_or = zt + DESSUS - INCR
    orange = ext(circ(R_GRILLE, *C_GRILLE) - circ(R_GRILLE - ANNEAU, *C_GRILLE), z_or, zt + DESSUS)
    orange += ext(circ(R_SAT, *C_SAT) - circ(R_SAT - ANNEAU, *C_SAT), z_or, zt + DESSUS)
    # point orange du logo, en grappe avec le satellite
    r_pt = R_GRILLE / PHI ** 4
    a_pt = np.radians(-80.0)
    d_pt = R_SAT + r_pt + R_GRILLE / PHI ** 5
    C_PT = (C_SAT[0] + d_pt * np.cos(a_pt), C_SAT[1] + d_pt * np.sin(a_pt))
    orange += ext(circ(r_pt, *C_PT, 64), z_or, zt + DESSUS)
    # logotype en bas à droite : « explorations » en bleu ciel, « 360 » en orange
    h = 6.5
    t1, t2 = texte("explorations", h), texte("360", h)
    b1, b2 = t1.bounds(), t2.bounds()
    esp = 0.5
    larg = (b1[2] - b1[0]) + esp + (b2[2] - b2[0])
    x_fin = EXT_X / 2 - 11.0
    y_bas = -EXT_Y / 2 + 7.0
    t1 = t1.translate((x_fin - larg - b1[0], y_bas - b1[1]))
    t2 = t2.translate((x_fin - (b2[2] - b2[0]) - b2[0], y_bas - b1[1]))
    ciel = ext(t1, z_or, zt + DESSUS)          # « explorations » en bleu ciel (extrudeur 3)
    orange += ext(t2, z_or, zt + DESSUS)
    # « centre du réseau » au-dessus du « 360 » : le Jetson reçoit tout et redistribue aux écrans
    orange += ext(reseau().translate(PICTO_C), z_or, zt + DESSUS)
    orange = orange ^ c
    ciel = ciel ^ c
    return {"bleu": c - orange - ciel, "orange": orange, "ciel": ciel}, C_PT


def kit_fantome():
    """Volume indicatif du Jetson pour l'aperçu (pieds, carte, radiateur, ventilateur)."""
    y_dos = CAV_Y / 2 - JEU_Y
    pieds = union([ext(circ(2.5, sx * 43, y_dos - 4 - (0 if sy > 0 else 58), 24), FOND, FOND + 6.6) for sx in (-1, 1) for sy in (-1, 1)])
    carte = ext(rect(-50, y_dos - 79, 50, y_dos), FOND + 6.6, FOND + 8.2)
    prises = ext(rect(-46, y_dos - 22, 40, y_dos), FOND + 8.2, FOND + 24.0)
    rad = ext(rect(-34, -KIT_Y / 2, 30, y_dos - 26), FOND + 9.0, FOND + 28.0)
    vent = ext(circ(19, -8, -10, 64), FOND + 28.0, FOND + KIT_H)
    return {"carte": carte + pieds, "prises": prises, "radiateur": rad + vent}


def kit_enveloppe():
    """Encombrement mesuré du kit, à angles vifs : le cas le plus défavorable, pour verif.py."""
    return ext(rect(-KIT_X / 2, -KIT_Y / 2, KIT_X / 2, KIT_Y / 2), FOND, FOND + KIT_H)


def construire():
    return {"bac": bac(), "couvercle": couvercle()[0]}


def vers_impression(nom, m_):
    if nom == "couvercle":          # dessus sur le plateau (rotation propre : rien n'est en miroir)
        return m_.rotate((180, 0, 0))
    return m_


if __name__ == "__main__":
    parts = construire()
    for n, d in parts.items():
        for c, m_ in d.items():
            bb = m_.bounding_box()
            print(n, c, m_.status(), round(m_.volume() / 1000, 1), "cm3", [round(v, 1) for v in bb])
    print("satellite", [round(v, 1) for v in C_SAT], "r", round(R_SAT, 1))
