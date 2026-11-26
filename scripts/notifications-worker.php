<?php
declare(strict_types=1);
require_once __DIR__.'/../security.php';
require_once __DIR__.'/../notifications.php';

if (PHP_SAPI!=='cli') exit(2);
$store=new DevStackNotices();
try {
    $snapshot=devstack_rpc('notification_snapshot',[]);
    $store->observeJob(is_array($snapshot['engine_job']??null)?$snapshot['engine_job']:null);
    if (is_array($snapshot['services']??null)) $store->observeServices($snapshot['services'],is_array($snapshot['service_results']??null)?$snapshot['service_results']:[]);
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
    // WinRT is the only system transport. The protected marker's mtime
    // prevents replay of pre-opt-in events. No marker means history/toasts.
    $windowsEnabledSince=devstack_windows_notification_since();
    if ($claim=$store->claimPush($windowsEnabledSince)) {
        $start=hrtime(true);
        $result=devstack_rpc('show_windows_notification',$claim['payload']);
        $accepted=($result['accepted']??false)===true;
        error_log(sprintf('DevStack Windows notification: category=%s journal_age_s=%d send_ms=%d outcome=%s',
            $claim['payload']['category'],$claim['journal_age_s'],
            (int)((hrtime(true)-$start)/1000000),$accepted?'accepted':'failed'));
        if (!$accepted) $store->pushFailed($claim['id'],false);
    }
} catch (Throwable) {
    if (isset($claim)&&is_array($claim)) $store->pushFailed($claim['id'],false);
    error_log('DevStack system notification unavailable.');
}
