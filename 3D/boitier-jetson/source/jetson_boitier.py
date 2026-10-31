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

# Cotes relevées sur le modèle 3D officiel du kit (carte P3768 + module P3767), dans le repère
# du constructeur (prises côté y mini). cao(x0, y0, z0, x1, y1, z1) les passe dans notre repère :
# boîte centrée, prises vers +Y, dessous du kit posé sur le fond du bac.
CAO_C = (46.0, 26.65)       # centre de l'encombrement du kit (103,4 x 91,1) dans le repère constructeur
CAO_Z0 = -5.0               # dessous du kit (pieds) dans le repère constructeur


def cao(x0, y0, z0, x1, y1, z1):
    """(xmin, ymin, zmin, xmax, ymax, zmax) constructeur -> idem dans notre repère (rotation de 180° autour de Z)."""
    return (CAO_C[0] - x1, CAO_C[1] - y1, z0 - CAO_Z0 + FOND_K, CAO_C[0] - x0, CAO_C[1] - y0, z1 - CAO_Z0 + FOND_K)


FOND_K = 2.4                # = FOND (le kit est posé sur le fond)
KIT = {                     # pièces du kit utiles au boîtier
    "socle": cao(-5.7, -18.7, -5.0, 97.7, 72.2, 1.6),       # semelle plastique et pieds
    "carte": cao(-4.0, -17.0, 0.0, 96.0, 62.0, 1.6),
    "radiateur": cao(15.1, 22.5, 11.9, 72.9, 61.5, 29.9),    # dessus des ailettes : point le plus haut
    "ventilateur": cao(26.6, 24.5, 21.0, 61.5, 59.5, 29.4),   # 35 x 35
    "module": cao(9.2, 16.5, 6.7, 78.8, 61.5, 12.3),
    "connecteur_40": cao(89.5, 3.6, -1.4, 94.5, 54.4, 10.2),
}
PRISES = {                  # à l'arrière, de gauche à droite vu de dos
    "alimentation": cao(-2.5, -18.5, -1.9, 7.6, -4.0, 12.6),
    "displayport": cao(9.8, -18.9, -0.4, 27.7, -3.8, 8.3),
    "usb_a": cao(30.1, -18.7, -0.8, 64.1, 0.1, 18.2),        # deux blocs de deux USB-A
    "ethernet": cao(64.8, -18.8, -2.2, 81.0, 2.9, 15.2),
    "usb_c": cao(82.9, -18.6, -0.2, 93.0, -7.8, 4.9),
}
MARGE_PRISE = 1.0           # jeu autour de chaque prise dans la paroi arrière

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


def nid_abeille(zone, centre, r_trou=2.6, pas=6.4):
    """Trous hexagonaux entièrement contenus dans la zone, en réseau centré sur `centre` :
    un trou au centre, rangées symétriques de part et d'autre (dessin régulier)."""
    bx = zone.bounds()
    n = int(max(bx[2] - bx[0], bx[3] - bx[1]) / pas) + 2
    trous = []
    for j in range(-n, n + 1):
        y = centre[1] + j * pas * 0.866
        for i in range(-n, n + 1):
            x = centre[0] + (i + (0.5 if j % 2 else 0.0)) * pas
            h = CS.circle(r_trou, 6).rotate(30).translate((x, y))
            if (zone ^ h).area() > 0.999 * h.area():
                trous.append(h)
    return union(trous)


def grille_ronde(centre, r_zone, r_trou, anneaux):
    """Trous ronds en anneaux concentriques (1, 6, 12, 18… trous) : dessin parfaitement symétrique,
    dans le langage de cercles du logo. Le dernier anneau affleure le bord de la zone."""
    pas = (r_zone - r_trou) / anneaux
    trous = [CS.circle(r_trou, 32).translate(tuple(centre))]
    for k in range(1, anneaux + 1):
        n = 6 * k
        for i in range(n):
            a = 2 * np.pi * i / n + (np.pi / n if k % 2 == 0 else 0.0)   # anneaux pairs décalés d'un demi-pas
            trous.append(CS.circle(r_trou, 32).translate((centre[0] + k * pas * np.cos(a), centre[1] + k * pas * np.sin(a))))
    return union(trous)


def contour_ext():
    return rect_arrondi(-EXT_X / 2, -EXT_Y / 2, EXT_X / 2, EXT_Y / 2, R_EXT)


def cavite():
    return rect_arrondi(-CAV_X / 2, -CAV_Y / 2, CAV_X / 2, CAV_Y / 2, R_EXT - PAROI)


