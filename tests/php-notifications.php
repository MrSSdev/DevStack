<?php
declare(strict_types=1);
require_once __DIR__.'/../backend/notifications.php';
function check_notice(bool $ok,string $message):void {if(!$ok){fwrite(STDERR,"FAIL: $message\n");exit(1);}}
$store=new DevStackNotices(':memory:');
$a=str_repeat('a',32);$b=str_repeat('b',32);$m=str_repeat('M',22);$n=str_repeat('N',22);
// Every POST handler is either registered as a journal operation or listed
// as an explicit exception. Runtime classification also rejects an unknown
// route before executing it; this guard catches the omission in CI.
$apiSource=file_get_contents(__DIR__.'/../backend/public/index.php');
$dispatch=explode('// Authenticated route dispatch (guarded by notification policy).',$apiSource,2);
check_notice(count($dispatch)===2,'Central API dispatch exists');
check_notice(!str_contains($dispatch[1],'devstack_json(')&&!str_contains($dispatch[1],'devstack_notice_operation('),'Authenticated routes must use the shared response boundary');
$routes=['/api/login','/api/logout'];
foreach (explode("\n",$dispatch[1]) as $line) {
    if (!str_starts_with($line,"    if (\$method === 'POST' && ")) continue;
    if (str_contains($line,'$engineRoute')) {
        $routes[]='/api/engines/:engine';
    } else {
        preg_match_all("~'(/api/[^']+)'~",$line,$matches);
        check_notice(count($matches[1])>0,'Explicit POST policy for every route: '.$line);
        array_push($routes,...$matches[1]);
    }
}
$declared=array_merge(array_keys(DevStackNotices::POST_OPERATIONS),array_keys(DevStackNotices::POST_LOCAL));
sort($routes);sort($declared);
check_notice($routes===$declared,'POST handlers and notification policy catalogue match');
check_notice(DevStackNotices::postPolicy('/api/new-feature')===null&&DevStackNotices::postPolicy('/api/notifications/new-feature')===null,'Unknown mutations have no implicit exception');
foreach (DevStackNotices::POST_OPERATIONS as $route=>$policy) {
    $sample=$route==='/api/engines/:engine'?'/api/engines/laravel':$route;
    check_notice(DevStackNotices::postPolicy($sample)==='journal','Registered operation '.$sample);
    $fixture=$route==='/api/services/control'?['service'=>'redis','action'=>'restart']:
        ($route==='/api/services/autostart'?['service'=>'redis','enabled'=>true]:[]);
    $response=$route==='/api/engines/:engine'?['job_id'=>str_repeat('f',32)]:[];
    $coverage=new DevStackNotices(':memory:');
    $eventId=$coverage->operation($sample,$fixture,$response,$a);
    $rows=$coverage->poll($a)['items'];
    check_notice(is_string($eventId)&&count($rows)===1&&$rows[0]['id']===$eventId&&$rows[0]['category']===$policy[0],
        'Every registered operation produces one journal record: '.$sample);
}
foreach (DevStackNotices::POST_LOCAL as $route=>$reason) {
    check_notice($reason!==''&&DevStackNotices::postPolicy($route)==='local','Explicit exception '.$route);
    check_notice($store->operation($route,[],[],$a)===null,'Local exception does not create history: '.$route);
}
// The first observation initializes the cursor to its own current time.
// Generate the new receipt timestamp after that baseline, not before the
// unrelated POST-policy checks (which may cross a wall-clock second).
$oldStamp=gmdate('c',time()-1);
$store->observeMail([['ID'=>$m,'Created'=>$oldStamp,'Subject'=>'Old mail']]);
check_notice($store->poll($a)['items']===[],'Initial mail baseline does not replay');
$stamp=gmdate('c');
$store->observeMail([['ID'=>$n,'Created'=>$stamp,'Subject'=>'New mail'],['ID'=>$m,'Created'=>$oldStamp]]);
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
// The WSL CA button is an ordinary settings operation. Keep its exact ID,
// fixed public history text and existing foreground acknowledgement rules.
$caStore=new DevStackNotices(':memory:');
$caStore->setCategories(DevStackNotices::CATEGORIES);
$caStore->poll($a);
$caId=$caStore->operation('/api/ca/wsl',[],['message'=>'untrusted diagnostic','sha256'=>str_repeat('A',64)],$a);
check_notice(is_string($caId)&&preg_match('~^op-[a-f0-9]{32}$~D',$caId)===1,'WSL CA success has an operation ID');
$caStore->acknowledge($caId,$a);
$caItems=$caStore->poll($b);
check_notice(count($caItems['items'])===1&&$caItems['toast']===[],'WSL CA does not duplicate its direct toast');
$caRow=$caItems['items'][0];
check_notice($caRow['id']===$caId&&$caRow['category']==='settings'&&$caRow['kind']==='success'&&$caRow['route']==='/#/settings'&&$caRow['unread']===true,'WSL CA success belongs to settings history');
check_notice(!str_contains(json_encode($caRow),'untrusted diagnostic')&&!str_contains(json_encode($caRow),str_repeat('A',64)),'WSL CA history excludes RPC diagnostics and fingerprint');
$caStore->mutate('read',$caId);
check_notice(!$caStore->poll($a)['items'][0]['unread'],'WSL CA read acknowledgement preserves history');
$caError=$caStore->operation('/api/ca/wsl',[],[],$a,false);
check_notice(is_string($caError)&&$caError!==$caId,'WSL CA failure also has an operation ID');
$caErrorRow=array_values(array_filter($caStore->poll($a)['items'],static fn($r)=>$r['id']===$caError))[0];
check_notice($caErrorRow['kind']==='error'&&$caErrorRow['category']==='settings'&&$caErrorRow['description']==='Operacja nie powiodła się.','WSL CA failure stores a generic settings notice');
check_notice($caStore->operation('/api/ca/wsl',['path'=>'invalid'],[],$a,false)===null,'Invalid WSL CA payload does not create history');
$caStore->leave($a);$caStore->leave($b);
check_notice($caStore->claimPush(time()-60)===null,'WSL CA settings results do not send Windows notifications');
$since=time()-60;
$store->setCategories(['mail']);
$store->observeMail([['ID'=>str_repeat('Z',22),'Created'=>gmdate('c'),'Subject'=>'Never include in push']]);
check_notice($store->claimPush($since)===null,'Any live client suppresses system push');
$store->leave($a);$store->leave($b);
// No sleep: fresh automatic mail must be eligible in this same cycle.
$push=$store->claimPush($since);
check_notice(is_array($push)&&$push['payload']['category']==='mail'&&$push['journal_age_s']>=0&&!str_contains(json_encode($push['payload']),'Never include'),'Immediate claim once with generic payload');
check_notice($store->claimPush($since)===null,'No repeated delivery');
$store->pushFailed($push['id'],false);
check_notice(!$store->poll($a)['subscribed'],'No browser subscription exposed');
$store->mutate('clear');

