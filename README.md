# Svědci Herčíka

Přehled naší party ve WoW Forever: příští dungeon, ready check dungeonových questů (co se nasdílí u vchodu
a pro co si kdo musí dojít), postavy s XP a výbavou, profese a novinky od včerejška. Ready check a výbava potřebují API klíče.
Data se berou z oficiálního Blizzard API (záloha: veřejná Armory), stránka se přegeneruje každé ráno kolem 5:20 (v létě 6:20, se zálohou o hodinu později) přes GitHub Actions. GitHub plánované běhy občas zpozdí.

## Úpravy

- **Přidat postavu:** do `party.json` připiš konec jejího odkazu na Armory, tedy `jmeno-prijmeni`
  (malými písmeny, diakritika zůstává, třeba `horaes-velký`). `size` je počet míst v partě (prázdná se ukážou jako volná).
- **Role:** odhadují se podle specu. Když někdo hraje jinak (Johnie tankuje s body v Holy), přepiš to v `party.json`
  v `roles`, třeba `"johnie-rotking": "tank"` (tank / heal / dps).
- **Novinky od včerejška:** porovná levely, XP, profese, výbavu a achievementy s posledním snímkem v `history/`
  před dneškem (ranní stav před včerejším večerem). Večerní push ho neposune. Bez historie se vezme `party.json`
  z nasazené stránky (`site_url`).
- **Tituly a počítadla:** ze statistik postav (smrti, jídlo, need rolly, lety…) dostane každý titul za včerejší večer,
  bez žebříčku a bez opakování stejné statistiky dva dny po sobě. Kdo nehrál, dostane hezký titul.
  Tituly a jejich varianty jsou v `titles.json` (klidně připiš další), počítadla za partu jsou `COUNTERS` v `build.py`.
  Statistika, kterou `titles.json` nezná, dostane obecný titul „Objevitel“. Vlastní klíče: `_lvl`, `_xp`, `_skill`,
  `_explore` (objevená místa z achievementů Explore, `{zone}`).
  Nový člen party dostane první den uvítací titul z `newcomers`.
  Titul za statistiku dostane jen ten, kdo v ní vede (nebo má aspoň 75 % nejlepšího, `LEAD`); kdo hrál,
  ale v ničem nevedl, dostane titul z `busy`.
- **Příští výprava:** dungeon jako pásmo na stupnici levelů, postavy jako tečky (vybledlé = mimo rozsah),
  pod tím další dva dungeony a souhrn ready checku.
- **Postavy:** klik na postavu ve skupinovce otevře pod ní její kartu: render uprostřed, kolem výbava jako okno
  postavy ve hře (rámeček barvy kvality, ▼ nejslabší kus, prázdné sloty čárkovaně), vedle staty z API
  (Health, atributy, obrana a útok/kouzla/léčení podle role), profese a pet. Kdo se odhlásil s Resurrection
  Sickness, má to na kartě. Odkaz `#char-jmeno-prijmeni` otevře rovnou danou postavu, jinak se otevře ta
  z „Já jsem“ v ready checku.
- **Ready check:** „Já jsem“ schová úkoly ostatních, pamatuje si to prohlížeč.
- **Profese:** „Co vyrobit pro partu“ ukáže jen upgrady, které někdo z party umí vyrobit (prázdný slot nebo vyšší
  item level, class je unese, level stačí; nejlepší kus na slot), u nich suroviny, které musí dodat někdo jiný
  (kůže od skinnera, Copper Rod od kováře…), kusy bez enchantu, které umí enchanter z party, a profese na stropu
  skillu. Pod tím je přehled profesí. Detaily receptů a předmětů se ukládají do `cache/static.json`, takže se
  z API stahuje jen to nové (ranní běh cache commitne). Smazáním souboru se stáhne znovu.
  Nové recepty se ukážou v novinkách.
- **Soukromí:** na stránku a do historie jde jen den posledního přihlášení, ne přesný čas.
- **Kronika:** zápisy z výprav v `kronika.json`. Na hlavní stránce je první odstavec posledního zápisu,
  celá kronika je na `kronika.html`. Píše se ručně (s Claudem) po výpravě.
- **Denní historie:** ranní plánovaný běh commitne `history/RRRR-MM-DD.json` se stavem všech postav
  (levely, XP, statistiky, výbava). Z rozdílu dvou dnů se pozná, co se ten večer stalo. Kvůli tomu má workflow
  právo zápisu do repozitáře, takže si před vlastním pushem udělej `git pull`.
- **Dungeony:** seznam a rozsahy levelů jsou podle
  [Wowhead pro Forever](https://www.wowhead.com/forever/guide/dungeons-overview-locations-details), opravíš je v `dungeons.json`.
  `new` označí nový dungeon z Forever, `alliance_side` dungeon na území Aliance, který se na stránce nezobrazuje.
- **Questy pro ready check:** `quests.json`, ručně přepsané (s Claudem), přidávají se, až se parta k dungeonu blíží.
  Dungeon, který v souboru není, ready check nemá. Zdroje mají pevné pořadí:
  1. **Wowhead** je hlavní zdroj: které questy dungeon má, ID, od jakého levelu, sdílení, start a předquesty.
     Nejrychleji ze souhrnu [Every Dungeon Quest in WoW Forever](https://www.wowhead.com/forever/guide/dungeons/every-dungeon-quest-location),
     detail z průvodce dungeonu (`source`). Stránky se vykreslují JavaScriptem, takže je čte jen prohlížeč, ne obyčejné stažení.
  2. **[Warcraft Tavern](https://www.warcrafttavern.com/forever/guides/)** (`tavern`) jen na poznámky: kde co leží,
     který boss co dropne. Jeho ID nepřebírat, u BFD a Gnomereganu linkuje verze ze Season of Discovery.
  3. **Blizzard API** rozhoduje, když se zdroje rozcházejí: ID musí existovat a mít typ Dungeon (ne Raid),
     sedí frakce, classa a rasa, XP.
  U questu: `req` od levelu, `sharable`, `start` ("dungeon" = začíná až uvnitř, předmětem nebo od postavy),
  `pre` předquesty mimo dungeon (poslední z `ids` musí být hotový), `note`.
  Omezení na classu nebo rasu se bere z API (`requirements`), jde ho i dopsat do questu jako `classes` / `races`.
  Classový quest vidí jen ta classa (štítek v její barvě, ostatní mají —), když ho v partě nikdo nemůže vzít,
  neukáže se vůbec. V ready checku vede u každého dungeonu odkaz na Wowhead i Warcraft Tavern.

Po pushnutí do `main` se stránka přegeneruje hned.

## Spuštění u sebe

```bash
python3 build.py
```

Stránka vznikne v `site/index.html`. Stačí Python 3, nic dalšího. Testy (bez sítě a klíčů, běží i v Actions):

```bash
python3 -m unittest discover -s tests
```
 Bez API klíčů se data vezmou z Armory.
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
