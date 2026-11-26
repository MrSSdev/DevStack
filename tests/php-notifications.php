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
// Closed clients must never reclaim a toast or renew their lease with an
// old poll, regardless of whether leave or the initial poll arrives first.
// An operation and a state/job snapshot must not renew presence either.
$presence=new DevStackNotices(':memory:');
$presence->observeMail([]);
$presence->subscribe($subscription);
$presence->setCategories(['mail']);
$presence->poll($a);$presence->poll($b);
$presence->leave($a);
$presence->leave($a); // Idempotent.
$presence->observeMail([['ID'=>str_repeat('L',22),'Created'=>gmdate('c'),'Subject'=>'Close race']]);
check_notice($presence->poll($a)['toast']===[],'Late closed-client poll cannot steal a toast');
check_notice($presence->claimPush()===null,'One remaining live tab still suppresses push');
$presence->leave($b);
$c=str_repeat('e',32);
$presence->leave($c); // Leave can precede registration.
check_notice($presence->poll($c)['toast']===[],'Leave before initial poll blocks delayed registration');
$presence->operation('/api/preferences',['theme'=>'dark'],[],$a);
$lateJob=['id'=>str_repeat('f',32),'slug'=>'demo','state'=>'scaffolding','message'=>'Working'];
$presence->operation('/api/projects',['slug'=>'demo','engine'=>'laravel'],['job_id'=>$lateJob['id']],$a);
check_notice($presence->observeJob($lateJob,$a)['show']===false,'Closed client cannot claim job presentation');
$immediate=$presence->claimPush();
check_notice(is_array($immediate)&&$immediate['payload']['category']==='mail','Closed clients and late operations do not block same-cycle push');
$new=str_repeat('9',32);
$presence->poll($new);
$presence->observeMail([['ID'=>str_repeat('K',22),'Created'=>gmdate('c'),'Subject'=>'Restored document']]);
check_notice($presence->claimPush()===null,'New document lifetime renews presence');
check_notice(array_column($presence->poll($new)['toast'],'id')===['mail-'.str_repeat('K',22)],'New document can receive foreground notifications');
$presence->leave($new);
// An unload request can disappear while the browser tears down a document.
// A new authenticated poll can retire only explicitly recorded old IDs,
// atomically with its own heartbeat; another live tab remains untouched.
$recovery=new DevStackNotices(':memory:');
$recovery->observeMail([]);$recovery->subscribe($subscription);$recovery->setCategories(['mail']);
$recovery->poll($a);$recovery->poll($b);
$recovery->poll($c,true,null,[$a,$a]);
$recovery->observeMail([['ID'=>str_repeat('R',22),'Created'=>gmdate('c')]]);
check_notice($recovery->poll($a)['toast']===[],'Recovered leave blocks late poll from retired ID');
$recovery->leave($c);
check_notice($recovery->claimPush()===null,'Recovery must preserve another live tab');
$recovery->leave($b);
check_notice($recovery->claimPush()!==null,'Recovered missing leave does not block push');
foreach ([[$c],['invalid'],array_fill(0,17,$a),'invalid',['x'=>$a]] as $invalid) {
    try {$recovery->poll($c,true,null,$invalid);check_notice(false,'Invalid retired list accepted');}
    catch(InvalidArgumentException) {}
}
// Verify the persisted 90-second lease and pruning without sleeping for it.
$fixture=tempnam(sys_get_temp_dir(),'devstack-notices-');
try {
    $leaseStore=new DevStackNotices($fixture);
    $leaseStore->observeMail([]);$leaseStore->subscribe($subscription);$leaseStore->setCategories(['mail']);
    $leaseStore->poll($a);
    $inspect=new PDO('sqlite:'.$fixture);
    $remaining=(int)$inspect->query('SELECT expires FROM clients')->fetchColumn()-time();
    check_notice($remaining>=88&&$remaining<=90,'Heartbeat lease is 90 seconds');
    $inspect->exec('UPDATE clients SET expires=0');
    $leaseStore->observeMail([['ID'=>str_repeat('J',22),'Created'=>gmdate('c')]]);
    check_notice($leaseStore->claimPush()!==null,'Expired unclosed client no longer blocks push');
    $leaseStore->leave($b);$inspect->exec('UPDATE closed_clients SET expires=0');
    $leaseStore->poll($a,false);
    check_notice((int)$inspect->query('SELECT COUNT(*) FROM closed_clients')->fetchColumn()===0,'Close markers are pruned after their retention period');
    unset($inspect,$leaseStore);
} finally {unlink($fixture);}
$local=new DevStackNotices(':memory:');
$local->observeMail([]);
$localId='mail-'.str_repeat('N',22);
$local->observeMail([['ID'=>substr($localId,5),'Created'=>gmdate('c'),'Subject'=>'Private fixture subject']]);
check_notice($local->claimPush()===null,'Web Push still requires a subscription');
check_notice($local->claimPush(time()+60)===null,'Local opt-in does not dispatch older events');
$local->poll($a,false);
check_notice($local->claimPush(time()-60)===null,'Live tab also suppresses local Windows delivery');
$local->leave($a);
$native=$local->claimPush(time()-60);
check_notice(is_array($native)&&$native['subscription']===null&&$native['payload']===
    ['id'=>$localId,'category'=>'mail','kind'=>'info'],'Local delivery works with no subscription and sends generic data');
check_notice($local->claimPush(time()-60)===null,'Local delivery shares the one-time claim');
$local->pushFailed($localId,false);
$afterLocalFailure=$local->poll($b);
check_notice($afterLocalFailure['toast']===[]&&$afterLocalFailure['items'][0]['id']===$localId,'Local failure keeps journal history without another automatic delivery');
$local->leave($b);
$local->subscribe($subscription);
$local->observeMail([['ID'=>str_repeat('W',22),'Created'=>gmdate('c')]]);
check_notice($local->claimPush(time()-60)!==null&&$local->claimPush()===null,
    'A native claim cannot be delivered again through a saved Web Push subscription');
echo "Notification journal, deletion, deduplication, presence, click read and system delivery policy: OK\n";
