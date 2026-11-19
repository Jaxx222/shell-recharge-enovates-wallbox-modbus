# SD card image

`seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2`

SECO Yocto image (kirkstone 1.05.00) for SOM-uQ7-MX6-2 / A75 with 512 MB RAM (2x256M), **with SSH server**.
It is only used once to set up root access to the Enovates system (see [../docs/ANLEITUNG.md](../docs/ANLEITUNG.md)).

The image file is **not** in the Git repository (251 MB, GitHub limit 100 MB, SECO license). Download:

    wget https://secostorage.blob.core.windows.net/secosoftware-public/imx6/a75/kirkstone_1-05-00/seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2
    sha256sum -c SHA256SUMS

Flash:

    bzcat seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
