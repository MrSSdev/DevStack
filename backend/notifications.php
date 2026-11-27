<?php
declare(strict_types=1);

// Report the configured local transport, not whether Windows displayed a
// banner. A missing, malformed or unsafe opt-in must never select a fallback.
function devstack_windows_notification_since(): ?int
{
    $path = '/etc/devstack/windows-local-notifications';
    clearstatcache(true, $path);
    $stat = @lstat($path);
    if (!is_array($stat) || ($stat['mode'] & 0170000) !== 0100000 ||
        $stat['uid'] !== 0 || ($stat['mode'] & 0022) !== 0 || $stat['size'] > 512) return null;
    $value = @file_get_contents($path, false, null, 0, 513);
    if (!is_string($value) || rtrim($value, "\r\n") !== '/mnt/c/DevStack/LocalNotifications/DevStack.Notifications.exe') return null;
    return (int)$stat['mtime'];
}

// Single-workstation event journal. It is private to the existing unprivileged
// panel account, never served as a file, and independent of Mailpit/project data.
final class DevStackNotices
{
    public const CATEGORIES = ['mail', 'project', 'installer', 'service', 'settings'];
    public const LIMIT = 200;
    private const CLIENT_LEASE_SECONDS = 90;
    // A document ID is never reused after pagehide. Retain a bounded close
    // marker well beyond ordinary in-flight HTTP request lifetimes.
    private const CLOSED_CLIENT_SECONDS = 86400;
    private PDO $db;

