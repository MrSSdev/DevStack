# Lokalne powiadomienia Windows (WinRT)

**Docelowy transport DevStack: lokalny WinRT, bez Web Push/WNS i bez Internetu.** Nie trzeba instalować python3-pywebpush, tworzyć subskrypcji przeglądarki, konfigurować VAPID ani zmieniać polityki Edge. PWA jest opcjonalna. Dawny transport przeglądarkowy jest zachowany w kodzie tylko dla zgodności i świadomego wycofania; znacznik lokalnego transportu go omija. Nie jest częścią docelowej konfiguracji.

DevStack używa istniejącego kolektora i jednej rezerwacji w SQLite.
Mały program GUI uruchamia Windows PowerShell 5.1 z CreateNoWindow,
wywołuje WinRT i kończy działanie. Nie wymaga internetu, zewnętrznego
modułu, nowej usługi, zadania harmonogramu ani stale działającego procesu.
Kliknięcie nowego dymka preferuje protokół zarejestrowanej PWA
web+devstack; bez wykrytej rejestracji pozostaje lokalny adres HTTPS.
Wybór jest automatyczny, bez dodatkowego przycisku wyboru przeglądarki.
Uwierzytelniony poll oznacza dokładnie wskazane powiadomienie jako przeczytane.
Toast w widocznym panelu pozostaje nieprzeczytany do zamknięcia lub
wygaśnięcia; wtedy odczytywany jest tylko jego identyfikator, bez usuwania
historii. Bezpośrednie operacje używają ID zwróconego przez API, a nie
lokalnego identyfikatora renderera. Toast wygasający w ukrytej karcie,
zakończenie życia dokumentu i toast ładowania nie oznaczają odczytu.
Testy obejmują rzeczywistą pocztę, ręczne zamknięcie i opróżnienie skrzynki.

## Aktywacja PWA

ASUS: 26.11.2026 wysyłka offline o 17:21:52, pierwsza kolekcja
o 17:22:00, WinRT accepted o 17:22:02 (journal_age_s=0, send_ms=1768).
Edge nie przechwytywał nawet https://devstack.test/ mimo włączonej
obsługi linków i skojarzeń HTTP/HTTPS MSEdgeHTM. Wersja 02db79e
(7/7 workflow) dodała web+devstack i bezpieczne mapowanie fragmentu.
Aktualizacja Ubuntu udana. Odświeżenie istniejącej PWA nie
zarejestrowało protokołu. Po ponownej instalacji PWA na ASUS-ie
Win+R -> web+devstack://open/mail uruchomiło PWA. Powiadomienie
SMTP offline nadal działało po reinstalacji; stary adres HTTPS
nadal otwierał przeglądarkę.

Nowy dymek wybiera protokół PWA przy wykryciu skojarzenia Windows
(UserChoice, bezpośrednia klasa lub per-user RegisteredApplications
z istniejącym shell/open/command lub pakiet AppX z rejestracją
protokołu w AppModel Repository, zgodnym PackageId, ContractId
Windows.Protocol i DelegateExecute). ASUS po aktualizacji d642089:
Render nadal generował HTTPS. Diagnostyka potwierdziła pakiet Edge PWA,
AppX ProgID bez domyślnej komendy i brak UserChoice. Nowy test odtwarza
ten układ bez identyfikatorów konkretnej instalacji. Jest to odczyt rejestru, nie
uruchomienie komendy ani modyfikacja skojarzeń. Brak rejestracji
lub błąd odczytu daje HTTPS. Oba warianty zachowują dokładnie tę samą
trasę i ID, bez dodatkowego przycisku otwarcia w przeglądarce.
Wybór następuje przy tworzeniu dymka: odinstalowanie PWA po jego
wyświetleniu nie zmieni zapisanego adresu i stary dymek może nie
otworzyć aplikacji. Następne dymki wybiorą HTTPS. Nie dodajemy
usługi ani procesu w tle.

Manifest mapuje web+devstack do /#/protocol?uri=%s; frontend sprowadza
ten fragment do sprawdzonej lokalnej trasy i ID. Odczyt nadal wymaga
uwierzytelnionego poll z CSRF. Testy obejmują obie ścieżki XML,
brak i usunięcie skojarzenia, ciepły i zimny start przez URL manifestu.
CI nie zastępuje próby kliknięcia rzeczywistego dymka Windows.
Jeżeli PWA wymaga ponownej instalacji, nie kasuj danych witryny.
Microsoft Edge może poprosić o jednorazową zgodę.