$store->setCategories([]);
$now=gmdate('c');
$store->observeMail([['ID'=>str_repeat('P',22),'Created'=>$now],['ID'=>str_repeat('Q',22),'Created'=>$now]]);
$store->leave($a);
check_notice($store->claimPush($since)===null,'Disabled mail category suppresses push');
$store->setCategories(['mail']);
$group=$store->claimPush($since);
check_notice(is_array($group)&&str_starts_with($group['id'],'batch-')&&$group['payload']['category']==='mail','Automatic mail batch is immediately eligible');
check_notice($store->claimPush($since)===null,'No repeated grouped delivery');
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

$presence->setCategories(['mail']);
$presence->poll($a);$presence->poll($b);
$presence->leave($a);
$presence->leave($a); // Idempotent.
$presence->observeMail([['ID'=>str_repeat('L',22),'Created'=>gmdate('c'),'Subject'=>'Close race']]);
check_notice($presence->poll($a)['toast']===[],'Late closed-client poll cannot steal a toast');
check_notice($presence->claimPush($since)===null,'One remaining live tab still suppresses push');
$presence->leave($b);
$c=str_repeat('e',32);
$presence->leave($c); // Leave can precede registration.
check_notice($presence->poll($c)['toast']===[],'Leave before initial poll blocks delayed registration');
$presence->operation('/api/preferences',['theme'=>'dark'],[],$a);
$lateJob=['id'=>str_repeat('f',32),'slug'=>'demo','state'=>'scaffolding','message'=>'Working'];
$presence->operation('/api/projects',['slug'=>'demo','engine'=>'laravel'],['job_id'=>$lateJob['id']],$a);
check_notice($presence->observeJob($lateJob,$a)['show']===false,'Closed client cannot claim job presentation');
$immediate=$presence->claimPush($since);
check_notice(is_array($immediate)&&$immediate['payload']['category']==='mail','Closed clients and late operations do not block same-cycle push');
$new=str_repeat('9',32);
$presence->poll($new);
$presence->observeMail([['ID'=>str_repeat('K',22),'Created'=>gmdate('c'),'Subject'=>'Restored document']]);
check_notice($presence->claimPush($since)===null,'New document lifetime renews presence');
check_notice(array_column($presence->poll($new)['toast'],'id')===['mail-'.str_repeat('K',22)],'New document can receive foreground notifications');
$presence->leave($new);
// An unload request can disappear while the browser tears down a document.
// A new authenticated poll can retire only explicitly recorded old IDs,
// atomically with its own heartbeat; another live tab remains untouched.
$recovery=new DevStackNotices(':memory:');
$recovery->observeMail([]);$recovery->setCategories(['mail']);
$recovery->poll($a);$recovery->poll($b);
$recovery->poll($c,true,null,[$a,$a]);
$recovery->observeMail([['ID'=>str_repeat('R',22),'Created'=>gmdate('c')]]);
check_notice($recovery->poll($a)['toast']===[],'Recovered leave blocks late poll from retired ID');
$recovery->leave($c);
check_notice($recovery->claimPush($since)===null,'Recovery must preserve another live tab');
$recovery->leave($b);
check_notice($recovery->claimPush($since)!==null,'Recovered missing leave does not block push');
foreach ([[$c],['invalid'],array_fill(0,17,$a),'invalid',['x'=>$a]] as $invalid) {
    try {$recovery->poll($c,true,null,$invalid);check_notice(false,'Invalid retired list accepted');}
    catch(InvalidArgumentException) {}
}
// Verify the persisted 90-second lease and pruning without sleeping for it.
$fixture=tempnam(sys_get_temp_dir(),'devstack-notices-');
try {
    $leaseStore=new DevStackNotices($fixture);
    $leaseStore->observeMail([]);$leaseStore->setCategories(['mail']);
    $leaseStore->poll($a);
    $inspect=new PDO('sqlite:'.$fixture);
    $remaining=(int)$inspect->query('SELECT expires FROM clients')->fetchColumn()-time();
    check_notice($remaining>=88&&$remaining<=90,'Heartbeat lease is 90 seconds');
    $inspect->exec('UPDATE clients SET expires=0');
    $leaseStore->observeMail([['ID'=>str_repeat('J',22),'Created'=>gmdate('c')]]);
    check_notice($leaseStore->claimPush($since)!==null,'Expired unclosed client no longer blocks push');
    $leaseStore->leave($b);$inspect->exec('UPDATE closed_clients SET expires=0');
    $leaseStore->poll($a,false);
    check_notice((int)$inspect->query('SELECT COUNT(*) FROM closed_clients')->fetchColumn()===0,'Close markers are pruned after their retention period');
    unset($inspect,$leaseStore);
} finally {unlink($fixture);}
$local=new DevStackNotices(':memory:');
$local->observeMail([]);
$localId='mail-'.str_repeat('N',22);
$local->observeMail([['ID'=>substr($localId,5),'Created'=>gmdate('c'),'Subject'=>'Private fixture subject']]);
check_notice($local->claimPush()===null,'No native opt-in means no background delivery');
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