    public function __construct(string $path = '/var/lib/devstack-web/notifications.sqlite3')
    {
        $old = umask(0077);
        try {
            $this->db = new PDO('sqlite:' . $path, null, null, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            ]);
            $this->db->exec('PRAGMA busy_timeout=4000');
            $this->db->exec("CREATE TABLE IF NOT EXISTS notices (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, route TEXT NOT NULL, kind TEXT NOT NULL,
                created INTEGER NOT NULL, updated INTEGER NOT NULL,
                unread INTEGER NOT NULL DEFAULT 1, delivery TEXT NOT NULL DEFAULT 'pending',
                owner TEXT NOT NULL DEFAULT '', direct INTEGER NOT NULL DEFAULT 0,
                push INTEGER NOT NULL DEFAULT 1, revision TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS closed_clients (id TEXT PRIMARY KEY, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_seen (id TEXT PRIMARY KEY, stamp INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS job_tombstones (id TEXT PRIMARY KEY, stamp INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS service_results_seen (id TEXT PRIMARY KEY, stamp INTEGER NOT NULL);");
        } finally {
            umask($old);
        }
    }

    private function execute(string $sql, array $args = []): PDOStatement
    {
        $q = $this->db->prepare($sql);
        $q->execute($args);
        return $q;
    }
    private function atomic(callable $fn): mixed
    {
        $this->db->exec('BEGIN IMMEDIATE');
        try {
            $result = $fn();
            $this->db->exec('COMMIT');
            return $result;
        } catch (Throwable $e) {
            $this->db->exec('ROLLBACK');
            throw $e;
        }
    }
    private function meta(string $key, ?string $default = null): ?string
    {
        $row = $this->execute('SELECT value FROM metadata WHERE key=?', [$key])->fetch();
        return $row ? $row['value'] : $default;
    }
    private function setMeta(string $key, string $value): void
    {
        $this->execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', [$key,$value]);
    }
    public static function client(mixed $value): string
    {
        return is_string($value) && preg_match('~^[a-f0-9]{32}$~D', $value) ? $value : '';
    }
    private static function validId(mixed $value): string
    {
        if (!is_string($value) || !preg_match('~^(?:op-[a-f0-9]{32}|job-[a-f0-9]{32}|mail-[A-Za-z0-9]{22}|batch-[a-f0-9]{32}|service-[a-f0-9]{32})$~D', $value)) {
            throw new InvalidArgumentException('Nieprawidłowe powiadomienie.');
        }
        return $value;
    }
    private static function clean(mixed $value, int $max = 360): string
    {
        if (!is_string($value)) return '';
        // Do not accept control characters or invalid UTF-8 from message data.
        $value = preg_replace('~[\x00-\x1f\x7f]~u', ' ', $value) ?? '';
        return mb_substr(trim($value), 0, $max, 'UTF-8');
    }
    private static function project(mixed $slug): string
    {
        return is_string($slug) && preg_match('~^[a-z][a-z0-9-]{1,39}$~D', $slug) ? $slug : '';
    }
    private function insert(string $id, string $category, string $title, string $description, string $route,
        string $kind, string $owner = '', bool $direct = false, bool $push = true, string $revision = ''): void
    {
        $now = time();
        $this->execute('INSERT OR IGNORE INTO notices
            (id,category,title,description,route,kind,created,updated,owner,direct,push,revision)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', [
                $id,$category,self::clean($title,100),self::clean($description),$route,$kind,$now,$now,
                $owner,(int)$direct,(int)$push,$revision,
            ]);
    }
    private function prune(): void
    {
        $this->execute('DELETE FROM clients WHERE expires<=?', [time()]);
        $this->execute('DELETE FROM closed_clients WHERE expires<=?', [time()]);
        $this->execute("INSERT OR IGNORE INTO job_tombstones SELECT id,? FROM notices WHERE id LIKE 'job-%' AND updated<? AND kind!='loading'",[time(),time()-30*86400]);
        $this->execute('DELETE FROM notices WHERE updated<? AND kind!=?', [time()-30*86400,'loading']);
        $this->execute("INSERT OR IGNORE INTO job_tombstones SELECT id,? FROM (SELECT id FROM notices ORDER BY updated DESC,id DESC LIMIT -1 OFFSET " . self::LIMIT . ") WHERE id LIKE 'job-%'",[time()]);
        $this->execute('DELETE FROM job_tombstones WHERE stamp<? AND id!=?', [time()-90*86400,'job-'.($this->meta('job')??'')]);
        $this->db->exec('DELETE FROM notices WHERE id IN (
            SELECT id FROM notices ORDER BY updated DESC,id DESC LIMIT -1 OFFSET ' . self::LIMIT . ')');
        // Delayed results are still in history; never replay a backlog as push.
        $this->execute("UPDATE notices SET delivery='history' WHERE delivery='pending' AND kind!='loading' AND updated<?", [time()-600]);
    }
    private function touch(string $client): bool
    {
        if ($client === '') return false;
        $now=time();
        // Only the authenticated notification poll renews presence. An older
        // request arriving after leave must not resurrect the closed document.
        return $this->execute('INSERT INTO clients(id,expires)
            SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM closed_clients WHERE id=? AND expires>?)
            ON CONFLICT(id) DO UPDATE SET expires=excluded.expires',
            [$client,$now+self::CLIENT_LEASE_SECONDS,$client,$now])->rowCount()===1;
    }
    private function isClosed(string $client): bool
    {
        return (bool)$this->execute('SELECT 1 FROM closed_clients WHERE id=? AND expires>?',[$client,time()])->fetchColumn();
    }
    private function closeClient(string $client): void
    {
        // Record even a leave that arrives before the first poll.
        $this->execute('INSERT INTO closed_clients(id,expires) VALUES(?,?)
            ON CONFLICT(id) DO UPDATE SET expires=excluded.expires',
            [$client,time()+self::CLOSED_CLIENT_SECONDS]);
        $this->execute('DELETE FROM clients WHERE id=?',[$client]);
    }
    public function leave(string $client): void
    {
        if ($client === '') return;
        $this->atomic(fn()=>$this->closeClient($client));
    }
    private function present(string $client): bool
    {
        return $client !== '' && (bool)$this->execute('SELECT 1 FROM clients WHERE id=? AND expires>?', [$client,time()])->fetchColumn();
    }
    private function anyPresent(): bool
    {
        return (bool)$this->execute('SELECT 1 FROM clients WHERE expires>? LIMIT 1', [time()])->fetchColumn();
    }
    private function row(string $id): array|false
    {
        return $this->execute('SELECT * FROM notices WHERE id=?', [$id])->fetch();
    }
    private static function publicRow(array $row): array
    {
        return ['id'=>$row['id'],'category'=>$row['category'],'title'=>$row['title'],
            'description'=>$row['description'],'route'=>$row['route'],'kind'=>$row['kind'],
            'created'=>(int)$row['created'],'updated'=>(int)$row['updated'],'unread'=>(bool)$row['unread']];
    }

    // Operation results are generated at the authenticated API boundary, not
    // supplied as arbitrary messages by a browser. Never store raw errors,
    // passwords, configuration, command output, verification URLs or bodies.
    public function operation(string $path, array $body, array $result, string $owner, bool $ok = true): ?string
    {
        $slug = self::project($body['slug'] ?? null);
        $category = 'project'; $route = '/#/projects'; $description = $slug;
        $kind = $ok ? 'success' : 'error'; $push = true;
        if ($path === '/api/projects' || preg_match('~^/api/engines/[a-z][a-z0-9-]{1,39}$~D', $path)) {
            $isEngine = str_starts_with($path, '/api/engines/') || ($body['engine'] ?? 'none') !== 'none';
            $job = $result['job_id'] ?? null;
            if ($ok && is_string($job) && preg_match('~^[a-f0-9]{32}$~D', $job)) {
                return $this->atomic(function () use ($job,$slug,$owner) {
                    $id = 'job-' . $job;
                    if ($this->execute('SELECT 1 FROM job_tombstones WHERE id=?',[$id])->fetchColumn()) return $id;
                    $this->insert($id,'installer',$slug!==''?'Instalacja projektu':'Instalator',
                        $slug,'/#/projects','loading',$owner,false,true,'accepted');
                    if ($owner!=='') $this->execute('UPDATE notices SET owner=? WHERE id=? AND kind=?',[$owner,$id,'loading']);
                    $this->setMeta('job', $job);
                    $this->prune();
                    return $id;
                });
            }
            $title = $isEngine ? 'Operacja instalatora' : 'Tworzenie projektu';
            if (!$isEngine && $ok) $title = 'Projekt utworzony';
            if ($isEngine) $category='installer';
        } elseif ($path === '/api/projects/delete') {
            $title = $ok ? 'Projekt usunięty' : 'Usuwanie projektu';
        } elseif (in_array($path, ['/api/config','/api/config/full'], true)) {
            $title = $ok ? 'Zapisano konfigurację Nginx' : 'Zapis konfiguracji Nginx';
        } elseif ($path === '/api/projects/test-db') {
            $title = $ok ? 'Sprawdzono połączenie z bazą' : 'Test połączenia z bazą';
            $push = false;
        } elseif ($path === '/api/services/control') {
            $category = 'service'; $route = '/#/dashboard';
            $service = $body['service'] ?? null;
            $action = $body['action'] ?? null;
            if (!is_string($service) || !preg_match('~^[a-z][a-z0-9-]{0,31}$~D',$service) ||
                !in_array($action,['start','stop','restart'],true)) return null;
            $description = $service . ' — ' . ['start'=>'uruchomienie','stop'=>'zatrzymanie','restart'=>'restart'][$action];
            $title = $ok ? 'Polecenie usługi przyjęte' : 'Sterowanie usługą';
            if ($ok) $kind='info'; // Queue acknowledgement, not a verified systemd result.
        } elseif ($path === '/api/services/autostart') {
            $service = $body['service'] ?? null;
            $enabled = $body['enabled'] ?? null;
            if (!is_string($service) || !preg_match('~^[a-z][a-z0-9-]{0,31}$~D',$service) || !is_bool($enabled)) return null;
            $category='settings'; $route='/#/dashboard'; $push=false;
            $description=$service . ' — ' . ($enabled?'włączenie':'wyłączenie');
            $title=$ok?'Zapisano autostart usługi':'Zapis autostartu usługi';
        } elseif (in_array($path, ['/api/preferences','/api/widget/services'], true)) {
            $category='settings'; $route='/#/settings'; $description='';
            $title=$ok?'Zapisano ustawienia':'Zapis ustawień'; $push=false;
        } elseif (in_array($path,['/api/mail/delete','/api/mail/delete-many','/api/mail/clear'],true)) {
            $category='mail'; $route='/#/mail'; $description='';
            $title=$ok?($path==='/api/mail/clear'?'Opróżniono skrzynkę':'Usunięto wiadomości'):'Usuwanie wiadomości';
            $push=false;
        } else return null;
        if (!$ok) $description .= ($description!==''?' · ':'') . 'Operacja nie powiodła się.';
        elseif (in_array($path,['/api/projects','/api/projects/delete','/api/config','/api/config/full','/api/projects/test-db'],true)) {
            // These fixed daemon methods return a public operation message.
            $message=self::clean($result['message']??'',220);
            if ($message!=='') $description .= ($description!==''?' · ':'') . $message;
        }
        if ($slug!=='' && $path!=='/api/projects/delete') $route='/#/project/'.$slug;
        return $this->atomic(function () use ($category,$title,$description,$route,$kind,$owner,$push) {
            $id='op-'.bin2hex(random_bytes(16));
            $this->insert($id,$category,$title,$description,$route,$kind,$owner,true,$push);
            $this->prune();
            return $id;
        });
    }

    private function observeJobInner(?array $job): ?string
    {
        $key=$job['id']??null;
        if (!is_string($key)||!preg_match('~^[a-f0-9]{32}$~D',$key)) return null;
        $state=$job['state']??'';
        if (!in_array($state,['preparing','scaffolding','configuring','migrating','checking','installing','updating','succeeded','failed'],true)) return null;
        $id='job-'.$key; $last=$this->meta('job'); $row=$this->row($id);
        $terminal=in_array($state,['succeeded','failed'],true);
        $slug=self::project($job['slug']??null);
        $title=$slug!==''?'Instalacja projektu':'Instalator';
        $description=($slug!==''?$slug.' · ':'').self::clean($job['message']??'',300);
        $route=$slug!==''?'/#/project/'.$slug:'/#/settings';
        $kind=$terminal?($state==='succeeded'?'success':'error'):'loading';
        if ($this->execute('SELECT 1 FROM job_tombstones WHERE id=?',[$id])->fetchColumn()) return $id;
        if ($last!==$key) {
            $this->setMeta('job',$key);
            // First activation must not announce the old, already completed job.
            if (!$row && ($last!==null||!$terminal)) $this->insert($id,'installer',$title,$description,$route,$kind,'',false,true,$state);
            $row=$this->row($id);
        }
        // Deletion deliberately removes the row but retains the source cursor.
        // Neither a poll nor completion of a cleared job can recreate it.
        if (!$row) return $id;
        if ($row['revision']!==$state||$row['description']!==$description) {
            $delivery=$terminal&&$row['kind']==='loading'?'pending':$row['delivery'];
            $this->execute('UPDATE notices SET title=?,description=?,route=?,kind=?,revision=?,updated=?,unread=1,delivery=? WHERE id=?',
                [$title,$description,$route,$kind,$state,time(),$delivery,$id]);
        }
        return $id;
    }
    public function observeJob(?array $job, string $client = ''): ?array
    {
        return $this->atomic(function () use ($job,$client) {
            $id=$this->observeJobInner($job);
            if ($id===null) return null;
            $row=$this->row($id); $show=false;
            if ($client!=='' && !$this->isClosed($client) && $row && !in_array($row['delivery'],['push','history'],true)) {
                $owner=$row['owner'];
                // A live initiating/owning tab keeps the installer toast.
                // Only one tab can claim an unowned running or pending job.
                if ($owner===$client || (!$this->present($owner)&&($row['kind']==='loading'||$row['delivery']==='pending'))) {
                    $show=true;
                    $delivery=$row['kind']==='loading'?'pending':'toast';
                    $this->execute('UPDATE notices SET owner=?,delivery=? WHERE id=?',[$client,$delivery,$id]);
                }
            }
            $this->prune();
            return ['id'=>substr($id,4),'show'=>$show];
        });
    }

    // Poll only the fixed local Mailpit listing. Its Created field is the
    // receipt time, not the untrusted message Date header. The cursor and IDs
    // are separate from history, so clearing history cannot replay old mail.
    public function observeMail(array $messages): void
    {
        $this->atomic(function () use ($messages) {
            $stored=$this->meta('mail_cursor');
            $cursor=$stored===null?time():(int)$stored;
            $next=$cursor; $new=[];
            foreach (array_slice($messages,0,200) as $m) {
                if (!is_array($m)||!is_string($m['ID']??null)||!preg_match('~^[A-Za-z0-9]{22}$~D',$m['ID'])) continue;
                $stamp=is_string($m['Created']??null)?strtotime($m['Created']):false;
                if ($stamp===false||$stamp<0||$stamp>time()+60||$stamp<$cursor-3600) continue;
                $next=max($next,$stamp);
                $id=$m['ID'];
                $q=$this->execute('INSERT OR IGNORE INTO mail_seen(id,stamp) VALUES(?,?)',[$id,$stamp]);
                if ($stored!==null&&$stamp>=$cursor&&$q->rowCount()===1) $new[]=$m;
            }
            $this->setMeta('mail_cursor',(string)$next);
            $this->execute('DELETE FROM mail_seen WHERE stamp<?',[$next-3600]);
            $this->db->exec('DELETE FROM mail_seen WHERE id IN (SELECT id FROM mail_seen ORDER BY stamp DESC LIMIT -1 OFFSET 20000)');
            if (count($new)===1) {
                $m=$new[0]; $id=$m['ID'];
                $this->insert('mail-'.$id,'mail','Nowa wiadomość e-mail',
                    self::clean($m['Subject']??'(bez tematu)',180),'/#/mail/'.$id,'info');
            } elseif (count($new)>1) {
                $this->insert('batch-'.bin2hex(random_bytes(16)),'mail','Nowe wiadomości e-mail',
                    count($new).' nowych wiadomości w Mailpit.','/#/mail','info');
            }
            $this->prune();
        });
    }

    public function observeServices(array $services, array $results = []): void
    {
        $this->atomic(function () use ($services,$results) {
            // A completed systemctl action has its own stable identity and
            // survives panel disconnection. The daemon keeps a bounded
            // receipt journal; a deleted history item must never be replayed.
            $now=time();
            $completed=[];
            foreach (array_slice($results,0,200) as $result) {
                if (!is_array($result)) continue;
                $key=$result['id']??null; $service=$result['service']??null;
                $action=$result['action']??null; $outcome=$result['outcome']??null;
                $state=$result['state']??null; $stamp=$result['stamp']??null;
                if (!is_string($key)||!preg_match('~^[a-f0-9]{32}$~D',$key)||
                    !is_string($service)||!preg_match('~^[a-z][a-z0-9-]{0,31}$~D',$service)||
                    !in_array($action,['start','stop','restart'],true)||
                    !in_array($outcome,['success','error'],true)||
                    !in_array($state,['active','inactive','failed','activating','deactivating','reloading','refreshing','unknown'],true)||
                    !is_int($stamp)||$stamp<$now-30*86400||$stamp>$now+5) continue;
                $id='service-'.$key;
                if ($this->execute('INSERT OR IGNORE INTO service_results_seen(id,stamp) VALUES(?,?)',[$id,$stamp])->rowCount()!==1) continue;
                // Defense in depth: even a malformed success receipt cannot
                // present a stopped service as a successful start/restart.
                $success=$outcome==='success'&&$state===($action==='stop'?'inactive':'active');
                $title=$success?['start'=>'Usługa uruchomiona','restart'=>'Usługa uruchomiona ponownie','stop'=>'Usługa zatrzymana'][$action]:
                    'Nie potwierdzono wykonania polecenia usługi';
                $description=self::clean($result['name']??$service,60).' — '.['start'=>'uruchomienie','restart'=>'restart','stop'=>'zatrzymanie'][$action];
                $this->insert($id,'service',$title,$description,'/#/dashboard',$success?'success':'error');
                $this->execute('UPDATE notices SET created=?,updated=? WHERE id=?',[$stamp,$stamp,$id]);
                if ($stamp>=$now-120) $completed[$service]=$state;
            }
            $this->execute('DELETE FROM service_results_seen WHERE stamp<?',[$now-31*86400]);
            $this->db->exec('DELETE FROM service_results_seen WHERE id IN (SELECT id FROM service_results_seen ORDER BY stamp DESC,id DESC LIMIT -1 OFFSET 20000)');
            $previous=json_decode($this->meta('service_states','{}'),true);
            $next=[];
            foreach (array_slice($services,0,24) as $service) {
                $id=$service['id']??null; $state=$service['state']??null;
                if (!is_string($id)||!preg_match('~^[a-z][a-z0-9-]{0,31}$~D',$id)||!is_string($state)) continue;
                $next[$id]=$state;
                // The same observed failure/recovery was already described
                // by a new terminal receipt. Spontaneous transitions remain.
                if (($completed[$id]??null)===$state) continue;
                if (isset($previous[$id])&&$previous[$id]!==$state&&$state==='failed') {
                    $this->insert('service-'.bin2hex(random_bytes(16)),'service','Awaria usługi',
                        self::clean($service['name']??$id,60),'/#/dashboard','error');
                }
                if (isset($previous[$id])&&$previous[$id]==='failed'&&$state==='active') {
                    $this->insert('service-'.bin2hex(random_bytes(16)),'service','Usługa ponownie aktywna',
                        self::clean($service['name']??$id,60),'/#/dashboard','success');
                }
            }
            $this->setMeta('service_states',json_encode($next,JSON_THROW_ON_ERROR));
            $this->prune();
        });
    }
    public function acknowledge(string $id, string $client): void
    {
        $id=self::validId($id);
        if ($client!=='') $this->execute("UPDATE notices SET delivery='toast' WHERE id=? AND owner=? AND direct=1 AND delivery='pending'",[$id,$client]);
    }

    public function poll(string $client, bool $claim = true, mixed $opened = null, mixed $retired = []): array
    {
        // Only bounded, explicitly retired lifetimes may be replayed by a
        // restored/reloaded document. A duplicate tab never retires a live ID.
        if (!is_array($retired)||!array_is_list($retired)||count($retired)>16) {
            throw new InvalidArgumentException('Nieprawidłowa lista zamkniętych klientów.');
        }
        foreach ($retired as $old) {
            if (self::client($old)===''||$old===$client) {
                throw new InvalidArgumentException('Nieprawidłowy zamknięty klient.');
            }
        }
        return $this->atomic(function () use ($client,$claim,$opened,$retired) {
            $this->prune();
            $present=$this->touch($client);
            if ($present) foreach (array_unique($retired) as $old) $this->closeClient($old);
            if ($opened!==null) {
                // The clicked journal event alone is read before collecting
                // toasts and returning the authoritative history snapshot.
                // A stale click after deletion is a no-op, not an insert.
                $id=self::validId($opened);
                $this->execute("UPDATE notices SET unread=0,
                    delivery=CASE WHEN delivery='pending' AND kind!='loading' THEN 'history' ELSE delivery END
                    WHERE id=?",[$id]);
            }
            $deliver=[];
            if ($present&&$claim) {
                $rows=$this->execute("SELECT * FROM notices WHERE delivery='pending' AND kind!='loading' AND id NOT LIKE 'job-%' ORDER BY created,id LIMIT 40")->fetchAll();
                foreach ($rows as $row) {
                    if (count($deliver)>=3) break;
                    // The initiating tab already has the operation toast; wait
                    // for its explicit acknowledgement. Other tabs do not repeat it.
                    if ($row['direct'] && ($row['owner']===$client||$this->present($row['owner'])||$row['created']>time()-3)) continue;
                    $this->execute("UPDATE notices SET delivery='toast',owner=? WHERE id=?",[$client,$row['id']]);
                    $deliver[]=self::publicRow($row);
                }
            }
            return ['items'=>array_map(self::publicRow(...),$this->execute('SELECT * FROM notices ORDER BY updated DESC,id DESC LIMIT '.self::LIMIT)->fetchAll()),
                'toast'=>$deliver,'push_categories'=>$this->categories(),'windows_enabled'=>devstack_windows_notification_since()!==null,'subscribed'=>false];
        });
    }
    public function mutate(string $action, mixed $id = null): void
    {
        $this->atomic(function () use ($action,$id) {
            if ($action==='clear') {
                $this->execute("INSERT OR IGNORE INTO job_tombstones SELECT id,? FROM notices WHERE id LIKE 'job-%'",[time()]);
                $this->db->exec('DELETE FROM notices');
            }
            elseif ($action==='read-all') $this->db->exec('UPDATE notices SET unread=0');
            elseif ($action==='delete') {
                $id=self::validId($id);
                if (str_starts_with($id,'job-')) $this->execute('INSERT OR IGNORE INTO job_tombstones SELECT id,? FROM notices WHERE id=?',[time(),$id]);
                $this->execute('DELETE FROM notices WHERE id=?',[$id]);
            }
            elseif ($action==='read') $this->execute('UPDATE notices SET unread=0 WHERE id=?',[self::validId($id)]);
            else throw new InvalidArgumentException('Nieznana operacja powiadomienia.');
        });
    }

    public function categories(): array
    {
        $value=json_decode($this->meta('push_categories','["mail","project","installer","service"]'),true);
        return is_array($value)?array_values(array_intersect(self::CATEGORIES,$value)):[];
    }
    public function setCategories(mixed $value): void
    {
        if (!is_array($value)||!array_is_list($value)||count($value)>count(self::CATEGORIES)||
            array_filter($value,static fn($v)=>!is_string($v)||!in_array($v,self::CATEGORIES,true))) {
            throw new InvalidArgumentException('Nieprawidłowe kategorie powiadomień.');
        }
        $this->setMeta('push_categories',json_encode(array_values(array_unique($value)),JSON_THROW_ON_ERROR));
    }
    // Legacy endpoint cleanup remains idempotent; no new subscription can be created.
    public function unsubscribe(): void
    {
        if ($this->meta('subscription')!==null) $this->execute("DELETE FROM metadata WHERE key='subscription'");
    }

    /** Reserve only local WinRT delivery; keep the historic journal field names. */
    public function claimPush(?int $windowsEnabledSince = null): ?array
    {
        return $this->atomic(function () use ($windowsEnabledSince) {
            $this->prune();
            // Retire only the obsolete subscription record, even with a live
            // panel or without opt-in. Never clear categories or history.
            $this->unsubscribe();
            if ($windowsEnabledSince===null || $this->anyPresent()) return null;
            // Automatic mail events have no initiating operation toast to
            // acknowledge. Once no client is present, dispatch in the same
            // collector cycle instead of waiting for the next timer tick.
            // Keep the existing grace period for all other events.
            foreach ($this->execute("SELECT * FROM notices WHERE delivery='pending' AND kind!='loading' AND push=1
                AND updated>=? AND ((category='mail' AND direct=0) OR updated<=?) ORDER BY updated,id LIMIT 200",
                [$windowsEnabledSince,time()-2])->fetchAll() as $row) {
                if (!in_array($row['category'],$this->categories(),true)) continue;
                // Claim once before the network call. Unknown delivery is not
                // retried (avoid duplicate system notifications).
                $this->execute("UPDATE notices SET delivery='push' WHERE id=?",[$row['id']]);
                return ['id'=>$row['id'],'journal_age_s'=>max(0,time()-(int)$row['updated']),
                    'subscription'=>null,
                    // Generic text only. No subject, recipient, hostname,
                    // project name, secret or original daemon output in push.
                    'payload'=>['id'=>$row['id'],'category'=>$row['category'],'kind'=>$row['kind']]];
            }
            return null;
        });
    }
    public function pushFailed(string $id, bool $expired): void
    {
        $this->atomic(function () use ($id,$expired) {
            $this->execute("UPDATE notices SET delivery='history' WHERE id=? AND delivery='push'",[$id]);
            if ($expired) $this->unsubscribe();
        });
    }
}

function devstack_notice_operation(string $path, array $body, array $result, bool $ok = true): array
{
    try {
        $store=new DevStackNotices();
        $id=$store->operation($path,$body,$result,DevStackNotices::client($_SERVER['HTTP_X_DEVSTACK_CLIENT']??null),$ok);
        if ($id!==null) $result['notification_id']=$id;
    } catch (Throwable) {
        // A notification failure must NEVER turn a completed mutation into an
        // apparent failed operation that invites a dangerous retry.
        error_log('DevStack notification journal unavailable.');
    }
    return $result;
}
