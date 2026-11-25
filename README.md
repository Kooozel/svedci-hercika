# Svědci Herčíka

Přehled naší party ve WoW Forever: příští dungeon, ready check dungeonových questů (co se nasdílí u vchodu
a pro co si kdo musí dojít), postavy s XP a výbavou, profese a novinky od včerejška. Ready check a výbava potřebují API klíče.
Data se berou z oficiálního Blizzard API (záloha: veřejná Armory), stránka se přegeneruje každé ráno kolem 5:20 (v létě 6:20, se zálohou o hodinu později) přes GitHub Actions. GitHub plánované běhy občas zpozdí.

## Úpravy

- **Přidat postavu:** do `players` v `party.json` připiš konec jejího odkazu na Armory, tedy `jmeno-prijmeni`
  (malými písmeny, diakritika zůstává, třeba `horaes-velký`). `size` je počet míst v partě (prázdná se ukážou jako volná).
- **Přezdívka hráče:** `name` u hráče v `players`, ukazuje se nad jménem postavy. Bez ní se vezme křestní jméno hlavní postavy. Hráč s prázdným `chars` se ukáže na volném místě.
- **Alt:** připiš ho hráči do `chars` za hlavní postavu. Hlavní postava (první) drží místo v partě a počítá se z ní plán výpravy,
  tituly, loot a řemesla. Alt má ready check, kartu postavy a novinky, ve skupinové fotce je u hráče jen poznámka „+5 postav“. Přepíná se v kartě postavy a přepnutý alt se ukáže i na místě svého hráče ve fotce (výchozí je naposledy hraná postava). Alt pod levelem 10 API nevrací, na stránce se objeví sám, až ho přeleze. Všude, kde se vybírá postava, jdou postavy po hráčích: hlavní první, alti od nejvyššího levelu (`order_chars`).
- **Role:** odhadují se podle specu. Když někdo hraje jinak (Johnie tankuje s body v Holy), přepiš to v `party.json`
  v `roles`, třeba `"johnie-rotking": "tank"` (tank / heal / dps).
- **Druhý pád jména:** v `genitive` (třeba `"zrza-jegod": "Zrzy"`), aby stránka psala „od Zrzy“.
  Nová postava bez něj dostane první pád.
- **Novinky od včerejška:** jeden řádek na člověka s postavou, která nahrála nejvíc; ostatní aktivní postavy hráče se rozbalí pod „+N další postavy“. U postavy bez level upu je „+35 % levelu“ (kolik levelu nahrála). Nové postavy hráče jsou jedním řádkem („🆕 Nové postavy: Kozel Pivoň (28), Kozel Smradlák (17)“). Porovná levely, XP, profese, výbavu a achievementy s posledním snímkem v `history/`
  před dneškem (ranní stav před včerejším večerem). Večerní push ho neposune. Místo čísel XP je hláška podle toho,
  kolik kdo nahrál vůči svému levelu (drobek, kus, level, víc levelů), nikdy vůči ostatním. U profesí nové
  řemeslo a body skillu jedním řádkem (anglické názvy), recepty jednou větou. Bez historie se vezme `party.json`
  z nasazené stránky (`site_url`).
- **Tituly a počítadla:** ze statistik všech postav hráče dohromady (smrti, jídlo, need rolly, lety…) dostane každý člověk jeden titul za včerejší večer,
  bez žebříčku a stejnou statistiku dostane stejný člověk nejdřív za tři dny (`TITLE_PAUSE`). Kdo nehrál, dostane hezký titul.
  Tituly a jejich varianty jsou v `titles.json` (klidně připiš další), počítadla za partu jsou `COUNTERS` v `build.py`.
  Počítadla jsou sbalená do jednoho řádku (tři součty a pokořené milníky), dlaždice se ukážou na klik.
  Statistika, kterou `titles.json` nezná, dostane obecný titul „Objevitel“. Vlastní klíče: `_lvl`, `_xp`, `_skill`,
  `_explore` (objevená místa z achievementů Explore, `{zone}`).
  Nový člen party dostane první den uvítací titul z `newcomers`.
  Titul za statistiku dostane jen ten, kdo v ní vede (nebo má aspoň 75 % nejlepšího, `LEAD`); kdo hrál,
  ale v ničem nevedl, dostane titul z `busy`. Stejný titul u víc lidí je jedna karta se všemi jmény. Jméno je všude „přezdívka · celé jméno postavy“ („Kozel · Kozel Mourningale, Hora · Horaes Velký“), u hráče s alty postava, která k titulu přispěla nejvíc.
