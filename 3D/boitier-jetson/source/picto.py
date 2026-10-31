"""Pictogramme « centre du réseau » : nœud plein relié à des satellites, comme le logo."""
import numpy as np
import manifold3d as mf
CS = mf.CrossSection


def _trait(p0, p1, w):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    a = np.degrees(np.arctan2(d[1], d[0]))
    return (CS.square((np.linalg.norm(d), w)).translate((0, -w / 2)).rotate(a).translate(tuple(p0))
            + CS.circle(w / 2, 20).translate(tuple(p0)) + CS.circle(w / 2, 20).translate(tuple(p1)))


def reseau(w=1.3, w_lien=1.0):
    """Environ 26 × 22 mm, centré sur le nœud central (0, 0)."""
    out = CS.circle(3.8, 64)                                   # le Jetson
    for ang, d, r in ((90, 10.5, 2.4), (210, 10.5, 2.4), (330, 10.5, 2.4),   # anneaux : écrans, PC, boîtes mail
                      (30, 7.5, 1.4), (150, 7.5, 1.4), (270, 7.5, 1.4)):     # points pleins
        u = np.array([np.cos(np.radians(ang)), np.sin(np.radians(ang))])
        c = u * d
        if r > 2:
            out += CS.circle(r + w / 2, 64).translate(tuple(c)) - CS.circle(r - w / 2, 64).translate(tuple(c))
            out += _trait(u * 3.0, u * (d - r), w_lien)
        else:
            out += CS.circle(r, 48).translate(tuple(c))
            out += _trait(u * 3.0, u * (d - r + 0.3), w_lien)
    return out