[Dokumentacja Microsoft](https://learn.microsoft.com/en-us/microsoft-edge/progressive-web-apps/how-to/handle-protocols).

## Rzeczywiste zakończenie sterowania usługą

Potwierdzenie przyjęcia polecenia pozostaje informacją, a nie sukcesem.
Dotychczasowy opóźniony systemd-run wykonuje wyłącznie dozwolone polecenie
systemctl. ExecStopPost jednorazowo zapisuje potwierdzony wynik w chronionej
bazie /var/lib/devstack/service-results.sqlite3. Sukces wymaga zarówno
poprawnego kodu zakończenia systemctl, jak i odpowiedniego ActiveState
(active dla start/restart, inactive dla stop). Nie jest to test
zdrowia aplikacji ani zgadywanie na podstawie samego upływu czasu.
Po błędzie lub braku potwierdzenia stanu pojawia się komunikat błędu.
Kolektor importuje wynik jako osobny wpis i toast lub powiadomienie
Windows. Stabilne ID i osobny znacznik w dzienniku zapobiegają
duplikatom i przywracaniu usuniętych wpisów. Baza root jest ograniczona
do 200 wyników z 30 dni; web nie ma do niej bezpośredniego dostępu.
Nie zmienia się katalog dozwolonych usług ani sposób uruchamiania WSL.
Awaryjne zakończenie całego WSL przed zapisaniem wyniku może pozostawić
tylko potwierdzenie przyjęcia; nie zgłaszamy wtedy niepotwierdzonego sukcesu.
Obsługa dotyczy poleceń z panelu DevStack, nie zewnętrznego systemctl
ani odrębnego mostka widżetu Windows.

CI sprawdza rzeczywisty restart (zmiana PID), start, stop, błąd,
blokadę współbieżności, import i trwałość odczytu historii.

## Układ katalogów i bezpieczeństwo

    C:\DevStack\
        LocalNotifications\
            DevStack.Notifications.exe
            show-local-notification.ps1

Instalator tworzy tylko potrzebne katalogi. Tymczasowa kompilacja i kopia
poprzedniego komponentu są usuwane po udanej wymianie. Nie zastępuje
nieznanych plików i odrzuca dowiązania/reparse points, nieoczekiwanego
właściciela lub uprawnienia zapisu dla niezaufanych kont. Katalogi są
dostępne do zapisu dla instalującego użytkownika, SYSTEM i administratorów.
Nie wolno umieszczać tutaj skryptów wykonywanych z podwyższonymi
uprawnieniami. Chronione zadanie hosts pozostaje w ProgramData; MSIX oraz
ustawienia widżetów pozostają w ich dotychczasowych lokalizacjach.
Instalator nie zmienia systemowej polityki uruchamiania skryptów ani
ustawień powiadomień użytkownika. Windows nadal decyduje, czy pokaże dymek.

Przy pierwszym Show nowa tożsamość aplikacji może jeszcze nie mieć
ustawień (ToastNotifier.Setting zwraca 0x80070490, ERROR_NOT_FOUND).
Ten konkretny błąd ujawniono na ASUS-ie. Reflection odczytuje rzeczywisty
HRESULT zamiast pustej wartości; tylko przy ERROR_NOT_FOUND dopuszcza
normalne wywołanie Show, które nadal respektuje politykę systemu.
Wszelkie inne błędy lub jawnie wyłączone powiadomienia pozostają błędem.
Nie ma sztucznego dymka podczas instalacji.

Kolektor zleca wysyłkę istniejącemu pomocnikowi devstackd. Pomocnik
uruchamia ograniczoną jednorazową jednostkę systemd z UID/GID dewelopera;
PID 1 ustawia tożsamość przed ograniczeniami. Bezpośrednia zmiana UID/GID
w dziecku zabezpieczonej usługi została odrzucona przez jądro na WSL
(EPERM), dlatego nie wolno wracać do subprocess.run(user=..., group=...).
Jednostka ma limit czasu, nie jest utrzymywana w spoczynku i nie zmienia
zabezpieczeń devstackd. Jednostka Type=oneshot czeka na zakończenie
programu. RemainAfterExit zachowuje jedynie stan zakończenia (nie proces),
a pomocnik odczytuje Result, ExecMainCode, ExecMainStatus i stan jednostki
bezpośrednio od systemd. Następnie zatrzymuje i czyści tę jednostkę, także
po błędzie lub timeout; PartOf wiąże jej życie z devstackd. CI ujawnił
niejednoznaczne wyniki systemd-run z --wait/--pipe/--collect, dlatego
nie używamy tych opcji ani tekstowych potwierdzeń ze stdout. Limit 10 s
dotyczy uruchomienia i zakończenia launchera. Systemd uruchamia usługę
z katalogu `/`, a `/usr/bin/env --chdir` wchodzi do katalogu launchera
już z uprawnieniami dewelopera. Nie dodaje to twardej zależności
WorkingDirectory od opcjonalnego montowania `/mnt` (obrazy Azure CI
mogą mieć wpis dla nieistniejącego dysku). Nie montujemy niczego na siłę;
brak katalogu lub pliku nadal powoduje błąd. CI porównuje oba warianty
na aktualnym runnerze i loguje stan `/mnt.mount`. Nie zapisujemy treści
powiadomień ani surowego wyjścia programu. CI sprawdza rzeczywisty RPC,
UID/GID, sukces, błąd, timeout oraz sprzątanie; Windows i WSL Interop
wymagają odbioru na urządzeniu.

## Zintegrowana aktualizacja testowa

Użyj działającej dystrybucji Ubuntu, izolowanych danych testowych,
zalogowanego użytkownika Windows i zwykłego terminala Ubuntu (bez sudo
dla całego aktualizatora). Przed pierwszą próbą zapisz stan źródeł
i punkt powrotu. Nie usuwaj wcześniej istniejących danych.

    cd ~/projects/devstack-source
    git status --short
    git branch --show-current
    git log -1 --oneline

Jeśli checkout jest czysty, zapisz dokładną kopię poprzedniego commita:

    git branch backup/before-local-notifications HEAD
    git fetch origin refs/heads/test/windows-local-notifications:refs/remotes/origin/test/windows-local-notifications
    git switch --no-track -c test/windows-local-notifications origin/test/windows-local-notifications
    git rev-parse HEAD

Jeżeli gałąź lokalna już istnieje, przełącz się na nią i wykonaj
git merge --ff-only origin/test/windows-local-notifications. Porównaj SHA
z zielonym przebiegiem CI przed instalacją. Aktualizator testowej gałęzi
nie pobiera sam kolejnych commitów. Nie wymagamy skonfigurowanego
upstreamu (checkout może śledzić tylko main). Wygenerowane kopie PWA
w backend/public są ignorowane przez Git; źródła są w frontend/public.

    bash scripts/update.sh --windows-local-notifications

Flaga jest potrzebna tylko przy pierwszym włączeniu. Następne zwykłe
aktualizacje (bash scripts/update.sh) odczytują chroniony znacznik przez
sudo i aktualizują już włączony launcher, również gdy jego pliki wymagają
naprawy. Bez znacznika i bez flagi nie instalują ani nie włączają tego
transportu. Błąd odczytu, brak uprawnień lub niebezpieczny znacznik
przerywa aktualizację przed wdrożeniem. Gałąź testowa nadal wymaga
istniejącego opt-in albo jawnej flagi. Zintegrowana aktualizacja:

1. Uruchamia dotychczasowe testy i buduje frontend bez roota.
2. Zatrzymuje timer powiadomień na czas wymiany launchera i pozwala
   zakończyć się aktywnemu jednorazowemu kolektorowi.
3. Przez WSL Interop uruchamia instalator w istniejącym koncie Windows;
   kompiluje mały program lokalnie przy użyciu .NET Framework Windows.
4. Wdraża część Ubuntu istniejącym aktualizatorem z kopią poprzedniego
   kodu i kontrolą zdrowia usług.
5. Dopiero po udanym wdrożeniu atomowo zapisuje chroniony przez root
   znacznik /etc/devstack/windows-local-notifications z nową ścieżką.
6. Usuwa wyłącznie stary komponent LocalNotifications z AppData,
   zachowując ustawienia użytkownika, tożsamość powiadomień i widżety.

Przy ponownym włączeniu istniejący znacznik zachowuje czas opt-in,
aby nie wysyłać starego backlogu ani nie odrzucać nowych zdarzeń przy
zwykłej aktualizacji. Jedno zdarzenie używa jednej ścieżki dostarczenia.
Istniejąca subskrypcja WNS może pozostać zapisana wyłącznie dla zgodności
ze starszą wersją. Aktywny WinRT jej nie używa. Nie tworzyć nowej subskrypcji
ani nie włączać Web Push. Jej sprzątnięcie i uporządkowanie ustawień to
odrębna zmiana, a nie warunek działania WinRT.
Nie zmienia się interwału poll (5 s) ani kolektora (8 s).

Jeśli normalne konto Windows nie może utworzyć C:\DevStack, aktualizator
zakończy się błędem przed wdrożeniem Ubuntu. W takim przypadku, i tylko
pod tym samym kontem Windows, uruchom zaufany skrypt z przejrzanego
checkoutu w podwyższonym Windows PowerShell 5.1:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode PrepareDirectory

Tryb ten przygotowuje katalog nadrzędny (jeśli jeszcze nie istnieje)
i prywatny katalog LocalNotifications. Nie uruchamiaj całej aktualizacji
w podwyższonej sesji. Ponów aktualizację z normalnego Ubuntu.

Aktualizator nie zmienia ACL istniejącego katalogu komponentu ani nie
tworzy katalogów staging obok niego. Buduje nowe pliki w tymczasowym
podkatalogu LocalNotifications, przenosi poprzednie dwa pliki do
podkatalogu kopii, instaluje nowe i przy błędzie przywraca poprzednie.
Po sukcesie usuwa własne katalogi tymczasowe. Katalog nadrzędny może
być tylko do odczytu dla zwykłego użytkownika, o ile administrator
wcześniej przygotował prywatny katalog komponentu.
Wymiana dwóch plików nie jest atomowa jako para; zintegrowany updater
zatrzymuje timer i czeka na zakończenie kolektora. Przy ręcznej
instalacji również najpierw zatrzymaj i opróżnij kolektor zgodnie z
poniższą procedurą. W razie przerwania lub niepełnego rollbacku zachowaj
podkatalog kopii i sprawdź jego zawartość; instalator odmawia usunięcia
nieznanych plików. Nadal nie wolno uruchamiać uprzywilejowanego kodu
hosts pod katalogiem należącym do zwykłego użytkownika.
Jeśli katalog już istnieje, ale ma niebezpieczne ACL, instalator celowo
odmawia zmiany uprawnień w ciemno.

Jeśli instalacja Windows się nie powiedzie, Ubuntu nie jest wdrażane.
Jeśli aktualizator Linux nie przejdzie kontroli, sam przywróci poprzedni
kod zgodnie z dotychczasową procedurą; pliki Windows mogą pozostać
zainstalowane, lecz znacznik transportu nie zostanie przełączony.
Jeśli zapis znacznika lub czyszczenie AppData zawiedzie po udanym
wdrożeniu Linux, aktualizator zgłosi błąd z opisem częściowego stanu.
W takiej sytuacji sprawdź komunikat i ponów właściwy etap albo wycofaj
test. Nie deklarujemy transakcji obejmującej dwa systemy operacyjne.

## Samodzielne demo i gotowa paczka (opcjonalne)

Zintegrowana aktualizacja nie wymaga osobnego pobierania ZIP.
Można jednak pobrać paczkę z workflow Windows local notification launcher
dla dokładnie tego samego SHA, zweryfikować COMMIT.txt i SHA256SUMS.txt
oraz zainstalować sam komponent w zwykłym Windows PowerShell 5.1:

    Get-Content .\COMMIT.txt
    foreach ($line in Get-Content .\SHA256SUMS.txt) {
        $expected, $path = $line -split '  ', 2
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) {
            throw "Nieprawidłowa suma pliku $path"
        }
    }
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -UseBuiltLauncher

