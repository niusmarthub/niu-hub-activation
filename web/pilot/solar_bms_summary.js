(function () {
    const windowSeconds = 5 * 60;
    const maximumAge = 17 * 60;
    const samples = [];
    let lastBank;

    function number(value) {
        if (value === null || value === undefined || String(value).trim() === '') return null;
        const result = Number(value);
        return Number.isFinite(result) ? result : null;
    }

    function metric(battery, key, now) {
        const value = number(battery.metrics[key]);
        const at = number(battery.measured_at?.[key]);
        if (value === null || at === null || at > now + 5 || now - at > maximumAge) return null;
        return { value, at };
    }

    function averagePower(now) {
        const start = now - windowSeconds;
        while (samples.length > 1 && samples[1].at <= start) samples.shift();
        let energy = 0;
        let duration = 0;
        for (const [index, sample] of samples.entries()) {
            const seconds = Math.max(0, (samples[index + 1]?.at ?? now) - Math.max(start, sample.at));
            energy += seconds * sample.watts;
            duration += seconds;
        }
        return duration ? energy / duration : samples.at(-1).watts;
    }

    window.niuRenderBmsSummary = function (host, layout, readings) {
        const powerField = host.querySelector('[data-bms-bank-power]');
        const runtimeField = host.querySelector('[data-bms-bank-runtime]');
        if (!powerField || !runtimeField) return;
        const french = host.dataset.lang === 'fr';
        const format = new Intl.NumberFormat(french ? 'fr-CA' : 'en-CA', { maximumFractionDigits: 1 });
        const unavailable = french ? 'Indisponible' : 'Unavailable';
        const now = Math.floor(Date.now() / 1000);
        const id = JSON.stringify(layout.ids);
        if (id !== lastBank) {
            samples.length = 0;
            lastBank = id;
        }

        const matched = new Map(readings.map(battery => [battery.id, battery]));
        const batteries = (layout.ids ?? []).map(id => matched.get(id));
        const voltages = batteries.map(battery => battery && metric(battery, 'voltage', now));
        const currents = batteries.map(battery => battery && metric(battery, 'current', now));
        powerField.textContent = '—';
        runtimeField.textContent = unavailable;

        if (!layout.series || !batteries.length || voltages.some(v => !v || v.value <= 0) || currents.some(i => !i)) {
            samples.length = 0;
            return;
        }
        const watts = -currents.reduce((sum, current, index) => sum + current.value * voltages[index].value, 0);
        powerField.textContent = format.format(Math.max(0, watts)) + ' W';
        if (watts <= 0 || currents.every(current => Math.abs(current.value) <= 0.1)) {
            samples.length = 0;
            runtimeField.textContent = watts < 0 && currents.some(current => Math.abs(current.value) > 0.1)
                ? (french ? 'En charge' : 'Charging')
                : (french ? 'En veille' : 'Standby');
            return;
        }

        const at = Math.min(now, ...voltages.map(v => v.at), ...currents.map(i => i.at));
        if (samples.length && at - samples.at(-1).at > windowSeconds) samples.length = 0;
        if (!samples.length || at > samples.at(-1).at) samples.push({ at, watts });
        const remainingAh = batteries.map((battery, index) => {
            const energy = metric(battery, 'stored_energy', now)?.value;
            if (energy != null && energy >= 0) return energy / voltages[index].value;
            const capacity = metric(battery, 'design_capacity', now)?.value;
            const charge = metric(battery, 'charge_pct', now)?.value;
            return capacity > 0 && charge >= 0 && charge <= 100 && charge != null ? capacity * charge / 100 : null;
        });
        if (remainingAh.includes(null)) return;

        let remainingWh = 0;
        for (let start = 0; start < batteries.length; start += layout.series) {
            const end = start + layout.series;
            const bankVoltage = voltages.slice(start, end).reduce((sum, v) => sum + v.value, 0);
            remainingWh += Math.min(...remainingAh.slice(start, end)) * bankVoltage;
        }
        runtimeField.textContent = format.format(remainingWh / averagePower(now)) + ' h';
    };
})();
