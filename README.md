# StarCore RP Mod Installer (PZ B42.13.1 MP)

Debian VPS instalátor Workshop modů:
- načte seznam ze souboru (Steam workshop URL nebo čisté Workshop ID)
- stáhne/aktualizuje přes steamcmd
- vytáhne správné `ModID` z `mod.info`
- zapíše `WorkshopItems=` a `Mods=` do INI (s backupem)
- zkontroluje "aktuálnost" modů (time_updated) přes Steam Web API (bez klíče)
- volitelně restartuje server přes pzmanager skripty

## Instalace na VPS
Nahraj složku do: `/opt/starcore_rp_installer`

## Rychlé použití
```bash
cd /opt/starcore_rp_installer
chmod +x sc_rp_mod_installer.sh
./sc_rp_mod_installer.sh verify mods.list
./sc_rp_mod_installer.sh freshness mods.list
./sc_rp_mod_installer.sh apply mods.list --restart
```

## Formát `mods.list`
1 řádek = URL nebo ID:
- https://steamcommunity.com/sharedfiles/filedetails/?id=3637076228
- 3637076228

Komentáře začínají `#`.


## Poznámka
SteamCMD download může trvat (mapy jsou velké). Nová verze instalátoru streamuje výstup SteamCMD live, takže uvidíš progress a nebude to vypadat, že to "zamrzlo".
# starcore_rp_installer
