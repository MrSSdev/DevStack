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
        $rows=is_array($page)&&is_array($page['messages']??null)?$page['messages']:null;
        if ($rows===null) throw new RuntimeException('Invalid mailbox listing');
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
        $result=devstack_push_helper('send',['subscription'=>$claim['subscription'],'payload'=>$claim['payload']]);
        if (($result['ok']??false)!==true) $store->pushFailed($claim['id'],($result['expired']??false)===true);
    }
} catch (Throwable) {
    if (isset($claim)&&is_array($claim)) $store->pushFailed($claim['id'],false);
    error_log('DevStack Web Push unavailable.');
}
