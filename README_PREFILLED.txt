StarCore RP ModInstaller (prefilled) - Build 42.13.1

- mods.list: prefilled Workshop links (maps + QoL + economy)
- config.env: paths already set for your VPS (edit if you move things)

Usage:
  chmod +x sc_rp_mod_installer.sh
  ./sc_rp_mod_installer.sh --verify --apply --restart

If you only want download without touching INI:
  ./sc_rp_mod_installer.sh --verify

Notes:
- Installer patches WorkshopItems= and Mods= in /root/Zomboid/Server/pzserver.ini
- Map order is NOT auto-patched by installer. Use the provided pzserver_RP_GTA_B42.13.1_modpack.ini as reference.
