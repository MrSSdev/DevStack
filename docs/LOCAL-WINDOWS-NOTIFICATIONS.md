# Test lokalnych powiadomień Windows na ASUS-ie

Kolektor DevStacka wysyła zdarzenie do małego programu Windows zbudowanego
w trybie GUI. Program uruchamia Windows PowerShell 5.1 z ustawieniem
CreateNoWindow, przekazuje dane do WinRT i kończy działanie. Oba procesy
powstają tylko podczas wysyłki. Rozwiązanie korzysta z .NET Framework
wbudowanego w Windows 11 i z istniejącej usługi DevStacka w Ubuntu.

Tryb lokalny jest domyślnie wyłączony. Po włączeniu kolektor używa jednej
rezerwacji w SQLite i wysyła zakwalifikowane zdarzenia przez WinRT.
Aktywna karta przejmuje powiadomienia w panelu. Windows otrzymuje tylko
identyfikator zdarzenia, kategorię i poziom ważności. Kliknięcie otwiera
adres DevStacka w domyślnej przeglądarce HTTPS. Zalogowany panel oznacza
wskazany wpis jako przeczytany przy najbliższym uwierzytelnionym pollu.

## Instalacja

Użyj izolowanej instalacji, testowej skrzynki Mailpit i już działającej
dystrybucji Ubuntu. W katalogu źródłowym sprawdź, czy git status nie
pokazuje lokalnych zmian. Zapisz nazwę aktualnej gałęzi i jej commit.
Pomocnicza gałąź zachowuje dokładny commit do wycofania.

    cd ~/projects/devstack-source
    git status --short
    git branch --show-current
    git rev-parse HEAD
    git branch backup/before-windows-local-notifications HEAD
    git fetch origin refs/heads/test/windows-local-notifications:refs/remotes/origin/test/windows-local-notifications
    git switch --track origin/test/windows-local-notifications
    git rev-parse HEAD
    bash scripts/build-frontend.sh
    sudo bash scripts/apply-update.sh

Porównaj commit po przełączeniu z SHA podanym dla testu. Następnie otwórz
katalog tego checkoutu w zwykłym Windows PowerShell 5.1 jako zalogowany
użytkownik Windows. Możesz użyć ścieżki UNC do już działającego Ubuntu,
np. zastępując NAZWA_UZYTKOWNIKA swoją nazwą w poniższym przykładzie.

    Set-Location '\\wsl.localhost\Ubuntu-26.04\home\NAZWA_UZYTKOWNIKA\projects\devstack-source'
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1

Instalator buduje program w profilu użytkownika Windows, kopiuje do niego
skrypt WinRT i rejestruje tożsamość powiadomień DevStack w HKCU. Na końcu
wypisuje pełną ścieżkę do pliku DevStack.Notifications.exe. Zachowaj
ją do włączenia transportu w Ubuntu.

### Gotowy program z GitHub Actions

Workflow Windows local notification launcher tworzy archiwum o nazwie
devstack-windows-local-notifications-SHA. Zawiera sprawdzony program GUI,
źródła, instalator, test i tę instrukcję. Archiwum jest dostępne przez
30 dni od kompilacji. Instalacja części Ubuntu nadal korzysta z checkoutu
gałęzi testowej.

Pobierz paczkę z przebiegu dla tego samego commita, rozpakuj ją i otwórz
jej katalog w Windows PowerShell. Porównaj COMMIT.txt z git rev-parse HEAD
w Ubuntu. Sumy z SHA256SUMS.txt pozwalają zweryfikować rozpakowane pliki.

    Get-Content .\COMMIT.txt
    foreach ($line in Get-Content .\SHA256SUMS.txt) {
        $expected, $path = $line -split '  ', 2
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $expected) {
            throw "Nieprawidłowa suma pliku $path"
        }
    }
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -UseBuiltLauncher

Tryby Demo i Uninstall są dostępne zarówno z paczki, jak i z checkoutu.

## Test

1. Odłącz Internet, zachowując lokalny dostęp do DevStacka i Mailpit.
   W otwartym wcześniej oknie PowerShell uruchom demo. Obserwuj ekran
   od wywołania do dymka, włącznie z pierwszym zimnym uruchomieniem.
   Kryterium testu to dymek DevStack i zero mignięć dodatkowego okna.
   Kliknięcie demonstracyjne otwiera skrzynkę testową.

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode Demo

2. W Ubuntu wstaw ścieżkę z instalatora i włącz transport.

       launcher="$(wslpath -u 'C:\Users\NAZWA\AppData\Local\DevStack\LocalNotifications\DevStack.Notifications.exe')"
       test -f "$launcher" &&
           printf '%s\n' "$launcher" | sudo install -m 0644 -o root -g root /dev/stdin /etc/devstack/windows-local-notifications
       sudo journalctl -u devstack-notifications.service -f

3. Wyślij fikcyjną wiadomość lokalnym SMTP przy otwartym panelu, a potem
   drugą po zamknięciu ostatniego okna DevStacka. W pierwszym przypadku
   oczekuj toastu w panelu, w drugim pojedynczego dymka Windows bez
   mignięcia konsoli. Zapisz czas od przyjęcia drugiego maila do dymka.
4. Kliknij drugi dymek po zakończeniu procesu Windows i sprawdź, czy
   otwiera właściwą wiadomość i oznacza tylko ten wpis jako przeczytany.
   Powtórz próbę z dwiema kartami i zamknięciem jednej z nich. Pozostała
   karta powinna przejąć powiadomienie również po zminimalizowaniu okna,
   jeśli Edge nadal wykonuje poll.
5. Log outcome=accepted oznacza, że wywołanie WinRT zakończyło się
   powodzeniem. Porównaj je z faktycznym dymkiem i zachowaniem ekranu.
   Jeśli bezpośrednie demo działa, a kolektor zwraca outcome=failed,
   sprawdź dokładną ścieżkę w znaczniku oraz czy Ubuntu i Windows używają
   tej samej sesji zalogowanego użytkownika z działającym WSL Interop.

## Pełne wycofanie

1. W Ubuntu usuń znacznik, żeby późniejsze zdarzenia wróciły do
   standardowego transportu skonfigurowanego przed testem.

       sudo rm -f -- /etc/devstack/windows-local-notifications

2. W tym samym koncie Windows i w katalogu gałęzi testowej zaczekaj na
   zakończenie ewentualnego wywołania i uruchom deinstalator.

       Get-Process -Name DevStack.Notifications -ErrorAction SilentlyContinue | Wait-Process -Timeout 10
       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install-local-notifications.ps1 -Mode Uninstall

   Deinstalator usuwa program, skrypt, tożsamość HKCU i jej ustawienia
   oraz zleca Windows wyczyszczenie dymków tej tożsamości. Jeśli Windows
   zwróci ostrzeżenie podczas czyszczenia historii, usuń pozostały testowy
   dymek DevStack z Centrum powiadomień.

3. W Ubuntu przełącz checkout na zapamiętaną gałąź przy tym samym
   commicie albo użyj kopii dokładnego commitu z pierwszego kroku.
   Ponownie zbuduj frontend i zainstaluj poprzednią wersję.

       cd ~/projects/devstack-source
       git switch --detach backup/before-windows-local-notifications
       bash scripts/build-frontend.sh
       sudo bash scripts/apply-update.sh
       git rev-parse HEAD
       systemctl is-active devstack-notifications.timer

   Wycofanie dotyczy zainstalowanego kodu i konfiguracji transportu.
   Dane testowe można usunąć standardowo w Mailpit i panelu.
