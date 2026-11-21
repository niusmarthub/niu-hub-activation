<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function tabletReply(int $status, array $data): never
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
    header('Allow: GET');
    tabletReply(405, ['success' => false]);
}

$key = trim((string)($_SERVER['HTTP_X_API_KEY'] ?? ''));
if ($key === '') {
    tabletReply(401, ['success' => false]);
}

require '/home/o1h7cnyp7fbo/niusmart_private/config.php';

try {
    $connection = $pdo->prepare("
        SELECT dc.site_id, dc.device_code, dc.active AS device_active, s.active AS site_active
        FROM device_connections dc
        JOIN sites s ON s.id = dc.site_id
        WHERE dc.api_key = ?
        LIMIT 1
    ");
    $connection->execute([$key]);
    $hub = $connection->fetch(PDO::FETCH_ASSOC);
    if (!$hub || !hash_equals((string)$hub['device_code'], (string)($_SERVER['HTTP_X_NIU_DEVICE'] ?? ''))) {
        tabletReply(401, ['success' => false]);
    }
    if ((int)$hub['device_active'] !== 1 || (int)$hub['site_active'] !== 1) {
        tabletReply(403, ['success' => false]);
    }

    $query = $pdo->prepare("
        SELECT function_type, function_number, custom_name, entity_id
        FROM device_mappings
        WHERE site_id = ? AND active = 1
          AND function_type IN (
            'toilet', 'toilet_lock', 'toilet_door', 'door', 'block_control',
            'block_closed_sensor', 'exterior_lighting', 'water_control',
            'water_open_confirm', 'water_closed_confirm', 'temperature', 'humidity',
            'fresh_water', 'waste_water', 'energy_total', 'energy_line_1', 'energy_line_2',
            'power_live', 'current_live', 'voltage_live', 'frequency_live',
            'current_line_1', 'current_line_2'
          )
        ORDER BY function_number, id
    ");
    $query->execute([(int)$hub['site_id']]);
    tabletReply(200, [
        'success' => true,
        'site_id' => (int)$hub['site_id'],
        'device_code' => $hub['device_code'],
        'mappings' => $query->fetchAll(PDO::FETCH_ASSOC),
    ]);
} catch (PDOException $exception) {
    tabletReply(500, ['success' => false]);
}