BOSS = [(sx * CAV_X / 2, sy * CAV_Y / 2) for sx in (-1, 1) for sy in (-1, 1)]        # quarts de colonne dans les 4 coins
VIS = [(sx * (CAV_X / 2 - 2.6), sy * (CAV_Y / 2 - 2.6)) for sx in (-1, 1) for sy in (-1, 1)]   # axe des vis


def fenetre_prises():
    """Fenêtre arrière (x mini, x maxi, bas) et profil de la languette : [(x0, x1, haut de la prise)]."""
    m = MARGE_PRISE
    iv = sorted((b[0] - m, b[3] + m, b[5] + m) for b in PRISES.values())
    # deux prises à moins de 3 mm : la limite passe au milieu (pas de lamelle de plastique)
    for i in range(len(iv) - 1):
        if iv[i + 1][0] - iv[i][1] < 3.0:
            mil = (iv[i][1] + iv[i + 1][0]) / 2
            iv[i] = (iv[i][0], mil, iv[i][2]); iv[i + 1] = (mil, iv[i + 1][1], iv[i + 1][2])
    bas = min(b[2] for b in PRISES.values()) - m
    return iv[0][0], iv[-1][1], bas, iv


# ---------------------------------------------------------------- bac
def bac():
    b = ext(contour_ext(), 0, H_BAC) - ext(cavite(), FOND, H_BAC + 1)
    # bossages de vis : quarts de colonne dans les quatre coins, du fond au haut
    for x, y in BOSS:
        b += ext(circ(BOSS_R, x, y, 48) ^ cavite(), FOND - 0.01, H_BAC)
    trous = []
    for x, y in VIS:
        trous.append(ext(circ(1.25, x, y, 32), H_BAC - 14, H_BAC + 1))        # M3, avant-trou Ø2,5
    # paroi arrière : les prises sont presque jointives, une seule fenêtre ouverte vers le haut ;
    # la languette du couvercle la referme en suivant la hauteur de chaque prise (pas de pont)
    xa, xb, zb, _ = fenetre_prises()
    trous.append(ext(rect(xa, CAV_Y / 2 - 3, xb, EXT_Y / 2 + 1), zb, H_BAC + 1))
    # butées latérales au ras du fond : le kit ne glisse plus de gauche à droite
    xs = KIT["socle"]
    for sx in (-1, 1):
        bord = xs[3] if sx > 0 else xs[0]
        b += ext(rect(min(sx * CAV_X / 2, bord + sx * 0.3), -6.0, max(sx * CAV_X / 2, bord + sx * 0.3), 6.0), FOND - 0.01, FOND + 5.0)
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
R_GRILLE = 30.0                         # grande grille ronde, au-dessus du ventilateur
C_GRILLE = ((KIT["ventilateur"][0] + KIT["ventilateur"][3]) / 2, (KIT["ventilateur"][1] + KIT["ventilateur"][4]) / 2)   # pile sur le ventilateur
ANG_SAT = 90.0 / PHI ** 2               # 34,4°, comme la caméra de la Vigie
R_SAT = R_GRILLE / PHI ** 2             # 14,1
D_SAT = R_GRILLE + R_GRILLE / PHI ** 4 + R_SAT
C_SAT = (C_GRILLE[0] + D_SAT * np.cos(np.radians(ANG_SAT)), C_GRILLE[1] + D_SAT * np.sin(np.radians(ANG_SAT)))
PICTO_ECH = 0.9                         # même encombrement que le satellite (≈ 23 mm)


def _place_picto():
    """Pictogramme sur la direction symétrique de celle du satellite (180° - 34,4°), éloigné de la
    grande grille du même écart que le satellite (R/φ⁴, bord à bord, mesuré au point le plus proche)."""
    ecart = R_GRILLE / PHI ** 4
    pts = np.vstack([np.asarray(q) for q in reseau().scale((PICTO_ECH, PICTO_ECH)).to_polygons()])
    a = np.radians(180.0 - ANG_SAT)
    u = np.array([np.cos(a), np.sin(a)])
    lo, hi = 0.0, 80.0
    for _ in range(60):
        d = (lo + hi) / 2
        c = np.array(C_GRILLE) + u * d
        if np.min(np.hypot(*(pts + c - np.array(C_GRILLE)).T)) - R_GRILLE < ecart:
            lo = d
        else:
            hi = d
    return tuple(np.array(C_GRILLE) + u * hi)


PICTO_C = _place_picto()
_r = KIT["radiateur"]
_v = KIT["ventilateur"]
# aux quatre coins du radiateur, à 3,2 mm de ses bords : hors du ventilateur et hors de la zone
# des trous de la grille (qui reste un disque complet). Placés symétriquement autour de la grille.
_cx, _cy = (_v[0] + _v[3]) / 2, (_v[1] + _v[4]) / 2
_dx = min(_cx - _r[0], _r[3] - _cx) - 3.2
_dy = min(_cy - _r[1], _r[4] - _cy) - 3.2
PLOTS = [(_cx + sx * _dx, _cy + sy * _dy) for sx in (-1, 1) for sy in (-1, 1)]
ANNEAU = 3.4                            # largeur des anneaux orange