$local->observeMail([['ID'=>str_repeat('W',22),'Created'=>gmdate('c')]]);
check_notice($local->claimPush(time()-60)!==null&&$local->claimPush()===null,
    'A native claim cannot be delivered again without opt-in');
// An upgrade may encounter the old metadata record. Retire it without
// clearing preferences, history or a live client's lease; absence of a
// native marker must not select a browser transport.
$legacyFile=tempnam(sys_get_temp_dir(),'devstack-legacy-notices-');
try {
    $legacy=new DevStackNotices($legacyFile);
    $inspect=new PDO('sqlite:'.$legacyFile);
    $inspect->prepare('INSERT INTO metadata(key,value) VALUES (?,?)')->execute(['subscription','{"endpoint":"legacy fixture"}']);
    $legacy->setCategories(['mail']);
    $legacy->observeMail([]);
    $legacy->observeMail([['ID'=>str_repeat('X',22),'Created'=>gmdate('c'),'Subject'=>'Preserve']]);
    check_notice($legacy->claimPush()===null,'Legacy subscription cannot enable background delivery');
    check_notice((int)$inspect->query("SELECT COUNT(*) FROM metadata WHERE key='subscription'")->fetchColumn()===0,'Legacy subscription retired');
    check_notice($legacy->categories()===['mail'],'System categories preserved');
    check_notice(count($legacy->poll($a,false)['items'])===1,'History preserved');
    check_notice($legacy->claimPush($since)===null,'Live client still suppresses native delivery');
    $legacy->leave($a);
    check_notice($legacy->claimPush($since)!==null,'Native transport still works after legacy cleanup');
    unset($inspect,$legacy);
} finally {unlink($legacyFile);}
// Service completion is a distinct, exactly-once terminal event. The
// acceptance itself remains info and a restart can finish between polls.
$svc=new DevStackNotices(':memory:');
$activeService=[['id'=>'mailpit','name'=>'Mailpit','state'=>'active']];
$svc->observeServices($activeService);
$accept=$svc->operation('/api/services/control',['service'=>'mailpit','action'=>'restart'],['accepted'=>true],$a);
$svc->acknowledge($accept,$a);
$restart=['id'=>str_repeat('1',32),'service'=>'mailpit','name'=>'Mailpit','action'=>'restart','outcome'=>'success','state'=>'active','stamp'=>time()];
$svc->observeServices($activeService,[$restart]);
$terminal='service-'.$restart['id'];
$result=$svc->poll($a);
$items=array_column($result['items'],null,'id');
check_notice(count($items)===2&&$items[$accept]['kind']==='info'&&$items[$terminal]['kind']==='success','Restart acceptance and verified outcome remain distinct');
check_notice($items[$terminal]['title']==='Usługa uruchomiona ponownie'&&array_column($result['toast'],'id')===[$terminal],'Quick restart has one actual completion toast');
$svc->mutate('delete',$terminal);
$svc->observeServices($activeService,[$restart]);
check_notice(count($svc->poll($a)['items'])===1,'Deleted receipt is not replayed');
$failure=[...$restart,'id'=>str_repeat('2',32),'outcome'=>'error','state'=>'failed'];
$failedService=[['id'=>'mailpit','name'=>'Mailpit','state'=>'failed']];
$svc->observeServices($failedService,[$restart,$failure]);
$items=array_column($svc->poll($a)['items'],null,'id');
check_notice(count($items)===2&&$items['service-'.$failure['id']]['kind']==='error','Actual failed action, without duplicate generic failure');
$svc->mutate('clear');
$svc->observeServices($failedService,[$restart,$failure]);
check_notice($svc->poll($a)['items']===[],'Clear does not recreate service receipts');
$svc->observeServices($activeService);
check_notice($svc->poll($a)['items'][0]['title']==='Usługa ponownie aktywna','Spontaneous recovery remains monitored');
$wrong=[...$restart,'id'=>str_repeat('3',32),'state'=>'inactive'];
$svc->observeServices([['id'=>'mailpit','name'=>'Mailpit','state'=>'inactive']],[$wrong]);
$items=array_column($svc->poll($a)['items'],null,'id');
check_notice($items['service-'.$wrong['id']]['kind']==='error','Inconsistent successful receipt cannot report service as active');


