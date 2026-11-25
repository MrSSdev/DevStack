<?php
declare(strict_types=1);

/**
 * Local administrator-only authentication mode switch.
 * This file is installed root-owned. Never expose it through a web route.
 */
if (PHP_SAPI !== 'cli' || !function_exists('posix_geteuid') || posix_geteuid() !== 0) {
    fwrite(STDERR, "Run using sudo in Ubuntu.\n");
    exit(1);
}
$dir = '/etc/devstack';
$modeFile = $dir . '/auth.mode';
$hashFile = $dir . '/auth.hash';
$engineModeFile = $dir . '/engine-access.mode';
if (!is_dir($dir) || !is_dir('/opt/devstack')) {
    fwrite(STDERR, "DevStack must be at least partially installed.\n");
    exit(1);
}
function save_policy(string $target, string $mode): void {
    $tmp = tempnam(dirname($target), '.auth-');
    if ($tmp === false) throw new RuntimeException('Cannot create temporary auth mode.');
    try {
        if (file_put_contents($tmp, $mode . "\n", LOCK_EX) === false) {
            throw new RuntimeException('Cannot write auth mode.');
        }
        if (!chown($tmp, 'root') || !chgrp($tmp, 'devstackweb') || !chmod($tmp, 0640)) {
            throw new RuntimeException('Cannot secure the administrator policy file.');
        }
        if (!rename($tmp, $target)) throw new RuntimeException('Cannot activate administrator policy.');
    } finally {
        if (is_file($tmp)) unlink($tmp);
    }
}
function save_mode(string $mode): void {
    global $modeFile;
    save_policy($modeFile, $mode);
}
function set_password(): void {
    global $hashFile;
    if (!is_file('/opt/devstack/scripts/set-password.php')) {
        throw new RuntimeException('Password setup script not installed.');
    }
    $cmd = ['/usr/bin/php', '/opt/devstack/scripts/set-password.php'];
    // passthru maintains the real terminal for the hidden password prompts.
    passthru(implode(' ', $cmd), $exit);
    if ($exit !== 0) throw new RuntimeException('Password was not accepted; authentication remains unchanged.');
    if (!is_file($hashFile) || filesize($hashFile) === 0) {
        throw new RuntimeException('Password hash is missing.');
    }
    save_mode('required');
}
$choice = $argv[1] ?? '';
try {
    switch ($choice) {
        case '--password':
            set_password();
            echo "Password authentication enabled.\n";
            break;
        case '--no-password':
            if (($argv[2] ?? '') !== '--i-understand-local-risk') {
                throw new RuntimeException('Explicit confirmation required: --i-understand-local-risk');
            }
            save_mode('disabled');
            echo "WARNING: Passwordless mode enabled. Anyone with local access to devstack.test can administer DevStack.\n";
            break;
        case '--allow-local-installers':
            if (count($argv) !== 3 || ($argv[2] ?? '') !== '--i-understand-local-risk') {
                throw new RuntimeException('Explicit confirmation required: --i-understand-local-risk');
            }
            $raw = @file_get_contents($modeFile);
            if (!is_string($raw) || trim($raw) !== 'disabled') {
                throw new RuntimeException('The panel must already be in passwordless mode. Its authentication setting was not changed.');
            }
            save_policy($engineModeFile, 'allow-passwordless');
            echo "Local framework installers enabled without a panel password.\n";
            echo "WARNING: Clients with local access to the panel can now initiate official installer and dependency code as the developer account.\n";
            break;
        case '--disallow-local-installers':
            save_policy($engineModeFile, 'deny-passwordless');
            echo "Passwordless framework installers disabled. Panel authentication unchanged.\n";
            break;
        case '--status':
            $raw = @file_get_contents($modeFile);
            $mode = is_string($raw) && trim($raw) === 'disabled' ? 'disabled' : 'required';
            echo "Password authentication: " . $mode . "\n";
            $engineMode = @file_get_contents($engineModeFile);
            $allowed = is_string($engineMode) && trim($engineMode) === 'allow-passwordless';
            echo "Passwordless framework installers: " . ($allowed ? 'allowed' : 'denied') . "\n";
            break;
        case '--setup':
            echo "DevStack login setup:\n  1) Password (recommended)\n  2) No password (local-only, less secure)\n";
            echo "Choose [1/2]: ";
            $selection = trim((string)fgets(STDIN));
            if ($selection === '2') {
                echo "Type DISABLE to confirm that anyone able to reach your local panel may administer services: ";
                if (trim((string)fgets(STDIN)) !== 'DISABLE') {
                    throw new RuntimeException('Passwordless setup cancelled.');
                }
                save_mode('disabled');
                echo "Passwordless mode enabled.\n";
            } else {
                set_password();
                echo "Password authentication enabled.\n";
            }
            break;
        default:
            throw new RuntimeException('Usage: sudo php configure-auth.php --setup | --password | --no-password --i-understand-local-risk | --allow-local-installers --i-understand-local-risk | --disallow-local-installers | --status');
    }
} catch (Throwable $e) {
    fwrite(STDERR, $e->getMessage() . "\n");
    exit(1);
}
