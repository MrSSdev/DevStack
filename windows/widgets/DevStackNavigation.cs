using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace DevStack.Widgets;

internal static class DevStackNavigation
{
    private const string Scheme = "web+devstack";
    private const string Classes = @"HKEY_CLASSES_ROOT\";
    private const string Repository = @"HKEY_CURRENT_USER\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages\";
    private const string UserChoice = @"HKEY_CURRENT_USER\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\web+devstack\UserChoice";

    // Same read-only association contract as the WinRT launcher. Do not
    // assume an Edge app ID, create a handler or execute registry commands.
    internal static bool HasPwa(Func<string,string,object?> readValue,
        Func<IEnumerable<string>> readCapabilities, Func<IEnumerable<string>> readPackages)
    {
        try
        {
            foreach (var package in readPackages())
            {
                if (!Regex.IsMatch(package, @"\Adevstack\.test-[A-Za-z0-9_.-]{1,220}\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)) continue;
                if (readValue(Repository + package + @"\App\Capabilities\URLAssociations", Scheme) is not string progId ||
                    !Regex.IsMatch(progId, @"\AAppX[A-Za-z0-9]{1,128}\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)) continue;
                var open = Classes + progId + @"\Shell\open";
                if (readValue(open, "PackageId") is string packageId &&
                    string.Equals(packageId, package, StringComparison.OrdinalIgnoreCase) &&
                    readValue(open, "ContractId") is string contract &&
                    string.Equals(contract, "Windows.Protocol", StringComparison.OrdinalIgnoreCase) &&
                    readValue(open + @"\command", "DelegateExecute") is string handler &&
                    Guid.TryParseExact(handler, "B", out _)) return true;
            }
            var candidates = new List<string> { Scheme };
            if (readValue(UserChoice, "ProgId") is string choice) candidates.Add(choice);
            foreach (var capability in readCapabilities())
                if (capability.StartsWith(@"Software\", StringComparison.OrdinalIgnoreCase) &&
                    readValue(@"HKEY_CURRENT_USER\" + capability + @"\URLAssociations", Scheme) is string progId)
                    candidates.Add(progId);
            foreach (var progId in candidates)
                if (Regex.IsMatch(progId, @"\A[A-Za-z0-9_.+-]{1,256}\z") &&
                    readValue(Classes + progId + @"\shell\open\command", "") is string command &&
                    !string.IsNullOrWhiteSpace(command)) return true;
        }
        catch { /* Unavailable or inaccessible associations use HTTPS. */ }
        return false;
    }

    private static bool HasRegisteredPwa()
    {
        if (!OperatingSystem.IsWindows()) return false;
        return HasPwa((path, name) => Registry.GetValue(path, name, null), () =>
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\RegisteredApplications");
            var values = new List<string>();
            if (key != null)
                foreach (var name in key.GetValueNames())
                    if (key.GetValue(name) is string capability) values.Add(capability);
            return values;
        }, () =>
        {
            using var key = Registry.CurrentUser.OpenSubKey(Repository.Substring(@"HKEY_CURRENT_USER\".Length).TrimEnd('\\'));
            return key?.GetSubKeyNames() ?? Array.Empty<string>();
        });
    }

    internal static string Target(string verb, bool usePwa)
    {
        var route = verb switch { "open-new" => "new", "open-projects" => "projects", _ => throw new InvalidOperationException("Unknown route") };
        return (usePwa ? "web+devstack://open/" : "https://devstack.test/#/") + route;
    }

    internal static void Open(string verb)
    {
        var fallback = Target(verb, false);
        if (HasRegisteredPwa())
        {
            try
            {
                Process.Start(new ProcessStartInfo { FileName = Target(verb, true), UseShellExecute = true });
                return;
            }
            catch (System.ComponentModel.Win32Exception) { /* Association may have disappeared. */ }
        }
        Process.Start(new ProcessStartInfo { FileName = fallback, UseShellExecute = true });
    }
}