Demo i Uninstall działają z checkoutu lub paczki. Demo nie włącza
transportu Ubuntu. Samodzielne włączenie po wdrożeniu właściwego Linux:

    sudo bash scripts/enable-windows-local-notifications.sh

## Odbiór na rzeczywistym urządzeniu

1. Po zintegrowanej aktualizacji zweryfikuj ścieżkę znacznika i stan usług:

       git log -1 --oneline
       sudo cat /etc/devstack/windows-local-notifications
       systemctl is-active devstackd devstack-notifications.timer
       sudo journalctl -u devstack-notifications.service -f

2. Odłącz internet, zachowując lokalny dostęp do DevStacka i Mailpit.
   W normalnym Windows PowerShell, w katalogu checkoutu, uruchom demo:

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode Demo

   Obserwuj pierwszy dymek, nazwę DevStack i brak mignięcia konsoli.
   Kliknij po zakończeniu launchera. Nie zmieniaj ustawień Windows
   ani Edge w trakcie pomiaru.

3. Wyślij fikcyjny e-mail przez lokalny SMTP przy otwartym panelu, a potem
   drugi po zamknięciu ostatniego okna. Oczekuj odpowiednio jednego toastu
   w panelu i jednego dymka Windows. Zapisz czas od przyjęcia SMTP do dymka.
   Log outcome=accepted oznacza powodzenie wywołania WinRT, nie dowodzi
   sam w sobie wyświetlenia. Sprawdź kliknięcie konkretnego maila oraz
   odczyt wyłącznie tego wpisu w historii.

