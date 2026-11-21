import {escape} from "./shared.js";

class NiuHubCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
    this._lang = String(this.config.lang || this.config.language || 'fr').toLowerCase() === 'en' ? 'en' : 'fr';
  }

  _t(fr, en) {
    return this._lang === 'en' ? en : fr;
  }

  set hass(hass) {
    const e = hass.states["sensor.niu_hub_telemetry"];
    const a = e ? e.attributes : {};

    const val = (v, suffix = "") =>
      (v === undefined || v === null || v === "")
        ? "—"
        : `${escape(v)}${suffix}`;

    const signal = Number(a.signal_dbm);
    let signalText = val(a.signal_dbm, " dBm");

    if (Number.isFinite(signal)) {
      if (signal >= -80) signalText += ` · ${this._t("EXCELLENT", "EXCELLENT")}`;
      else if (signal >= -90) signalText += ` · ${this._t("BON", "GOOD")}`;
      else if (signal >= -100) signalText += ` · ${this._t("MOYEN", "FAIR")}`;
      else signalText += ` · ${this._t("FAIBLE", "WEAK")}`;
    }

    this.innerHTML = `
      <ha-card>
        <div class="wrap">
          <div class="title">NIUSMART HUB</div>

          <div class="grid">
            <div class="item">
              <div class="label">IP NETBIRD</div>
              <div class="value">${val(a.netbird_ip)}</div>
            </div>

            <div class="item">
              <div class="label">${this._t('OPÉRATEUR CELLULAIRE','CELLULAR OPERATOR')}</div>
              <div class="value">${val(a.cell_operator)}</div>
            </div>

            <div class="item">
              <div class="label">${this._t('SIGNAL CELLULAIRE','CELLULAR SIGNAL')}</div>
              <div class="value">${signalText}</div>
            </div>

            <div class="item">
              <div class="label">ICCID / SIM</div>
              <div class="value small">${val(a.sim_iccid)}</div>
            </div>

            <div class="item">
              <div class="label">CPU</div>
              <div class="value">${val(a.cpu_percent, " %")}</div>
            </div>

            <div class="item">
              <div class="label">${this._t('MÉMOIRE','MEMORY')}</div>
              <div class="value">
                ${val(a.memory_used_mb)} / ${val(a.memory_total_mb)} ${this._t('Mo','MB')}
              </div>
            </div>

            <div class="item">
              <div class="label">${this._t('DISQUE','DISK')}</div>
              <div class="value">
                ${val(a.disk_used_gb)} / ${val(a.disk_total_gb)} ${this._t('Go','GB')}
              </div>
            </div>

            <div class="item">
              <div class="label">${this._t('ESPACE LIBRE','FREE SPACE')}</div>
              <div class="value">
                ${val(a.disk_free_gb, " ${this._t('Go','GB')}")} · ${val(a.disk_used_percent, " % ${this._t('utilisé','used')}")}
              </div>
            </div>
          </div>
        </div>
      </ha-card>

      <style>
        ha-card {
          border-radius: 18px;
          overflow: hidden;
        }

        .wrap {
          padding: 18px 20px 20px;
        }

        .title {
          font-size: 20px;
          font-weight: 800;
          margin-bottom: 16px;
          letter-spacing: .3px;
        }

        .grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
        }

        .item {
          padding: 14px;
          border-radius: 14px;
          background: rgba(127,127,127,.10);
          min-width: 0;
        }

        .label {
          font-size: 11px;
          font-weight: 700;
          opacity: .65;
          margin-bottom: 6px;
        }

        .value {
          font-size: 16px;
          font-weight: 700;
          overflow-wrap: anywhere;
        }

        .value.small {
          font-size: 14px;
        }

        @media (max-width: 900px) {
          .grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }
      </style>
    `;
  }

  getCardSize() {
    return 3;
  }
}

customElements.define("niu-tablet-hub-card", NiuHubCard);
