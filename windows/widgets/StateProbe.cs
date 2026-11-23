using System.Diagnostics;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;

namespace DevStack.Widgets;

/// <summary>Windows-only probes. No passive code path invokes "wsl -d".</summary>
internal static class StateProbe
{
    internal const string Distro = "Ubuntu-26.04";
    private static readonly HttpClient Client = new(new HttpClientHandler
    {
        AllowAutoRedirect = false,
        UseCookies = false,
        UseProxy = false
        // Never bypass TLS validation: the DevStack mkcert CA must be trusted.
    })
    {
        Timeout = TimeSpan.FromSeconds(3)
    };

    internal record Snapshot(
        bool? WslRunning,
        string WslError,
        bool? Nginx,
        bool? PhpFpm,
        bool? MariaDb,
        bool? Redis,
        DateTimeOffset CheckedAt);

    internal static bool ParseRunningDistros(string text, string distro)
    {
        return text.Replace("\0", "").Trim('\uFEFF')
            .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries)
            .Any(name => string.Equals(name.Trim(), distro, StringComparison.OrdinalIgnoreCase));
    }

    // Read-only WSL query. Never invokes wsl.exe -d or starts the distro.
    // The visibility cancellation token stops an in-flight read when Win+W closes.
    internal static async Task<Snapshot> CaptureWslOnlyAsync(CancellationToken cancellationToken = default)
    {
        var checkedAt = DateTimeOffset.Now;
        try
        {
            var state = await RunAsync(
                new[] { "--list", "--running", "--quiet" }, 5, cancellationToken);
            if (state.ExitCode != 0)
                return new(null, "Błąd odczytu WSL", null, null, null, null, checkedAt);

            return new(ParseRunningDistros(state.Output, Distro), "",
                null, null, null, null, checkedAt);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return new(null, "Odczyt niedostępny", null, null, null, null, checkedAt);
        }
    }

    // Reuse the same WSL state snapshot. Do not execute wsl.exe twice just
    // because both WSL and DevStack cards are shown on the board.
    internal static async Task<Snapshot> CaptureServicesAfterWslAsync(
        Snapshot wsl, CancellationToken cancellationToken = default)
    {
        if (wsl.WslRunning != true)
            return wsl; // No HTTPS/TCP requests while Ubuntu is off or unknown.

        cancellationToken.ThrowIfCancellationRequested();
        try
        {
            var http = ProbeDashboardAsync(cancellationToken);
            var db = ProbeTcpAsync(3306, cancellationToken);
            var redis = ProbeTcpAsync(6379, cancellationToken);
            await Task.WhenAll(http, db, redis);
            cancellationToken.ThrowIfCancellationRequested();
            var (nginx, php) = await http;
            return wsl with
            {
                Nginx = nginx, PhpFpm = php,
                MariaDb = await db, Redis = await redis
            };
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return wsl with
            {
                Nginx = false, PhpFpm = false,
                MariaDb = false, Redis = false
            };
        }
    }

    internal static async Task<Snapshot> CaptureAsync()
    {
        var wsl = await CaptureWslOnlyAsync();
        return await CaptureServicesAfterWslAsync(wsl);
    }

    private static async Task<(bool Nginx, bool Php)> ProbeDashboardAsync(
        CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = new HttpRequestMessage(
                HttpMethod.Get, "https://devstack.test/api/session");
            using var reply = await Client.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            // Any HTTP reply proves a web listener answered; the anonymous session
            // endpoint is handled by PHP-FPM and is already part of DevStack.
            if (!reply.IsSuccessStatusCode)
                return (true, false);
            var payload = await reply.Content.ReadAsStringAsync(cancellationToken);
            return (true, payload.Contains("\"authenticated\"", StringComparison.Ordinal));
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return (false, false);
        }
    }

    private static async Task<bool> ProbeTcpAsync(
        int port, CancellationToken cancellationToken = default)
    {
        try
        {
            using var client = new TcpClient();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromMilliseconds(1400));
            await client.ConnectAsync("127.0.0.1", port, timeout.Token);
            return client.Connected;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>Only called by a deliberate Action.Execute on the WSL widget.</summary>
    internal static Task<(int ExitCode, string Output)> StartAsync() =>
        RunAsync(new[] { "-d", Distro, "-u", "ubuntu", "--", "/usr/bin/true" }, 35);

    /// <summary>Only called after the explicit two-click stop confirmation.</summary>
    internal static Task<(int ExitCode, string Output)> StopAsync() =>
        RunAsync(new[] { "--terminate", Distro }, 20);

    private static async Task<(int ExitCode, string Output)> RunAsync(
        IReadOnlyList<string> args, int timeoutSeconds,
        CancellationToken cancellationToken = default)
    {
        var info = new ProcessStartInfo
        {
            FileName = "wsl.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        foreach (var arg in args)
            info.ArgumentList.Add(arg);

        cancellationToken.ThrowIfCancellationRequested();
        using var process = Process.Start(info)
            ?? throw new InvalidOperationException("wsl.exe failed to start");
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            // Kill only our read-only wsl.exe CLI helper, not Ubuntu itself.
            try { process.Kill(entireProcessTree: true); } catch { /* exiting */ }
            if (cancellationToken.IsCancellationRequested)
                throw;
            return (-1, "Przekroczono czas oczekiwania");
        }

        var output = await stdout;
        var error = await stderr;
        cancellationToken.ThrowIfCancellationRequested();
        // Windows tools may produce redirected UTF-16-like output with interspersed NULs.
        // We only compare ASCII names from --quiet, so this is sufficient.
        return (process.ExitCode, (output + " " + error).Replace("\0", "").Trim());
    }
}
