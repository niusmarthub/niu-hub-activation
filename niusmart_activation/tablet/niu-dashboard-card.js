import {entity, escape, state, tablet} from "./shared.js";

const STYLE = `<style>
        niu-tablet-dashboard-card {
          display:block;
          width:100%;
        }

        .niu-dashboard {
          padding:6px;
          font-family:var(--paper-font-body1_-_font-family);
          box-sizing:border-box;
        }

        .grid {
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:8px;
          margin-bottom:8px;
        }

        .card {
          border:1px solid var(--divider-color);
          border-radius:8px;
          padding:10px;
          box-sizing:border-box;
          background:var(--ha-card-background, var(--card-background-color));
        }

        .title {
          font-size:16px;
          font-weight:800;
          margin-bottom:8px;
        }

        .values {
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:8px;
        }

        .value {
          border:1px solid var(--divider-color);
          border-radius:6px;
          padding:8px;
        }

        .label {
          font-size:10px;
          opacity:.65;
          text-transform:uppercase;
          margin-bottom:3px;
        }

        .big {
          font-size:18px;
          font-weight:800;
        }

        .row {
          display:flex;
          justify-content:space-between;
          gap:10px;
          padding:4px 0;
          border-bottom:1px solid var(--divider-color);
          font-size:12px;
        }

        .row:last-child {
          border-bottom:0;
        }

        .normal {
          margin-top:8px;
          padding:6px;
          text-align:center;
          border-radius:5px;
          background:#124d35;
          color:#7fffb4;
          font-size:11px;
          font-weight:800;
        }

        .fail {
          margin-top:8px;
          padding:6px;
          text-align:center;
          border-radius:5px;
          background:#641f25;
          color:#fff;
          font-size:11px;
          font-weight:800;
        }

        .energy {
          display:grid;
          grid-template-columns:repeat(3,1fr);
          gap:6px;
        }

        @media (max-width:700px) {
          .grid {
            grid-template-columns:1fr;
          }

          .card {
            padding:7px;
          }

          .title {
            font-size:14px;
          }
        }
      </style>`;

class NiuDashboardCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
    this._lang = String(this.config.lang || this.config.language || 'fr').toLowerCase() === 'en' ? 'en' : 'fr';
  }

  _t(fr, en) {
    return this._lang === 'en' ? en : fr;
  }

  _displayState(value) {
    if (this._lang !== 'en') return value;
    const map = {
      'Ventilation': 'Ventilation',
      'Climatisation': 'Cooling',
      'Chauffage': 'Heating',
      'Déshumidification': 'Dehumidification',
      'Éteint': 'Off',
      'Vitesse 1': 'Speed 1',
      'Vitesse 2': 'Speed 2',
      'Vitesse 3': 'Speed 3',
      'Auto': 'Auto',
      'ON': 'ON',
      'OFF': 'OFF'
    };
    return map[value] || value;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  _state(id) {
    return this._hass?.states?.[id]?.state ?? '--';
  }

  _unit(id) {
    return this._hass?.states?.[id]?.attributes?.unit_of_measurement ?? '';
  }

  _render() {
    if (!this._hass) return;
    const data = tablet(this._hass);
    if (!data) {
      this.textContent = this._t('Configuration NiuSmart en attente', 'Waiting for NiuSmart configuration');
      return;
    }
    const {options, climate, solar} = data;
    const locale = this._lang === 'en' ? 'en-CA' : 'fr-CA';
    const format = (value, unit, decimals = 1) => Number.isFinite(value)
      ? value.toLocaleString(locale, {maximumFractionDigits: decimals}) + ' ' + unit : '—';
    const sensor = id => this._hass.states[id];
    const raw = id => {
      const source = String(sensor(id)?.state ?? '').trim();
      return source === '' ? NaN : Number(source.replace(',', '.'));
    };
    const value = (id, unit) => format(raw(id), sensor(id)?.attributes?.unit_of_measurement || unit);
    const mapped = type => entity(data, type);
    const row = (label, reading) => '<div class="row"><span>' + escape(label)
      + '</span><strong>' + escape(reading) + '</strong></div>';
    const card = (title, rows, grid = '') => '<div class="card"><div class="title">' + escape(title)
      + '</div><div class="' + grid + '">' + rows.join('') + '</div></div>';
    const metric = (label, reading) => '<div class="value"><div class="label">' + escape(label)
      + '</div><strong>' + escape(reading) + '</strong></div>';
    const energy = (type, unit, legacyDivisor, decimals) => {
      const id = mapped(type);
      const sourceUnit = sensor(id)?.attributes?.unit_of_measurement;
      const conversions = {
        kWh: {kWh: 1, Wh: 1000, MWh: 0.001},
        kW: {kW: 1, W: 1000, mW: 1000000},
        A: {A: 1, mA: 1000}, V: {V: 1, mV: 1000}, Hz: {Hz: 1, kHz: 0.001}
      };
      const hubMeter = /^sensor\.(?:[a-z0-9_]+_)?energy_/.test(id ?? '');
      const divisor = conversions[unit][sourceUnit]
        ?? (hubMeter && ['kWh', 'V', 'Hz'].includes(unit) ? 1 : legacyDivisor);
      return {numeric: raw(id) / divisor, unit, decimals};
    };
    const cards = [];
    if (mapped('temperature') || mapped('humidity')) {
      const id = mapped('temperature');
      const unit = sensor(id)?.attributes?.unit_of_measurement;
      const temperature = raw(id);
      const legacy = !unit && /^sensor\.cbox_/.test(id ?? '');
      cards.push(card(this._t('🌡️ TEMPÉRATURE / HUMIDITÉ', '🌡️ TEMPERATURE / HUMIDITY'), [
        metric(this._t('Température', 'Temperature'), format(legacy && temperature > 100 ? temperature / 10 : temperature, unit || '°C')),
        metric(this._t('Humidité', 'Humidity'), value(mapped('humidity'), '%'))
      ], 'values'));
    }
    if (options.has_power_monitoring === 1) {
      const power = energy('power_live', 'kW', 1000, 4);
      const voltage = energy('voltage_live', 'V', 10, 1);
      const current = energy('current_live', 'A', 1000, 3);
      const estimated = !mapped('current_live') && voltage.numeric > 0 && Number.isFinite(power.numeric);
      if (estimated) current.numeric = power.numeric * 1000 / voltage.numeric;
      const reading = item => format(item.numeric, item.unit, item.decimals);
      cards.push(card(this._t('⚡ ÉNERGIE', '⚡ ENERGY'), [
        metric('Total', reading(energy('energy_total', 'kWh', 100, 2))),
        metric(this._t('Ligne 1', 'Line 1'), reading(energy('energy_line_1', 'kWh', 100, 2))),
        metric(this._t('Ligne 2', 'Line 2'), reading(energy('energy_line_2', 'kWh', 100, 2))),
        metric(this._t('Puissance', 'Power'), reading(power)),
        metric(estimated ? this._t('Ampérage estimé', 'Estimated current') : this._t('Ampérage', 'Current'), reading(current)),
        metric(this._t('Tension', 'Voltage'), reading(voltage)),
        metric(this._t('Fréquence', 'Frequency'), reading(energy('frequency_live', 'Hz', 1, 2))),
        metric('Amp. L1', reading(energy('current_line_1', 'A', 1000, 3))),
        metric('Amp. L2', reading(energy('current_line_2', 'A', 1000, 3)))
      ], 'energy'));
    }
    if (options.has_fresh_water === 1 || options.has_waste_water === 1) {
      const rows = [];
      if (options.has_fresh_water === 1) rows.push(row(this._t('Eau propre', 'Fresh water'), value(mapped('fresh_water'), '%')));
      if (options.has_waste_water === 1) rows.push(row(this._t('Eaux usées', 'Waste water'), value(mapped('waste_water'), '%')));
      cards.push(card(this._t('💧 RÉSERVOIRS', '💧 TANKS'), rows));
    }
    if (options.has_solar_panels === 1) {
      const load = state(this._hass, solar.load_state);
      cards.push(card(this._t('☀️ SOLAIRE', '☀️ SOLAR'), [
        row(this._t('Tension PV', 'PV voltage'), value(solar.pv_voltage, 'V')),
        row(this._t('Puissance PV', 'PV power'), value(solar.pv_power, 'W')),
        row(this._t('Tension batterie', 'Battery voltage'), value(solar.battery_voltage, 'V')),
        row(this._t('Température batterie', 'Battery temperature'), value(solar.battery_temperature, '°C')),
        row(this._t('Sortie LOAD', 'LOAD output'), ['on', 'off'].includes(load) ? load.toUpperCase() : '—'),
        row(this._t('Tension de sortie', 'Output voltage'), value(solar.load_voltage, 'V')),
        row(this._t('Courant de sortie', 'Output current'), value(solar.load_current, 'A')),
        row(this._t('Puissance de sortie', 'Output power'), value(solar.load_power, 'W'))
      ]));
    }
    for (let n = 1; n <= options.climate_count; n++) {
      const zone = climate[String(n)] || {};
      const read = id => {
        const current = state(this._hass, id);
        return current && !['unknown', 'unavailable'].includes(current) ? this._displayState(current) : '—';
      };
      const failure = state(this._hass, zone.power_fail);
      cards.push(card(this._t('❄️ CLIMATISEUR ', '❄️ AIR CONDITIONER ') + n, [
        row(this._t('État', 'Status'), read(zone.state)),
        row(this._t('Mode', 'Mode'), read(zone.mode)),
        row(this._t('Ventilateur', 'Fan'), read(zone.fan)),
        row(this._t('Consigne chauffage', 'Heating setpoint'), value(zone.heat_setpoint, '°C')),
        row(this._t('Consigne climatisation', 'Cooling setpoint'), value(zone.cool_setpoint, '°C')),
        row(this._t('Alimentation', 'Power supply'), failure === 'on' ? 'POWER FAIL' : failure === 'off' ? 'NORMAL' : '—')
      ]));
    }
    this.innerHTML = STYLE + '<div class="niu-dashboard"><div class="grid">' + cards.join('') + '</div></div>';
  }
}

customElements.define('niu-tablet-dashboard-card', NiuDashboardCard);