using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class LocalNotificationLauncher
{
    private static int Main(string[] args)
    {
        if (args.Length != 1 || args[0].Length > 344)
            return 2;

        try
        {
            byte[] payload = Convert.FromBase64String(args[0]);
            if (payload.Length == 0 || payload.Length > 256 ||
                Array.Exists(payload, value => value < 0x20 || value > 0x7e))
                return 2;

            string directory = AppDomain.CurrentDomain.BaseDirectory;
            string script = File.ReadAllText(Path.Combine(directory, "show-local-notification.ps1"));
            string command = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
            string powershell = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
            var start = new ProcessStartInfo(powershell)
            {
                Arguments = "-NoLogo -NoProfile -NonInteractive -EncodedCommand " + command,
                WorkingDirectory = directory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            // The launcher is built as a Windows GUI executable. CreateNoWindow
            // also prevents a console from being allocated for the child.
            using (Process process = Process.Start(start))
            {
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                process.StandardInput.WriteLine(Encoding.ASCII.GetString(payload));
                process.StandardInput.Close();
                if (process.WaitForExit(5000))
                    return process.ExitCode;

                process.Kill();
                return 124;
            }
        }
        catch (Exception)
        {
            // Report a machine-readable status to WSL without opening a dialog.
            return 1;
        }
    }
}
