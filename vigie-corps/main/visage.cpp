/*
 * Vigie — détection de visage.
 *
 * Modèle MSR+MNP d'ESP-DL (composant espressif/human_face_detect), en flash.
 * Sur l'ESP32-P4, une image 800×800 est analysée en quelques dizaines de ms :
 * le prétraitement d'ESP-DL la réduit lui-même à la taille du modèle.
 *
 * visage_detecter_ordre() permet de forcer l'ordre des octets du RGB565
 * (diagnostic : vérifier lequel des deux le modèle reconnaît).
 */
#include "visage.h"
#include "sdkconfig.h"

#if CONFIG_VIGIE_VISAGE && __has_include("human_face_detect.hpp")

#include <algorithm>
#include <list>
#include "esp_log.h"
#include "human_face_detect.hpp"

static const char *TAG = "visage";
static HumanFaceDetect *detecteur = nullptr;

extern "C" bool visage_disponible(void)
{
    return true;
}

extern "C" int visage_detecter_ordre(const uint8_t *rgb565, int largeur, int hauteur,
                                     visage_t *res, int max, bool gros_boutiste)
{
    if (!detecteur) {
        detecteur = new HumanFaceDetect();
        ESP_LOGI(TAG, "Modèle de visage chargé");
    }
    dl::image::img_t img = {
        .data = (void *)rgb565,
        .width = (uint16_t)largeur,
        .height = (uint16_t)hauteur,
        .pix_type = gros_boutiste ? dl::image::DL_IMAGE_PIX_TYPE_RGB565BE
                                  : dl::image::DL_IMAGE_PIX_TYPE_RGB565LE,
    };
    std::list<dl::detect::result_t> &trouves = detecteur->run(img);
    trouves.sort([](const dl::detect::result_t &a, const dl::detect::result_t &b) {
        return a.score > b.score;
    });
    int n = 0;
    for (const auto &r : trouves) {
        if (n >= max) {
            break;
        }
        if (r.box.size() < 4) {
            continue;
        }
        res[n].x1 = std::max(0, r.box[0]);
        res[n].y1 = std::max(0, r.box[1]);
        res[n].x2 = std::min(largeur - 1, r.box[2]);
        res[n].y2 = std::min(hauteur - 1, r.box[3]);
        res[n].score = r.score;
        n++;
    }
    return n;
}

extern "C" int visage_detecter(const uint8_t *rgb565, int largeur, int hauteur, visage_t *res, int max)
{
    return visage_detecter_ordre(rgb565, largeur, hauteur, res, max, false);
}

#else  /* composant absent ou détection désactivée dans menuconfig */

extern "C" bool visage_disponible(void)
{
    return false;
}

extern "C" int visage_detecter_ordre(const uint8_t *rgb565, int largeur, int hauteur,
                                     visage_t *res, int max, bool gros_boutiste)
{
    (void)rgb565; (void)largeur; (void)hauteur; (void)res; (void)max; (void)gros_boutiste;
    return -1;
}

extern "C" int visage_detecter(const uint8_t *rgb565, int largeur, int hauteur, visage_t *res, int max)
{
    return visage_detecter_ordre(rgb565, largeur, hauteur, res, max, false);
}

#endif
