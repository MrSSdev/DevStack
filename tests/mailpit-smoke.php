<?php
declare(strict_types=1);
// Run only with the isolated fixture mailbox created by the CI workflow.
require_once __DIR__ . '/../backend/security.php';

function smoke_check(bool $condition, string $description): void
{
    if (!$condition) throw new RuntimeException($description);
}
$page = [];
for ($attempt = 0; $attempt < 20; $attempt++) {
    $page = devstack_mailpit_request('list', []);
    if (($page['total'] ?? 0) === 2) break;
    usleep(250000);
}
smoke_check(($page['total'] ?? 0) === 2, 'Two fixture messages expected');
$list = devstack_mailpit_request('list', ['query' => 'DevStack SMTP fixture']);
smoke_check(count($list['messages'] ?? []) === 1, 'Search one fixture');
$summary = $list['messages'][0];
$id = $summary['ID'];
smoke_check($summary['Subject'] === 'DevStack SMTP fixture', 'SMTP capture subject');
$message = devstack_mailpit_request('message', ['id' => $id]);
smoke_check($message['ID'] === $id, 'Message ID');
smoke_check(str_contains($message['Text'], 'Plain fixture body'), 'Plain text body');
smoke_check(str_contains($message['HTML'], 'HTML fixture body'), 'HTML body');
smoke_check(count($message['Attachments']) === 1, 'Attachment metadata');
$part = $message['Attachments'][0]['PartID'];
smoke_check(devstack_mailpit_request('attachment', ['id' => $id, 'part' => $part]) === "DevStack fixture attachment\n", 'Exact attachment bytes');
smoke_check(devstack_mailpit_request('delete', ['id' => $id]) === ['deleted' => true], 'Delete exactly one fixture');
for ($attempt = 0; $attempt < 20; $attempt++) {
    $page = devstack_mailpit_request('list', []);
    if (($page['total'] ?? 0) === 1) break;
    usleep(250000);
}
smoke_check(($page['total'] ?? 0) === 1 && $page['messages'][0]['Subject'] === 'DevStack PHP fixture', 'Other message retained');
echo "Mailpit list, search, text, HTML, attachment, single delete and PHP capture: OK\n";