- **Pořadí stránky:** hlavička (Tank, Healer a „mainy 25–31“ se počítají z hlavních postav), lišta, skupinová fotka, Příští výprava, tituly, novinky, ready check, loot, kronika, řemesla.
  Lišta má odkazy ve stejném pořadí a zvýrazní sekci, kterou čteš: tu, jejíž nadpis je v horní třetině pod lištou, jinak tu, která tou třetinou prochází. Po kliknutí na odkaz svítí on, dokud sám nescrolluješ. Posuvné řady (lišta, postavy v ready checku)
  ztmavnou u okraje, za kterým ještě něco je. Na mobilu mají odkazy a štítky aspoň 36 px na výšku.
- **Příští výprava:** dungeon, rozsah levelů a souhrn ready checku. Souhrn má za každého hráče, co jeho postavě chybí („Johnie ✋ 1× dojít“).
  S vybranou postavou začíná „Ty (jméno): …“ a ukazuje jen ty ostatní, kteří ještě něco potřebují; za ostatní hráče mluví o postavě
  z jejich sloupce v ready checku (odhad, nebo co jsi jim přepnul). Když se vybraná postava do dungeonu nevejde, řekne to. Odkaz vede na celý ready check. Pod tím mřížka „Kam dál“: dungeony začínající
  pod 40 (`AHEAD_UNTIL`) × hráči, kolik levelů zbývá, než bude dungeon procházka. Za hráče se v každém řádku bere postava
  s největší rezervou (nejnižší, která se do dungeonu vejde; jinak nejbližší pod rozsahem), tooltip buňky řekne kterou.
  Jen řádek příští výpravy bere stejnou postavu jako ready check, ať se mřížka a tabulka questů pod ní neliší.
  Tvůj sloupec a ručně přepnuté sloupce z ready checku počítají ve všech řádcích se zvolenou postavou. Hranice „do lvl“ je konec
  rozsahu, nebo Wowhead Easy z `difficulty`, co přijde dřív (vysvětluje ji věta pod legendou, na mobilu má sloupec hlavičku „lvl“). Dungeon, který je procházka pro všechny, zmizí,
  nahoře je, co uteče nejdřív. Sloupec vybraného hráče je zvýrazněný. Když za hráče jde jiná postava než hlavní, má buňka tečku v barvě jejího povolání a tooltip ji jmenuje („Kozel · Kozel Smradlák (Mage): …“). Na mobilu je v mřížce jen příští výprava, dva dungeony
  před ní a tři po ní (`AHEAD_BEFORE`, `AHEAD_AFTER`), zbytek je pod „další dungeony“. Odehrané jsou na desktopu i na mobilu sbalené pod řádkem „✓ odehrané (N)“. Vybírá se podle levelů, domluvený dungeon se dá napsat
  do `party.json` jako `"next": "Wailing Caverns"`. Odehraný dungeon dostane v `dungeons.json` `"done": "RRRR-MM-DD"` (nebo `true`, když datum
  nevíme) a zmizí z plánu a lootu. V seznamu všech dungeonů zůstane se štítkem odehráno. Dokud se do něj
  někdo levelem vejde (alt), je v ready checku ve výběru „už odehrané“.
- **Postavy:** klik na postavu ve skupinovce otevře pod ní její kartu a posune stránku k ní. Na mobilu (do 600 px) je
  fotka seznam s hlavou, přezdívkou, jménem, levelem, XP a rolí a karta vyjede zespodu (zavírá ji křížek, Escape
  nebo klik vedle). Nahoře v kartě jsou všechny postavy hráče celým jménem a levelem. Karta: render uprostřed, kolem výbava jako okno
  postavy ve hře (rámeček barvy kvality, 🩴 = nejslabší kus podle item levelu, prázdné sloty čárkovaně), vedle
  hláška, jak je oblečený (průměrný item level proti levelu), co mu chybí („chodí bez čepice“), „Jak na tom je“
  (HP a dva hlavní staty role číslem, `STAT_ROWS` v `build.py`, zbytek je na Armory), stupně řemesel a pet. Kdo se odhlásil s Resurrection
  Sickness, má to na kartě. Odkaz `#char-jmeno-prijmeni` otevře rovnou danou postavu; obnovení stránky
  kartu znovu neotevře a sama od sebe se neotevře žádná.
