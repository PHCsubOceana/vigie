idf_component_register(
    SRCS "main.c" "ui.c" "camera.c" "visage.cpp" "son.c"
         "fonts/vigie_phrase_40.c" "fonts/vigie_titre_28.c" "fonts/vigie_petit_24.c" "fonts/vigie_chiffres_96.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash esp_timer esp_driver_ppa esp_driver_i2c esp_mm
)
