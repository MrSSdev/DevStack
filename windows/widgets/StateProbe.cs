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

    internal static async Task<Snapshot> CaptureAsync()
    {
        var checkedAt = DateTimeOffset.Now;
        try
        {
            var state = await RunAsync(new[] { "--list", "--running", "--quiet" }, 5);
            if (state.ExitCode != 0)
                return new(null, "Błąd odczytu WSL", null, null, null, null, checkedAt);

            bool running = ParseRunningDistros(state.Output, Distro);
            if (!running)
                return new(false, "", null, null, null, null, checkedAt);

            // After the passive Windows-only state check, probe service REACHABILITY
            // using Windows HTTPS/TCP only; never call wsl.exe -d from a status poll.
            var http = ProbeDashboardAsync();
            var db = ProbeTcpAsync(3306);
            var redis = ProbeTcpAsync(6379);
            await Task.WhenAll(http, db, redis);
            var (nginx, php) = await http;
            return new(true, "", nginx, php, await db, await redis, checkedAt);
        }
        catch
        {
            return new(null, "Odczyt niedostępny", null, null, null, null, checkedAt);
        }
    }

    private static async Task<(bool Nginx, bool Php)> ProbeDashboardAsync()
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, "https://devstack.test/api/session");
            using var reply = await Client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
            // Any HTTP reply proves a web listener answered; the anonymous session
            // endpoint is handled by PHP-FPM and is already part of DevStack.
            if (!reply.IsSuccessStatusCode)
                return (true, false);
            var payload = await reply.Content.ReadAsStringAsync();
            return (true, payload.Contains("\"authenticated\"", StringComparison.Ordinal));
        }
        catch
        {
            return (false, false);
        }
    }

    private static async Task<bool> ProbeTcpAsync(int port)
    {
        try
        {
            using var client = new TcpClient();
            using var timeout = new CancellationTokenSource(TimeSpan.FromMilliseconds(1400));
            await client.ConnectAsync("127.0.0.1", port, timeout.Token);
            return client.Connected;
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
        IReadOnlyList<string> args, int timeoutSeconds)
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

        using var process = Process.Start(info)
            ?? throw new InvalidOperationException("wsl.exe failed to start");
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSeconds));
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException)
        {
            try { process.Kill(entireProcessTree: true); } catch { /* exiting */ }
            return (-1, "Przekroczono czas oczekiwania");
        }

        var output = await stdout;
        var error = await stderr;
        // Windows tools may produce redirected UTF-16-like output with interspersed NULs.
        // We only compare ASCII names from --quiet, so this is sufficient.
        return (process.ExitCode, (output + " " + error).Replace("\0", "").Trim());
    }
}