4. Powtórz z dwiema kartami, zamknięciem jednej i zminimalizowanym oknem.
   Sprawdź również brak stale działającego launchera, restart Windows,
   świadome uruchomienie Ubuntu, wysyłkę offline i brak automatycznego
   uruchamiania WSL. CI nie zastępuje tych prób. Uśpiony lub zamrożony
   dokument może utracić dzierżawę, dlatego nie gwarantujemy, że sam
   fakt posiadania otwartego okna zawsze zatrzyma dymek systemowy.

Jeżeli demo i bezpośrednie wywołanie z Ubuntu działają, ale kolektor
zwraca failed, sprawdź Interop procesu systemd i sesję zalogowanego
użytkownika. Nie uruchamiaj notyfikatora z uprawnieniami roota ani
nie osłabiaj zabezpieczeń jednostek systemd.

## Wycofanie lokalnego komponentu (tylko świadomy rollback)

Poniższa procedura służy odtworzeniu poprzedniej wersji, nie konfiguracji
docelowej. Web Push/WNS nie jest zalecanym transportem. Jeżeli zachowano
starą subskrypcję WNS, usunięcie znacznika może dopuścić jej ponowne użycie.
Nie stosuj tej procedury jako globalnego wyłącznika powiadomień. Jeżeli
celem jest brak dostarczania systemowego, wyłącz kategorie albo
powiadomienia aplikacji w ustawieniach Windows.

