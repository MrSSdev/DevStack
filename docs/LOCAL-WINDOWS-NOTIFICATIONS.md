# Test lokalnych powiadomień Windows na ASUS-ie

DevStack używa istniejącego kolektora i jednej rezerwacji w SQLite.
Mały program GUI uruchamia Windows PowerShell 5.1 z CreateNoWindow,
wywołuje WinRT i kończy działanie. Nie wymaga internetu, zewnętrznego
modułu, nowej usługi, zadania harmonogramu ani stale działającego procesu.
Kliknięcie dymka otwiera lokalny adres HTTPS w domyślnej przeglądarce.
Uwierzytelniony poll oznacza dokładnie wskazane powiadomienie jako przeczytane.

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

Flaga jest jawnym opt-in. Bez niej standardowa aktualizacja main nie
instaluje ani nie włącza tego transportu. Zintegrowana aktualizacja:

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
Nie usuwa się subskrypcji WNS; pozostaje dostępna do wycofania.
Nie zmienia się interwału poll (5 s) ani kolektora (8 s).

Jeśli normalne konto Windows nie może utworzyć C:\DevStack, aktualizator
zakończy się błędem przed wdrożeniem Ubuntu. W takim przypadku, i tylko
pod tym samym kontem Windows, uruchom zaufany skrypt z przejrzanego
checkoutu w podwyższonym Windows PowerShell 5.1:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode PrepareDirectory

Tryb ten tworzy tylko katalog z odpowiednim ACL. Nie uruchamiaj całej
aktualizacji w podwyższonej sesji. Ponów aktualizację z normalnego Ubuntu.
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

## Pełne wycofanie pierwszego testu (powrót do WNS)

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
