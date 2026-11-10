# Svědci Herčíka

Přehled naší party ve WoW Forever: levely, spec, role, profese a který dungeon sedí všem.
Data se berou z veřejné Armory, stránka se přegeneruje každé ráno v 6:00 (v létě v 7:00) přes GitHub Actions.

## Úpravy

- **Přidat postavu:** do `party.json` připiš konec jejího odkazu na Armory, tedy `jmeno-prijmeni`
  (malými písmeny, diakritika zůstává, třeba `horaes-velký`).
- **Dungeony:** seznam a rozsahy levelů jsou podle
  [Wowhead pro Forever](https://www.wowhead.com/forever/guide/dungeons-overview-locations-details), opravíš je v `dungeons.json`.
  `new` označí nový dungeon z Forever, `alliance_side` dungeon na území Aliance, který se na stránce nezobrazuje.

Po pushnutí do `main` se stránka přegeneruje hned.

## Spuštění u sebe

```bash
python3 build.py
```

Stránka vznikne v `site/index.html`. Stačí Python 3, nic dalšího.

## První nastavení na GitHubu

1. Repozitář musí být veřejný (GitHub Pages zdarma).
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → „Stránka party“ → Run workflow.

GitHub vypne plánované běhy, když se v repozitáři 60 dní nic nestane. Pak stačí workflow znovu zapnout v záložce Actions.

## Omezení

Armory není oficiální API. Když Blizzard změní stránku, skript přestane data najít a stránka zůstane ve stavu
z posledního úspěšného běhu.