1. Wyłącz lokalny transport w Ubuntu:

       sudo rm -f -- /etc/devstack/windows-local-notifications

2. W normalnym Windows PowerShell w katalogu aktualnego checkoutu
   zaczekaj na ewentualny proces i odinstaluj komponent:

       Get-Process -Name DevStack.Notifications -ErrorAction SilentlyContinue | Wait-Process -Timeout 10
       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode Uninstall

   Deinstalator usuwa tylko własne pliki, tożsamość HKCU i ustawienia
   powiadomień. Pozostałe katalogi w C:\DevStack oraz widżety są zachowane.
   Jeśli czyszczenie historii Windows zwróci ostrzeżenie, usuń testowy
   dymek ręcznie w Centrum powiadomień.

3. W Ubuntu przywróć zapisany commit (w razie potrzeby użyj właściwej
   nazwy gałęzi backup) i ponownie zainstaluj jego kod:

       cd ~/projects/devstack-source
       git switch --detach backup/before-local-notifications
       bash scripts/build-frontend.sh
       sudo bash scripts/apply-update.sh
       git rev-parse HEAD
       systemctl is-active devstack-notifications.timer

   Powrót dotyczy kodu i transportu, nie kasuje danych Mailpit, projektów
   ani dziennika. Przy wycofywaniu późniejszej aktualizacji z już włączonym
   transportem lokalnym przywróć pasujący poprzedni komponent Windows
   i jego znacznik zamiast bezwarunkowo usuwać tożsamość aplikacji.
