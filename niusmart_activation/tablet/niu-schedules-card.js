import {escape, mappings, tablet} from "./shared.js";

class NiuSchedulesCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
    this._lang = String(this.config.lang || this.config.language || 'fr').toLowerCase() === 'en' ? 'en' : 'fr';
  }

  _t(fr, en) {
    return this._lang === 'en' ? en : fr;
  }

  set hass(hass) {
    this._hass = hass;

    if (!this._built) {
      this._build();
      this._built = true;
      this._load();
      this._timer = setInterval(() => this._load(), 60000);
    }
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
    this._built = false;
  }

  _build() {
    this.innerHTML = `
      <style>
        niu-tablet-schedules-card {
          display:block;
          width:100%;
        }

        .wrap {
          padding:6px;
          box-sizing:border-box;
        }

        .card {
          border:1px solid var(--divider-color);
          border-radius:8px;
          padding:10px;
          background:var(--ha-card-background,var(--card-background-color));
        }

        .title {
          font-size:15px;
          font-weight:800;
          margin-bottom:10px;
        }

        .group {
          border-top:1px solid var(--divider-color);
          padding:10px 0;
        }

        .group:first-of-type {
          border-top:0;
          padding-top:0;
        }

        .function {
          font-size:13px;
          font-weight:800;
          margin-bottom:5px;
        }

        .days {
          font-size:11px;
          opacity:.72;
          margin-bottom:6px;
        }

        .times {
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:8px;
        }

        .time-box {
          border:1px solid var(--divider-color);
          border-radius:6px;
          padding:7px 8px;
        }

        .time-label {
          font-size:9px;
          opacity:.65;
          text-transform:uppercase;
          margin-bottom:2px;
        }

        .time-value {
          font-size:14px;
          font-weight:800;
        }

        .note {
          margin-top:10px;
          padding:8px;
          border-radius:6px;
          background:rgba(3,169,244,.08);
          font-size:10px;
          font-weight:700;
          text-align:center;
        }

        .empty {
          padding:8px 0;
          opacity:.7;
          font-size:12px;
        }

        @media (max-width:700px) {
          .card {
            padding:7px;
          }

          .group {
            padding:8px 0;
          }

          .function {
            font-size:12px;
          }

          .time-value {
            font-size:13px;
          }
        }
      </style>

      <div class="wrap">
        <div class="card">
          <div class="title">${this._t('HORAIRES DU HUB', 'HUB SCHEDULES')}</div>
          <div id="list">
            <div class="empty">${this._t('Chargement des horaires...', 'Loading schedules...')}</div>
          </div>

          <div class="note">
            ${this._t('Lecture seulement — Les horaires se modifient sur NiuSmart.com', 'Read only — Schedules are edited on NiuSmart.com')}
          </div>
        </div>
      </div>
    `;
  }

  _dayLabel(days) {
    const names = {
      1:this._t('Lun','Mon'),
      2:this._t('Mar','Tue'),
      3:this._t('Mer','Wed'),
      4:this._t('Jeu','Thu'),
      5:this._t('Ven','Fri'),
      6:this._t('Sam','Sat'),
      7:this._t('Dim','Sun')
    };

    const sorted = [...days].sort((a,b) => a-b);

    if (sorted.length === 7) {
      return this._t('Tous les jours', 'Every day');
    }

    if (
      sorted.length === 5 &&
      sorted.join(',') === '1,2,3,4,5'
    ) {
      return this._lang === 'en' ? 'Mon · Tue · Wed · Thu · Fri' : 'Lun · Mar · Mer · Jeu · Ven';
    }

    if (
      sorted.length === 2 &&
      sorted.join(',') === '6,7'
    ) {
      return this._lang === 'en' ? 'Sat · Sun' : 'Sam · Dim';
    }

    return sorted.map(d => names[d] || d).join(' · ');
  }

  async _load() {
    const list = this.querySelector('#list');
    if (!list) return;

    try {
      const response = await fetch(
        '/local/niusmart/schedules.json?t=' + Date.now(),
        { cache:'no-store' }
      );

      if (!response.ok) {
        throw new Error('HTTP ' + response.status);
      }

      const data = await response.json();
      const options = tablet(this._hass)?.options;
      const events = Array.isArray(data.events) ? data.events.filter(event =>
        event.function_type !== 'exterior_lighting' || options?.has_exterior_lighting === 1
      ) : [];

      if (!events.length) {
        list.innerHTML =
          `<div class="empty">${this._t('Aucun horaire programmé dans le Hub.', 'No schedule programmed in the Hub.')}</div>`;
        return;
      }

      const functionNames = {
        block_control: this._t('INSTALLATION SANITAIRE', 'SANITARY INSTALLATION'),
        exterior_lighting: this._t('ÉCLAIRAGE EXTÉRIEUR', 'EXTERIOR LIGHTING')
      };

      const functionLabels = {
        block_control: [this._t('Fermeture','Closing'), this._t('Ouverture','Opening')],
        exterior_lighting: [this._t('Allumage','Turn on'), this._t('Extinction','Turn off')]
      };

      const grouped = {};

      for (const e of events) {
        const type = e.function_type || e.name || 'unknown';
        const start = String(e.on_at || '--').substring(0,5);
        const end = String(e.off_at || '--').substring(0,5);
        const key = `${e.schedule_id || e.name || ""}|${type}|${start}|${end}`;

        if (!grouped[key]) {
          grouped[key] = {
            type,
            start,
            end,
            entity_id: e.entity_id || "",
            mapping_id: e.mapping_id || "",
            relays: [],
            days:[]
          };
        }

        if (e.weekday != null) {
          grouped[key].days.push(Number(e.weekday));
        if (e.entity_id && !grouped[key].relays.includes(e.entity_id)) {
          grouped[key].relays.push(e.entity_id);
        }
        }
      }

      const relayNames = Object.fromEntries(mappings(tablet(this._hass), 'exterior_lighting')
        .map(item => [item.entity_id, item.custom_name]));

      list.innerHTML = Object.values(grouped).map(g => {
        const labels =
          functionLabels[g.type] || [this._t('Début','Start'),this._t('Fin','End')];

        return `
          <div class="group">
            <div class="function">
              ${escape(functionNames[g.type] || g.type)}
            </div>

            <div class="days">
              ${this._dayLabel([...new Set(g.days)])}

            </div>



            ${g.type === 'exterior_lighting' ? `<div style="font-size:10px;opacity:.75;margin-bottom:7px;"><strong>${this._t('Relais associés :','Associated relays:')}</strong> ${g.relays.map(id => relayNames[id] || this._hass?.states?.[id]?.attributes?.friendly_name || id).map(escape).join(' · ') || "—"}</div>` : ""}
            <div class="times">
              <div class="time-box">
                <div class="time-label">${labels[0]}</div>
                <div class="time-value">${escape(g.start)}</div>
              </div>

              <div class="time-box">
                <div class="time-label">${labels[1]}</div>
                <div class="time-value">${escape(g.end)}</div>
              </div>
            </div>
          </div>
        `;
      }).join('');

    } catch (err) {
      list.innerHTML =
        `<div class="empty">${this._t('Impossible de lire les horaires mémorisés du Hub.', 'Unable to read schedules stored in the Hub.')}</div>`;
    }
  }
}

customElements.define('niu-tablet-schedules-card', NiuSchedulesCard);