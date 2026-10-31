"""Équilibre visuel de la silhouette de face : centre de gravité des surfaces vues (z = 0),
l'orange pesant 1,5 fois plus que le bleu (une couleur saturée et claire « pèse » davantage)."""
import itertools
import numpy as np
import vigie_robot as vr

POIDS_ORANGE = 1.5


def centroide(cs):
    pts_x = 0.0
    aire = 0.0
    for p in cs.to_polygons():
        p = np.asarray(p)
        x, y = p[:, 0], p[:, 1]
        x1, y1 = np.roll(x, -1), np.roll(y, -1)
        c = x * y1 - x1 * y
        a = c.sum() / 2
        aire += a
        pts_x += ((x + x1) * c).sum() / 6
    return aire, pts_x


def bilan():
    parts, _ = vr.construire()
    m = 0.0
    mx = 0.0
    for n in ("corps", "tete"):
        for c, man in parts[n].items():
            a, ax = centroide(man.slice(0.3))
            w = POIDS_ORANGE if c == "orange" else 1.0
            m += w * a
            mx += w * ax
    # l'écran (disque sombre) et le socle pèsent aussi dans la silhouette, centrés en x = 0
    m += np.pi * vr.R_OUV ** 2 + 2 * vr.SOCLE_X * (vr.SOCLE_HAUT - vr.SOCLE_BAS)
    bb = parts["corps"]["bleu"].bounding_box()
    return mx / m, bb[0], bb[3]


if __name__ == "__main__":
    print("réglage actuel : x du centre de gravité visuel = %.2f mm, emprise x %.1f .. %.1f" % bilan())
    meilleur = None
    for n2, pa in itertools.product(range(170, 236, 10), range(80, 141, 10)):
        if abs(((n2 - pa) + 180) % 360 - 180) < 60:      # garder la grappe ouverte
            continue
        vr.BRAS_N2_ANG, vr.BRAS_P_ANG = float(n2), float(pa)
        gx, x0, x1 = bilan()
        if x1 - x0 > 235:                                  # plateau 250 mm, marge
            continue
        score = abs(gx)
        if meilleur is None or score < meilleur[0]:
            meilleur = (score, n2, pa, gx, x0, x1)
    print("meilleur : nœud 2 à %d°, point à %d° -> x = %.2f mm, emprise %.1f .. %.1f" % meilleur[1:])