def couvercle():
    zt = H_BAC
    c = ext(contour_ext(), zt, zt + DESSUS)
    # lèvre de centrage tout autour (sauf devant les bossages), sous le dessus
    lev = (cavite().offset(-LEVRE_JEU, mf.JoinType.Round) - cavite().offset(-LEVRE_JEU - LEVRE_E, mf.JoinType.Round))
    for x, y in BOSS:
        lev -= circ(BOSS_R + 0.6, x, y, 48)
    c += ext(lev, zt - LEVRE_P, zt + 0.01)
    # languette qui referme la fenêtre arrière au ras de chaque prise (profil en escalier)
    jeu = 0.25
    xa, xb, zb, iv = fenetre_prises()
    lang = ext(rect(xa + jeu, CAV_Y / 2 + 0.01, xb - jeu, EXT_Y / 2), zb, zt + 0.01)
    lang -= union([ext(rect(x0 - jeu, CAV_Y / 2 - 1, x1 + jeu, EXT_Y / 2 + 1), zb - 1, haut) for x0, x1, haut in iv])
    c += lang
    # quatre plots qui descendent jusqu'à 0,5 mm du dessus du radiateur, de part et d'autre du
    # ventilateur (jamais au-dessus des pales) : le Jetson ne peut plus sauter
    for x, y in PLOTS:
        c += ext(circ(3.0, x, y, 32), KIT["radiateur"][5] + 0.5, zt + 0.01)
    trous = []
    # grilles : grande ronde et satellite
    g_zone = circ(R_GRILLE - ANNEAU - 1.2, *C_GRILLE) - union([circ(4.4, x, y, 32) for x, y in PLOTS])   # pas de trou sur les plots
    s_zone = circ(R_SAT - ANNEAU - 1.0, *C_SAT)
    trous.append(ext(grille_ronde(C_GRILLE, R_GRILLE - ANNEAU - 1.2, 2.1, 4)
                     + grille_ronde(C_SAT, R_SAT - ANNEAU - 1.0, 1.4, 1), zt - 1, zt + DESSUS + 1))
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
    h = 8.0                     # plus gros pour une impression plus nette (retour de Philippe)
    t1, t2 = texte("explorations", h), texte("360", h)
    b1, b2 = t1.bounds(), t2.bounds()
    esp = 0.5
    larg = (b1[2] - b1[0]) + esp + (b2[2] - b2[0])
    x_debut = C_GRILLE[0] - larg / 2      # centré sur l'axe de la grille
    y_milieu = (C_GRILLE[1] + R_GRILLE + EXT_Y / 2) / 2   # milieu du bandeau entre la grille et le bord
    y_bas = y_milieu - (b1[3] - b1[1]) / 2
    t1 = t1.translate((x_debut - b1[0], y_bas - b1[1]))
    t2 = t2.translate((x_debut + (b1[2] - b1[0]) + esp - b2[0], y_bas - b1[1]))
    ciel = ext(t1, z_or, zt + DESSUS)          # « explorations » en bleu ciel (extrudeur 3)
    orange += ext(t2, z_or, zt + DESSUS)
    # « centre du réseau » au-dessus du « 360 » : le Jetson reçoit tout et redistribue aux écrans
    orange += ext(reseau().scale((PICTO_ECH, PICTO_ECH)).translate(PICTO_C), z_or, zt + DESSUS)
    orange = orange ^ c
    ciel = ciel ^ c
    return {"bleu": c - orange - ciel, "orange": orange, "ciel": ciel}, C_PT


def kit_fantome():
    """Volumes simplifiés du kit (boîtes relevées sur la CAO), pour l'aperçu et verif.py."""
    def boite(b):
        return ext(rect(b[0], b[1], b[3], b[4]), b[2], b[5])
    carte = boite(KIT["socle"]) + boite(KIT["carte"]) + boite(KIT["connecteur_40"])
    prises = union([boite(b) for b in PRISES.values()])
    rad = boite(KIT["module"]) + boite(KIT["radiateur"])
    v = KIT["ventilateur"]
    vent = ext(circ(16.5, (v[0] + v[3]) / 2, (v[1] + v[4]) / 2, 64), v[5] - 0.6, v[5] + 0.3)
    return {"carte": carte, "prises": prises, "radiateur": rad + vent}


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
