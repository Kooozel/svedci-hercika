# Svědci Herčíka

Přehled naší party ve WoW Forever: levely a XP, spec, role, profese, který dungeon sedí všem
a ready check dungeonových questů (kdo má quest v logu, kdo hotový a komu chybí). Ready check potřebuje API klíče.
Data se berou z oficiálního Blizzard API (záloha: veřejná Armory), stránka se přegeneruje každé ráno v 6:00 (v létě v 7:00) přes GitHub Actions.

## Úpravy

- **Přidat postavu:** do `party.json` připiš konec jejího odkazu na Armory, tedy `jmeno-prijmeni`
  (malými písmeny, diakritika zůstává, třeba `horaes-velký`).
- **Dungeony:** seznam a rozsahy levelů jsou podle
  [Wowhead pro Forever](https://www.wowhead.com/forever/guide/dungeons-overview-locations-details), opravíš je v `dungeons.json`.
  `new` označí nový dungeon z Forever, `alliance_side` dungeon na území Aliance, který se na stránce nezobrazuje.
- **Questy pro ready check:** `quests.json`, ručně sebrané z Wowhead Forever (Wowhead automatické stahování blokuje).
  `series` je pořadí řetězu, `sharable` jestli jde quest sdílet, `req` od kterého levelu. Nový dungeon doplníš sem.

Po pushnutí do `main` se stránka přegeneruje hned.

## Spuštění u sebe

```bash
python3 build.py
```

Stránka vznikne v `site/index.html`. Stačí Python 3, nic dalšího. Bez API klíčů se data vezmou z Armory.
S klíči (ze souboru mimo repozitář):

```bash
(set -a; . ~/.config/bnet/credentials.env; set +a; python3 build.py)
```

## První nastavení na GitHubu

1. Repozitář musí být veřejný (GitHub Pages zdarma).
2. Settings → Pages → Source: **GitHub Actions**.
3. Na [develop.battle.net](https://develop.battle.net/access/clients) založ API klienta a jeho Client ID a Secret
   ulož do Settings → Secrets and variables → Actions jako `BNET_CLIENT_ID` a `BNET_CLIENT_SECRET`.
4. Actions → „Stránka party“ → Run workflow.

GitHub vypne plánované běhy, když se v repozitáři 60 dní nic nestane. Pak stačí workflow znovu zapnout v záložce Actions.

## Zdroje dat

- **Blizzard API:** namespace `profile-forever-{region}`, adresa postavy bez realmu
  (`/profile/wow/character/jmeno-prijmeni`). Pro Forever to Blizzard zatím nedokumentuje, takže se to může změnit.
  Data postavy se aktualizují, když se odhlásí ze hry.
- **Armory (záloha):** když API nefunguje nebo chybí klíče, skript čte data schovaná ve stránce Armory.
  To není oficiální rozhraní a rozbije se, když Blizzard stránku změní.

Když selžou oba zdroje u všech postav, stránka zůstane ve stavu z posledního úspěšného běhu.
