Matches birth, death and marriage records from
[Geneteka](https://geneteka.genealodzy.pl) and creates an HTML output with
links between families.

## Po polsku

Program automatycznie łączy akty małżeństwa, urodzin i zgonów
z [Geneteki](https://geneteka.genealodzy.pl) i produkuje zbiór
stron HTML z odnośnikami pomiędzy rodzinami.

Przykładowy wynik działania programu: http://przodkowie.ml/

### Przykład użycia programów

1. Ściągnięcie danych z Geneteki (wszystkie filtry z GUI).

Sposób najprostszy: skopiuj w przeglądarce adres URL wyszukiwania
w Genetece i podaj go przez `--url` — wszystkie filtry zostaną
przejęte z adresu:

```
python fetch.py --url "https://geneteka.genealodzy.pl/index.php?op=gt&lang=pol&bdm=B&w=07mz&rid=944&search_lastname=Korzeniowski&from_date=1800&to_date=1900&exac=1&parents=1"
```

Można też podać te same filtry co w GUI jako opcje programu
(pełna lista: `python fetch.py --help`):

```
python fetch.py -w 07mz -t B -r 944 --lastname Korzeniowski --from-date 1800 --to-date 1900 --exac
```

Obsługiwane filtry (te same, które GUI wysyła do `api/getAct.php`):
`w` (województwo), `bdm` (B/S/D/A), `rid` (parafia lub B/S/D/A — wszystkie),
`lang`, `search_lastname`, `search_name`, `search_lastname2`,
`search_name2`, `from_date`, `to_date`, `exac` (dokładne dopasowanie),
`pair` (para: małżonkowie lub dziecko+matka), `parents` (szukaj też po
rodzicach), `near` (także w pobliskich parafiach).

Stara składnia nadal działa:

```
python fetch.py 07mz B 944
python fetch.py 07mz S 857
python fetch.py 07mz D 1745
```

Surowe odpowiedzi API są zapisywane bez zmian, więc zawierają **wszystkie**
kolumny tabeli, w tym kolumnę ze wskazówkami ikon [i] (uwagi), informacją
o archiwum (z.png) i odnośnikiem do skanu (s.png → metryki.genealodzy.pl).

Jeśli użyto filtrów wyszukiwania, nazwa pliku zawiera dodatkowy
8-znakowy znacznik zapytania, więc wyniki różnych kwerend nie mieszają się.

### Pobieranie całej parafii (dwuprzebiegowe)

API `getAct.php` zwraca kompletne, poprawnie stronicowane strony z rodzicami
**tylko wtedy, gdy aktywny jest filtr imienia lub nazwiska**. Bez filtra
serwer ucina strony do kilku wierszy, ignoruje paginację i pomija kolumny
rodziców. Dlatego kwerenda całej parafii (bez `search_lastname`/
`search_name`) działa dwuprzebiegowo:

1. **Przebieg 1** — lista rekordów parafii (niezapisywana do `data_raw`,
   bo odpowiedzi bez filtra mogą mieszać księgi i są ucięte);
2. **Przebieg 2** — pobranie per nazwisko z `search_lastname` (kompletne
   strony, rodzice uwzględnieni), zapis 1:1 do `data_raw`.

`search_lastname` pasuje do nazwiska pana **lub** pani młodej, więc jedno
zapytanie pokrywa każdy rekord zawierający dane nazwisko. Przebieg 2
porządkuje nazwiska od najczęstszych i pomija te, których rekordy są już
pobrane — dla Zręcina S: 416 unikalnych nazwisk → 116 kwerend (72% mniej
zapytań).

Ten sam rekord może więc trafić do kilku plików `data_raw`; `merge.py`
deduplikuje wiersze po kluczu rekordu, zachowując wersję z największą
liczbą wypełnionych kolumn (tę z rodzicami).

Znane ograniczenia API (obsługiwane automatycznie):
- sporadycznie zwracana jest pusta strona mimo `recordsTotal > 0` — strona
  jest wtedy ponawiana;
- po serii szybkich zapytań serwer chwilowo throttluje (puste odpowiedzi
  200) — fetch.py czeka i ponawia zapytanie;
- czasem API ignoruje `rid`/filtr i zwraca wyniki dla **całego
  województwa** (zawyżony `recordsTotal`). Przebieg 2 porównuje totał
  każdej kwerendy nazwiska z totałem kwerendy bez filtra — wynik większy
  niż totał bez filtra to matematycznie niemożliwy glitch; nazwisko jest
  wtedy ponawiane, a jeśli glitch się utrzymuje, pomijane z ostrzeżeniem
  (ponowne uruchomienie fetch.py je dorobi);
- dla małych parafii pewność kompletności daje też pobranie wąskich
  zakresów rocznych (`from_date`/`to_date`).

2. Wstępnie przetworzenie danych

```
python merge.py
```

merge.py parsuje wszystkie kolumny każdego wiersza:
- uwagi z ikon [i] w dowolnej kolumnie (`comments`),
- informację o archiwum i link do archiwum (`archives`, `archives_url`),
- odnośnik do skanu, jeśli jest dostępny (`scan_url`),
- użytkownika, który zaindeksował akt (`user_entered`),
- deduplikację wierszy (ten sam akt z różnych kwerend → jeden rekord,
  preferowana wersja z rodzicami).

3. Wygenerowanie plików HTML

```
python generate.py
```

## Klucze danych w merge.py (kolumna "Uwagi")

Rekordy w `data/*.json` zawierają następujące pola wyciągane z ikon w kolumnie "Uwagi":

| Klucz | Źródło | Obecność |
|---|---|---|
| `comments` | `title` ikony `i.png` (wszystkie kolumny, podział po \r, deduplikacja) | gdy są uwagi |
| `archives` | `title` ikony `z.png` | gdy podano miejsce przechowywania |
| `archives_url` | `href` linku otaczającego `z.png` | gdy archiwum ma stronę WWW |
| `scan_url` | `href` linku otaczającego `s.png` | gdy dostępny jest skan |
| `user_entered` | parametr `uname=` w linku przy `a.png` | gdy podano indeksującego |

Klucze `archives_url` i `scan_url` są **całkowicie pomijane**, gdy dany rekord nie ma
odpowiedniego linku (np. archiwum parafialne bez strony WWW) — zamiast fałszywych wartości.