- **Kdo jsi:** při první návštěvě stránka ukáže jen volbu hráče (za něj stejný odhad jako u ostatních: naposledy hraná postava, která se vejde do příští výpravy) a „jen koukám“.
  Jinou postavu si zvolíš v liště, stránka si ji pamatuje v prohlížeči (`localStorage`, `me`) a lišta, výprava, ready check,
  loot i Dílna berou všude tu samou. Odkaz `#char-…` volbu přeskočí.
- **Ready check:** nahoře jsou čipy tvých postav (jméno v barvě classy a level) pro přepnutí alta. Vybraná postava je vpravo v liště
  („Hraju za“: klik otevře menu tvých postav, stránka nikam neodskočí; „změnit hráče“ otevře znovu úvodní volbu) a podle ní se řídí ready check, loot, Dílna a tvoje místo ve skupinové fotce. Dungeon se předvybere podle levelu vybrané postavy, pod ním je,
  co si má dojít, co jí může někdo nasdílet a co začíná uvnitř. Nasdílet může postava jiného hráče, která se do dungeonu levelem vejde.
  Scarlet Monastery má ve výběru jednu položku za všechna křídla („Scarlet Monastery (4 křídla) · 28–45“), protože questy přecházejí přes křídla; které křídlo, říká poznámka u questu. Kam dál a loot jsou po křídlech. Názvy dungeonů jsou všude celé, zkratky (SM:, bez Excavation Site) jen v Kam dál na mobilu.
  Pod úkoly je tabulka „Celá parta“: řádek = quest, sloupec = hráč s postavou, kterou do dungeonu nejspíš vezme
  (stejná jako ve výpravě, u postavy mimo rozsah kolik chybí). Tvůj sloupec ukazuje tvou vybranou postavu a je zvýrazněný.
  Hráč s víc postavami má u jména ▾: klik nabídne jeho postavy. U tvého sloupce to změní tvou postavu, u ostatních
  jen to, co vidíš ty (`sessionStorage`, do zavření karty, ručně přepnutý má tečku a „zpět na odhad“).
  „Můžeš nasdílet … potřebuje“ jmenuje jen postavy, které jsou zrovna ve sloupcích. Předquest v logu je „⛓ dokončit“
  a na kartě vlastní skupina „Dokonči řetěz“.
  Na mobilu je tabulka bez popisů questů a posouvá se do strany. Volbu si pamatuje prohlížeč.
- **Řemesla:** „Dílna“ nabídne nejvýš tři kusy (`CRAFT_MAX`), které někdo z party umí vyrobit a jinému nebo sobě
  výrazně pomůžou (velký upgrade nebo prázdný slot, class je unese, level stačí), u nich kdo co přinese
  (kůže od skinnera, Copper Rod od kováře…), kdo umí třpytky (enchanty, bez jmen, komu chybí) a kdo je na stropu
  skillu. Řemeslníky, příjemce i dodavatele jmenuje celým „přezdívka · jméno postavy“, ať je jasné, na koho se přelogovat; s vybranou postavou, pro kterou nic není, napíše „Pro postavu Kozel · Kozel Rohatý teď nikdo nic nevyrobí.“ Pod tím je přehled se skillem číslem (strop v tooltipu; na mobilu řádek = hráč a jeho profese). Číslo má barvu povolání postavy, která to umí (celé jméno v tooltipu),
  hlášky k vedlejším profesím skoro na nule a jedna věta o profesích, které nikdo nemá. Detaily receptů a předmětů se ukládají do `cache/static.json`, takže se
  z API stahuje jen to nové (ranní běh cache commitne). Smazáním souboru se stáhne znovu.
  Nové recepty se ukážou v novinkách.
- **Soukromí:** na stránku a do historie jde jen den posledního přihlášení, ne přesný čas.
- **Kronika:** zápisy z výprav v `kronika.json`. Na hlavní stránce je první odstavec posledního zápisu,
  celá kronika je na `kronika.html`. Píše se ručně (s Claudem) po výpravě. Zápis může mít `titles`
  (slug → `emoji`, `title`, `detail`): ruční tituly za ten večer, třeba za špatný zeppelin, které přebijí ty ze statistik.
  Platí jen první den po výpravě.
- **Denní historie:** ranní plánovaný běh commitne `history/RRRR-MM-DD.json` se stavem hlavních postav a altů, kteří se od posledního snímku změnili
  (levely, XP, statistiky, výbava). Z rozdílu dvou dnů se pozná, co se ten večer stalo. Kvůli tomu má workflow
  právo zápisu do repozitáře, takže si před vlastním pushem udělej `git pull`.
