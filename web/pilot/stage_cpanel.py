"""Stage NiuSmart updates from copies of the current cPanel files."""
import argparse
from pathlib import Path


def swap(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"Missing or ambiguous source: {before[:100]}")
    return source.replace(before, after)


def cellular_api(source):
    old_guard = "if ($interface !== 'eth0' && !($site === 2 && $interface === 'wwan0'))"
    guard = "if (!in_array($interface, ['eth0', 'wwan0'], true))"
    if guard in source:
        return source
    if old_guard in source:
        return swap(source, old_guard, guard)

    source = swap(source,
        "if (!is_array($body) || ($body['interface'] ?? '') !== 'eth0')",
        "$interface = is_array($body) ? ($body['interface'] ?? '') : '';\n" + guard)
    source = swap(source, 'SELECT sampled_at, tracked_rx_bytes', 'SELECT interface_name, sampled_at, tracked_rx_bytes')
    source = swap(source, "    $deltaRx = $previous &&", """    if ($previous && $previous['interface_name'] === 'wwan0' && $interface === 'eth0') {
        $db->commit();
        reply_usage($control + ['legacy_sample_ignored'=>true]);
    }
    $sameInterface = $previous && $previous['interface_name'] === $interface;
    $deltaRx = $sameInterface &&""")
    source = swap(source, '    $deltaTx = $previous &&', '    $deltaTx = $sameInterface &&')
    source = swap(source, "[$id,$site,'eth0',$rx,$tx,$at]", "[$id,$site,$interface,$rx,$tx,$at]")
    source = swap(source,
        'usage_history (connection_id,site_id,sampled_at,tracked_rx_bytes,tracked_tx_bytes,delta_rx_bytes,delta_tx_bytes,received_at) VALUES (?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP())',
        'usage_history (connection_id,site_id,interface_name,sampled_at,tracked_rx_bytes,tracked_tx_bytes,delta_rx_bytes,delta_tx_bytes,received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, UTC_TIMESTAMP())')
    return swap(source, '[$id,$site,$at,$rx,$tx,$deltaRx,$deltaTx]', '[$id,$site,$interface,$at,$rx,$tx,$deltaRx,$deltaTx]')


def cellular_web(source):
    selection = """ $q = $pdo->prepare("SELECT interface_name, sampled_at FROM niusmart_hub_cellular_usage
    WHERE site_id=? AND interface_name IN ('wwan0','eth0')
    ORDER BY (interface_name='wwan0') DESC, sampled_at DESC LIMIT 1");
 $q->execute([$id]);
 $latest = $q->fetch(PDO::FETCH_ASSOC);
 $interface = $latest['interface_name'] ?? 'eth0';
 $last = $latest['sampled_at'] ?? null;"""
    if selection not in source:
        legacy_selection = " $q=$pdo->prepare('SELECT MAX(sampled_at) FROM niusmart_hub_cellular_usage WHERE site_id=?');$q->execute([$id]);$last=$q->fetchColumn();"
        pilot_selection = """ $interface = $id === 2 ? 'wwan0' : 'eth0';
 $q=$pdo->prepare('SELECT MAX(sampled_at) FROM niusmart_hub_cellular_usage WHERE site_id=? AND interface_name=?');$q->execute([$id,$interface]);$last=$q->fetchColumn();"""
        if pilot_selection in source:
            source = swap(source, pilot_selection, selection)
        else:
            source = swap(source, legacy_selection, selection)
            source = swap(source, 'WHERE site_id=? AND sampled_at>=?', 'WHERE site_id=? AND interface_name=? AND sampled_at>=?')
            source = swap(source, "[$id,$start->getTimestamp()", "[$id,$interface,$start->getTimestamp()")

    if 'niusmart_hub_cellular_monthly_opening' in source:
        guard = "if ($periods['month'] !== null && $interface !== 'wwan0') {"
        if guard not in source:
            source = swap(source, "if ($periods['month'] !== null) {", guard)
    return source


def bms_api(source):
    source = swap(source, 'SELECT e.entity_id, e.friendly_name, s.state',
                  'SELECT e.entity_id, e.friendly_name, s.state,\n                    TIMESTAMPDIFF(SECOND, s.last_update, UTC_TIMESTAMP()) AS age_seconds')
    return swap(source, "$bmsBatteries[$id]['metrics'][$metric] = $row['state'];",
                """$bmsBatteries[$id]['metrics'][$metric] = $row['state'];
            if ($row['age_seconds'] !== null) {
                $bmsBatteries[$id]['measured_at'][$metric] = time() - (int)$row['age_seconds'];
            }""")


def site_page(source):
    return swap(source, '            const snapshot = JSON.stringify(received);',
                """            window.niuRenderBmsSummary?.(solarBmsHost, config, received);
            const snapshot = JSON.stringify(received);""")


def solar_card(source):
    source = swap(source, "        ['load_state', $solarFrench ? 'Sortie LOAD' : 'LOAD output', ''],",
                  """        ['load_state', $solarFrench ? 'Sortie LOAD' : 'LOAD output', ''],
        ['bms_bank_power', $solarFrench ? 'Puissance tirée des batteries' : 'Battery discharge power', 'W'],
        ['bms_bank_runtime', $solarFrench ? 'Autonomie estimée' : 'Estimated runtime', 'h'],""")
    source = swap(source, '                            <dd data-solar-key=',
                  """                            <?php if ($key === 'bms_bank_power' || $key === 'bms_bank_runtime'): ?>
                                <dd class="solar-value" <?= $key === 'bms_bank_power' ? 'data-bms-bank-power' : 'data-bms-bank-runtime' ?>>—</dd>
                            <?php else: ?>
                            <dd data-solar-key=""")
    source = swap(source, '                            </dd>\n                        </div>',
                  '                            </dd>\n                            <?php endif; ?>\n                        </div>')
    return swap(source, '<script src="solar_site_card.js" defer></script>',
                '<script src="solar_site_card.js" defer></script>\n<script src="solar_bms_summary.js" defer></script>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cellular-only", action="store_true", help="Stage only the cellular PHP endpoints")
    parser.add_argument("source", type=Path)
    parser.add_argument("staged", type=Path)
    args = parser.parse_args()
    transforms = {
        "hub_cellular_usage_api.php": cellular_api,
        "hub_eco_web.php": cellular_web,
    }
    if not args.cellular_only:
        transforms.update({
            "site_states_api.php": bms_api,
            "site.php": site_page,
            "solar_site_card.php": solar_card,
        })
    files = {}
    for name, transform in transforms.items():
        try:
            files[name] = transform((args.source / name).read_text())
        except ValueError as error:
            parser.error(f"{name}: {error}")
    args.staged.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (args.staged / name).write_text(content)
    if not args.cellular_only:
        javascript = Path(__file__).with_name("solar_bms_summary.js")
        (args.staged / javascript.name).write_bytes(javascript.read_bytes())


if __name__ == "__main__":
    main()
