import {association, availableSwitch, entity, switchState, tablet} from "./shared.js";

class NiuToiletsCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
    this._lang = String(this.config.lang || this.config.language || 'fr').toLowerCase() === 'en' ? 'en' : 'fr';
  }

  _t(fr, en) {
    return this._lang === 'en' ? en : fr;
  }

  set hass(hass) {
    this._hass = hass;

    const config = tablet(hass);
    const count = config?.options.toilet_count || 0;
    if (this._count !== count || this._lastLang !== this._lang) {
      this._count = count;
      this._lastLang = this._lang;
      this.hidden = count === 0;
      this._build();
    }

    this._update();
  }

  getCardSize() {
    return 11;
  }

  _state(entityId) {
    return this._hass?.states?.[entityId]?.state ?? 'unavailable';
  }

  _items() {
    const config = tablet(this._hass);
    return Array.from({length: this._count}, (_, index) => {
      const n = index + 1;
      return {
        n, service: entity(config, 'toilet', n), occupied: entity(config, 'toilet_lock', n),
        door: entity(config, 'toilet_door', n)
      };
    });
  }

  _build() {
    const root = this.shadowRoot || this.attachShadow({ mode: 'open' });

    root.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100%;
          max-width: none !important;
        }

        ha-card {
          width: 100%;
          max-width: none !important;
          box-sizing: border-box;
          overflow: hidden;
        }

        .wrap {
          width: 100%;
          box-sizing: border-box;
          padding: 4px 6px 6px;
        }

        .title {
          font-size: 18px;
          font-weight: 800;
          margin: 0 0 5px;
        }

        .table-wrap {
          width: 100%;
          overflow-x: auto;
          -webkit-overflow-scrolling: touch;
        }

        .table {
          display: grid;
          width: 100%;
          min-width: 0;
          grid-template-columns:
            34px
            1.1fr
            1fr
            1fr
            1.1fr
            1.3fr;
          border-top: 1px solid var(--divider-color);
          border-left: 1px solid var(--divider-color);
          box-sizing: border-box;
        }

        .head,
        .cell {
          box-sizing: border-box;
          border-right: 1px solid var(--divider-color);
          border-bottom: 1px solid var(--divider-color);
          padding: 4px 6px;
          display: flex;
          align-items: center;
          min-height: 36px;
          line-height: 1.15;
        }

        .head {
          font-size: 11px;
          font-weight: 800;
          opacity: 0.8;
          text-transform: uppercase;
        }

        .cell {
          font-size: 13px;
          font-weight: 600;
        }

        .num {
          justify-content: center;
          text-align: center;
        }

        .toilet {
          gap: 5px;
        }

        .ok {
          color: #12b977;
          font-weight: 800;
        }

        .bad {
          color: #ef4444;
          font-weight: 800;
        }

        .control {
          padding: 5px;
        }

        .control button {
          width: 100%;
          min-height: 36px;
          border: 1px solid #a84444;
          border-radius: 5px;
          background: #5a2025;
          color: #fff;
          font-size: 11px;
          font-weight: 800;
          line-height: 1.1;
          padding: 6px 6px;
          cursor: pointer;
        }

        .control button.restore {
          background: #16563e;
          border-color: #25845f;
        }

        @media (max-width: 1000px) {
          .wrap {
            padding: 8px;
          }

          .title {
            font-size: 17px;
          }

          .table {
            min-width: 0;
            grid-template-columns:
              28px
              1.1fr
              1fr
              1fr
              1.1fr
              1.3fr;
          }

          .head,
          .cell {
            padding: 4px 5px;
            min-height: 34px;
          }

          .head {
            font-size: 10px;
          }

          .cell {
            font-size: 12px;
          }

          .control button {
            font-size: 10px;
            min-height: 34px;
          }
        }
      </style>

      <ha-card>
        <div class="wrap">
          <div class="title">🚻 ${this._t('ÉTAT DES TOILETTES', 'TOILET STATUS')} (${this._count})</div>

          <div class="table-wrap">
            <div class="table">
              <div class="head num">#</div>
              <div class="head">${this._t('TOILETTE', 'TOILET')}</div>
              <div class="head">${this._t('STATUT', 'STATUS')}</div>
              <div class="head">${this._t('DISPONIBILITÉ', 'AVAILABILITY')}</div>
              <div class="head">${this._t('STATUT PORTE', 'DOOR STATUS')}</div>
              <div class="head">${this._t('CONTRÔLE', 'CONTROL')}</div>

              ${this._items().map(t => `
                <div class="cell num">${t.n}</div>
                <div class="cell toilet">🚽 <strong>${this._t('Toilette', 'Toilet')} ${t.n}</strong></div>
                <div class="cell" id="service-${t.n}"></div>
                <div class="cell" id="occupied-${t.n}"></div>
                <div class="cell" id="door-${t.n}"></div>
                <div class="cell control">
                  <button id="btn-${t.n}"></button>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
      </ha-card>
    `;

    this._items().forEach(t => {
      root.getElementById(`btn-${t.n}`).addEventListener('click', () => {
        switchState(this._hass, entity(tablet(this._hass), 'toilet', t.n), 'toggle');
      });
    });
  }

  _update() {
    if (!this.shadowRoot || !this._hass) return;
    const contact = association(tablet(this._hass), 'block_closed_sensor');
    const blockClosed = this._state(contact?.entity_id) === (contact?.custom_name === 'off' ? 'off' : 'on');

    this._items().forEach(t => {
      const service = this._state(t.service);
      const occupied = blockClosed ? 'blocked' : this._state(t.occupied);
      const door = this._state(t.door);

      const serviceEl = this.shadowRoot.getElementById(`service-${t.n}`);
      const occupiedEl = this.shadowRoot.getElementById(`occupied-${t.n}`);
      const doorEl = this.shadowRoot.getElementById(`door-${t.n}`);
      const btn = this.shadowRoot.getElementById(`btn-${t.n}`);

      if (serviceEl) {
        if (service === 'on') {
          serviceEl.innerHTML = `<span class="bad">● ${this._t('HORS SERVICE', 'OUT OF SERVICE')}</span>`;
        } else if (service === 'off') {
          serviceEl.innerHTML = `<span class="ok">● ${this._t('EN SERVICE', 'IN SERVICE')}</span>`;
        } else {
          serviceEl.innerHTML = '<span>—</span>';
        }
      }

      if (occupiedEl) {
        if (occupied === 'blocked') {
          occupiedEl.innerHTML = `<span class="bad">● ${this._t('INSTALLATION FERMÉE', 'INSTALLATION CLOSED')}</span>`;
        } else if (occupied === 'on') {
          occupiedEl.innerHTML = `<span class="bad">● ${this._t('OCCUPÉE', 'OCCUPIED')}</span>`;
        } else if (occupied === 'off') {
          occupiedEl.innerHTML = `<span class="ok">● ${this._t('DISPONIBLE', 'AVAILABLE')}</span>`;
        } else {
          occupiedEl.innerHTML = '<span>—</span>';
        }
      }

      if (doorEl) {
        if (door === 'on') {
          doorEl.innerHTML = `<span class="ok">● ${this._t('PORTE FERMÉE', 'DOOR CLOSED')}</span>`;
        } else if (door === 'off') {
          doorEl.innerHTML = `<span class="bad">● ${this._t('PORTE OUVERTE', 'DOOR OPEN')}</span>`;
        } else {
          doorEl.innerHTML = '<span>—</span>';
        }
      }

      if (btn) {
        btn.disabled = !availableSwitch(this._hass, t.service);
        if (service === 'on') {
          btn.textContent = this._t('REMETTRE EN SERVICE', 'RETURN TO SERVICE');
          btn.classList.add('restore');
        } else {
          btn.textContent = this._t('METTRE HORS SERVICE', 'TAKE OUT OF SERVICE');
          btn.classList.remove('restore');
        }
      }
    });
  }
}

if (!customElements.get('niu-tablet-toilets-card')) {
  customElements.define('niu-tablet-toilets-card', NiuToiletsCard);
}