- **Dungeony:** seznam a rozsahy levelů jsou podle
  [Wowhead pro Forever](https://www.wowhead.com/forever/guide/dungeons-overview-locations-details), opravíš je v `dungeons.json`.
  `new` označí nový dungeon z Forever, `alliance_side` dungeon na území Aliance, který se na stránce nezobrazuje.
  `difficulty` je stupnice z Wowhead průvodce dungeonu (Hard / Medium / At Level / Easy, tabulka nad questy):
  ready check ji ukáže místo rozpětí jako „na krev · dá se · akorát · procházka“ a zvýrazní stupeň podle
  nejslabšího v partě. Dungeon bez ní (Wowhead ji nemá) ukáže rozpětí.
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

- **Bossové a loot:** `bosses.json`, ručně přepsané (s Claudem) pro dungeony, ke kterým se parta blíží. U bosse je krátká
  hláška (`quip`), taktika a loot. Taktika (`tip`) a poznámka dungeonu jmenují role a classy (tank, healer, mage, paladin…), ne hráče,
  ať sedí, ať jde kdokoli (hlídá to test); jména můžou být jen v hláškách. U kusu jsou jen ti, kdo ho unesou a komu sedí hlavní stat (`stat`: Caster, Spirit, Agility, Strength…),
  s hláškou místo velký / malý upgrade (🎲 = hodí se víc lidem, domluvit se před rollem). Vzácné BoE kusy z trashe
  (`"boe": true`) se nevypisují, jen věta. Na štítku je celé jméno postavy. Za hráče jsou vidět postavy ze sloupců ready checku toho dungeonu
  (tvoje, ručně přepnuté přes ▾, jinak odhad), 🎲 i „nikomu“ se počítají jen z nich; „až na N“ = kus nasadí na levelu N. Co který štítek znamená, ukážou vzorové štítky pod „?“ pod lootem. Item level se u kusu neukazuje. Pod tím odměny z questů. Forever bossy ani většinu jejich lootu v API zatím nemá, takže typ (`inv`, `sub`), `level` a `req` se píšou ručně
  ze zdrojů (Warcraft Wiki, Method). Když API předmět zná (třeba questová odměna), stačí `id`.
  Číselné staty kusů (armor, DPS, Str, spell power…) se berou z Wowhead tooltipu podle `id` a ukládají do
  `cache/static.json` (stahuje se jen nové, klíče k tomu nejsou potřeba). Ručně zapsané `values` u kusu mají přednost,
  `stats` je jen popisek na stránku. Nošená výbava má staty z API. Ruční `stat` určuje, komu se kus ukáže
  (pro koho je myšlený), test hlídá, že neodporuje statům kusu (výjimky v `LABEL_OK` v testech).
- **Upgrady podle statů:** loot i Dílna sečtou staty kusu podle vah (`weights.json`) a porovnají
  s tím, co postava nosí (prsten a trinket se slabším z obou, 2H zbraň s main hand a off hand dohromady).
  Stránka ukáže jen hrubě velký upgrade (aspoň o 20 % a 4 body lepší), malý upgrade (aspoň o 10 % a 1 bod)
  nebo nic moc, a to hláškou z `hlasky.json` („bere všema deseti“, „neurazí“, „ani za pivo“), přesná čísla schválně
  ne (`BIG_UP`, `SMALL_UP` v `build.py`). Kde staty neznáme, rozhoduje item level („asi lepší“, +N ilvl v tooltipu).
  Podklad je v `docs/staty-forever.md`.
- **Hlášky:** `hlasky.json`, každý seznam aspoň tři varianty (hlídá test). Štítky lootu a legenda jsou celý den stejné
  na celé stránce, věty u postav se střídají podle dne a postavy a na jedné stránce se neopakují (ani s tituly).
  Po výpravě se můžou připsat nové, třeba s narážkami na ten večer.
- **Váhy statů:** `weights.json`, hrubé odhady pro classu a roli: hlavní staty (`main`), vedlejší (`minor`), kolik
  bodů je +1 DPS zbraně podle slotu (`weapon`) a převody jednotek (`units`, třeba 2 AP = 1 Str). Profil se hledá
  podle classy, role a specu (`"shaman dps enhancement"`, pak `"shaman dps"`, pak `"shaman"`).

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
