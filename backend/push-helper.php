<?php
declare(strict_types=1);

// Optional distro-packaged pywebpush. The subprocess runs as devstackweb,
// receives bounded JSON through stdin and never interpolates shell arguments.
function devstack_push_helper(string $operation, array $payload=[]): array
{
    if (!in_array($operation,['public','send'],true)) throw new InvalidArgumentException('Nieznana operacja Web Push.');
    $process=proc_open(['/usr/bin/python3','-I',__DIR__.'/scripts/notification-push.py',$operation],
        [0=>['pipe','r'],1=>['pipe','w'],2=>['file','/dev/null','a']],$pipes,null,
        ['PATH'=>'/usr/bin:/bin','LANG'=>'C.UTF-8'],['bypass_shell'=>true]);
    if (!is_resource($process)) return ['ok'=>false];
    try {
        fwrite($pipes[0],json_encode($payload,JSON_THROW_ON_ERROR));
        fclose($pipes[0]); unset($pipes[0]);
        stream_set_timeout($pipes[1],12);
        $raw=stream_get_contents($pipes[1],8192);
        $timed=stream_get_meta_data($pipes[1])['timed_out'];
        fclose($pipes[1]); unset($pipes[1]);
        if ($timed) proc_terminate($process);
        $decoded=is_string($raw)?json_decode($raw,true):null;
        return !$timed&&is_array($decoded)?$decoded:['ok'=>false];
    } finally {
        foreach ($pipes as $pipe) fclose($pipe);
        proc_close($process);
    }
}