// Application instruction results use the same durable collector tombstones.
$sync = new DevStackNotices(':memory:');
$receipt = str_repeat('7',32);
$acceptArgs = [
    '/api/extensions/instructions',['extension'=>'devstack-tools','action'=>'set_automatic','enabled'=>false],
    ['receipt'=>$receipt,'project_slug'=>'webapp','project_name'=>'Projekt testowy'],$a,
];
$accepted = $sync->operation(...$acceptArgs);
$sync->acknowledge($accepted,$a);
check_notice($sync->operation(...$acceptArgs) === $accepted,'A single acknowledgement per operation receipt');
$event = ['id'=>$receipt,'extension'=>'devstack-tools','slug'=>'webapp','name'=>'Projekt testowy',
    'action'=>'set_automatic','enabled'=>false,'outcomes'=>['done'=>1],'stamp'=>time()-3];
$terminal = 'op-'.$receipt;
check_notice($sync->poll($a)['items'][0]['kind']==='info' && $sync->claimPush(time()-60)===null,
    'Acceptance alone is informational and does not push');
$sync->observeInstructionResults([$event,$event]);
$events = $sync->poll($a);
$byId = array_column($events['items'],null,'id');
check_notice(count($byId)===2 && $byId[$accepted]['receipt']===$receipt && $byId[$terminal]['receipt']===$receipt,
    'Terminal event correlates with exactly one acknowledgement');
