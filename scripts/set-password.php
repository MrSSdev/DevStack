<?php
declare(strict_types=1);
if (PHP_SAPI !== 'cli' || function_exists('posix_geteuid') && posix_geteuid() !== 0) {
    fwrite(STDERR, "Run as root from the WSL terminal.\n");
    exit(1);
}
function private_prompt(string $message): string {
    fwrite(STDOUT, $message);
    system('stty -echo');
    try {
        $value = rtrim((string) fgets(STDIN), "\r\n");
    } finally {
        system('stty echo');
        fwrite(STDOUT, "\n");
    }
    return $value;
}
$one = private_prompt('Nowe haslo do panelu DevStack: ');
$two = private_prompt('Powtorz haslo: ');
if ($one !== $two || strlen($one) < 14 || strlen($one) > 200) {
    fwrite(STDERR, "Hasla musza byc jednakowe i miec od 14 do 200 bajtow.\n");
    exit(1);
}
$hash = password_hash($one, PASSWORD_ARGON2ID);
if ($hash === false) {
    fwrite(STDERR, "Cannot hash password.\n");
    exit(1);
}
$file = '/etc/devstack/auth.hash';
if (file_put_contents($file, $hash . "\n", LOCK_EX) === false) {
    fwrite(STDERR, "Cannot save password hash.\n");
    exit(1);
}
chown($file, 'root');
chgrp($file, 'devstackweb');
chmod($file, 0640);
fwrite(STDOUT, "Haslo panelu zapisane (Argon2id).\n");
