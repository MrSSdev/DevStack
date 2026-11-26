<?php
declare(strict_types=1);
require_once __DIR__.'/../security.php';
require_once __DIR__.'/../notifications.php';
require_once __DIR__.'/../push-helper.php';

if (PHP_SAPI!=='cli') exit(2);
$store=new DevStackNotices();
try {
    $snapshot=devstack_rpc('notification_snapshot',[]);
    $store->observeJob(is_array($snapshot['engine_job']??null)?$snapshot['engine_job']:null);
    if (is_array($snapshot['services']??null)) $store->observeServices($snapshot['services']);
} catch (Throwable) {
    // Do not infer service failure from unavailable status.
}
try {
    $messages=[];
    for ($start=0;$start<200;$start+=40) {
        $page=devstack_mailpit_request('list',['start'=>(string)$start]);
        if (!is_array($page)||!array_key_exists('messages',$page)||($page['messages']!==null&&!is_array($page['messages']))) {
            throw new RuntimeException('Invalid mailbox listing');
        }
        $rows=$page['messages']??[];
        $messages=array_merge($messages,$rows);
        if (count($rows)<40) break;
    }
    $store->observeMail($messages);
} catch (Throwable) {
    // Mailpit is optional and can be stopped. Never clear its cursor here.
}
try {
    // At most one delivery per timer cycle; subscriptions are opt-in.
    if ($claim=$store->claimPush()) {
        $start=hrtime(true);
        $result=devstack_push_helper('send',['subscription'=>$claim['subscription'],'payload'=>$claim['payload']]);
        $ok=($result['ok']??false)===true;
        $status=$result['http_status']??null;
        $http=is_int($status)&&$status>=100&&$status<=599?(string)$status:'unavailable';
        // Log timings and provider status only. A successful HTTP response is
        // acceptance by the push service, not proof of a displayed OS banner.
        error_log(sprintf('DevStack push: category=%s journal_age_s=%d send_ms=%d outcome=%s http=%s',
            $claim['payload']['category'],$claim['journal_age_s'],
            (int)((hrtime(true)-$start)/1000000),$ok?'accepted':'failed',$http));
        if (!$ok) $store->pushFailed($claim['id'],($result['expired']??false)===true);
    }
} catch (Throwable) {
    if (isset($claim)&&is_array($claim)) $store->pushFailed($claim['id'],false);
    error_log('DevStack Web Push unavailable.');
}
