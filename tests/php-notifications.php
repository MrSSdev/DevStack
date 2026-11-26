<?php
declare(strict_types=1);
require_once __DIR__.'/../backend/notifications.php';
function check_notice(bool $ok,string $message):void {if(!$ok){fwrite(STDERR,"FAIL: $message\n");exit(1);}}
$store=new DevStackNotices(':memory:');
$a=str_repeat('a',32);$b=str_repeat('b',32);$m=str_repeat('M',22);$n=str_repeat('N',22);
$stamp=gmdate('c');
$store->observeMail([['ID'=>$m,'Created'=>$stamp,'Subject'=>'Old mail']]);
check_notice($store->poll($a)['items']===[],'Initial mail baseline does not replay');
$store->observeMail([['ID'=>$n,'Created'=>$stamp,'Subject'=>'New mail'],['ID'=>$m,'Created'=>$stamp]]);
$first=$store->poll($a);
check_notice(count($first['toast'])===1&&$first['toast'][0]['id']==='mail-'.$n,'One foreground claim');
check_notice($store->poll($b)['toast']===[],'No duplicate toast in another tab');
$store->mutate('read','mail-'.$n);
check_notice($store->poll($a)['items'][0]['unread']===false,'Read is persisted');
$store->mutate('delete','mail-'.$n);
$store->observeMail([['ID'=>$n,'Created'=>$stamp,'Subject'=>'New mail']]);
check_notice($store->poll($a)['items']===[],'Deleted mail notice never returns');
$job=str_repeat('c',32);
$id=$store->operation('/api/projects',['slug'=>'demo','engine'=>'laravel'],['job_id'=>$job],$a);
check_notice($id==='job-'.$job,'Stable job identity');
$state=['id'=>$job,'slug'=>'demo','state'=>'scaffolding','message'=>'Working'];
check_notice($store->observeJob($state,$a)['show']===true,'Owner sees running job');
check_notice($store->observeJob($state,$b)['show']===false,'Other tab does not repeat job');
$state['state']='succeeded';$state['message']='Ready';
check_notice($store->observeJob($state,$a)['show']===true,'Terminal result stays with owner');
check_notice(count($store->poll($a)['items'])===1&&$store->poll($a)['items'][0]['kind']==='success','One evolving history record');
$store->mutate('clear');
$store->observeJob($state,$a);
check_notice($store->poll($a)['items']===[],'Cleared job result cannot return');
$other=['id'=>str_repeat('d',32),'slug'=>'second','state'=>'scaffolding','message'=>'Working'];
$store->observeJob($other,$b);
$store->observeJob($state,$a); // An older snapshot arrives after the new job.
check_notice(count($store->poll($a)['items'])===1&&$store->poll($a)['items'][0]['id']==='job-'.$other['id'],'Deleted older job stays deleted after a newer job');
$store->mutate('clear');
$id=$store->operation('/api/services/control',['service'=>'redis','action'=>'restart'],['accepted'=>true],$a);
$store->acknowledge($id,$a);
check_notice($store->poll($b)['toast']===[],'Operation response acknowledged once');
check_notice($store->poll($a)['items'][0]['kind']==='info','Accepted service action is not falsely marked success');
$store->mutate('read-all');
check_notice(!$store->poll($a)['items'][0]['unread'],'Read all');
$store->mutate('clear');
$key=rtrim(strtr(base64_encode("\x04".str_repeat('x',64)),'+/','-_'),'=');
$auth=rtrim(strtr(base64_encode(str_repeat('y',16)),'+/','-_'),'=');
$subscription=['endpoint'=>'https://fcm.googleapis.com/fcm/send/fixture','keys'=>['p256dh'=>$key,'auth'=>$auth]];
$store->subscribe($subscription);
$store->setCategories(['mail']);
$store->observeMail([['ID'=>str_repeat('Z',22),'Created'=>$stamp,'Subject'=>'Never include in push']]);
check_notice($store->claimPush()===null,'Any live client suppresses system push');
$store->leave($a);$store->leave($b);
// No sleep: fresh automatic mail must be eligible in this same cycle.
$push=$store->claimPush();
check_notice(is_array($push)&&$push['payload']['category']==='mail'&&$push['journal_age_s']>=0&&!str_contains(json_encode($push['payload']),'Never include'),'Immediate claim once with generic payload');
check_notice($store->claimPush()===null,'No repeated delivery');
$store->pushFailed($push['id'],true);
check_notice(!$store->poll($a)['subscribed'],'Expired subscription removed');
$store->mutate('clear');
$store->subscribe($subscription);
$store->setCategories([]);
$now=gmdate('c');
$store->observeMail([['ID'=>str_repeat('P',22),'Created'=>$now],['ID'=>str_repeat('Q',22),'Created'=>$now]]);
$store->leave($a);
check_notice($store->claimPush()===null,'Disabled mail category suppresses push');
$store->setCategories(['mail']);
$group=$store->claimPush();
check_notice(is_array($group)&&str_starts_with($group['id'],'batch-')&&$group['payload']['category']==='mail','Automatic mail batch is immediately eligible');
check_notice($store->claimPush()===null,'No repeated grouped delivery');
foreach (['https://127.0.0.1/x','https://evil.invalid','https://fcm.googleapis.com.evil.invalid/fcm/send/test','http://fcm.googleapis.com/fcm/send/test','https://user@fcm.googleapis.com/fcm/send/test','https://fcm.googleapis.com:123/fcm/send/test','https://fcm.googleapis.com/other'] as $url) {
    try {DevStackNotices::subscription(['endpoint'=>$url,'keys'=>$subscription['keys']]);check_notice(false,'Unsafe endpoint accepted');}
    catch(InvalidArgumentException) {}
}
// Clicking a system notification must update exactly one event in the same
// authenticated poll, without replaying it as a toast or recreating a row.
$clickStore=new DevStackNotices(':memory:');
$clickStore->observeMail([]);
$clickStamp=gmdate('c');
$one='mail-'.str_repeat('U',22);$two='mail-'.str_repeat('V',22);
$clickStore->observeMail([['ID'=>substr($one,5),'Created'=>$clickStamp,'Subject'=>'Clicked']]);
$clickStore->observeMail([['ID'=>substr($two,5),'Created'=>$clickStamp,'Subject'=>'Unrelated']]);
$clicked=$clickStore->poll($a,true,$one);
$indexed=array_column($clicked['items'],null,'id');
check_notice(!$indexed[$one]['unread']&&$indexed[$two]['unread'],'Click marks only its event read');
check_notice(array_column($clicked['toast'],'id')===[$two],'Clicked pending event is not replayed as a toast');
$again=array_column($clickStore->poll($b,false)['items'],null,'id');
check_notice(!$again[$one]['unread']&&$again[$two]['unread'],'Click read state persists');
$clickStore->mutate('delete',$one);
check_notice(count($clickStore->poll($a,true,$one)['items'])===1,'Stale click does not restore deleted event');
try {$clickStore->poll($a,true,'invalid');check_notice(false,'Invalid click ID accepted');}
catch(InvalidArgumentException) {}
echo "Notification journal, deletion, deduplication, presence, click read and push policy: OK\n";