check_notice($byId[$terminal]['kind']==='success' && $byId[$terminal]['title']==='Automatyczne instrukcje wyłączone' &&
    $byId[$terminal]['description']==='Projekt testowy' && $byId[$terminal]['route']==='/#/project/webapp',
    'Verified successful project result uses the registry name and route');
check_notice(array_column($events['toast'],'id')===[$terminal] && $sync->poll($b)['toast']===[],
    'One live client receives the terminal toast');
$sync->mutate('delete',$terminal);
$sync->observeInstructionResults([$event]);
check_notice(count($sync->poll($b)['items'])===1,'Deleted instruction result is not replayed');
$sync->leave($a);
$sync->leave($b);
$global = [...$event,'id'=>str_repeat('8',32),'slug'=>'','name'=>'','action'=>'sync','enabled'=>null,
    'outcomes'=>['done'=>2,'conflict'=>1,'error'=>1]];
$sync->observeInstructionResults([$global]);
$push = $sync->claimPush(time()-60);
check_notice($push['payload']===['id'=>'op-'.$global['id'],'category'=>'project','kind'=>'error'],
    'Closed-panel global result uses existing WinRT and includes only the safe payload');
check_notice($sync->claimPush(time()-60)===null,'Native delivery is claimed once');
$row = array_column($sync->poll('',false)['items'],null,'id')['op-'.$global['id']];
check_notice($row['route']==='/#/extensions/devstack-tools' && str_contains($row['description'],'Konflikty 1') &&
    str_contains($row['description'],'Gotowe 2'),'Mixed global outcomes produce a single summary');
$manual = [...$event,'id'=>str_repeat('9',32),'outcomes'=>['manual'=>1]];
$removed = [...$event,'id'=>str_repeat('a',32),'outcomes'=>['removed'=>1]];
$empty = [...$global,'id'=>str_repeat('b',32),'outcomes'=>[]];
$invalid = [...$event,'id'=>str_repeat('c',32),'slug'=>'../outside'];
$sync->observeInstructionResults([$manual,$removed,$empty,$invalid]);
$events = array_column($sync->poll('',false)['items'],null,'id');
check_notice($events['op-'.$manual['id']]['kind']==='info' &&
    str_contains($events['op-'.$manual['id']]['description'],'Pozostały dowiązania ręczne'),
    'Manual link retention is not presented as complete detachment');
check_notice($events['op-'.$removed['id']]['route']==='/#/projects' && $events['op-'.$removed['id']]['kind']==='info',
    'Removed registration does not route to a different project under its old slug');
check_notice($events['op-'.$empty['id']]['title']==='Brak projektów do synchronizacji' &&
    !isset($events['op-'.$invalid['id']]),'Empty batch and invalid context are handled');
$sync->mutate('clear');
$sync->observeInstructionResults([$event,$global,$manual,$removed,$empty]);
check_notice($sync->poll('',false)['items']===[],'Clearing history does not re-import instruction results');
echo "Notification journal, durable receipts, deletion, deduplication, presence and delivery policy: OK\n";
